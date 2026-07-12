"""
Browser-extension API.

The extension runs inside the user's OWN logged-in browser, so it never hits
the anti-bot walls that blocked server-side auto-apply. These endpoints give it
everything it needs: the applicant profile, a resume PDF to attach, and a place
to report completed applications back to the Tracker.
"""
import html as _html
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import User, SavedResume, AdaptedResume, JobApplication
from middleware.auth import get_current_user

router = APIRouter()


def _latest_resume(resumes) -> dict:
    return resumes[0].resume_json if resumes else {}


@router.get("/profile")
async def extension_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Everything the extension needs to fill an application form."""
    res = await db.execute(
        select(SavedResume).where(SavedResume.user_id == current_user.id)
        .order_by(desc(SavedResume.created_at)).limit(1)
    )
    resume = (res.scalars().first().resume_json if res else {}) or {}
    resume = resume or {}
    personal = resume.get("personal", {}) or {}
    name = personal.get("name") or current_user.name or ""
    parts = name.split()
    skills_raw = resume.get("skills", {})
    if isinstance(skills_raw, dict):
        skills = skills_raw.get("technical", []) + skills_raw.get("soft", [])
    elif isinstance(skills_raw, list):
        skills = skills_raw
    else:
        skills = []

    # Derive years of experience from experience entries
    import re
    years = 0
    for e in (resume.get("experience") or []):
        try:
            s = int(re.search(r"\d{4}", str(e.get("start", ""))).group())
            end = 2026 if e.get("current") else int(re.search(r"\d{4}", str(e.get("end", ""))).group())
            years += max(0, end - s)
        except Exception:
            years += 1

    return {
        "name": name,
        "first_name": parts[0] if parts else "",
        "last_name": " ".join(parts[1:]) if len(parts) > 1 else "",
        "email": personal.get("email") or current_user.email or "",
        "phone": personal.get("phone", ""),
        "location": personal.get("location", ""),
        "city": (personal.get("location", "").split(",")[0].strip() if personal.get("location") else ""),
        "linkedin": personal.get("linkedin", ""),
        "github": personal.get("github", ""),
        "website": personal.get("website", ""),
        "headline": personal.get("title", ""),
        "summary": resume.get("summary", ""),
        "skills": skills[:30],
        "years_experience": years,
        "has_resume": bool(personal),
        # Sensible defaults for common screening questions — the extension caches
        # any the user overrides in-page, so they only answer novel ones once.
        "answers": {
            "notice_period": "30 days",
            "willing_to_relocate": "Yes",
            "authorized_to_work": "Yes",
            "expected_ctc": "",
            "current_ctc": "",
            "why_this_role": (resume.get("summary", "") or "")[:280],
        },
    }


def _resume_html(resume: dict, fallback_name: str) -> str:
    p = resume.get("personal", {}) or {}
    def esc(x): return _html.escape(str(x or ""))
    exp = ""
    for e in resume.get("experience", [])[:6]:
        bl = "".join(f"<li>{esc(b)}</li>" for b in (e.get("bullets") or [])[:5])
        exp += (f"<div class='role'><b>{esc(e.get('role'))}</b> — {esc(e.get('company'))} "
                f"<span class='dt'>({esc(e.get('start'))}–{'Present' if e.get('current') else esc(e.get('end'))})</span>"
                f"<ul>{bl}</ul></div>")
    edu = "".join(
        f"<div>{esc(ed.get('degree'))} {esc(ed.get('field'))}, {esc(ed.get('institution'))} "
        f"<span class='dt'>({esc(ed.get('start'))}–{esc(ed.get('end'))})</span></div>"
        for ed in resume.get("education", [])[:4])
    sk = resume.get("skills", {})
    skills = (sk.get("technical", []) + sk.get("soft", [])) if isinstance(sk, dict) else (sk if isinstance(sk, list) else [])
    return f"""<html><head><meta charset='utf-8'><style>
    body{{font-family:Georgia,serif;color:#111;padding:40px;line-height:1.5;font-size:12px}}
    h1{{font-size:22px;margin:0}} h2{{font-size:13px;border-bottom:1px solid #999;margin:16px 0 6px;text-transform:uppercase;letter-spacing:1px}}
    .contact{{color:#555;font-size:11px;margin:4px 0 12px}} .role{{margin-bottom:10px}} .dt{{color:#777;font-weight:normal}}
    ul{{margin:4px 0 0 18px}} li{{margin:2px 0}}</style></head><body>
    <h1>{esc(p.get('name') or fallback_name)}</h1>
    <div class='contact'>{esc(p.get('title'))} · {esc(p.get('email'))} · {esc(p.get('phone'))} · {esc(p.get('location'))}</div>
    {f"<h2>Summary</h2><div>{esc(resume.get('summary'))}</div>" if resume.get('summary') else ""}
    <h2>Experience</h2>{exp}
    {f"<h2>Education</h2>{edu}" if edu else ""}
    {f"<h2>Skills</h2><div>{', '.join(esc(s) for s in skills[:25])}</div>" if skills else ""}
    </body></html>"""


@router.get("/resume.pdf")
async def extension_resume_pdf(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Render the user's latest resume to a PDF the extension attaches to forms."""
    res = await db.execute(
        select(SavedResume).where(SavedResume.user_id == current_user.id)
        .order_by(desc(SavedResume.created_at)).limit(1)
    )
    row = res.scalars().first()
    resume = (row.resume_json if row else {}) or {}
    html = _resume_html(resume, current_user.name or "Candidate")
    try:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage",
                      "--disable-gpu", "--blink-settings=imagesEnabled=false"],
            )
            page = await browser.new_page()
            await page.set_content(html, wait_until="load")
            pdf = await page.pdf(format="A4", print_background=True,
                                 margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
            await browser.close()
        safe = "".join(c for c in (current_user.name or "resume") if c.isalnum() or c in " _-").strip() or "resume"
        return Response(content=pdf, media_type="application/pdf",
                        headers={"Content-Disposition": f'inline; filename="{safe}_resume.pdf"'})
    except Exception as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=500, detail=f"PDF render failed: {e}")


class ReportApplicationRequest(BaseModel):
    job_id: str = ""
    company: str
    role: str
    job_url: str = ""
    platform: str = ""
    match_score: int = 0
    status: str = "applied"


@router.post("/applications")
async def report_application(
    req: ReportApplicationRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Extension reports a completed application → lands on the Tracker board.
    Charges auto_apply credits (same as server-side apply)."""
    from services.credits import charge
    await charge(current_user, db, "auto_apply")

    job_id = req.job_id or str(uuid.uuid4())
    dup = await db.execute(select(JobApplication).where(
        JobApplication.user_id == current_user.id, JobApplication.job_id == job_id))
    if dup.scalar_one_or_none():
        return {"success": True, "duplicate": True}

    app = JobApplication(
        id=str(uuid.uuid4()), user_id=current_user.id, job_id=job_id,
        company=req.company, role=req.role, job_url=req.job_url or None,
        platform=req.platform or None, match_score=req.match_score,
        status=req.status if req.status in
            ("bookmarked", "applied", "screening", "interview", "offer", "rejected", "accepted")
            else "applied",
        notes="Applied via Mithra browser extension",
    )
    db.add(app)
    await db.commit()
    return {"success": True, "id": app.id}


@router.get("/ping")
async def ping(current_user: User = Depends(get_current_user)):
    """Extension uses this to verify the saved token still works."""
    return {"ok": True, "name": current_user.name, "email": current_user.email,
            "plan": current_user.plan.value if hasattr(current_user.plan, "value") else str(current_user.plan)}
