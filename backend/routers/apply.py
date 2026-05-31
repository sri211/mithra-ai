from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from agents.job_applicator_agent import stream_apply_progress

router = APIRouter()


class ApplyRequest(BaseModel):
    job_url: str
    job_id: str
    user_profile: dict
    resume_path: str = ""
    cover_letter: str = ""


@router.post("/start")
async def start_apply(req: ApplyRequest):
    return StreamingResponse(
        stream_apply_progress(req.job_url, req.user_profile, req.resume_path),
        media_type="text/event-stream",
    )
