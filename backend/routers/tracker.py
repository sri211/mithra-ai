from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
import uuid
from datetime import datetime

from middleware.auth import get_optional_user

router = APIRouter()

# Per-user in-memory store (replace with DB in production)
_user_applications: dict[str, dict[str, dict]] = {}


def get_user_apps(user_id: str) -> dict[str, dict]:
    if user_id not in _user_applications:
        _user_applications[user_id] = {}
    return _user_applications[user_id]


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
async def list_applications(user=Depends(get_optional_user)):
    user_id = str(user.id) if user else "guest"
    apps_dict = get_user_apps(user_id)
    apps = list(apps_dict.values())
    board = {stage: [a for a in apps if a["status"] == stage] for stage in KANBAN_STAGES}
    return {"board": board, "total": len(apps)}


@router.post("/")
async def create_application(req: ApplicationCreate, user=Depends(get_optional_user)):
    user_id = str(user.id) if user else "guest"
    apps_dict = get_user_apps(user_id)
    app_id = str(uuid.uuid4())
    app = {
        "id": app_id,
        "created_at": datetime.utcnow().isoformat(),
        "applied_date": req.applied_date or datetime.utcnow().date().isoformat(),
        **req.model_dump(),
    }
    apps_dict[app_id] = app
    return app


@router.patch("/{app_id}")
async def update_application(app_id: str, req: ApplicationUpdate, user=Depends(get_optional_user)):
    from fastapi import HTTPException
    user_id = str(user.id) if user else "guest"
    apps_dict = get_user_apps(user_id)
    if app_id not in apps_dict:
        raise HTTPException(status_code=404, detail="Application not found")
    update = {k: v for k, v in req.model_dump().items() if v is not None}
    apps_dict[app_id].update(update)
    return apps_dict[app_id]


@router.delete("/{app_id}")
async def delete_application(app_id: str, user=Depends(get_optional_user)):
    user_id = str(user.id) if user else "guest"
    apps_dict = get_user_apps(user_id)
    apps_dict.pop(app_id, None)
    return {"deleted": True}
