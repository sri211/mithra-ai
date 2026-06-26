import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from db.database import get_db
from db.models import JobSearch
from middleware.auth import get_optional_user
from agents.job_finder_agent import search_jobs, rank_jobs_for_profile, get_job_details, generate_jobs_with_resume

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


@router.post("/search")
async def search(
    req: JobSearchRequest,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_optional_user),
):
    resume_matched = False

    # Resume-based search: full resume → generate perfectly matched jobs via Claude
    if req.resume_profile and req.resume_profile.get("personal"):
        jobs = await generate_jobs_with_resume(
            req.resume_profile,
            location=req.location,
            experience_years=req.experience_years,
            remote=req.remote,
            salary_min=req.salary_min,
        )
        if jobs:
            resume_matched = True
        else:
            # Claude failed — fall back to standard search with resume title as query
            title = req.resume_profile.get("personal", {}).get("title", "") or req.query
            jobs = await search_jobs(title, req.location, req.experience_years, req.salary_min, req.job_type, req.remote, req.portals)
    else:
        jobs = await search_jobs(
            req.query, req.location, req.experience_years,
            req.salary_min, req.job_type, req.remote, req.portals
        )

    # If jobs weren't resume-matched (came from JSearch/fallback), rank by profile
    if not resume_matched and jobs:
        profile = req.user_profile or (
            {
                "title": req.resume_profile.get("personal", {}).get("title", ""),
                "skills": req.resume_profile.get("skills", {}),
                "experience": req.resume_profile.get("experience", [])[:2],
            }
            if req.resume_profile
            else {}
        )
        if profile:
            jobs = await rank_jobs_for_profile(jobs, profile)

    # Save search to DB when user is authenticated
    if current_user:
        effective_query = req.query or req.resume_profile.get("personal", {}).get("title", "resume-match")
        job_search = JobSearch(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            query=effective_query,
            location=req.location or None,
            results_json={"count": len(jobs)},
        )
        db.add(job_search)
        await db.commit()

    return {"jobs": jobs, "total": len(jobs)}


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = await get_job_details(job_id)
    if not job:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Job not found")
    return job
