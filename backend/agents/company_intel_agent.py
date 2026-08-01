"""
Company Intelligence — a full company dossier at effectively zero marginal cost.

Cost design:
  • Hard facts come from FREE public APIs: Wikipedia REST + Wikidata SPARQL
    (founders, founded year, HQ, employee count, revenue, website, ticker).
  • Judgement calls (culture, interview reputation, pros/cons) come from ONE
    Haiku call, then the whole dossier is cached for 30 days and shared by every
    user who looks up that company. Second lookup onwards = ₹0.
  • Logos via Clearbit's free logo endpoint.
"""
import asyncio
import json
import re
from typing import Optional

import httpx
from loguru import logger

from services.ai_cache import cache_get, cache_set
from services.claude_service import complete_claude_json
from services.company_size import classify_company

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_REST = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
UA = {"User-Agent": "MithraAI/1.0 (career platform; contact@mithraai.in)"}


# ── Free layer: Wikipedia + Wikidata ─────────────────────────────────────────

_NON_COMPANY = ("film", "movie", "song", "album", "tv series", "television series",
                "actor", "actress", "singer", "musician", "novel", "book ", "drama",
                "directed by", "screenplay", "soundtrack", " born ", "footballer",
                "cricketer", "politician", "village", "town", "given name", "surname",
                "episode", "sitcom", "web series", "short film", "documentary")
_COMPANY = ("company", "corporation", "e-commerce", "ecommerce", "startup", "start-up",
            "firm", "enterprise", "founded", "headquarter", "technology", "platform",
            "marketplace", "retailer", "private limited", "pvt", "ltd", "inc.", "bank",
            "manufacturer", "conglomerate", "multinational", "subsidiary", "brand",
            "fintech", "saas", "unicorn", "b2b", "b2c", "logistics", "services company")


async def _score_candidate(client: httpx.AsyncClient, title: str) -> tuple[int, dict]:
    """Fetch a candidate's summary and score how company-like it is.
    Higher = more likely the actual company. Negative = clearly not a company."""
    try:
        r = await client.get(WIKI_REST + title.replace(" ", "_"), headers=UA, timeout=10)
        d = r.json()
        desc = (d.get("description") or "").lower()
        extract = (d.get("extract") or "").lower()[:400]
        blob = f"{desc} {extract}"
        score = 0
        for kw in _NON_COMPANY:
            if kw in blob:
                score -= 5
        for kw in _COMPANY:
            if kw in blob:
                score += 3
        # Wikidata instance-of check is the strongest signal
        qid = d.get("wikibase_item") or ""
        if qid:
            try:
                rr = await client.get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json",
                                      headers=UA, timeout=8)
                claims = rr.json().get("entities", {}).get(qid, {}).get("claims", {})
                p31 = claims.get("P31", [])
                inst_ids = {c.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
                            for c in p31 if isinstance(c.get("mainsnak", {}).get("datavalue", {}).get("value"), dict)}
                # business (Q4830453), enterprise (Q6881511), company/organization types
                COMPANY_QIDS = {"Q4830453", "Q6881511", "Q783794", "Q891723", "Q167037",
                                "Q18388277", "Q19832486", "Q3918409", "Q22687"}
                NONCOMPANY_QIDS = {"Q11424", "Q5398426", "Q7889", "Q482994", "Q571", "Q5"}  # film, tv, video game, album, book, human
                if inst_ids & COMPANY_QIDS:
                    score += 20
                if inst_ids & NONCOMPANY_QIDS:
                    score -= 20
            except Exception:
                pass
        return score, d
    except Exception:
        return -100, {}


async def _wiki_search(client: httpx.AsyncClient, name: str) -> Optional[str]:
    """Return the Wikipedia title of the actual COMPANY named `name` — verified via
    Wikidata instance-of + description, so 'Udaan' resolves to the e-commerce firm
    rather than the 2010 film."""
    try:
        r = await client.get(WIKI_API, params={
            "action": "query", "list": "search",
            "srsearch": f"{name} company OR startup OR business OR e-commerce",
            "format": "json", "srlimit": 6,
        }, headers=UA, timeout=10)
        hits = r.json().get("query", {}).get("search", [])
        low0 = name.lower().split()[0]
        # Only consider titles that actually contain the searched name. If none do,
        # don't fall back to an unrelated top hit (that surfaced "E-commerce in India"
        # for "Udaan") — return None so the dossier stays thin rather than wrong.
        titles = [h["title"] for h in (hits or [])]
        candidates = [t for t in titles if low0 in t.lower()][:5]
        # Always try the explicit "(company)" disambiguation page too.
        for guess in (f"{name} (company)", f"{name} (business)"):
            if guess not in candidates:
                candidates.append(guess)
        if not candidates:
            return None

        scored = await asyncio.gather(*[_score_candidate(client, t) for t in candidates])
        best_title, best_score, best_d = None, -999, {}
        for title, (sc, d) in zip(candidates, scored):
            # Exact-name match gets a nudge so "Udaan" beats "Udaan Foundation" etc.
            if title.lower() == name.lower():
                sc += 4
            if sc > best_score:
                best_title, best_score, best_d = title, sc, d
        # A clearly non-company best match (e.g. only a film exists) → return None
        # so we don't present a movie as a company.
        if best_score < 0:
            return None
        return best_title
    except Exception:
        return None


async def _wiki_summary(client: httpx.AsyncClient, title: str) -> dict:
    try:
        r = await client.get(WIKI_REST + title.replace(" ", "_"), headers=UA, timeout=10)
        d = r.json()
        return {
            "summary": d.get("extract", ""),
            "wikipedia_url": (d.get("content_urls", {}).get("desktop", {}) or {}).get("page", ""),
            "thumbnail": (d.get("thumbnail", {}) or {}).get("source", ""),
            "wikidata_id": (d.get("wikibase_item") or ""),
        }
    except Exception:
        return {}


async def _wikidata_facts(client: httpx.AsyncClient, qid: str) -> dict:
    """Structured facts straight from Wikidata — free, no key."""
    if not qid:
        return {}
    query = f"""
    SELECT ?inceptionDate ?employees ?revenue ?revenueCurrencyLabel ?website ?tickerLabel
           (GROUP_CONCAT(DISTINCT ?founderLabel; separator=", ") AS ?founders)
           (GROUP_CONCAT(DISTINCT ?hqLabel; separator=", ") AS ?hq)
           (GROUP_CONCAT(DISTINCT ?industryLabel; separator=", ") AS ?industries)
           (GROUP_CONCAT(DISTINCT ?ceoLabel; separator=", ") AS ?ceos)
    WHERE {{
      OPTIONAL {{ wd:{qid} wdt:P571 ?inceptionDate. }}
      OPTIONAL {{ wd:{qid} wdt:P1128 ?employees. }}
      OPTIONAL {{ wd:{qid} p:P2139/psv:P2139 ?revNode.
                  ?revNode wikibase:quantityAmount ?revenue;
                           wikibase:quantityUnit ?revCur.
                  ?revCur rdfs:label ?revenueCurrencyLabel. FILTER(LANG(?revenueCurrencyLabel)="en") }}
      OPTIONAL {{ wd:{qid} wdt:P856 ?website. }}
      OPTIONAL {{ wd:{qid} wdt:P414 ?ticker. ?ticker rdfs:label ?tickerLabel. FILTER(LANG(?tickerLabel)="en") }}
      OPTIONAL {{ wd:{qid} wdt:P112 ?founder. ?founder rdfs:label ?founderLabel. FILTER(LANG(?founderLabel)="en") }}
      OPTIONAL {{ wd:{qid} wdt:P159 ?hqx. ?hqx rdfs:label ?hqLabel. FILTER(LANG(?hqLabel)="en") }}
      OPTIONAL {{ wd:{qid} wdt:P452 ?ind. ?ind rdfs:label ?industryLabel. FILTER(LANG(?industryLabel)="en") }}
      OPTIONAL {{ wd:{qid} wdt:P169 ?ceo. ?ceo rdfs:label ?ceoLabel. FILTER(LANG(?ceoLabel)="en") }}
    }}
    GROUP BY ?inceptionDate ?employees ?revenue ?revenueCurrencyLabel ?website ?tickerLabel
    LIMIT 1
    """
    try:
        r = await client.get(WIKIDATA_SPARQL, params={"query": query, "format": "json"},
                             headers=UA, timeout=15)
        rows = r.json().get("results", {}).get("bindings", [])
        if not rows:
            return {}
        b = rows[0]
        get = lambda k: b.get(k, {}).get("value", "")
        founded = get("inceptionDate")[:4] if get("inceptionDate") else ""
        return {
            "founded": founded,
            "founders": [f.strip() for f in get("founders").split(",") if f.strip()][:4],
            "ceo": [c.strip() for c in get("ceos").split(",") if c.strip()][:2],
            "headquarters": get("hq").split(",")[0].strip() if get("hq") else "",
            "employees": int(float(get("employees"))) if get("employees") else 0,
            "revenue": float(get("revenue")) if get("revenue") else 0,
            "revenue_currency": get("revenueCurrencyLabel"),
            "website": get("website"),
            "ticker": get("tickerLabel"),
            "industries": [i.strip() for i in get("industries").split(",") if i.strip()][:4],
        }
    except Exception as e:
        logger.warning(f"Wikidata lookup failed for {qid}: {e!r}")
        return {}


# ── Judgement layer: one cached Haiku call ───────────────────────────────────

SYSTEM_COMPANY_INTEL = """You are a careers analyst writing an honest, balanced company briefing
for a job seeker in India. Be specific and factual; never invent precise numbers you are unsure of.
If you don't know something, use an empty string/array rather than guessing.

Output ONLY this JSON:
{
  "what_they_do": "2 sentences on the business in plain language",
  "culture": "3-4 sentences: work culture, pace, management style, WLB reality",
  "employer_reputation": "2-3 sentences on how employees generally rate it and why",
  "interview_process": "2-3 sentences: typical rounds and what they screen for",
  "interview_difficulty": "Easy|Moderate|Hard",
  "pros": ["4 specific, honest pros"],
  "cons": ["3-4 specific, honest cons"],
  "who_thrives": "1-2 sentences on the kind of person who does well here",
  "salary_note": "1-2 sentences on how their pay compares in the Indian market",
  "roles_hired": ["5-6 common role families they hire for"],
  "employee_rating_estimate": <number 1.0-5.0, your best estimate of their typical Glassdoor-style rating>,
  "wlb_rating": <number 1.0-5.0>,
  "career_growth_rating": <number 1.0-5.0>,
  "confidence": "high|medium|low"
}"""


async def _ai_intel(name: str, facts: dict) -> dict:
    context = f"Company: {name}\n"
    if facts.get("summary"):
        context += f"Wikipedia: {facts['summary'][:900]}\n"
    if facts.get("industries"):
        context += f"Industries: {', '.join(facts['industries'])}\n"
    if facts.get("employees"):
        context += f"Employees: {facts['employees']:,}\n"
    if facts.get("headquarters"):
        context += f"HQ: {facts['headquarters']}\n"
    context += "\nWrite the honest careers briefing for an Indian job seeker."
    try:
        raw = await complete_claude_json(SYSTEM_COMPANY_INTEL,
                                         [{"role": "user", "content": context}], max_tokens=1600)
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Company AI intel failed for {name}: {e!r}")
        return {}


# ── Public API ───────────────────────────────────────────────────────────────

def _domain_for(name: str, website: str) -> str:
    if website:
        m = re.sub(r"^https?://(www\.)?", "", website).split("/")[0]
        if m:
            return m
    return re.sub(r"[^a-z0-9]", "", (name or "").lower()) + ".com"


async def get_company_intel(name: str) -> dict:
    """Full dossier. Cached 30 days per company — shared across all users."""
    key_name = (name or "").strip()
    if not key_name:
        return {}

    cached = await cache_get("company_intel_v2", key_name)
    if cached and isinstance(cached, dict):
        cached["cached"] = True
        return cached

    facts: dict = {}
    async with httpx.AsyncClient(follow_redirects=True) as client:
        title = await _wiki_search(client, key_name)
        if title:
            summary = await _wiki_summary(client, title)
            facts.update(summary)
            if summary.get("wikidata_id"):
                facts.update(await _wikidata_facts(client, summary["wikidata_id"]))

    ai = await _ai_intel(key_name, facts)

    website = facts.get("website", "")
    domain = _domain_for(key_name, website)
    size_bucket = classify_company(key_name)
    if facts.get("employees"):
        e = facts["employees"]
        size_bucket = "large" if e >= 5000 else "mid" if e >= 200 else "small"

    dossier = {
        "name": key_name,
        "logo": f"https://logo.clearbit.com/{domain}",
        "website": website,
        "wikipedia_url": facts.get("wikipedia_url", ""),
        "linkedin_url": f"https://www.linkedin.com/company/{re.sub(r'[^a-z0-9]+', '-', key_name.lower()).strip('-')}",
        "summary": facts.get("summary", ""),
        "founded": facts.get("founded", ""),
        "founders": facts.get("founders", []),
        "ceo": facts.get("ceo", []),
        "headquarters": facts.get("headquarters", ""),
        "employees": facts.get("employees", 0),
        "revenue": facts.get("revenue", 0),
        "revenue_currency": facts.get("revenue_currency", ""),
        "ticker": facts.get("ticker", ""),
        "industries": facts.get("industries", []),
        "size_bucket": size_bucket,
        # AI judgement layer
        "what_they_do": ai.get("what_they_do", ""),
        "culture": ai.get("culture", ""),
        "employer_reputation": ai.get("employer_reputation", ""),
        "interview_process": ai.get("interview_process", ""),
        "interview_difficulty": ai.get("interview_difficulty", ""),
        "pros": ai.get("pros", []),
        "cons": ai.get("cons", []),
        "who_thrives": ai.get("who_thrives", ""),
        "salary_note": ai.get("salary_note", ""),
        "roles_hired": ai.get("roles_hired", []),
        "employee_rating": ai.get("employee_rating_estimate", 0),
        "wlb_rating": ai.get("wlb_rating", 0),
        "career_growth_rating": ai.get("career_growth_rating", 0),
        "confidence": ai.get("confidence", "low"),
        "sources": [s for s in [
            "Wikipedia" if facts.get("summary") else "",
            "Wikidata" if facts.get("founded") or facts.get("employees") else "",
            "Mithra AI analysis" if ai else "",
        ] if s],
        "cached": False,
    }

    # Only cache dossiers that actually have substance
    if dossier["summary"] or dossier["what_they_do"]:
        await cache_set("company_intel_v2", dossier, 24 * 30, key_name)
    return dossier


async def suggest_companies(q: str) -> list[dict]:
    """Free typeahead via Wikipedia search."""
    q = (q or "").strip()
    if len(q) < 2:
        return []
    cached = await cache_get("company_suggest", q.lower())
    if cached and isinstance(cached, list):
        return cached
    out = []
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            # opensearch is Wikipedia's prefix/typeahead endpoint — far better for
            # partial words than full-text search ("flipk" → "Flipkart").
            r = await client.get(WIKI_API, params={
                "action": "opensearch", "search": q, "limit": 8,
                "namespace": 0, "format": "json",
            }, headers=UA, timeout=8)
            data = r.json()
            titles = data[1] if len(data) > 1 else []
            descs = data[2] if len(data) > 2 else []
            for i, title in enumerate(titles):
                hint = (descs[i] if i < len(descs) else "")[:90]
                # Skip obvious non-company pages
                if any(w in title.lower() for w in ("list of", "category:", "disambiguation")):
                    continue
                out.append({
                    "name": title,
                    "logo": f"https://logo.clearbit.com/{_domain_for(title, '')}",
                    "hint": re.sub(r"<[^>]+>", "", hint),
                })
            out = out[:6]
    except Exception:
        pass
    if out:
        await cache_set("company_suggest", out, 24 * 30, q.lower())
    return out
