"""
Mithra Orchestrator — routes user intent to the right specialist agent.
"""
import json
from services.claude_service import complete_claude_json, stream_claude
from typing import AsyncIterator

SYSTEM_ROUTER = """You are Mithra, an elite AI career companion. You are warm, sharp, and deeply knowledgeable about careers.

You have access to these specialist capabilities:
- resume_builder: Create or update resumes
- resume_adaptor: Adapt resume to a job description
- job_finder: Search for jobs
- job_application: Apply to jobs automatically
- network: Find connections at companies
- interview_prep: Mock interviews and coaching
- tracker: Manage application pipeline
- general: General career advice

Given the user's message and current page context, output JSON:
{
  "intent": "<one of the capabilities above>",
  "confidence": <0-1>,
  "response": "<your warm, helpful reply to the user>",
  "action": "<optional specific action to take>",
  "params": {}
}"""

SYSTEM_CHAT = """You are Mithra — an elite AI career companion with the combined expertise of a top resume writer, executive recruiter, career coach, and talent strategist.

You are warm, direct, witty, and genuinely invested in helping people land their dream jobs. You give real, specific advice — not generic platitudes.

You can help with anything career-related:
- **Resume Builder**: Build complete, ATS-optimised resumes through conversation, LinkedIn import, or guided forms
- **Resume Adaptor**: Tailor resumes to any job description for maximum match score
- **Job Finder**: Search across LinkedIn, Naukri, Indeed, Glassdoor and 50+ portals simultaneously
- **Auto-Apply**: Automatically open, fill, and submit job applications
- **Network Intelligence**: Find the right people at target companies — recruiters, hiring managers, alumni
- **Interview Prep**: Run mock interviews, give STAR-method feedback, build study plans
- **Application Tracker**: Manage your full pipeline from bookmarked to offer
- **Career Strategy**: Salary negotiation, career pivots, personal branding, skill gaps

Current page context: {page_context}

Response style:
- Be concise and actionable — one clear recommendation, not five hedged options
- Use bold for key terms, bullet points for lists
- When giving advice, be specific (name frameworks, tools, real numbers)
- End responses with a clear next step or question to keep momentum
- Occasionally be witty — this is a stressful process and a bit of warmth helps"""


def _build_user_profile_context(user_profile: dict | None) -> str:
    """Build a user profile context string to prepend to the system prompt."""
    if not user_profile or not user_profile.get("name"):
        return ""
    parts = [f"\n\n=== USER PROFILE ==="]
    if user_profile.get("name"):
        parts.append(f"Name: {user_profile['name']}")
    if user_profile.get("currentRole"):
        parts.append(f"Current Role: {user_profile['currentRole']}")
    if user_profile.get("targetRole"):
        parts.append(f"Target Role: {user_profile['targetRole']}")
    if user_profile.get("yearsOfExperience"):
        parts.append(f"Years of Experience: {user_profile['yearsOfExperience']}")
    if user_profile.get("skills"):
        parts.append(f"Skills: {', '.join(user_profile['skills'])}")
    if user_profile.get("experienceSummary"):
        parts.append(f"Experience Summary: {user_profile['experienceSummary']}")
    parts.append(f"\nALWAYS address this user by their first name ({user_profile['name'].split()[0]}) in your responses.")
    parts.append("Give advice specifically tailored to their background, current role, and target role.")
    parts.append("=== END USER PROFILE ===")
    return "\n".join(parts)


async def route_intent(message: str, page_context: str, history: list[dict], user_profile: dict | None = None) -> dict:
    messages = [{"role": "user", "content": f"Current page: {page_context}\n\nUser message: {message}"}]
    system = SYSTEM_ROUTER + _build_user_profile_context(user_profile)
    raw = await complete_claude_json(system, messages)
    try:
        return json.loads(raw)
    except Exception:
        return {"intent": "general", "confidence": 0.5, "response": raw, "action": None, "params": {}}


async def stream_response(
    message: str,
    page_context: str,
    history: list[dict],
    user_profile: dict | None = None,
) -> AsyncIterator[str]:
    system = SYSTEM_CHAT.format(page_context=page_context) + _build_user_profile_context(user_profile)
    messages = history + [{"role": "user", "content": message}]
    async for chunk in stream_claude(system, messages):
        yield chunk
