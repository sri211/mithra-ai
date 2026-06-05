import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from db.database import get_db
from db.models import User, SavedResume, AdaptedResume, JobSearch
from middleware.auth import get_current_user

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class SaveResumeRequest(BaseModel):
    name: str
    resume_json: Any
    template: str = "modern"
    ats_score: float = 0.0


class SaveAdaptedResumeRequest(BaseModel):
    original_resume_id: Optional[str] = None
    jd_text: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    adapted_json: Any
    ats_before: float = 0.0
    ats_after: float = 0.0


class SaveJobSearchRequest(BaseModel):
    query: str
    location: Optional[str] = None
    results_json: Optional[Any] = None


# ── Saved Resumes ─────────────────────────────────────────────────────────────

@router.post("/resumes")
async def save_resume(
    req: SaveResumeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    saved = SavedResume(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        name=req.name,
        resume_json=req.resume_json,
        template=req.template,
        ats_score=req.ats_score,
    )
    db.add(saved)
    await db.commit()
    await db.refresh(saved)
    return {
        "id": saved.id,
        "name": saved.name,
        "template": saved.template,
        "ats_score": saved.ats_score,
        "created_at": saved.created_at.isoformat(),
    }


@router.get("/resumes")
async def list_resumes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedResume)
        .where(SavedResume.user_id == current_user.id)
        .order_by(desc(SavedResume.created_at))
    )
    resumes = result.scalars().all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "template": r.template,
            "ats_score": r.ats_score,
            "resume_json": r.resume_json,
            "created_at": r.created_at.isoformat(),
        }
        for r in resumes
    ]


@router.delete("/resumes/{resume_id}")
async def delete_resume(
    resume_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SavedResume).where(
            SavedResume.id == resume_id,
            SavedResume.user_id == current_user.id,
        )
    )
    resume = result.scalar_one_or_none()
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    await db.delete(resume)
    await db.commit()
    return {"deleted": True}


# ── Adapted Resumes ───────────────────────────────────────────────────────────

@router.post("/adapted-resumes")
async def save_adapted_resume(
    req: SaveAdaptedResumeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    adapted = AdaptedResume(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        original_resume_id=req.original_resume_id,
        jd_text=req.jd_text,
        company=req.company,
        role=req.role,
        adapted_json=req.adapted_json,
        ats_before=req.ats_before,
        ats_after=req.ats_after,
    )
    db.add(adapted)
    await db.commit()
    await db.refresh(adapted)
    return {
        "id": adapted.id,
        "company": adapted.company,
        "role": adapted.role,
        "ats_before": adapted.ats_before,
        "ats_after": adapted.ats_after,
        "created_at": adapted.created_at.isoformat(),
    }


@router.get("/adapted-resumes")
async def list_adapted_resumes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AdaptedResume)
        .where(AdaptedResume.user_id == current_user.id)
        .order_by(desc(AdaptedResume.created_at))
    )
    items = result.scalars().all()
    return [
        {
            "id": r.id,
            "company": r.company,
            "role": r.role,
            "ats_before": r.ats_before,
            "ats_after": r.ats_after,
            "adapted_json": r.adapted_json,
            "created_at": r.created_at.isoformat(),
        }
        for r in items
    ]


# ── Job Searches ──────────────────────────────────────────────────────────────

@router.post("/job-searches")
async def save_job_search(
    req: SaveJobSearchRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    search = JobSearch(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        query=req.query,
        location=req.location,
        results_json=req.results_json,
    )
    db.add(search)
    await db.commit()
    await db.refresh(search)
    return {
        "id": search.id,
        "query": search.query,
        "location": search.location,
        "created_at": search.created_at.isoformat(),
    }


@router.get("/job-searches")
async def list_job_searches(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(JobSearch)
        .where(JobSearch.user_id == current_user.id)
        .order_by(desc(JobSearch.created_at))
        .limit(5)
    )
    items = result.scalars().all()
    return [
        {
            "id": s.id,
            "query": s.query,
            "location": s.location,
            "results_json": s.results_json,
            "created_at": s.created_at.isoformat(),
        }
        for s in items
    ]
