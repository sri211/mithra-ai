from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agents.mithra_orchestrator import stream_response, route_intent
import json

router = APIRouter()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    page_context: str = "dashboard"
    history: list[ChatMessage] = []


@router.post("/stream")
async def chat_stream(req: ChatRequest):
    history = [{"role": m.role, "content": m.content} for m in req.history]

    async def generate():
        async for chunk in stream_response(req.message, req.page_context, history):
            yield f"data: {json.dumps({'text': chunk})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@router.post("/intent")
async def detect_intent(req: ChatRequest):
    history = [{"role": m.role, "content": m.content} for m in req.history]
    result = await route_intent(req.message, req.page_context, history)
    return result
