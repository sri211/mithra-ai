"""
Network Intelligence Agent — finds real connections at target companies.
Uses Google search to find actual LinkedIn profiles, then enriches with Claude-drafted outreach.
"""
import json
import os
import urllib.parse
import httpx
from loguru import logger
from services.claude_service import complete_claude_json, complete_claude

SYSTEM_NETWORK = """You are a world-class networking strategist and talent intelligence expert.

Given a target company and role, generate 10 realistic, valuable people to connect with.
These should be a diverse mix of: hiring managers, recruiters, team members, alumni, and influencers.

Use real-sounding Indian professional names. Make all details realistic and specific to the company.

For each person, generate a LinkedIn SEARCH URL (not a direct profile URL) that actually finds them:
- Format: https://www.linkedin.com/search/results/people/?keywords=<ROLE>+<COMPANY>&origin=GLOBAL_SEARCH_HEADER
- Example: https://www.linkedin.com/search/results/people/?keywords=Engineering+Manager+Google+India&origin=GLOBAL_SEARCH_HEADER

Output ONLY valid JSON (no markdown, no preamble):
{
  "company_insights": {
    "culture": "brief culture description",
    "hiring_status": "Actively Hiring",
    "growth_stage": "Enterprise",
    "key_teams": ["team1", "team2"]
  },
  "connections": [
    {
      "id": "conn_001",
      "name": "Full Name",
      "role": "their exact job title at the company",
      "company": "<the target company>",
      "avatar": "initials e.g. AK",
      "color": "#hexcolor",
      "type": "hiring_manager|recruiter|team_member|alumnus|influencer",
      "mutual": <integer 3-20>,
      "why": "1-2 sentence specific reason why connecting helps",
      "draft": "personalized LinkedIn message under 300 chars referencing something specific",
      "linkedin_search": "https://www.linkedin.com/search/results/people/?keywords=<encoded+search>&origin=GLOBAL_SEARCH_HEADER",
      "email_pattern": "firstname.lastname@<company_domain>.com"
    }
  ],
  "networking_action_plan": [
    "Step 1: ...",
    "Step 2: ...",
    "Step 3: ..."
  ]
}

Generate exactly 10 connections with diverse types. Make the names, roles, and search URLs specific to the target company."""

SYSTEM_OUTREACH = """Write a personalized LinkedIn connection request message (max 300 chars).
Reference something specific — their work, company project, mutual connection.
Professional but human. No desperation, no copy-paste feel."""


def build_linkedin_search_url(name: str, role: str, company: str) -> str:
    """Build a LinkedIn people search URL for a specific name+company."""
    if name and name not in ("Unknown", ""):
        query = f"{name} {company}".strip()
    else:
        query = f"{role} {company}".strip()
    encoded = urllib.parse.quote(query)
    return f"https://www.linkedin.com/search/results/people/?keywords={encoded}&origin=GLOBAL_SEARCH_HEADER"


async def search_linkedin_profiles(role: str, company: str) -> list[dict]:
    """
    Search for real LinkedIn profiles using DuckDuckGo HTML (most permissive for servers).
    Falls back to constructing name-based LinkedIn search URLs.
    Returns list of {name, linkedin_url, role_snippet}.
    """
    from bs4 import BeautifulSoup
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.5",
    }
    profiles = []
    seen_urls = set()

    # Try DuckDuckGo HTML search (no JS required, less blocking)
    queries = [
        f'"{company}" "{role}" site:linkedin.com/in',
        f'"{company}" {role} linkedin.com/in',
    ]
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=15) as client:
        for q in queries:
            if len(profiles) >= 6:
                break
            try:
                encoded_q = urllib.parse.quote(q)
                r = await client.get(f"https://html.duckduckgo.com/html/?q={encoded_q}")
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                # DuckDuckGo HTML result links
                for a in soup.find_all("a", class_="result__a") or soup.find_all("a", href=True):
                    href = a.get("href", "")
                    # DDG wraps URLs in redirect: extract actual URL
                    if "uddg=" in href:
                        href = urllib.parse.unquote(href.split("uddg=")[1].split("&")[0])
                    if "linkedin.com/in/" not in href:
                        continue
                    # Clean to base profile URL
                    base = href.split("?")[0].rstrip("/")
                    if base in seen_urls:
                        continue
                    seen_urls.add(base)
                    title = a.get_text(strip=True)
                    name = title.split(" - ")[0].split(" | ")[0].split(" – ")[0].strip()
                    role_snippet = ""
                    if " - " in title:
                        role_snippet = title.split(" - ", 1)[1].split(" | ")[0].strip()
                    profiles.append({"name": name, "linkedin_url": base, "role_snippet": role_snippet})
            except Exception as e:
                logger.warning(f"DDG search failed for '{q}': {e}")

    logger.info(f"DDG found {len(profiles)} LinkedIn profiles for {role} at {company}")
    return profiles[:8]


async def find_people_from_hunter(company_domain: str) -> list[dict]:
    """
    Use Hunter.io domain search to find REAL people with verified emails at a company.
    Returns list of {name, email, position, linkedin_url}.
    """
    key = os.getenv("HUNTER_API_KEY", "")
    if not key:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.hunter.io/v2/domain-search",
                params={"domain": company_domain, "api_key": key, "limit": 10}
            )
        if r.status_code == 200:
            data = r.json().get("data", {})
            emails = data.get("emails", [])
            people = []
            for e in emails:
                if not e.get("value"):
                    continue
                first = e.get("first_name", "")
                last = e.get("last_name", "")
                name = f"{first} {last}".strip()
                if not name:
                    continue
                # Build LinkedIn search URL for this specific person
                linkedin_search = build_linkedin_search_url(name, e.get("position", ""), company_domain.split(".")[0])
                people.append({
                    "name": name,
                    "email": e["value"],
                    "position": e.get("position", ""),
                    "linkedin_search": linkedin_search,
                    "verified": e.get("verification", {}).get("status") == "valid",
                    "confidence": e.get("confidence", 0),
                    "source": "hunter",
                })
            # Sort by confidence score
            people.sort(key=lambda x: x["confidence"], reverse=True)
            logger.info(f"Hunter.io found {len(people)} real people at {company_domain}")
            return people[:15]
    except Exception as e:
        logger.warning(f"Hunter.io failed for {company_domain}: {e}")
    return []


def get_company_domain(company: str) -> str:
    """Best-guess company email domain — strips geographic/legal suffixes."""
    KNOWN = {
        "google": "google.com", "microsoft": "microsoft.com", "amazon": "amazon.com",
        "flipkart": "flipkart.com", "swiggy": "swiggy.in", "zomato": "zomato.com",
        "razorpay": "razorpay.com", "cred": "cred.club", "phonepe": "phonepe.com",
        "meesho": "meesho.com", "byju": "byjus.com", "paytm": "paytm.com",
        "infosys": "infosys.com", "wipro": "wipro.com", "tcs": "tcs.com",
        "netflix": "netflix.com", "meta": "meta.com", "apple": "apple.com",
        "uber": "uber.com", "ola": "olacabs.com", "myntra": "myntra.com",
        "glanbia": "glanbia.com", "deloitte": "deloitte.com", "pwc": "pwc.com",
        "mckinsey": "mckinsey.com", "bcg": "bcg.com", "accenture": "accenture.com",
        "nestle": "nestle.com", "unilever": "unilever.com", "hul": "unilever.com",
        "marico": "marico.com", "dabur": "dabur.com", "godrej": "godrej.com",
        "reliance": "ril.com", "tata": "tata.com", "mahindra": "mahindra.com",
        "hcl": "hcltech.com", "tech mahindra": "techmahindra.com",
    }
    # Strip common geographic/legal suffixes before lookup
    STRIP_WORDS = [
        " india", " india pvt", " india ltd", " india limited", " pvt ltd",
        " private limited", " limited", " ltd", " inc", " corp", " group",
        " holdings", " global", " international", " asia", " apac",
        " south asia", " & co", " llp", " llc",
    ]
    clean = company.lower()
    for sw in STRIP_WORDS:
        clean = clean.replace(sw, "")
    clean = clean.strip()

    # Check known domains first (on original and stripped)
    for key_company, domain in KNOWN.items():
        if key_company in clean or key_company in company.lower():
            return domain

    # Fall back: use the stripped company name as domain
    clean_slug = clean.replace(" ", "").replace(".", "").replace(",", "")
    return f"{clean_slug}.com"


COLORS = ["#7c3aed", "#06b6d4", "#10b981", "#f59e0b", "#ec4899", "#6366f1", "#ef4444", "#8b5cf6", "#14b8a6", "#f97316"]


SYSTEM_ENRICH = """You are a networking strategist. Given a list of real LinkedIn profiles found via Google search, enrich each with:
1. Their likely relationship to the target role (hiring_manager / recruiter / team_member / alumnus / influencer)
2. A specific, personalized LinkedIn outreach message (under 300 chars) that references something real about their background
3. A clear reason why connecting with them helps the job seeker

Also suggest 3 additional role TYPES to search for (not fake names — just roles like "Head of HR at {company}", "Ecommerce Category Manager at {company}").

Output JSON:
{
  "enriched": [
    {
      "id": "conn_001",
      "name": "<from input>",
      "role": "<their actual role from snippet>",
      "company": "<company>",
      "type": "hiring_manager|recruiter|team_member|alumnus|influencer",
      "why": "<specific reason this person helps>",
      "draft": "<personalized message under 300 chars>",
      "linkedin_url": "<from input — do not change>",
      "is_real": true
    }
  ],
  "additional_searches": [
    {"role": "Head of HR", "search_label": "HR / Recruiter"},
    {"role": "Category Manager", "search_label": "Category Manager"}
  ],
  "company_insights": {
    "culture": "<1 line about company culture>",
    "hiring_status": "Actively Hiring|Selective Hiring|Unknown",
    "key_teams": ["team1", "team2"]
  }
}"""


async def find_connections(company: str, target_role: str, user_profile: dict) -> dict:
    domain = get_company_domain(company)
    logger.info(f"Network search: company={company}, domain={domain}, role={target_role}")

    # Step 1a: PRIMARY — Hunter.io real people with verified emails
    hunter_people = await find_people_from_hunter(domain)

    # Step 1b: SECONDARY — search for LinkedIn profiles via DuckDuckGo
    bing_profiles = await search_linkedin_profiles(target_role, company)

    # Merge: prefer Hunter.io people (have real emails), enrich with LinkedIn URLs
    bing_name_map = {p["name"].lower(): p for p in bing_profiles}
    real_profiles = []

    # Add Hunter.io people first (real verified emails)
    for p in hunter_people:
        name_lower = p["name"].lower()
        bing_match = bing_name_map.get(name_lower)
        real_profiles.append({
            "name": p["name"],
            "linkedin_url": bing_match["linkedin_url"] if bing_match else build_linkedin_search_url(p["name"], p.get("position", target_role), company),
            "role_snippet": p.get("position", ""),
            "email": p["email"],
            "email_verified": p.get("verified", False),
            "source": "hunter",
        })

    # Add Bing-only profiles (no Hunter match)
    hunter_names = {p["name"].lower() for p in hunter_people}
    for p in bing_profiles:
        if p["name"].lower() not in hunter_names:
            real_profiles.append({
                "name": p["name"],
                "linkedin_url": p["linkedin_url"],
                "role_snippet": p.get("role_snippet", ""),
                "email": None,
                "email_verified": False,
                "source": "bing",
            })

    logger.info(f"Total real profiles: {len(real_profiles)} (Hunter: {len(hunter_people)}, DDG: {len(bing_profiles)})")

    # Step 2: If we found real profiles, enrich them with Claude
    if real_profiles:
        enrich_content = (
            f"Company: {company}\n"
            f"Target Role: {target_role}\n"
            f"User Profile: {json.dumps(user_profile) if user_profile else 'job seeker'}\n\n"
            f"Real people found at {company} (via Hunter.io verified emails + DuckDuckGo LinkedIn search):\n"
            f"{json.dumps(real_profiles, indent=2)}\n\n"
            f"Enrich these REAL people with connection type, outreach messages, and reasons to connect. "
            f"Also suggest 2-3 additional role types to search for at {company}."
        )
        try:
            raw = await complete_claude_json(SYSTEM_ENRICH, [{"role": "user", "content": enrich_content}], max_tokens=4096)
            enriched_data = json.loads(raw)
            enriched = enriched_data.get("enriched", [])
            additional = enriched_data.get("additional_searches", [])
            company_insights = enriched_data.get("company_insights", {})

            # Build email lookup from real_profiles
            real_email_map = {p["name"].lower(): p for p in real_profiles}

            connections = []
            for i, person in enumerate(enriched):
                name = person.get("name", "")
                linkedin_url = person.get("linkedin_url", "")
                # Get real email from our Hunter.io source
                source_data = real_email_map.get(name.lower(), {})
                real_email = source_data.get("email")
                email_verified = source_data.get("email_verified", False)

                connections.append({
                    "id": f"conn_{i+1:03d}",
                    "name": name,
                    "role": person.get("role", source_data.get("role_snippet", target_role)),
                    "company": company,
                    "avatar": "".join(p[0] for p in name.split()[:2]).upper() if name else "??",
                    "color": COLORS[i % len(COLORS)],
                    "type": person.get("type", "team_member"),
                    "mutual": 0,
                    "why": person.get("why", ""),
                    "draft": person.get("draft", ""),
                    "linkedin_url": linkedin_url,
                    "linkedin_search": linkedin_url or build_linkedin_search_url(name, person.get("role", target_role), company),
                    "email_pattern": real_email or f"<firstname>.<lastname>@{domain}",
                    "email_verified": email_verified,
                    "is_real": True,
                    "source": source_data.get("source", "bing"),
                })

            # Add search suggestion cards for additional roles
            for i, sr in enumerate(additional[:3]):
                role_label = sr.get("role", "")
                connections.append({
                    "id": f"search_{i+1:03d}",
                    "name": f"Find: {sr.get('search_label', role_label)}",
                    "role": role_label,
                    "company": company,
                    "avatar": "🔍",
                    "color": "#64748b",
                    "type": "search_suggestion",
                    "mutual": 0,
                    "why": f"Search for real {role_label} professionals at {company} on LinkedIn",
                    "draft": "",
                    "linkedin_search": build_linkedin_search_url("", role_label, company),
                    "email_pattern": "",
                    "is_real": False,
                    "is_search_card": True,
                })

            return {
                "connections": connections,
                "company_insights": {
                    "culture": company_insights.get("culture", f"Professional environment at {company}"),
                    "hiring_status": company_insights.get("hiring_status", "Actively Hiring"),
                    "growth_stage": "Enterprise",
                    "key_teams": company_insights.get("key_teams", []),
                },
                "networking_action_plan": [
                    f"1. Connect with real people found above — these are verified LinkedIn profiles",
                    f"2. Personalize the AI-drafted message before sending",
                    f"3. Mention a specific detail from their profile or company news",
                    f"4. Follow up once after 1 week if no response",
                ],
                "real_profiles_found": len(enriched),
            }
        except Exception as e:
            logger.error(f"Enrich step failed: {e}")

    # Step 3: Fallback — return search-suggestion cards (no fake names)
    role_types = [
        (f"Head of {target_role}", "Senior Leader"),
        (f"Recruiter {company}", "Talent Acquisition"),
        (f"{target_role} Manager", "Hiring Manager"),
        (f"HR Business Partner {company}", "HR Contact"),
        (f"Director {target_role} {company}", "Director"),
    ]
    connections = []
    for i, (search_role, label) in enumerate(role_types):
        connections.append({
            "id": f"search_{i+1:03d}",
            "name": f"Search: {label}",
            "role": search_role,
            "company": company,
            "avatar": "🔍",
            "color": COLORS[i % len(COLORS)],
            "type": "search_suggestion",
            "mutual": 0,
            "why": f"Search LinkedIn for real {label} professionals at {company}",
            "draft": "",
            "linkedin_search": build_linkedin_search_url("", search_role, company),
            "email_pattern": f"<firstname>.<lastname>@{domain}",
            "is_real": False,
            "is_search_card": True,
        })
    return {
        "connections": connections,
        "company_insights": {
            "culture": f"Professional environment at {company}",
            "hiring_status": "Unknown",
            "growth_stage": "Enterprise",
            "key_teams": [],
        },
        "networking_action_plan": [
            f"1. Click 'Find on LinkedIn' to search for real people at {company}",
            "2. Connect with whoever you find in these roles",
            "3. Personalize your outreach with something specific from their profile",
        ],
        "real_profiles_found": 0,
    }


async def draft_outreach(person: dict, user_profile: dict, context: str = "") -> str:
    content = f"Target person: {json.dumps(person)}\nMy profile: {json.dumps(user_profile)}\nContext: {context}"
    messages = [{"role": "user", "content": content}]
    return await complete_claude(SYSTEM_OUTREACH, messages, max_tokens=150)
