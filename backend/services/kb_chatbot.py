"""
Mithra chatbot — a FREE, self-hosted retrieval assistant. No Claude, no external
API cost. Answers from a curated Indian-jobs + Mithra knowledge base, injects live
user data, and learns from 👍/👎 feedback.

Design:
  • Pure-Python retrieval (stdlib only) — tiny RAM, instant, ₹0.
  • BM25-style keyword scoring + fuzzy match + tag/synonym boosting.
  • Live-data intents (credits, applications, resume) answered from the DB.
  • Feedback loop: upvoted Q→A pairs get boosted; gaps are logged for admin.
  • Optional Gemini fallback stays OFF unless GEMINI_API_KEY is set (future switch).
"""
import math
import os
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Optional

from kb_seed import KB_SEED

_STOP = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "to", "of", "in", "on",
    "for", "and", "or", "with", "at", "by", "i", "me", "my", "you", "your", "how", "what",
    "do", "does", "can", "could", "should", "would", "will", "it", "this", "that", "as",
    "if", "so", "but", "not", "no", "yes", "have", "has", "get", "want", "need", "please",
    "tell", "about", "give", "show", "there", "here", "am", "we", "our",
}

# lightweight synonym expansion so "pay"/"ctc"/"package" all hit "salary"
_SYNONYMS = {
    "ctc": "salary", "package": "salary", "pay": "salary", "compensation": "salary",
    "stipend": "salary", "sde": "developer", "swe": "developer", "programmer": "developer",
    "coder": "developer", "pm": "product manager", "hr": "human resources",
    "ba": "business analyst", "ds": "data scientist", "ml": "machine learning",
    "cv": "resume", "resumes": "resume", "jd": "job description", "wfh": "remote",
    "freshers": "fresher", "graduate": "fresher", "interview": "interview",
    "credit": "credits", "coin": "credits", "coins": "credits", "cost": "credits",
    "apply": "auto apply", "autoapply": "auto apply",
}


def _norm(word: str) -> str:
    w = re.sub(r"[^a-z0-9+#]", "", word.lower())
    w = _SYNONYMS.get(w, w)
    # naive stem
    for suf in ("ing", "ers", "er", "ies", "es", "s"):
        if len(w) > 4 and w.endswith(suf):
            w = w[: -len(suf)]
            break
    return w


def _tokenize(text: str) -> list[str]:
    toks = [_norm(t) for t in re.findall(r"[a-zA-Z0-9+#]+", (text or "").lower())]
    return [t for t in toks if t and t not in _STOP and len(t) > 1]


class _KB:
    """In-memory index over seed + DB-stored entries. Rebuilt when DB entries change."""

    def __init__(self):
        self.entries: list[dict] = []
        self.idf: dict[str, float] = {}
        self._loaded_dynamic = False
        self._build(KB_SEED)

    def _build(self, entries: list[dict]):
        self.entries = []
        df: Counter = Counter()
        for e in entries:
            triggers = e.get("triggers", [])
            tokens: Counter = Counter()
            for t in triggers:
                tokens.update(_tokenize(t))
            tokens.update({_norm(t): 2 for t in e.get("tags", [])})  # tags weigh more
            ent = {
                "id": e["id"],
                "answer": e["answer"],
                "category": e.get("category", "general"),
                "tokens": tokens,
                "trigger_text": " ".join(triggers).lower(),
                "boost": float(e.get("boost", 0)),
            }
            self.entries.append(ent)
            for tok in set(tokens):
                df[tok] += 1
        n = max(len(self.entries), 1)
        self.idf = {tok: math.log(1 + n / (1 + c)) for tok, c in df.items()}

    def merge_dynamic(self, dynamic: list[dict]):
        """Add admin/learned entries from the DB and rebuild the index."""
        self._build(KB_SEED + dynamic)
        self._loaded_dynamic = True

    def search(self, query: str) -> tuple[Optional[dict], float]:
        q = _tokenize(query)
        if not q:
            return None, 0.0
        qc = Counter(q)
        best, best_score = None, 0.0
        for e in self.entries:
            # BM25-ish dot product on shared tokens, weighted by IDF
            score = 0.0
            for tok, qn in qc.items():
                if tok in e["tokens"]:
                    score += self.idf.get(tok, 1.0) * min(qn, 2) * min(e["tokens"][tok], 3)
            # fuzzy safety net against the trigger text (handles typos / phrasing)
            fuzzy = SequenceMatcher(None, query.lower()[:120], e["trigger_text"][:200]).ratio()
            score = score + fuzzy * 2.5 + e["boost"]
            if score > best_score:
                best, best_score = e, score
        # Normalize roughly to 0..1 by query length so the threshold is stable
        norm = best_score / (len(q) * 3 + 3)
        return best, min(norm, 1.0)


_kb = _KB()


def rebuild_from_db(dynamic_entries: list[dict]):
    _kb.merge_dynamic(dynamic_entries)


# ── Live-data intents ────────────────────────────────────────────────────────

def _live_answer(message: str, ctx: dict) -> Optional[str]:
    """Answer questions about the user's own data. ctx = {credits, plan, apps, resume_loaded, name}."""
    m = message.lower()
    name = ctx.get("name") or "there"

    if any(k in m for k in ("my credit", "credits left", "credit balance", "how many credit",
                            "coins left", "balance", "credits do i")):
        bal = ctx.get("credits")
        plan = ctx.get("plan", "free")
        if bal is not None:
            return (f"You have **{bal} credits** on your {plan} plan. "
                    "Credits refresh monthly. Costs: Resume Adapt 25 · AI Build 15 · Interview 10 · "
                    "Auto-Apply 8 · Cover Letter 5 · Job Search 2 · Chat 1 · **Resume Score is free**. "
                    "Top up or upgrade anytime on the [Pricing](/pricing) page.")
    if any(k in m for k in ("my application", "applications", "how many jobs applied", "my tracker",
                            "jobs i applied", "application status")):
        apps = ctx.get("apps")
        if apps is not None:
            return (f"You have **{apps} application(s)** on your [Tracker](/tracker) board. "
                    "Every job you apply to (via Job Finder, Auto-Apply, or manually) lands there so you "
                    "can track it from applied → interview → offer.")
    # Only fire for STATUS questions about their resume — not "how to improve my resume".
    resume_status = (any(k in m for k in ("do i have a resume", "is my resume", "which resume do i",
                                          "resume loaded", "have i uploaded", "did i save my resume",
                                          "resume saved"))
                     and not any(k in m for k in ("how", "tip", "improve", "ats", "better", "write",
                                                  "make", "format", "template", "score")))
    if resume_status:
        if ctx.get("resume_loaded"):
            return ("You have a resume ready. Use [Resume Adaptor](/resume-adaptor) to tailor it to a job, "
                    "or [Resume Score](/resume-score) for a free ATS check.")
        return ("You don't have a saved resume yet. Head to [Resume Builder](/resume-builder) — build from "
                "scratch, a conversation, or upload your existing PDF — then hit **Save Resume**.")
    if any(k in m for k in ("my plan", "which plan", "am i pro", "am i elite", "my subscription")):
        return f"You're on the **{ctx.get('plan','free')}** plan. See what each plan unlocks on [Pricing](/pricing)."
    return None


# ── Public API ───────────────────────────────────────────────────────────────

GREETINGS = ("hi", "hello", "hey", "hii", "helo", "yo", "namaste", "hola")

CONFIDENCE_THRESHOLD = 0.16   # below this we treat it as "not confident"


def answer(message: str, ctx: dict) -> dict:
    """Returns {answer, entry_id, confidence, matched}. ctx carries live user data."""
    msg = (message or "").strip()
    if not msg:
        return {"answer": "Ask me anything about your job search, resumes, interviews, or how to use Mithra!",
                "entry_id": "empty", "confidence": 1.0, "matched": True}

    low = msg.lower().strip("!?. ")
    if low in GREETINGS or low.startswith(("hi ", "hello ", "hey ")):
        name = ctx.get("name") or "there"
        return {"answer": f"Hi {name}! 👋 I'm Mithra, your career assistant. Ask me about salaries, "
                          "top companies, interview prep, resume tips, or how to use any feature here. "
                          "What are you working on?",
                "entry_id": "greeting", "confidence": 1.0, "matched": True}

    # 1. Live user-data questions win
    live = _live_answer(msg, ctx)
    if live:
        return {"answer": live, "entry_id": "live", "confidence": 1.0, "matched": True}

    # 2. Knowledge-base retrieval
    entry, conf = _kb.search(msg)
    if entry and conf >= CONFIDENCE_THRESHOLD:
        return {"answer": entry["answer"], "entry_id": entry["id"], "confidence": round(conf, 3), "matched": True}

    # 3. Optional Gemini fallback (only if the key is configured — off by default)
    if os.getenv("GEMINI_API_KEY"):
        g = _gemini_fallback(msg, ctx)
        if g:
            return {"answer": g, "entry_id": "gemini", "confidence": 0.5, "matched": True}

    # 4. Honest miss — guide them, and log the gap for admin to fill
    suggestion = entry["answer"] if entry else ""
    return {
        "answer": ("I don't have a confident answer for that yet — I'm still learning! "
                   "Here's what I can help with: **salaries** by role, **top companies**, **in-demand skills**, "
                   "**interview prep**, **resume tips**, and **how to use** Resume Builder, Job Finder, "
                   "Resume Adaptor, Auto-Apply, Company Intel, Interview Prep or the Tracker. "
                   "Try rephrasing, or tell me which of these you need."
                   + (f"\n\n_Closest topic I found:_\n{suggestion[:300]}" if suggestion and conf > 0.08 else "")),
        "entry_id": "unknown",
        "confidence": round(conf, 3),
        "matched": False,
    }


def _gemini_fallback(message: str, ctx: dict) -> Optional[str]:
    """Free-tier Google Gemini fallback. Disabled unless GEMINI_API_KEY is set."""
    key = os.getenv("GEMINI_API_KEY", "")
    if not key:
        return None
    try:
        import httpx
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"gemini-2.0-flash:generateContent?key={key}")
        sys = ("You are Mithra, a concise, friendly career assistant for job seekers in India. "
               "Answer in 3-5 sentences, practical and specific. If asked about the Mithra app, "
               "mention the relevant feature (Resume Builder, Job Finder, Resume Adaptor, Auto-Apply, "
               "Company Intel, Interview Prep, Tracker).")
        payload = {"contents": [{"parts": [{"text": f"{sys}\n\nUser: {message}"}]}]}
        with httpx.Client(timeout=15) as c:
            r = c.post(url, json=payload)
            if r.status_code != 200:
                return None
            cand = r.json().get("candidates", [{}])[0]
            parts = cand.get("content", {}).get("parts", [{}])
            return (parts[0].get("text") or "").strip() or None
    except Exception:
        return None
