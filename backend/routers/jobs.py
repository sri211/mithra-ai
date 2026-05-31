from fastapi import APIRouter
from pydantic import BaseModel
from agents.job_finder_agent import search_jobs, rank_jobs_for_profile, get_job_details

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


@router.post("/search")
async def search(req: JobSearchRequest):
    jobs = await search_jobs(
        req.query, req.location, req.experience_years,
        req.salary_min, req.job_type, req.remote, req.portals
    )
    if req.user_profile:
        jobs = await rank_jobs_for_profile(jobs, req.user_profile)
    return {"jobs": jobs, "total": len(jobs)}


@router.get("/{job_id}")
async def get_job(job_id: str):
    job = await get_job_details(job_id)
    if not job:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Job not found")
    return job
