from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agents.interview_agent import generate_questions, evaluate_answer, stream_coaching, generate_study_plan
import json

router = APIRouter()


class QuestionRequest(BaseModel):
    role: str
    company: str = ""
    interview_type: str = "behavioral"
    difficulty: str = "medium"
    count: int = 10


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


@router.post("/questions")
async def get_questions(req: QuestionRequest):
    result = await generate_questions(req.role, req.company, req.interview_type, req.difficulty, req.count)
    return result


@router.post("/evaluate")
async def evaluate(req: EvaluateRequest):
    result = await evaluate_answer(req.question, req.answer, req.role)
    return result


@router.post("/coach/stream")
async def coach_stream(req: CoachRequest):
    async def generate():
        async for chunk in stream_coaching(req.question, req.answer, req.history):
            yield f"data: {json.dumps({'text': chunk})}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/study-plan")
async def study_plan(req: StudyPlanRequest):
    plan = await generate_study_plan(req.role, req.timeline_days, req.weak_areas)
    return {"plan": plan}
