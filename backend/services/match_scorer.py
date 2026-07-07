"""
Deterministic resume-to-job match scoring. Zero API cost.

Replaces the Claude ranking call: computes skill overlap, title/domain
similarity, seniority alignment and location fit locally, and produces the
same fields the UI shows (match_score, skills_matched, skills_missing,
experience_match, domain_match). Deterministic — the same resume and job
always get the same score, which users read as "correct".
"""
import re

# Common skill aliases so "JS" matches "JavaScript" etc.
_ALIASES = {
    "js": "javascript", "ts": "typescript", "py": "python",
    "reactjs": "react", "react.js": "react", "nextjs": "next.js",
    "nodejs": "node.js", "node": "node.js", "postgres": "postgresql",
    "ml": "machine learning", "ai": "artificial intelligence",
    "gcp": "google cloud", "aws": "amazon web services", "k8s": "kubernetes",
    "ppc": "paid ads", "sem": "search engine marketing", "seo": "search engine optimization",
    "pm": "product management", "hr": "human resources", "fp&a": "financial planning",
    "excel": "microsoft excel", "powerpoint": "microsoft powerpoint",
}

_STOPWORDS = {
    "and", "or", "the", "a", "an", "of", "in", "for", "with", "to", "at",
    "senior", "junior", "lead", "principal", "staff", "associate", "assistant",
    "i", "ii", "iii", "intern", "trainee", "manager", "executive", "specialist",
    "engineer", "developer", "analyst", "consultant", "coordinator", "head",
}

_SENIORITY_YEARS = {"entry": (0, 2), "mid": (2, 5), "senior": (5, 9), "lead": (8, 14), "principal": (10, 20), "director": (12, 30)}


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    return _ALIASES.get(s, s)


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9+#.]+", (text or "").lower())
    return {_norm(w) for w in words if w not in _STOPWORDS and len(w) > 1}


def _resume_skills(resume: dict) -> list[str]:
    raw = resume.get("skills", {})
    if isinstance(raw, dict):
        skills = raw.get("technical", []) + raw.get("soft", []) + raw.get("tools", []) + raw.get("certifications", [])
    elif isinstance(raw, list):
        skills = raw
    else:
        skills = []
    return [s for s in (str(x).strip() for x in skills) if s]


def _years_of_experience(resume: dict) -> float:
    exps = resume.get("experience", []) or []
    total = 0.0
    for e in exps:
        try:
            start = int(re.search(r"\d{4}", str(e.get("start", ""))).group())
            end_str = str(e.get("end", ""))
            end_m = re.search(r"\d{4}", end_str)
            end = 2026 if e.get("current") or not end_m else int(end_m.group())
            total += max(0, min(end - start, 15))
        except Exception:
            total += 1.5  # can't parse — assume a modest stint
    return min(total, 25)


def _skill_in_text(skill: str, text_tokens: set[str], text: str) -> bool:
    s = _norm(skill)
    if s in text_tokens:
        return True
    # multi-word skills: substring check against raw text
    if " " in s and s in text:
        return True
    return False


def score_job_for_resume(job: dict, resume: dict) -> dict:
    """Mutates and returns job with match_score + breakdown fields."""
    resume_skills = _resume_skills(resume)
    resume_skill_norms = {_norm(s) for s in resume_skills}
    resume_title = (resume.get("personal", {}) or {}).get("title", "") or ""
    resume_roles = " ".join(str(e.get("role", "")) for e in (resume.get("experience") or [])[:4])
    resume_domain_text = f"{resume_title} {resume_roles} {resume.get('summary', '')}".lower()
    resume_domain_tokens = _tokenize(resume_domain_text)
    years = _years_of_experience(resume)

    job_title = job.get("title", "") or ""
    job_desc = (job.get("description", "") or "").lower()
    job_skills = [str(s) for s in (job.get("skills") or [])]
    job_text = f"{job_title.lower()} {job_desc} {' '.join(job_skills).lower()}"
    job_tokens = _tokenize(job_text)

    # ── 1. Skills (45 pts) ───────────────────────────────────────────────
    matched, missing = [], []
    compare_against = job_skills if job_skills else []
    if compare_against:
        for js in compare_against:
            if _norm(js) in resume_skill_norms or _skill_in_text(js, resume_domain_tokens, resume_domain_text):
                matched.append(js)
            else:
                missing.append(js)
        skill_ratio = len(matched) / max(len(compare_against), 1)
    else:
        # No structured skills on job — check resume skills against description text
        for rs in resume_skills:
            if _skill_in_text(rs, job_tokens, job_text):
                matched.append(rs)
        skill_ratio = min(len(matched) / 5.0, 1.0)  # 5+ hits = full marks
    skills_pts = 45 * skill_ratio

    # ── 2. Title/domain (30 pts) ─────────────────────────────────────────
    title_tokens = _tokenize(job_title)
    overlap = title_tokens & resume_domain_tokens
    domain_ratio = len(overlap) / max(len(title_tokens), 1) if title_tokens else 0.3
    domain_pts = 30 * min(domain_ratio * 1.5, 1.0)  # generous: half-overlap ≈ full marks

    # ── 3. Seniority (15 pts) ────────────────────────────────────────────
    seniority = (job.get("seniority") or "mid").lower()
    lo, hi = _SENIORITY_YEARS.get(seniority, (2, 5))
    if lo <= years <= hi + 1:
        seniority_pts, exp_verdict = 15, "good fit"
    elif years > hi + 1:
        seniority_pts, exp_verdict = 9, "you may be overqualified"
    elif years >= lo - 1.5:
        seniority_pts, exp_verdict = 11, "slightly junior but within reach"
    else:
        seniority_pts, exp_verdict = 4, "role needs more experience"

    # ── 4. Location (10 pts) ─────────────────────────────────────────────
    resume_loc = ((resume.get("personal", {}) or {}).get("location", "") or "").lower()
    job_loc = (job.get("location", "") or "").lower()
    if job.get("remote") == "Remote":
        loc_pts = 10
    elif resume_loc and job_loc and any(part.strip() in job_loc for part in resume_loc.split(",") if len(part.strip()) > 2):
        loc_pts = 10
    elif not resume_loc:
        loc_pts = 7
    else:
        loc_pts = 4

    raw = skills_pts + domain_pts + seniority_pts + loc_pts
    # Map 0-100 raw to display 40-96 so weak matches don't show insulting single digits
    score = int(round(40 + raw * 0.56))
    score = max(40, min(96, score))

    job["match_score"] = score
    job["skills_matched"] = matched[:8]
    job["skills_missing"] = missing[:6]
    job["experience_match"] = f"Role level: {seniority or 'mid'} · you have ~{int(years)} yrs — {exp_verdict}"
    if domain_ratio >= 0.5:
        job["domain_match"] = f"'{job_title}' aligns strongly with your background"
    elif domain_ratio >= 0.2:
        job["domain_match"] = f"'{job_title}' partially overlaps your background"
    else:
        job["domain_match"] = f"'{job_title}' is outside your usual domain"
    job["apply_priority"] = "high" if score >= 78 else "medium" if score >= 62 else "low"
    return job


def score_jobs_for_resume(jobs: list[dict], resume: dict) -> list[dict]:
    for job in jobs:
        try:
            score_job_for_resume(job, resume)
        except Exception:
            job.setdefault("match_score", 60)
    return sorted(jobs, key=lambda j: j.get("match_score") or 0, reverse=True)
