"""
Resume Adaptor Agent — tailors resume to a specific job description.
"""
import json
from loguru import logger
from services.claude_service import complete_claude_json, complete_claude

SYSTEM_ADAPTOR = """You are the world's best ATS optimization specialist and ex-senior recruiter at Google, Amazon, and McKinsey.

Your mission: Transform a resume to be PERFECTLY tailored for a specific job description — maximising both ATS keyword score and human recruiter appeal.

PROCESS (follow this exactly):
1. Parse the JD — extract every required skill, preferred skill, keyword, responsibility, and qualification. Note the company's language and tone.
2. Score the current resume against the JD (honest score, 0-100).
3. For EACH proposed change, create a suggested_change entry with {section, original, suggested, reason}.
4. Produce the fully adapted_resume applying all those changes.
5. Preserve the EXACT template/style structure of the original resume — same sections, same ordering of top-level fields.

RULES:
- Never fabricate experience or skills that aren't implied by the original resume.
- Never make the resume worse — every change must improve relevance.
- The `original` field in each suggested change MUST be copied VERBATIM from the resume field provided in the prompt. NEVER invent, hallucinate, or paraphrase the original text. Copy it exactly as given in the "ACTUAL CURRENT ..." sections below.
- Only suggest changes to sections that are genuinely relevant to this specific JD. If the candidate is an HR/MBA professional applying for an HR role, do NOT suggest adding software engineering, distributed systems, or coding skills that are not in their background.
- suggested_changes must list SPECIFIC before/after text with reasons, not vague descriptions.
- cover_letter_hook must be unique to this candidate + this role — reference their strongest relevant achievement.
- interview_prep_tip must name the specific technical area or behavioural theme the company will focus on.
- style_preserved must always be true — never restructure the resume template.

Output structured JSON:
{
  "ats_score_before": <0-100>,
  "ats_score_after": <0-100>,
  "missing_keywords": [],
  "matched_keywords": [],
  "suggested_changes": [
    {
      "section": "<e.g. summary | experience[0].bullets[1] | skills.technical>",
      "original": "<exact original text>",
      "suggested": "<proposed new text>",
      "reason": "<why this change improves ATS/recruiter appeal>"
    }
  ],
  "adapted_resume": { <complete resume in same structure as input — ALL suggested_changes applied> },
  "changes_made": ["Changed summary from '...' to '...' to mirror JD language", "..."],
  "style_preserved": true,
  "cover_letter_hook": "<2 sentences, specific to candidate + role>",
  "interview_prep_tip": "<specific topic + why this company asks about it>"
}"""

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


async def adapt_resume(resume: dict, jd_text: str, jd_parsed: dict) -> dict:
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

    content = f"""JOB DESCRIPTION:
{jd_text}

PARSED JD:
{json.dumps(jd_parsed, indent=2)}

CURRENT RESUME (full JSON):
{json.dumps(resume, indent=2)}
{verbatim_section}
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
