from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agents.interview_agent import (
    generate_questions, evaluate_answer, stream_coaching, generate_study_plan, generate_report,
)
import json

from services.credits import charge_action

router = APIRouter()


class QuestionRequest(BaseModel):
    role: str
    company: str = ""
    interview_type: str = "behavioral"
    difficulty: str = "medium"
    count: int = 8
    topic: str = ""
    experience_level: str = "fresher"   # fresher | experienced
    degree: str = ""
    company_tier: str = ""              # dream | core | mass
    formats: list[str] = ["open"]       # open | mcq | coding


class ReportRequest(BaseModel):
    role: str
    company: str = ""
    experience_level: str = "fresher"
    results: list[dict] = []


class EvaluateRequest(BaseModel):
    question: str
    answer: str
    role: str


class CoachRequest(BaseModel):
    question: str
    answer: str
    history: list[dict] = []


class StudyPlanRequest(BaseModel):
    role: str
    timeline_days: int = 14
    weak_areas: list[str] = []


@router.post("/questions", dependencies=[Depends(charge_action("interview_session"))])
async def get_questions(req: QuestionRequest):
    result = await generate_questions(
        req.role, req.company, req.interview_type, req.difficulty, req.count,
        topic=req.topic, experience_level=req.experience_level, degree=req.degree,
        company_tier=req.company_tier, formats=req.formats,
    )
    return result


@router.post("/report")
async def report(req: ReportRequest):
    """Placement Readiness Report from a completed session. Free — part of the session
    the user already paid for when generating questions."""
    return await generate_report(req.role, req.company, req.experience_level, req.results)


@router.post("/evaluate", dependencies=[Depends(charge_action("interview_feedback"))])
async def evaluate(req: EvaluateRequest):
    result = await evaluate_answer(req.question, req.answer, req.role)
    return result


@router.post("/coach/stream", dependencies=[Depends(charge_action("chat_message"))])
async def coach_stream(req: CoachRequest):
    async def generate():
        async for chunk in stream_coaching(req.question, req.answer, req.history):
            yield f"data: {json.dumps({'text': chunk})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/study-plan", dependencies=[Depends(charge_action("interview_feedback"))])
async def study_plan(req: StudyPlanRequest):
    plan = await generate_study_plan(req.role, req.timeline_days, req.weak_areas)
    return {"plan": plan}
