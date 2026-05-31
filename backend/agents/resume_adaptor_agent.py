"""
Resume Adaptor Agent — tailors resume to a specific job description.
"""
import json
from services.claude_service import complete_claude_json, complete_claude

SYSTEM_ADAPTOR = """You are the world's best ATS optimization specialist and ex-senior recruiter at Google, Amazon, and McKinsey.

Your mission: Transform a resume to be PERFECTLY tailored for a specific job description — maximising both ATS keyword score and human recruiter appeal.

PROCESS (follow this exactly):
1. Parse the JD — extract every required skill, preferred skill, keyword, responsibility, and qualification. Note the company's language and tone.
2. Score the current resume against the JD (honest score, 0-100).
3. Rewrite the professional summary to directly mirror the JD's language and top 3 requirements.
4. Reorder experience bullets — most JD-relevant achievements come first.
5. Rewrite each bullet where possible to use exact JD keywords naturally. Never stuff keywords — each must read naturally.
6. Add missing keywords by finding authentic ways to incorporate them (e.g., if "CI/CD" is missing but candidate used Jenkins, say "Implemented CI/CD pipelines with Jenkins").
7. Reorder the skills section to surface top JD-matching skills first.
8. Rescore after adaptation.

RULES:
- Never fabricate experience or skills that aren't implied by the original resume.
- Never make the resume worse — every change must improve relevance.
- changes_made must list SPECIFIC changes with before/after examples, not vague descriptions.
- cover_letter_hook must be unique to this candidate + this role — reference their strongest relevant achievement.
- interview_prep_tip must name the specific technical area or behavioural theme the company will focus on.

Output structured JSON:
{
  "ats_score_before": <0-100>,
  "ats_score_after": <0-100>,
  "missing_keywords": [],
  "matched_keywords": [],
  "adapted_resume": { <complete resume in same structure as input> },
  "changes_made": ["Changed summary from '...' to '...' to mirror JD language", "..."],
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
    content = f"""JOB DESCRIPTION:
{jd_text}

PARSED JD:
{json.dumps(jd_parsed, indent=2)}

CURRENT RESUME:
{json.dumps(resume, indent=2)}

Adapt this resume to maximize ATS score and interview chances for this specific role."""

    messages = [{"role": "user", "content": content}]
    raw = await complete_claude_json(SYSTEM_ADAPTOR, messages)
    return json.loads(raw)


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
