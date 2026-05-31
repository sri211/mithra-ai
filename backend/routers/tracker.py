from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
import json, uuid
from datetime import datetime

router = APIRouter()

# In-memory store for demo; replace with DB repo in production
_applications: dict[str, dict] = {}


class ApplicationCreate(BaseModel):
    company: str
    role: str
    job_url: str = ""
    location: str = ""
    salary: str = ""
    status: str = "applied"
    notes: str = ""
    applied_date: str = ""
    portal: str = ""


class ApplicationUpdate(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None
    next_step: Optional[str] = None
    interview_date: Optional[str] = None


KANBAN_STAGES = ["bookmarked", "applied", "screening", "interview", "offer", "rejected", "accepted"]


@router.get("/")
async def list_applications():
    apps = list(_applications.values())
    board = {stage: [a for a in apps if a["status"] == stage] for stage in KANBAN_STAGES}
    return {"board": board, "total": len(apps)}


@router.post("/")
async def create_application(req: ApplicationCreate):
    app_id = str(uuid.uuid4())
    app = {
        "id": app_id,
        "created_at": datetime.utcnow().isoformat(),
        "applied_date": req.applied_date or datetime.utcnow().date().isoformat(),
        **req.model_dump(),
    }
    _applications[app_id] = app
    return app


@router.patch("/{app_id}")
async def update_application(app_id: str, req: ApplicationUpdate):
    if app_id not in _applications:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Application not found")
    update = {k: v for k, v in req.model_dump().items() if v is not None}
    _applications[app_id].update(update)
    return _applications[app_id]


@router.delete("/{app_id}")
async def delete_application(app_id: str):
    _applications.pop(app_id, None)
    return {"deleted": True}
