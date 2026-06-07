"""
Resume Score Checker — comprehensive resume analysis.
Scores 7 dimensions for a total of 100 points.
Returns actionable feedback per category.
"""
import json
import re
from services.claude_service import complete_claude_json

ACTION_VERBS = {
    "led","managed","developed","built","created","designed","implemented",
    "delivered","launched","grew","increased","decreased","reduced","improved",
    "optimised","optimized","achieved","exceeded","collaborated","coordinated",
    "established","generated","spearheaded","drove","transformed","analysed",
    "analyzed","negotiated","mentored","trained","resolved","streamlined",
    "automated","accelerated","consolidated","evaluated","facilitated",
    "influenced","initiated","introduced","oversaw","produced","restructured",
    "scaled","secured","simplified","strengthened","supervised","upgraded",
}

QUANT_PATTERN = re.compile(r"\d+[\.,]?\d*\s?(%|percent|x|×|\$|₹|cr|lakh|k|m|b|times|hrs|hours|weeks|days|minutes|users|customers|clients|team|people|projects|products|features|releases)", re.IGNORECASE)


def score_contact(resume: dict) -> dict:
    p = resume.get("personal", {})
    checks = {
        "Name":      bool(p.get("name", "").strip()),
        "Email":     bool(p.get("email", "").strip()),
        "Phone":     bool(p.get("phone", "").strip()),
        "Location":  bool(p.get("location", "").strip()),
        "LinkedIn":  bool(p.get("linkedin", "").strip()),
        "Portfolio": bool(p.get("github", "").strip() or p.get("website", "").strip()),
    }
    pts = {
        "Name": 2, "Email": 2, "Phone": 2,
        "Location": 1, "LinkedIn": 2, "Portfolio": 1,
    }
    earned = sum(pts[k] for k, v in checks.items() if v)
    total = 10
    missing = [k for k, v in checks.items() if not v]
    return {
        "name": "Contact Information",
        "score": earned,
        "max": total,
        "checks": checks,
        "strengths": [f"{k} present" for k, v in checks.items() if v],
        "gaps": [f"Missing {k}" for k in missing],
        "tip": ("Add " + ", ".join(missing) + " to your header.") if missing else "Contact section is complete.",
    }


def score_summary(resume: dict) -> dict:
    summary = resume.get("summary", "").strip()
    words = len(summary.split()) if summary else 0
    present = bool(summary)
    good_length = 50 <= words <= 200

    score = 0
    if present: score += 6
    if good_length: score += 4
    if words >= 100: score += 3
    if words > 200: score -= 2  # too long
    score = max(0, min(score, 15))

    gaps, strengths = [], []
    if not present:
        gaps.append("No professional summary found")
        tip = "Add a 3–5 sentence summary tailored to your target role."
    elif words < 50:
        gaps.append(f"Summary too short ({words} words). Aim for 50–150.")
        tip = "Expand your summary with specific achievements and skills."
    elif words > 200:
        gaps.append(f"Summary too long ({words} words). Keep it under 150.")
        tip = "Trim your summary to 3–5 impactful sentences."
    else:
        strengths.append(f"Good length ({words} words)")
        tip = "Consider adding a key metric or accomplishment to stand out."

    return {
        "name": "Professional Summary",
        "score": score,
        "max": 15,
        "strengths": strengths,
        "gaps": gaps,
        "tip": tip,
    }


def score_experience(resume: dict) -> dict:
    exp = resume.get("experience", [])
    score = 0
    strengths, gaps = [], []

    if not exp:
        return {"name": "Work Experience", "score": 0, "max": 25, "strengths": [], "gaps": ["No work experience found"], "tip": "Add work experience, internships, or freelance projects."}

    score += 5  # has experience
    if len(exp) >= 2: score += 2

    # Action verbs check
    all_bullets = [b for role in exp for b in role.get("bullets", []) if b]
    verb_count = sum(1 for b in all_bullets if b.strip().split()[0].lower().rstrip("s") in ACTION_VERBS if b.strip())
    verb_pct = verb_count / len(all_bullets) if all_bullets else 0
    if verb_pct >= 0.7: score += 5; strengths.append("Strong action verbs throughout")
    elif verb_pct >= 0.4: score += 3; gaps.append(f"Only {int(verb_pct*100)}% bullets start with strong action verbs")
    else: gaps.append("Most bullets don't start with action verbs (Led, Built, Increased…)")

    # Quantified achievements
    quant_count = sum(1 for b in all_bullets if QUANT_PATTERN.search(b))
    quant_pct = quant_count / len(all_bullets) if all_bullets else 0
    if quant_pct >= 0.5: score += 8; strengths.append(f"{quant_count} quantified achievements found")
    elif quant_pct >= 0.25: score += 5; gaps.append(f"Only {quant_count} of {len(all_bullets)} bullets have numbers/metrics")
    else: score += 2; gaps.append(f"Very few quantified results — add metrics (%, ₹, users, time saved)")

    # Bullet count per role
    avg_bullets = sum(len(r.get("bullets", [])) for r in exp) / len(exp)
    if avg_bullets >= 3: score += 3; strengths.append("Good detail per role")
    elif avg_bullets >= 2: score += 2
    else: gaps.append("Add 3–5 bullet points per role")

    # Dates present
    has_dates = all(r.get("start") for r in exp)
    if has_dates: score += 2; strengths.append("Dates present for all roles")
    else: gaps.append("Missing start/end dates for some roles")

    score = min(score, 25)
    tip = "Add quantified results to your bullets: '↑ sales by 25%', 'reduced load time by 40%', 'led team of 6'." if quant_pct < 0.5 else "Experience section is strong — keep metrics specific."

    return {"name": "Work Experience", "score": score, "max": 25, "strengths": strengths, "gaps": gaps, "tip": tip}


def score_skills(resume: dict) -> dict:
    skills = resume.get("skills", {})
    tech = skills.get("technical", [])
    soft = skills.get("soft", [])
    certs = skills.get("certifications", [])
    langs = skills.get("languages", [])

    score = 0
    strengths, gaps = [], []

    if len(tech) >= 6: score += 6; strengths.append(f"{len(tech)} technical skills listed")
    elif len(tech) >= 3: score += 4; gaps.append(f"Add more technical skills (currently {len(tech)})")
    elif len(tech) >= 1: score += 2; gaps.append("Technical skills section is sparse")
    else: gaps.append("No technical skills found — add tools, languages, software")

    if soft: score += 3; strengths.append(f"{len(soft)} soft skills")
    else: gaps.append("Add soft skills (Leadership, Communication, Problem-solving)")

    if certs: score += 4; strengths.append(f"{len(certs)} certification(s) listed")
    else: gaps.append("Certifications boost ATS scores — add any relevant ones")

    if langs: score += 2; strengths.append("Languages listed")

    score = min(score, 15)
    tip = "Add 8–12 role-specific technical skills. Certifications add significant ATS weight." if score < 10 else "Good skills coverage — ensure skills match your target JD keywords."

    return {"name": "Skills & Certifications", "score": score, "max": 15, "strengths": strengths, "gaps": gaps, "tip": tip}


def score_education(resume: dict) -> dict:
    edu = resume.get("education", [])
    score = 0
    strengths, gaps = [], []

    if not edu:
        return {"name": "Education", "score": 2, "max": 10, "strengths": [], "gaps": ["No education section found"], "tip": "Add your highest degree, institution, and graduation year."}

    score += 5  # has degree
    if edu[0].get("institution"): score += 3; strengths.append("Institution named")
    else: gaps.append("Add institution name")
    if edu[0].get("end") or edu[0].get("start"): score += 2; strengths.append("Dates present")
    else: gaps.append("Add graduation year")

    tip = "Education section is complete." if score >= 8 else "Add degree field, institution name, and graduation year."
    return {"name": "Education", "score": min(score, 10), "max": 10, "strengths": strengths, "gaps": gaps, "tip": tip}


def score_ats(resume: dict) -> dict:
    """
    ATS compatibility: section headers, keyword presence, no formatting issues.
    """
    score = 0
    strengths, gaps = [], []

    # Check standard sections exist
    has_summary = bool(resume.get("summary", "").strip())
    has_exp = bool(resume.get("experience", []))
    has_skills = bool(resume.get("skills", {}).get("technical", []))
    has_edu = bool(resume.get("education", []))

    sections_present = sum([has_summary, has_exp, has_skills, has_edu])
    if sections_present == 4: score += 5; strengths.append("All standard ATS sections present")
    elif sections_present >= 3: score += 3
    else: gaps.append(f"Missing {4 - sections_present} standard sections (Summary/Experience/Skills/Education)")

    # Keyword density (via bullet + skills content)
    all_text = " ".join([
        resume.get("summary", ""),
        " ".join(resume.get("skills", {}).get("technical", [])),
        " ".join(b for r in resume.get("experience", []) for b in r.get("bullets", [])),
    ])
    word_count = len(all_text.split())
    if word_count >= 300: score += 5; strengths.append("Good keyword-rich content for ATS")
    elif word_count >= 150: score += 3
    else: score += 1; gaps.append("Too little text for ATS — expand bullets and summary")

    # Email/phone format check
    p = resume.get("personal", {})
    if "@" in p.get("email", "") and "." in p.get("email", ""):
        score += 3; strengths.append("Contact info in ATS-readable format")
    else:
        gaps.append("Ensure email/phone are in plain text (not tables or images)")

    score += 2  # structured JSON data = inherently ATS-parseable
    strengths.append("Resume uses standard structured format")

    score = min(score, 15)
    tip = "Add more keyword-rich content matching your target job description." if word_count < 300 else "ATS compatibility is strong — align section headers with standard labels."
    return {"name": "ATS Compatibility", "score": score, "max": 15, "strengths": strengths, "gaps": gaps, "tip": tip}


def score_format(resume: dict) -> dict:
    score = 7  # start high, deduct
    strengths, gaps = [], []

    total_bullets = sum(len(r.get("bullets", [])) for r in resume.get("experience", []))
    total_words_estimate = len(resume.get("summary", "").split()) + total_bullets * 15

    if total_words_estimate < 150: score -= 3; gaps.append("Resume seems too short — aim for 400–800 words")
    elif total_words_estimate > 900: score -= 2; gaps.append("Resume may be too long — trim to 1–2 pages")
    else: strengths.append("Appropriate resume length")

    if resume.get("projects", []): score = min(score + 2, 10); strengths.append("Projects section adds depth")

    if resume.get("achievements", []): score = min(score + 1, 10); strengths.append("Achievements section present")

    score = max(0, min(score, 10))
    tip = "Aim for 400–700 words. Add a Projects section to showcase hands-on work." if score < 7 else "Format and length look good."
    return {"name": "Format & Length", "score": score, "max": 10, "strengths": strengths, "gaps": gaps, "tip": tip}


async def score_resume(resume: dict, target_role: str = "") -> dict:
    """Full resume score — 7 categories, 100 points total."""

    categories = [
        score_contact(resume),
        score_summary(resume),
        score_experience(resume),
        score_skills(resume),
        score_education(resume),
        score_ats(resume),
        score_format(resume),
    ]

    total_score = sum(c["score"] for c in categories)
    max_score   = sum(c["max"]   for c in categories)  # = 100

    # Get Claude's qualitative top-3 improvements
    resume_text = json.dumps(resume, indent=1)[:3000]
    prompt = f"""You are a world-class resume coach. A candidate has this resume (JSON):
{resume_text}

Overall score: {total_score}/100
Target role: {target_role or "not specified"}

Give exactly 3 specific, actionable improvements as a JSON list:
{{
  "top_improvements": [
    {{"title": "short title", "detail": "1-2 sentence actionable advice with specific example"}},
    {{"title": "...", "detail": "..."}},
    {{"title": "...", "detail": "..."}}
  ],
  "one_line_verdict": "One honest sentence about this resume's overall quality and job readiness."
}}
Only output JSON."""

    try:
        raw = await complete_claude_json(
            "You are a resume coach. Output only JSON.",
            [{"role": "user", "content": prompt}],
            max_tokens=800
        )
        ai = json.loads(raw)
    except Exception:
        ai = {
            "top_improvements": [
                {"title": "Add Metrics", "detail": "Quantify achievements with numbers — % improvement, team size, revenue impact."},
                {"title": "Strengthen Summary", "detail": "Open with your title, years of experience, and top 2 skills."},
                {"title": "ATS Keywords", "detail": "Mirror exact phrases from the job description in your skills and bullets."},
            ],
            "one_line_verdict": "Solid foundation — focus on quantifying achievements and ATS optimisation to stand out."
        }

    grade = "A" if total_score >= 85 else "B" if total_score >= 70 else "C" if total_score >= 55 else "D"

    return {
        "overall_score": total_score,
        "max_score": max_score,
        "grade": grade,
        "one_line_verdict": ai.get("one_line_verdict", ""),
        "categories": categories,
        "top_improvements": ai.get("top_improvements", []),
    }
