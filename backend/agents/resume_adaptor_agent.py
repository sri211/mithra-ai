"""
Resume Adaptor Agent — tailors resume to a specific job description.
"""
import json
from loguru import logger
from services.claude_service import complete_claude_json, complete_claude

SYSTEM_ADAPTOR = """You are an elite resume strategist who analyzes resumes from 5 distinct perspectives simultaneously:

1. ATS ALGORITHM LENS: Keyword density, section headers (must match exact JD terms), formatting compatibility, absence of tables/graphics/columns that confuse parsers, action verb strength.

2. HR SCREENER LENS (30-second scan): Does the title/summary immediately signal relevance? Is the most recent role clearly aligned? Are there obvious gaps or red flags? Does the progression make sense?

3. HIRING MANAGER LENS: Quality of achievements — are bullets outcome-driven with metrics? Does experience depth match the seniority required? Are responsibilities described in terms of business impact, not just tasks?

4. DOMAIN EXPERT LENS: Are the right tools, methodologies, certifications, and domain-specific terminology present? Does the candidate speak the language of the field?

5. CULTURAL FIT LENS: Does the language align with the target company's values? Are there signals of traits the company prizes (e.g., ownership, scale, speed, collaboration)?

CORE RULES:
- The `original` field in each suggested_change MUST be copied VERBATIM from the "ACTUAL CURRENT ..." sections provided. NEVER invent the original text.
- Only suggest changes that match the candidate's ACTUAL background. An HR professional should get HR-optimized changes, not software engineering suggestions.
- Every suggested change must specify WHICH LENS it addresses (e.g., "ATS: Add keyword 'Talent Acquisition' which appears 4x in JD")
- Never fabricate skills or experience not implied by the original resume.
- Produce 4-8 highly targeted changes, not 20 generic ones.
- style_preserved: always true.

Output JSON:
{
  "ats_score_before": <0-100>,
  "ats_score_after": <0-100>,
  "perspective_scores": {
    "ats": <0-100>, "hr_screener": <0-100>, "hiring_manager": <0-100>,
    "domain_expert": <0-100>, "cultural_fit": <0-100>
  },
  "missing_keywords": [],
  "matched_keywords": [],
  "suggested_changes": [
    {
      "section": "<summary | experience[0].bullets[1] | skills.technical | etc.>",
      "lens": "<ATS | HR Screener | Hiring Manager | Domain Expert | Cultural Fit>",
      "original": "<VERBATIM from ACTUAL CURRENT sections>",
      "suggested": "<specific rewrite>",
      "reason": "<why this change, which lens it addresses, what metric it improves>"
    }
  ],
  "adapted_resume": { <complete resume JSON with all changes applied> },
  "changes_made": [],
  "style_preserved": true,
  "cover_letter_hook": "<2 sentences specific to this candidate + role>",
  "interview_prep_tip": "<specific topic + reason>"
}"""

SYSTEM_COMPANY_INTELLIGENCE = """You are a talent intelligence expert with deep knowledge of how top companies hire.
Given a company name and role, produce a structured company hiring intelligence report covering:
1. Company Culture & Values - what they genuinely care about (cite specific known values e.g. Amazon's LPs, Google's "Googleyness")
2. Resume Screening Approach - how their ATS and HR team evaluates resumes for this role level
3. Keywords That Matter - specific terms, frameworks, tools they use internally or value
4. What Gets You Past Round 1 - specific patterns in successful candidates
5. Red Flags They Screen For - what immediately disqualifies candidates
6. Impact Metrics They Love - specific types of numbers/outcomes that impress them
7. Cultural Fit Signals - how they spot alignment through resume language

Be specific. If it's Flipkart say "Flipkart values 'Bias for Action' and 'Customer Obsession', prefers candidates who show GMV/revenue impact numbers, looks for category management + P&L ownership evidence." Not generic platitudes."""

SYSTEM_JD_PARSER = """Extract the following from this job description as JSON:
{
  "title": "",
  "company": "",
  "location": "",
  "salary_range": "",
  "required_skills": [],
  "preferred_skills": [],
  "required_experience_years": 0,
  "education_required": "",
  "key_responsibilities": [],
  "keywords": [],
  "seniority_level": "",
  "job_type": ""
}"""


async def parse_job_description(jd_text: str) -> dict:
    messages = [{"role": "user", "content": jd_text}]
    raw = await complete_claude_json(SYSTEM_JD_PARSER, messages)
    return json.loads(raw)


async def get_company_intelligence(company: str, role: str) -> str:
    """Fetch deep hiring intelligence for a specific company and role."""
    content = f"Company: {company}\nRole: {role}\n\nProvide the structured company hiring intelligence report."
    messages = [{"role": "user", "content": content}]
    return await complete_claude(SYSTEM_COMPANY_INTELLIGENCE, messages, max_tokens=1200)


async def adapt_resume(resume: dict, jd_text: str, jd_parsed: dict, company_name: str = "", role_name: str = "") -> dict:
    # Extract literal section texts so Claude cannot hallucinate the "original" values
    personal = resume.get("personal", {})
    summary_text = resume.get("summary", "")
    title_text = personal.get("title", "")
    skills = resume.get("skills", {})
    tech_skills = skills.get("technical", [])
    soft_skills = skills.get("soft", [])
    certs = skills.get("certifications", [])

    experience_texts = []
    for i, exp in enumerate(resume.get("experience", [])):
        bullets = "\n".join(f"    - {b}" for b in exp.get("bullets", []) if b)
        experience_texts.append(
            f"  [{i}] {exp.get('role', '')} at {exp.get('company', '')} "
            f"({exp.get('start', '')}–{'Present' if exp.get('current') else exp.get('end', '')})\n{bullets}"
        )

    verbatim_section = f"""
ACTUAL RESUME CONTENT — use these VERBATIM for the `original` field in suggested_changes:

ACTUAL CURRENT TITLE: {title_text}
ACTUAL CURRENT SUMMARY: {summary_text}
ACTUAL CURRENT TECHNICAL SKILLS: {", ".join(tech_skills)}
ACTUAL CURRENT SOFT SKILLS: {", ".join(soft_skills)}
ACTUAL CURRENT CERTIFICATIONS: {", ".join(certs)}
ACTUAL CURRENT EXPERIENCE:
{"".join(experience_texts)}
"""

    # Build company intelligence block if company context provided
    company_intel_block = ""
    if company_name and company_name.strip():
        target_role = role_name.strip() if role_name else jd_parsed.get("title", "the role")
        try:
            intelligence = await get_company_intelligence(company_name.strip(), target_role)
            company_intel_block = f"\nCOMPANY INTELLIGENCE FOR {company_name.strip()} - {target_role}:\n{intelligence}\n"
        except Exception as e:
            logger.warning(f"Company intelligence fetch failed for {company_name}: {e}")

    # Capture exact top-level structure of source resume for strict preservation
    source_keys = list(resume.keys())
    source_structure_note = f"""
STRICT STRUCTURE RULES — MANDATORY:
- The `adapted_resume` field MUST contain ALL and ONLY these top-level keys in this exact order: {source_keys}
- Do NOT add any new top-level keys (no 'references', 'hobbies', 'certifications' at top level unless already present)
- Do NOT remove any existing top-level keys
- Do NOT reorder top-level keys
- Do NOT change the schema of nested objects (if 'skills' has keys {list(resume.get('skills', {}).keys())}, keep exactly those keys)
- Do NOT change section names or heading styles
- ONLY modify text VALUES within existing fields — change bullet text, summary text, skill keywords
- If a section exists in the source but has no relevant changes for the JD, keep it exactly as-is
"""

    content = f"""JOB DESCRIPTION:
{jd_text}

PARSED JD:
{json.dumps(jd_parsed, indent=2)}
{company_intel_block}
CURRENT RESUME (full JSON):
{json.dumps(resume, indent=2)}
{verbatim_section}
{source_structure_note}
Adapt this resume to maximize ATS score and interview chances for this specific role.
REMINDER: The `original` field in every suggested_change must be copied VERBATIM from the "ACTUAL CURRENT ..." lines above."""

    messages = [{"role": "user", "content": content}]
    raw = await complete_claude_json(SYSTEM_ADAPTOR, messages, max_tokens=8192)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Adaptor JSON parse failed: {e} | Raw start: {raw[:300]}")
        raise ValueError(f"AI returned malformed response. Please try again. ({e})")


async def generate_cover_letter(resume: dict, jd_text: str, tone: str = "professional") -> str:
    system = f"""Write a compelling, {tone} cover letter (3 paragraphs, ~250 words).
Opening: Hook with a specific achievement relevant to the role.
Middle: Connect top 3 experiences to the job requirements.
Closing: Express enthusiasm and clear CTA.
Do NOT use generic phrases like 'I am writing to apply...'"""

    content = f"Resume:\n{json.dumps(resume, indent=2)}\n\nJob Description:\n{jd_text}"
    messages = [{"role": "user", "content": content}]
    return await complete_claude(system, messages, max_tokens=600)


async def score_resume_vs_jd(resume: dict, jd_text: str) -> dict:
    system = """Score this resume against the job description. Output JSON:
{
  "overall_score": <0-100>,
  "keyword_match": <0-100>,
  "experience_match": <0-100>,
  "skills_match": <0-100>,
  "education_match": <0-100>,
  "missing_keywords": [],
  "present_keywords": [],
  "recommendations": []
}"""
    content = f"Resume:\n{json.dumps(resume)}\n\nJD:\n{jd_text}"
    messages = [{"role": "user", "content": content}]
    raw = await complete_claude_json(system, messages)
    return json.loads(raw)
