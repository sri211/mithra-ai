import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from db.database import get_db
from db.models import JobSearch
from middleware.auth import get_optional_user
from agents.job_finder_agent import get_job_pool, get_job_details
from services.match_scorer import score_jobs_for_resume

router = APIRouter()


class JobSearchRequest(BaseModel):
    query: str = ""
    location: str = ""
    experience_years: int = 0
    salary_min: int = 0
    job_type: str = ""
    remote: str = ""
    portals: list[str] = []
    user_profile: dict = {}
    resume_profile: dict = {}


def _query_from_resume(resume: dict) -> str:
    personal = resume.get("personal", {}) or {}
    title = personal.get("title", "")
    if title:
        return title
    exps = resume.get("experience") or []
    if exps:
        return exps[0].get("role", "") or "software engineer"
    return "software engineer"


@router.post("/search")
async def search(
    req: JobSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    has_resume = bool(req.resume_profile and req.resume_profile.get("personal"))

    # Resolve the effective query: explicit text, else derived from resume
    effective_query = (req.query or "").strip()
    if not effective_query and has_resume:
        effective_query = _query_from_resume(req.resume_profile)
    if not effective_query:
        effective_query = "software engineer"

    # Shared cached pool: one external fetch per query+location per 24h serves all users
    jobs = await get_job_pool(effective_query, req.location)

    # Per-user filters (free, local)
    if req.remote and req.remote != "All":
        filtered = [j for j in jobs if (j.get("remote") or "").lower() == req.remote.lower()]
        if len(filtered) >= 3:
            jobs = filtered
    if req.salary_min:
        filtered = [j for j in jobs if (j.get("salary_max") or 0) == 0 or (j.get("salary_max") or 0) >= req.salary_min]
        if len(filtered) >= 3:
            jobs = filtered

    # Local deterministic resume scoring — zero API cost, consistent results
    if has_resume:
        jobs = score_jobs_for_resume(jobs, req.resume_profile)
    else:
        for i, job in enumerate(jobs):
            if not (job.get("match_score") or 0) > 0:
                job["match_score"] = max(55, 78 - i * 3)
        jobs = sorted(jobs, key=lambda j: j.get("match_score") or 0, reverse=True)

    if current_user:
        db.add(JobSearch(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            query=effective_query,
            location=req.location or None,
            results_json={"count": len(jobs)},
        ))
        await db.commit()

    return {"jobs": jobs, "total": len(jobs)}


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = await get_job_details(job_id)
    if not job:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Job not found")
    return job
