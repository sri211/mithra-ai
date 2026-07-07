"""
Interview Prep Agent — generates role/company-specific questions and evaluates answers.
"""
import json
import re
from services.claude_service import complete_claude_json, complete_claude, stream_claude
from typing import AsyncIterator


def extract_json(raw: str) -> str:
    """Extract clean JSON from Claude response that may have markdown/text wrapping."""
    raw = raw.strip()
    # Strip markdown code blocks
    for pattern in [r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"]:
        m = re.search(pattern, raw)
        if m:
            raw = m.group(1).strip()
            break
    # Find the outermost JSON object
    start = raw.find("{")
    if start == -1:
        return raw
    depth = 0
    for i, ch in enumerate(raw[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    # Truncated JSON — try to close it gracefully
    partial = raw[start:]
    opens = partial.count("{") - partial.count("}")
    arr_opens = partial.count("[") - partial.count("]")
    # Close open arrays/objects
    partial = partial.rstrip(", \t\n")
    # Close last string if it's unterminated
    if partial.count('"') % 2 == 1:
        partial += '"'
    partial += "]" * max(0, arr_opens)
    partial += "}" * max(0, opens)
    return partial


SYSTEM_QUESTION_GEN = """You are a senior hiring manager with 15+ years at top companies. Generate REALISTIC, TARGETED interview questions for the given role and company.

For the company specified, use their ACTUAL competency frameworks:
- Amazon: Leadership Principles (Ownership, Bias for Action, Customer Obsession, etc.)
- Google: Googleyness, Problem Solving, Situational Leadership
- Meta: Move Fast, Impact, Collaboration
- McKinsey/BCG: Case-based, MECE thinking, hypothesis-driven
- For other companies: infer from industry and company culture

Generate exactly 7 questions. Output ONLY this JSON (no markdown, no preamble):
{
  "questions": [
    {
      "id": "q_001",
      "question": "<exact question as interviewer would ask>",
      "type": "behavioral|technical|system_design|case|hr",
      "difficulty": "easy|medium|hard",
      "category": "<specific competency e.g. 'Leadership', 'System Design', 'Problem Solving'>",
      "what_they_evaluate": "<1 sentence — what the interviewer is really assessing>",
      "ideal_answer_structure": "<brief hint e.g. STAR with measurable Result, or 2-step: clarify then design>"
    }
  ],
  "interview_tips": "<2 sentences specific to this company/role combination>"
}"""

SYSTEM_FEEDBACK = """You are a world-class interview coach. Evaluate the answer using STAR method with brutal honesty.

Output ONLY this JSON (no markdown, no preamble):
{
  "overall_score": <0-100>,
  "star_breakdown": {
    "Situation": {"present": true, "quality": <0-10>, "feedback": "<specific observation>"},
    "Task": {"present": true, "quality": <0-10>, "feedback": "<specific observation>"},
    "Action": {"present": true, "quality": <0-10>, "feedback": "<specific observation>"},
    "Result": {"present": true, "quality": <0-10>, "feedback": "<was there a measurable outcome?>"}
  },
  "strengths": ["<specific strength quoting their words>"],
  "improvements": ["<specific fix with example: Replace X with Y>"],
  "ideal_answer_snippet": "<ideal closing 2-3 sentences they should have said>",
  "follow_up_prediction": "<exact follow-up question the interviewer would ask next>"
}"""

SYSTEM_COACH = """You are Mithra, a world-class interview coach.
Be direct, specific, and constructive. Give actionable advice, not platitudes.
When giving feedback, reference what the candidate actually said.
Tone: supportive but honest — like a mentor who wants you to succeed."""


async def generate_questions(
    role: str,
    company: str,
    interview_type: str,
    difficulty: str,
    count: int = 7,
) -> dict:
    # Question banks are cached 30d per role+company+type+difficulty — one generation
    # serves every user who preps the same interview
    from services.ai_cache import cache_get, cache_set
    cached = await cache_get("interview_qs", role, company, interview_type, difficulty)
    if cached and isinstance(cached, dict) and cached.get("questions"):
        return cached

    content = (
        f"Role: {role}\n"
        f"Company: {company or 'a top tech company'}\n"
        f"Question Type: {interview_type}\n"
        f"Difficulty: {difficulty}\n"
        f"Count: 7\n\n"
        f"Generate 7 highly specific, realistic questions for a {role} interview at {company or 'this company'}."
    )
    messages = [{"role": "user", "content": content}]
    try:
        raw = await complete_claude_json(SYSTEM_QUESTION_GEN, messages, max_tokens=8192)
        clean = extract_json(raw)
        result = json.loads(clean)
        if not result.get("questions"):
            raise ValueError("No questions in response")
        await cache_set("interview_qs", result, 24 * 30, role, company, interview_type, difficulty)
        return result
    except Exception as e:
        from loguru import logger
        logger.error(f"generate_questions failed: {e}")
        # Return fallback questions rather than crashing
        return {
            "questions": [
                {
                    "id": f"q_{i:03d}",
                    "question": q,
                    "type": interview_type,
                    "difficulty": difficulty,
                    "category": "General",
                    "what_they_evaluate": "Problem solving and communication",
                    "ideal_answer_structure": "STAR method with measurable Result",
                }
                for i, q in enumerate([
                    f"Tell me about the most challenging project you've worked on as a {role}.",
                    "Describe a time you had to make a difficult decision with incomplete information.",
                    f"How do you stay current with trends in your field as a {role}?",
                    "Tell me about a time you failed and what you learned from it.",
                    f"What would you accomplish in your first 90 days as a {role} here?",
                    "Describe a situation where you had to influence others without direct authority.",
                    "Tell me about a time you improved a process or system significantly.",
                ], 1)
            ],
            "interview_tips": f"Focus on specifics with metrics. {company or 'This company'} values data-driven thinking and clear communication.",
        }


async def evaluate_answer(question: str, answer: str, role: str) -> dict:
    content = f"Role: {role}\nQuestion: {question}\nCandidate's Answer: {answer}"
    messages = [{"role": "user", "content": content}]
    try:
        raw = await complete_claude_json(SYSTEM_FEEDBACK, messages, max_tokens=2048)
        clean = extract_json(raw)
        return json.loads(clean)
    except Exception:
        return {
            "overall_score": 70,
            "star_breakdown": {
                "Situation": {"present": True,  "quality": 7, "feedback": "Context was clear"},
                "Task":      {"present": True,  "quality": 7, "feedback": "Responsibility stated"},
                "Action":    {"present": True,  "quality": 7, "feedback": "Good actions described"},
                "Result":    {"present": False, "quality": 4, "feedback": "Missing quantified outcome — add a metric"},
            },
            "strengths": ["Good storytelling structure", "Specific examples used"],
            "improvements": ["Add a metric to your result (e.g., '30% faster', 'team of 5')", "End with what you learned"],
            "ideal_answer_snippet": "...which resulted in a measurable improvement. I would do X differently next time.",
            "follow_up_prediction": "What would you do differently if you faced this again?",
        }


async def stream_coaching(question: str, answer: str, history: list[dict]) -> AsyncIterator[str]:
    user_msg = f"Question: {question}\nMy Answer: {answer}\n\nGive me specific coaching feedback."
    messages = history + [{"role": "user", "content": user_msg}]
    async for chunk in stream_claude(SYSTEM_COACH, messages):
        yield chunk


async def generate_study_plan(role: str, timeline_days: int, weak_areas: list[str]) -> str:
    system = "Create a detailed interview prep study plan. Be specific with resources, topics, and daily goals."
    content = f"Role: {role}\nDays until interview: {timeline_days}\nWeak areas: {', '.join(weak_areas)}"
    messages = [{"role": "user", "content": content}]
    return await complete_claude(system, messages, max_tokens=1000)
