"""
Application Tracker — DB-backed kanban board.

Uses the JobApplication table (shared with Auto Apply, so auto-applied jobs
appear on the board automatically). Data survives restarts and re-logins.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import JobApplication
from middleware.auth import get_current_user

router = APIRouter()

KANBAN_STAGES = ["bookmarked", "applied", "screening", "interview", "offer", "rejected", "accepted"]


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


def _serialize(app: JobApplication) -> dict:
    return {
        "id": app.id,
        "company": app.company,
        "role": app.role,
        "job_url": app.job_url or "",
        "location": "",
        "salary": "",
        "status": app.status or "applied",
        "notes": app.notes or "",
        "next_step": app.notes or "",
        "portal": app.platform or "",
        "match_score": app.match_score or 0,
        "applied_date": app.applied_at.date().isoformat() if app.applied_at else "",
        "created_at": app.applied_at.isoformat() if app.applied_at else "",
        "auto_submitted": False,
    }


@router.get("/")
async def list_applications(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(JobApplication)
        .where(JobApplication.user_id == user.id)
        .order_by(desc(JobApplication.applied_at))
    )
    apps = [_serialize(a) for a in result.scalars().all()]
    board = {stage: [a for a in apps if a["status"] == stage] for stage in KANBAN_STAGES}
    # Unknown statuses land in "applied" so nothing silently disappears
    known = set(KANBAN_STAGES)
    for a in apps:
        if a["status"] not in known:
            board["applied"].append(a)
    return {"board": board, "total": len(apps)}


@router.post("/")
async def create_application(
    req: ApplicationCreate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    status = req.status if req.status in KANBAN_STAGES else "applied"
    applied_at = datetime.now(timezone.utc)
    if req.applied_date:
        try:
            applied_at = datetime.fromisoformat(req.applied_date).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    app = JobApplication(
        id=str(uuid.uuid4()),
        user_id=user.id,
        job_id=str(uuid.uuid4()),
        company=req.company,
        role=req.role,
        job_url=req.job_url or None,
        platform=req.portal or None,
        status=status,
        notes=req.notes or None,
        applied_at=applied_at,
    )
    db.add(app)
    await db.commit()
    await db.refresh(app)
    return _serialize(app)


@router.patch("/{app_id}")
async def update_application(
    app_id: str,
    req: ApplicationUpdate,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(JobApplication).where(
            JobApplication.id == app_id,
            JobApplication.user_id == user.id,
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    if req.status is not None:
        app.status = req.status if req.status in KANBAN_STAGES else app.status
    if req.notes is not None:
        app.notes = req.notes
    await db.commit()
    await db.refresh(app)
    return _serialize(app)


@router.delete("/{app_id}")
async def delete_application(
    app_id: str,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(JobApplication).where(
            JobApplication.id == app_id,
            JobApplication.user_id == user.id,
        )
    )
    app = result.scalar_one_or_none()
    if app:
        await db.delete(app)
        await db.commit()
    return {"deleted": True}
