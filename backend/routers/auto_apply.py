import uuid
import base64
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, List, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from db.database import get_db
from db.models import User, JobApplication, ApplyCampaign
from middleware.auth import get_current_user

router = APIRouter()


# ── Schemas ───────────────────────────────────────────────────────────────────

class CampaignUpsertRequest(BaseModel):
    role: str
    location: str
    ctc_min: int = 5
    ctc_max: int = 50
    experience_level: str = "mid"
    company_size: List[str] = ["any"]
    job_type: str = "fulltime"


class MarkAppliedRequest(BaseModel):
    job_id: str
    company: str
    role: str
    job_url: Optional[str] = None
    platform: Optional[str] = None
    match_score: int = 0
    cover_letter: Optional[str] = None
    jd_snippet: Optional[str] = None
    auto_submitted: bool = False


class UpdateStatusRequest(BaseModel):
    status: str


class AutoSubmitRequest(BaseModel):
    job_url: str
    job_id: str
    company: str
    role: str
    match_score: int = 0
    profile: dict  # name, email, phone, location, linkedin


# ── Campaign ──────────────────────────────────────────────────────────────────

@router.post("/campaign")
async def upsert_campaign(
    req: CampaignUpsertRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ApplyCampaign).where(ApplyCampaign.user_id == current_user.id)
    )
    existing = result.scalar_one_or_none()

    criteria = req.dict()
    if existing:
        existing.name = f"{req.role} in {req.location}"
        existing.criteria = criteria
        existing.status = "active"
    else:
        campaign = ApplyCampaign(
            id=str(uuid.uuid4()),
            user_id=current_user.id,
            name=f"{req.role} in {req.location}",
            criteria=criteria,
            status="active",
            jobs_found=0,
            jobs_applied=0,
        )
        db.add(campaign)

    await db.commit()
    return {"success": True}


@router.get("/campaign")
async def get_campaign(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ApplyCampaign).where(ApplyCampaign.user_id == current_user.id)
    )
    campaign = result.scalar_one_or_none()
    if not campaign:
        return {"campaign": None}
    return {
        "campaign": {
            "id": campaign.id,
            "name": campaign.name,
            "criteria": campaign.criteria,
            "status": campaign.status,
            "jobs_found": campaign.jobs_found,
            "jobs_applied": campaign.jobs_applied,
        }
    }


@router.delete("/campaign")
async def delete_campaign(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(ApplyCampaign).where(ApplyCampaign.user_id == current_user.id)
    )
    campaign = result.scalar_one_or_none()
    if campaign:
        await db.delete(campaign)
        await db.commit()
    return {"success": True}


# ── Applications ──────────────────────────────────────────────────────────────

@router.post("/mark-applied")
async def mark_applied(
    req: MarkAppliedRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Prevent duplicates
    dup = await db.execute(
        select(JobApplication).where(
            JobApplication.user_id == current_user.id,
            JobApplication.job_id == req.job_id,
        )
    )
    if dup.scalar_one_or_none():
        return {"success": True, "duplicate": True}

    app = JobApplication(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        job_id=req.job_id,
        company=req.company,
        role=req.role,
        job_url=req.job_url,
        platform=req.platform,
        match_score=req.match_score,
        cover_letter=req.cover_letter,
        jd_snippet=req.jd_snippet,
        status="applied",
        notes="Auto-submitted via Mithra AI" if req.auto_submitted else None,
    )
    db.add(app)

    # Increment campaign counter
    await db.execute(
        update(ApplyCampaign)
        .where(ApplyCampaign.user_id == current_user.id)
        .values(jobs_applied=ApplyCampaign.jobs_applied + 1)
    )

    await db.commit()
    return {"success": True, "id": app.id}


@router.get("/applications")
async def get_applications(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(JobApplication)
        .where(JobApplication.user_id == current_user.id)
        .order_by(JobApplication.applied_at.desc())
    )
    apps = result.scalars().all()
    return {
        "applications": [
            {
                "id": a.id,
                "job_id": a.job_id,
                "company": a.company,
                "role": a.role,
                "job_url": a.job_url,
                "platform": a.platform,
                "match_score": a.match_score,
                "status": a.status,
                "cover_letter": a.cover_letter,
                "applied_at": a.applied_at.isoformat() if a.applied_at else None,
                "notes": a.notes,
            }
            for a in apps
        ]
    }


@router.patch("/applications/{app_id}/status")
async def update_status(
    app_id: str,
    req: UpdateStatusRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(JobApplication).where(
            JobApplication.id == app_id,
            JobApplication.user_id == current_user.id,
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    valid = {"applied", "viewed", "shortlisted", "interview", "offer", "rejected"}
    if req.status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status")

    app.status = req.status
    await db.commit()
    return {"success": True}


@router.delete("/applications/{app_id}")
async def delete_application(
    app_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(JobApplication).where(
            JobApplication.id == app_id,
            JobApplication.user_id == current_user.id,
        )
    )
    app = result.scalar_one_or_none()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")

    await db.delete(app)
    await db.commit()
    return {"success": True}


# ── Auto-Submit (Playwright) ──────────────────────────────────────────────────

@router.post("/submit")
async def auto_submit(
    req: AutoSubmitRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Open a job URL with Playwright, detect form fields, pre-fill with user profile,
    take a screenshot, and return the result. The user then confirms the application.
    """
    result = {
        "success": False,
        "portal": "portal",
        "title": "",
        "fields_filled": 0,
        "message": "",
        "screenshot": None,
        "apply_url": req.job_url,
    }

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"],
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            # Navigate
            try:
                await page.goto(req.job_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(2500)
            except Exception:
                pass  # proceed even on timeout — page may have partially loaded

            title = await page.title()
            url = page.url.lower()
            result["title"] = title
            result["apply_url"] = page.url

            # Detect portal
            if "linkedin.com" in url:
                result["portal"] = "LinkedIn"
            elif "naukri.com" in url:
                result["portal"] = "Naukri"
            elif "instahyre.com" in url:
                result["portal"] = "Instahyre"
            elif "indeed.com" in url:
                result["portal"] = "Indeed"
            elif "glassdoor" in url:
                result["portal"] = "Glassdoor"
            elif "internshala.com" in url:
                result["portal"] = "Internshala"
            elif "wellfound.com" in url or "angel.co" in url:
                result["portal"] = "Wellfound"
            elif "hirist.com" in url or "hirist.tech" in url:
                result["portal"] = "Hirist"
            else:
                result["portal"] = "Job Portal"

            # Attempt to fill visible form fields
            name = req.profile.get("name", "")
            email = req.profile.get("email", "")
            phone = req.profile.get("phone", "")
            location = req.profile.get("location", "")

            fill_map = [
                # name fields
                ('input[name="name"], input[name="full_name"], input[name="fullName"]', name),
                ('input[placeholder*="name" i]:not([placeholder*="company" i]):not([placeholder*="user" i])', name),
                # email fields
                ('input[type="email"]', email),
                ('input[name="email"]', email),
                ('input[placeholder*="email" i]', email),
                # phone fields
                ('input[type="tel"]', phone),
                ('input[name="phone"], input[name="mobile"], input[name="contact"]', phone),
                ('input[placeholder*="phone" i], input[placeholder*="mobile" i]', phone),
                # location fields
                ('input[name="location"], input[name="city"], input[placeholder*="location" i]', location),
            ]

            filled = 0
            for selector, value in fill_map:
                if not value.strip():
                    continue
                try:
                    elems = page.locator(selector)
                    count = await elems.count()
                    for i in range(min(count, 1)):  # fill first match only
                        elem = elems.nth(i)
                        if await elem.is_visible(timeout=1000) and await elem.is_enabled(timeout=1000):
                            await elem.fill(value, timeout=2000)
                            filled += 1
                except Exception:
                    pass

            result["fields_filled"] = filled

            # Take viewport screenshot
            screenshot_bytes = await page.screenshot(
                type="jpeg",
                quality=70,
                clip={"x": 0, "y": 0, "width": 1280, "height": 800},
            )
            result["screenshot"] = base64.b64encode(screenshot_bytes).decode()

            if filled > 0:
                result["success"] = True
                result["message"] = (
                    f"Opened {req.company} application on {result['portal']}. "
                    f"Auto-filled {filled} field(s) with your profile data. "
                    f"Review and click Submit on the page."
                )
            else:
                result["success"] = True
                result["message"] = (
                    f"Opened {req.company} application on {result['portal']}. "
                    f"Portal requires login before showing the form. "
                    f"Open the link and apply — your adapted resume is ready."
                )

            await browser.close()

    except Exception as e:
        result["success"] = False
        result["message"] = f"Could not open the application page: {str(e)[:120]}"

    return result
