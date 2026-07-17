import uuid
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from db.database import get_db
from db.models import JobSearch
from middleware.auth import get_optional_user
from agents.job_finder_agent import get_job_pool, get_job_details
from services.match_scorer import score_jobs_for_resume
from services.company_size import classify_company, matches_company_type

router = APIRouter()


class JobSearchRequest(BaseModel):
    query: str = ""
    location: str = ""
    experience_years: int = 0
    salary_min: int = 0
    job_type: str = ""
    remote: str = ""
    portals: list[str] = []          # e.g. ["LinkedIn","Naukri","Indeed","Google"]
    company_type: str = ""           # "" | "small" | "mid" | "large" (comma-separated ok)
    company_name: str = ""           # optional — show only this company's roles
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
    if current_user:
        from services.credits import charge
        await charge(current_user, db, "job_search")

    has_resume = bool(req.resume_profile and req.resume_profile.get("personal"))

    effective_query = (req.query or "").strip()
    if not effective_query and has_resume:
        effective_query = _query_from_resume(req.resume_profile)
    if not effective_query:
        effective_query = "software engineer"

    # A company filter becomes part of the search itself so the pool is that
    # company's roles, matched to the user's profile/query.
    company_name = (req.company_name or "").strip()
    pool_query = f"{effective_query} at {company_name}" if company_name else effective_query

    # Platform preference is part of the cache key — different portals, different pool
    portals = [p for p in (req.portals or []) if p and p.lower() != "all"]
    portal_key = ",".join(sorted(p.lower() for p in portals))

    jobs = await get_job_pool(pool_query, req.location, portals=portals, company=company_name)

    # ── Local filters (free) ────────────────────────────────────────────
    def keep_min(lst, n=3):
        return lst if len(lst) >= n else None

    if company_name:
        want = company_name.lower()
        filtered = [j for j in jobs if want in (j.get("company") or "").lower()]
        # If the portal returned nothing for that company, keep the generated pool
        jobs = filtered or jobs

    if req.company_type:
        filtered = [j for j in jobs if matches_company_type(j.get("company", ""), req.company_type)]
        jobs = keep_min(filtered) or jobs

    if portals:
        wanted = {p.lower() for p in portals}
        filtered = [j for j in jobs if (j.get("portal") or "").lower() in wanted]
        jobs = keep_min(filtered) or jobs

    if req.remote and req.remote != "All":
        filtered = [j for j in jobs if (j.get("remote") or "").lower() == req.remote.lower()]
        jobs = keep_min(filtered) or jobs

    if req.salary_min:
        filtered = [j for j in jobs if (j.get("salary_max") or 0) == 0 or (j.get("salary_max") or 0) >= req.salary_min]
        jobs = keep_min(filtered) or jobs

    # Tag every job with its company size so the UI can show it
    for j in jobs:
        j["company_type"] = classify_company(j.get("company", ""))

    # Local deterministic resume scoring — zero API cost
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
            query=effective_query + (f" @{company_name}" if company_name else ""),
            location=req.location or None,
            results_json={"count": len(jobs), "portals": portal_key, "company_type": req.company_type},
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
