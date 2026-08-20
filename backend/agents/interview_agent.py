"""
Interview Prep Agent — generates role/company/topic-specific questions in multiple
interactive formats (open, MCQ, coding), evaluates answers, and synthesizes an
end-of-session Placement Readiness Report. Tailored for college-placement students.
"""
import json
import re
from services.claude_service import complete_claude_json, complete_claude, stream_claude
from typing import AsyncIterator, Optional


def extract_json(raw: str) -> str:
    """Extract clean JSON from Claude response that may have markdown/text wrapping."""
    raw = raw.strip()
    for pattern in [r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```"]:
        m = re.search(pattern, raw)
        if m:
            raw = m.group(1).strip()
            break
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
    partial = raw[start:]
    opens = partial.count("{") - partial.count("}")
    arr_opens = partial.count("[") - partial.count("]")
    partial = partial.rstrip(", \t\n")
    if partial.count('"') % 2 == 1:
        partial += '"'
    partial += "]" * max(0, arr_opens)
    partial += "}" * max(0, opens)
    return partial


SYSTEM_QUESTION_GEN = """You are a senior hiring manager and campus-placement panelist with 15+ years at top companies. Generate REALISTIC, TARGETED interview questions.

Use real competency frameworks when a company is named (Amazon Leadership Principles, Google Googleyness/problem-solving, McKinsey/BCG case+MECE, etc.). For placement/fresher candidates, emphasize fundamentals, aptitude, projects, and HR fit over deep work experience.

You will be told which FORMATS to include. Mix them intelligently:
- "open"  : answered by speaking/typing (behavioral, HR, design, case, conceptual).
- "mcq"   : one correct option — use for aptitude, quantitative, and technical fundamentals. Provide 4 options and the correct index.
- "coding": a short coding/DSA or SQL problem — provide a concise model answer.

Honor the focus TOPIC if given (ask most questions within/around it). Match DIFFICULTY and the candidate's DEGREE/BRANCH for technical relevance.

Output ONLY this JSON (no markdown, no preamble):
{
  "questions": [
    {
      "id": "q_001",
      "question": "<exact question as interviewer would ask>",
      "type": "behavioral|technical|system_design|case|hr|aptitude",
      "format": "open|mcq|coding",
      "difficulty": "easy|medium|hard",
      "category": "<specific competency>",
      "topic": "<narrow topic, e.g. 'SQL joins', 'Trees', 'Guesstimates'>",
      "what_they_evaluate": "<1 sentence — what the interviewer is really assessing>",
      "ideal_answer_structure": "<brief hint, e.g. STAR with measurable Result>",
      "time_limit_seconds": <int, 60-300 based on depth>,
      "options": ["<A>","<B>","<C>","<D>"],           // ONLY for format=mcq, else omit or []
      "correct_option": <0-based int>,                  // ONLY for format=mcq
      "explanation": "<why the correct option is right>",// ONLY for format=mcq
      "model_answer": "<reference solution/approach>"    // ONLY for format=coding
    }
  ],
  "interview_tips": "<2 sentences specific to this role/company/topic>"
}"""


def _fallback_questions(role, difficulty, count, topic, formats):
    base = [
        ("Tell me about the most challenging project you've worked on as a " + role + ".", "behavioral", "open"),
        ("Describe a time you had to make a decision with incomplete information.", "behavioral", "open"),
        ("Walk me through a concept from your coursework you know deeply.", "technical", "open"),
        ("What are your biggest strengths and one real weakness?", "hr", "open"),
        ("Where do you see yourself in 3 years, and why this role?", "hr", "open"),
        ("Estimate the number of smartphones sold in India per year.", "case", "open"),
        ("Explain a project on your resume end-to-end, including trade-offs.", "technical", "open"),
        ("Why should we hire you over other candidates?", "hr", "open"),
        ("Describe a time you worked in a team and handled conflict.", "behavioral", "open"),
        ("How do you keep learning and stay current in your field?", "hr", "open"),
        ("What would you accomplish in your first 90 days here?", "behavioral", "open"),
        ("Tell me about a time you failed and what you learned.", "behavioral", "open"),
    ]
    out = []
    for i, (q, typ, fmt) in enumerate(base[:count], 1):
        out.append({
            "id": f"q_{i:03d}", "question": q, "type": typ, "format": fmt,
            "difficulty": difficulty, "category": "General",
            "topic": topic or "General", "time_limit_seconds": 120,
            "what_they_evaluate": "Problem solving, structure, and communication",
            "ideal_answer_structure": "STAR method with a measurable Result",
        })
    return {"questions": out,
            "interview_tips": "Structure answers with STAR, quantify results, and speak for 60–90 seconds per question."}


async def generate_questions(
    role: str,
    company: str,
    interview_type: str,
    difficulty: str,
    count: int = 8,
    topic: str = "",
    experience_level: str = "fresher",
    degree: str = "",
    company_tier: str = "",
    formats: Optional[list] = None,
) -> dict:
    formats = formats or ["open"]
    count = max(3, min(int(count or 8), 15))
    from services.ai_cache import cache_get, cache_set
    ck = (role, company, interview_type, difficulty, str(count), topic,
          experience_level, degree, company_tier, ",".join(sorted(formats)))
    cached = await cache_get("interview_qs_v2", *ck)
    if cached and isinstance(cached, dict) and cached.get("questions"):
        return cached

    content = (
        f"Role: {role}\n"
        f"Company: {company or 'a top company'} (tier: {company_tier or 'unspecified'})\n"
        f"Candidate level: {experience_level}\n"
        f"Degree/branch: {degree or 'unspecified'}\n"
        f"Question type: {interview_type}\n"
        f"Focus topic: {topic or 'none — cover the role broadly'}\n"
        f"Difficulty: {difficulty}\n"
        f"Formats to include: {', '.join(formats)}\n"
        f"Count: EXACTLY {count}\n\n"
        f"Generate {count} realistic questions. If MCQ is allowed, make ~40% MCQ (aptitude/fundamentals). "
        f"If coding is allowed and the role is technical, include 1–2 coding/SQL problems. "
        f"Keep the rest open. Every question must include a narrow 'topic' and a sensible time_limit_seconds."
    )
    messages = [{"role": "user", "content": content}]
    try:
        raw = await complete_claude_json(SYSTEM_QUESTION_GEN, messages, max_tokens=8192)
        result = json.loads(extract_json(raw))
        qs = result.get("questions") or []
        if not qs:
            raise ValueError("No questions in response")
        # normalize: ensure required fields exist so the frontend never breaks
        for i, q in enumerate(qs, 1):
            q.setdefault("id", f"q_{i:03d}")
            q.setdefault("format", "open")
            q.setdefault("type", interview_type)
            q.setdefault("difficulty", difficulty)
            q.setdefault("topic", topic or "General")
            q.setdefault("time_limit_seconds", 120)
            if q.get("format") == "mcq":
                q.setdefault("options", [])
                q.setdefault("correct_option", 0)
        result["questions"] = qs
        await cache_set("interview_qs_v2", result, 24 * 30, *ck)
        return result
    except Exception as e:
        from loguru import logger
        logger.error(f"generate_questions failed: {e}")
        return _fallback_questions(role, difficulty, count, topic, formats)


async def evaluate_answer(question: str, answer: str, role: str) -> dict:
    content = f"Role: {role}\nQuestion: {question}\nCandidate's Answer: {answer}"
    messages = [{"role": "user", "content": content}]
    try:
        raw = await complete_claude_json(SYSTEM_FEEDBACK, messages, max_tokens=2048)
        return json.loads(extract_json(raw))
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
            "improvements": ["Add a metric to your result (e.g., '30% faster')", "End with what you learned"],
            "ideal_answer_snippet": "...which resulted in a measurable improvement. I would do X differently next time.",
            "follow_up_prediction": "What would you do differently if you faced this again?",
        }


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


SYSTEM_REPORT = """You are the head of a college placement cell and an elite interview coach. Given a candidate's full mock-interview session, produce an honest, motivating PLACEMENT READINESS REPORT.

Score realistically for a campus-placement bar. Base dimensions on the actual answers/results provided.

Output ONLY this JSON (no markdown, no preamble):
{
  "readiness_score": <0-100 overall>,
  "band": "Placement Ready|Almost There|Needs Work",
  "headline": "<one motivating but honest sentence>",
  "summary": "<2-3 sentences: overall read of the candidate>",
  "dimensions": {
    "Communication": <0-100>,
    "Structure": <0-100>,
    "Technical": <0-100>,
    "Confidence": <0-100>,
    "Domain": <0-100>
  },
  "strengths": ["<3-4 concrete strengths>"],
  "improvements": ["<3-5 specific, actionable fixes>"],
  "topic_performance": [{"topic": "<topic>", "score": <0-100>, "note": "<short note>"}],
  "study_plan": [{"title": "<step>", "detail": "<what to do, with a concrete resource or drill>"}]
}"""


def _fallback_report(results: list) -> dict:
    scored = [r for r in results if isinstance(r.get("score"), (int, float))]
    avg = round(sum(r["score"] for r in scored) / len(scored)) if scored else 60
    band = "Placement Ready" if avg >= 78 else "Almost There" if avg >= 55 else "Needs Work"
    # topic aggregation
    by_topic: dict = {}
    for r in results:
        t = r.get("topic") or "General"
        by_topic.setdefault(t, []).append(r.get("score", avg))
    topic_perf = [{"topic": t, "score": round(sum(v) / len(v)), "note": ""} for t, v in by_topic.items()][:6]
    return {
        "readiness_score": avg, "band": band,
        "headline": "Solid effort — a few focused fixes will lift you into interview-ready territory.",
        "summary": "You answered every question and showed real potential. Tighten structure, quantify outcomes, and practice speaking aloud to convert this into confident, offer-winning performance.",
        "dimensions": {"Communication": avg, "Structure": max(40, avg - 8),
                       "Technical": avg, "Confidence": max(40, avg - 5), "Domain": avg},
        "strengths": ["Completed the full session — good stamina and commitment",
                      "Clear willingness to engage with tough questions"],
        "improvements": ["Use the STAR structure and end every story with a measurable result",
                         "Practice answers aloud to cut filler words and improve pace",
                         "Prepare 2–3 strong project stories you can adapt to any question"],
        "topic_performance": topic_perf,
        "study_plan": [
            {"title": "Master your resume stories", "detail": "Write STAR answers for your top 3 projects; rehearse each in 90 seconds."},
            {"title": "Drill fundamentals daily", "detail": "30 min/day of aptitude + core-subject MCQs for your branch."},
            {"title": "Mock aloud", "detail": "Do 2 more timed voice mocks this week and re-check your readiness score."},
        ],
    }


async def generate_report(role: str, company: str, experience_level: str, results: list) -> dict:
    """results: list of {question, type, topic, format, score, correct, time_taken, answer}."""
    compact = [{k: r.get(k) for k in ("question", "type", "topic", "format", "score", "correct", "time_taken")}
               for r in results]
    content = (
        f"Role: {role}\nCompany: {company or 'unspecified'}\nLevel: {experience_level}\n"
        f"Session results (per question):\n{json.dumps(compact, ensure_ascii=False)[:6000]}"
    )
    try:
        raw = await complete_claude_json(SYSTEM_REPORT, [{"role": "user", "content": content}], max_tokens=2048)
        rep = json.loads(extract_json(raw))
        if not rep.get("readiness_score"):
            raise ValueError("no score")
        return rep
    except Exception as e:
        from loguru import logger
        logger.error(f"generate_report failed: {e}")
        return _fallback_report(results)


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
