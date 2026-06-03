from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from agents.mithra_orchestrator import stream_response, route_intent
import json

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class UserProfile(BaseModel):
    name: str = ""
    currentRole: str = ""
    targetRole: str = ""
    skills: list[str] = []
    experienceSummary: str = ""
    yearsOfExperience: int = 0


class ChatRequest(BaseModel):
    message: str
    page_context: str = "dashboard"
    history: list[ChatMessage] = []
    user_profile: Optional[UserProfile] = None
    resume_loaded: bool = False


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    history = [{"role": m.role, "content": m.content} for m in req.history]
    profile = req.user_profile.model_dump() if req.user_profile else None

    async def generate():
        async for chunk in stream_response(req.message, req.page_context, history, user_profile=profile, resume_loaded=req.resume_loaded):
            yield f"data: {json.dumps({'text': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/intent")
async def detect_intent(req: ChatRequest):
    history = [{"role": m.role, "content": m.content} for m in req.history]
    profile = req.user_profile.model_dump() if req.user_profile else None
    result = await route_intent(req.message, req.page_context, history, user_profile=profile)
    return result
