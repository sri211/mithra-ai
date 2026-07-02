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
    has_query = bool(req.query and req.query.strip())
    has_resume = bool(req.resume_profile and req.resume_profile.get("personal"))
    jobs: list[dict] = []

    if has_query:
        # Text search: fetch relevant jobs by query, then rank by resume for personalised scores
        jobs = await search_jobs(
            req.query, req.location, req.experience_years,
            req.salary_min, req.job_type, req.remote, req.portals,
        )
        if jobs and has_resume:
            profile = {
                "title": req.resume_profile.get("personal", {}).get("title", ""),
                "skills": req.resume_profile.get("skills", {}),
                "experience": req.resume_profile.get("experience", [])[:3],
                "summary": req.resume_profile.get("summary", ""),
            }
            jobs = await rank_jobs_for_profile(jobs, profile)

    elif has_resume:
        # No query: pure resume-driven generation — every job hand-picked for this candidate
        jobs = await generate_jobs_with_resume(
            req.resume_profile,
            location=req.location,
            experience_years=req.experience_years,
            remote=req.remote,
            salary_min=req.salary_min,
        )
        if not jobs:
            # Claude failed — fall back to title search + ranking
            title = req.resume_profile.get("personal", {}).get("title", "software engineer")
            jobs = await search_jobs(title, req.location, req.experience_years, req.salary_min, req.job_type, req.remote, req.portals)
            if jobs:
                profile = {
                    "title": req.resume_profile.get("personal", {}).get("title", ""),
                    "skills": req.resume_profile.get("skills", {}),
                    "experience": req.resume_profile.get("experience", [])[:3],
                }
                jobs = await rank_jobs_for_profile(jobs, profile)
    else:
        jobs = await search_jobs(
            req.query or "software engineer", req.location, req.experience_years,
            req.salary_min, req.job_type, req.remote, req.portals,
        )

    # Sort by match_score descending so best matches are always first
    if jobs:
        jobs = sorted(jobs, key=lambda j: j.get("match_score") or 0, reverse=True)

    if current_user:
        effective_query = req.query or (
            req.resume_profile.get("personal", {}).get("title", "resume-match")
            if has_resume else "search"
        )
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
