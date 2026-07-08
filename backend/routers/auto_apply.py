import uuid
import base64
import asyncio
import json
import os
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from cryptography.fernet import Fernet

from db.database import get_db
from db.models import User, JobApplication, ApplyCampaign, PortalCredential
from middleware.auth import get_current_user

router = APIRouter()

# ── Encryption ────────────────────────────────────────────────────────────────

def _get_fernet() -> Fernet:
    key = os.getenv("FERNET_KEY", "")
    if not key:
        # Derive a stable key from JWT_SECRET (not ideal but avoids new env var)
        import hashlib, base64 as b64
        secret = os.getenv("JWT_SECRET", "mithra_ai_jwt_secret_2026_very_long_random_string")
        raw = hashlib.sha256(secret.encode()).digest()
        key = b64.urlsafe_b64encode(raw).decode()
    return Fernet(key.encode() if isinstance(key, str) else key)

def encrypt_pw(plain: str) -> str:
    return _get_fernet().encrypt(plain.encode()).decode()

def decrypt_pw(enc: str) -> str:
    return _get_fernet().decrypt(enc.encode()).decode()

# ── In-memory submit sessions (1 worker only, see Dockerfile) ─────────────────
# Each session: { queue, input_event, input_value, user_id }
_sessions: dict = {}


# ── Schemas ────────────────────────────────────────────────────────────────────

class CampaignUpsertRequest(BaseModel):
    role: str; location: str
    ctc_min: int = 5; ctc_max: int = 50; experience_level: str = "mid"

class MarkAppliedRequest(BaseModel):
    job_id: str; company: str; role: str
    job_url: Optional[str] = None; platform: Optional[str] = None
    match_score: int = 0; cover_letter: Optional[str] = None
    jd_snippet: Optional[str] = None; auto_submitted: bool = False

class UpdateStatusRequest(BaseModel):
    status: str

class AutoSubmitRequest(BaseModel):
    job_url: str; job_id: str; company: str; role: str
    match_score: int = 0
    profile: dict   # name, email, phone, location, linkedin

class SessionInputRequest(BaseModel):
    value: str

class CredentialSaveRequest(BaseModel):
    portal: str    # linkedin | naukri | instahyre | indeed
    username: str  # email or phone
    password: str  # plain — encrypted before storing


# ── Portal helpers ─────────────────────────────────────────────────────────────

def detect_portal(url: str) -> str:
    u = url.lower()
    if "linkedin.com"   in u: return "linkedin"
    if "naukri.com"     in u: return "naukri"
    if "instahyre.com"  in u: return "instahyre"
    if "indeed.com"     in u: return "indeed"
    if "glassdoor"      in u: return "glassdoor"
    if "wellfound.com"  in u or "angel.co" in u: return "wellfound"
    if "internshala"    in u: return "internshala"
    if "hirist"         in u: return "hirist"
    return "other"

async def _screenshot(page) -> str:
    try:
        buf = await page.screenshot(type="jpeg", quality=65,
                                     clip={"x":0,"y":0,"width":1280,"height":800})
        return base64.b64encode(buf).decode()
    except Exception:
        return ""

async def _detect_blocker(page) -> str:
    url = page.url.lower()
    try: title = (await page.title()).lower()
    except: title = ""
    login_hints = ["/login","/signin","/sign-in","/auth/","accounts.google.com",
                   "login.microsoftonline","checkpoint/challenge","uas/login",
                   "nlogin","session/new"]
    title_hints = ["sign in","log in","login","join now","sign up to"]
    if any(h in url for h in login_hints) or any(h in title for h in title_hints):
        return "login"
    try:
        if await page.locator("iframe[src*='recaptcha'],iframe[src*='captcha']").count():
            return "captcha"
    except: pass
    return ""

async def _try_click_submit(page) -> bool:
    """Click the final submit button after form fill (assisted-apply confirm step)."""
    for sel in [
        'button[type="submit"]:visible',
        'button:has-text("Submit application")', 'button:has-text("Submit Application")',
        'button:has-text("Submit")', 'button:has-text("Send application")',
        'button:has-text("Apply")', 'input[type="submit"]',
        '[data-automation="submit-button"]',
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                await el.scroll_into_view_if_needed(timeout=1000)
                await el.click(timeout=2500)
                await page.wait_for_timeout(2500)
                return True
        except Exception:
            continue
    return False


async def _try_click_apply(page) -> bool:
    for sel in [
        '.jobs-apply-button','button.jobs-apply-button',
        'button:has-text("Easy Apply")','button:has-text("Apply Now")',
        'button:has-text("Apply")','a:has-text("Apply Now")',
        'a:has-text("Apply")','#apply-button','.btn-apply',
        '[data-automation="apply-button"]','a[data-test="apply-button"]',
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=800):
                await el.scroll_into_view_if_needed(timeout=1000)
                await el.click(timeout=2000)
                await page.wait_for_timeout(1500)
                return True
        except: continue
    return False

async def _fill_form(page, profile: dict) -> int:
    name  = profile.get("name","").strip()
    email = profile.get("email","").strip()
    phone = profile.get("phone","").strip()
    loc   = profile.get("location","").strip()
    li    = profile.get("linkedin","").strip()

    groups = [
        (['input[name="name"]','input[name="full_name"]','input[name="fullName"]',
          'input[id*="name" i]:not([id*="company" i]):not([id*="last" i])',
          'input[placeholder*="full name" i]','input[placeholder*="your name" i]'], name),
        (['input[type="email"]','input[name="email"]','input[id*="email" i]',
          'input[placeholder*="email" i]'], email),
        (['input[type="tel"]','input[name="phone"]','input[name="mobile"]',
          'input[id*="phone" i]','input[placeholder*="phone" i]',
          'input[placeholder*="mobile" i]'], phone),
        (['input[name="location"]','input[name="city"]','input[id*="location" i]',
          'input[placeholder*="location" i]','input[placeholder*="city" i]'], loc),
        (['input[name="linkedin"]','input[id*="linkedin" i]',
          'input[placeholder*="linkedin" i]'], li),
    ]
    filled = 0
    for selectors, value in groups:
        if not value: continue
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=400) and await el.is_enabled(timeout=400):
                    await el.clear(); await el.type(value, delay=25)
                    filled += 1; break
            except: continue
    return filled

async def _try_portal_login(page, portal: str, username: str, password: str) -> str:
    """Returns 'success' | 'otp' | 'captcha' | 'failed'"""
    try:
        if portal == "linkedin":
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)
            await page.fill("#username", username, timeout=5000)
            await page.fill("#password", password, timeout=5000)
            await page.click("button[type='submit']", timeout=5000)
            await page.wait_for_timeout(4000)
            url = page.url.lower()
            if "/feed" in url or "/jobs" in url or "/in/" in url: return "success"
            if "checkpoint" in url or "challenge" in url or "verification" in url: return "otp"
            if "captcha" in url: return "captcha"
            return "failed"

        elif portal == "naukri":
            await page.goto("https://www.naukri.com/nlogin/login", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)
            for sel in ["#usernameField","input[placeholder*='email' i]","input[name='email']"]:
                try: await page.fill(sel, username, timeout=2000); break
                except: pass
            for sel in ["#passwordField","input[type='password']"]:
                try: await page.fill(sel, password, timeout=2000); break
                except: pass
            for sel in [".loginButton","button[type='submit']","[data-automation='login-cta']"]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        await el.click(timeout=3000); break
                except: pass
            await page.wait_for_timeout(3500)
            url = page.url.lower()
            if "mnjuser" in url or "naukri.com/my" in url or "dashboard" in url: return "success"
            if "otp" in url or "verify" in url: return "otp"
            if "/login" in url: return "failed"
            return "success"  # redirect away from login = likely success

        elif portal == "instahyre":
            await page.goto("https://www.instahyre.com/login/", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)
            for sel in ["input[name='email']","input[type='email']"]:
                try: await page.fill(sel, username, timeout=2000); break
                except: pass
            for sel in ["input[name='password']","input[type='password']"]:
                try: await page.fill(sel, password, timeout=2000); break
                except: pass
            for sel in ["button[type='submit']","input[type='submit']"]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=1000):
                        await el.click(timeout=3000); break
                except: pass
            await page.wait_for_timeout(3000)
            return "success" if "login" not in page.url.lower() else "failed"

        else:
            return "failed"

    except Exception:
        return "failed"

async def _fill_otp(page, otp: str):
    for sel in ['input[name="pin"]','input[name="otp"]','input[name="code"]',
                'input[id*="otp" i]','input[id*="verification" i]',
                'input[placeholder*="code" i]','input[placeholder*="OTP" i]',
                'input[placeholder*="enter" i][maxlength]']:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1000):
                await el.clear(); await el.fill(otp)
                # click submit
                for ss in ['button[type="submit"]','button:has-text("Verify")','button:has-text("Submit")']:
                    try:
                        s = page.locator(ss).first
                        if await s.is_visible(timeout=500): await s.click(); break
                    except: pass
                return
        except: pass


# ── Playwright submit session (runs as background task) ───────────────────────

async def _run_submit_session(session_id: str, req: AutoSubmitRequest, user: User):
    # NOTE: never use the request-scoped DB session here — it's closed once the
    # /submit/start request returns. Open fresh sessions from AsyncSessionLocal.
    from db.database import AsyncSessionLocal
    session   = _sessions.get(session_id)
    if not session: return
    q: asyncio.Queue = session["queue"]

    async def emit(data: dict):
        await q.put(data)

    async def wait_input(timeout=180) -> Optional[str]:
        evt: asyncio.Event = session["input_event"]
        try:
            await asyncio.wait_for(evt.wait(), timeout=timeout)
            evt.clear()
            return session["input_value"]
        except asyncio.TimeoutError:
            return None

    from playwright.async_api import async_playwright
    pw_ctx = async_playwright()
    pw     = await pw_ctx.__aenter__()
    browser = None

    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox",
                  "--disable-dev-shm-usage","--disable-gpu",
                  "--disable-blink-features=AutomationControlled"],
        )
        context = await browser.new_context(
            viewport={"width":1280,"height":800},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        page = await context.new_page()
        portal = detect_portal(req.job_url)

        # ── Check for saved credentials (fresh DB session — background task) ──
        async with AsyncSessionLocal() as cred_db:
            cred_row = await cred_db.execute(
                select(PortalCredential).where(
                    PortalCredential.user_id == user.id,
                    PortalCredential.portal  == portal,
                )
            )
            cred = cred_row.scalar_one_or_none()

        # ── Navigate to job URL ───────────────────────────────────────────────
        await emit({"type":"status","message":"Opening job page…"})
        try:
            await page.goto(req.job_url, wait_until="domcontentloaded", timeout=22000)
        except Exception: pass
        await page.wait_for_timeout(2000)

        ss = await _screenshot(page)
        await emit({"type":"screenshot","data":ss})

        blocker = await _detect_blocker(page)

        # ── Handle login wall ─────────────────────────────────────────────────
        if blocker == "login":
            if cred:
                # Auto-login with stored credentials
                await emit({"type":"status","message":f"Logging in to {portal.title()}…"})
                pw_dec = decrypt_pw(cred.password_enc)
                login_result = await _try_portal_login(page, portal, cred.username, pw_dec)

                if login_result == "success":
                    await emit({"type":"status","message":"Logged in ✓ — navigating to job…"})
                    try: await page.goto(req.job_url, wait_until="domcontentloaded", timeout=20000)
                    except: pass
                    await page.wait_for_timeout(2000)
                    ss = await _screenshot(page)
                    await emit({"type":"screenshot","data":ss})

                elif login_result == "otp":
                    ss = await _screenshot(page)
                    await emit({
                        "type":"input_needed","field":"otp",
                        "message":f"OTP sent to your {portal.title()} email/phone — enter it below:",
                        "screenshot":ss,
                    })
                    otp = await wait_input(180)
                    if otp:
                        await _fill_otp(page, otp)
                        await page.wait_for_timeout(3000)
                        await emit({"type":"status","message":"OTP submitted — navigating to job…"})
                        try: await page.goto(req.job_url, wait_until="domcontentloaded", timeout=20000)
                        except: pass
                        await page.wait_for_timeout(2000)
                    else:
                        ss = await _screenshot(page)
                        await emit({"type":"done","success":False,"screenshot":ss,
                                    "message":"OTP timed out. Apply manually using the link below.",
                                    "apply_url":req.job_url})
                        return

                elif login_result == "captcha":
                    ss = await _screenshot(page)
                    await emit({"type":"done","success":False,"screenshot":ss,
                                "message":f"{portal.title()} blocked login with a CAPTCHA. "
                                          "Try the 'Open Application' button to apply manually.",
                                "apply_url":req.job_url})
                    return

                else:  # failed
                    ss = await _screenshot(page)
                    await emit({"type":"done","success":False,"screenshot":ss,
                                "needs_credentials":portal,
                                "message":f"Saved {portal.title()} credentials didn't work — "
                                          "update them and retry.",
                                "apply_url":req.job_url})
                    return

            else:
                # No credentials — end session with a needs_credentials done event
                # (a separate prompt event would be instantly replaced by done in the UI)
                ss = await _screenshot(page)
                await emit({"type":"done","success":False,"screenshot":ss,
                            "needs_credentials":portal,
                            "message":f"This job is on {portal.title()} which requires login. "
                                      f"Add your {portal.title()} credentials, then retry auto-apply.",
                            "apply_url":req.job_url})
                return

        elif blocker == "captcha":
            ss = await _screenshot(page)
            await emit({"type":"done","success":False,"screenshot":ss,
                        "message":"CAPTCHA detected. Use 'Open Application' to apply manually.",
                        "apply_url":req.job_url})
            return

        # ── Click Apply button ────────────────────────────────────────────────
        await emit({"type":"status","message":"Looking for Apply button…"})
        await _try_click_apply(page)
        await page.wait_for_timeout(1500)

        # Second login wall (some portals show login only after clicking Apply)
        blocker2 = await _detect_blocker(page)
        if blocker2 == "login" and cred:
            await emit({"type":"status","message":f"Re-logging in to {portal.title()}…"})
            pw_dec = decrypt_pw(cred.password_enc)
            r2 = await _try_portal_login(page, portal, cred.username, pw_dec)
            if r2 == "success":
                try: await page.goto(req.job_url, wait_until="domcontentloaded", timeout=20000)
                except: pass
                await page.wait_for_timeout(2000)
                await _try_click_apply(page)
                await page.wait_for_timeout(1500)
            elif r2 == "otp":
                ss = await _screenshot(page)
                await emit({"type":"input_needed","field":"otp",
                            "message":"OTP required — enter it below:","screenshot":ss})
                otp = await wait_input(180)
                if otp:
                    await _fill_otp(page, otp)
                    await page.wait_for_timeout(2500)

        # ── Fill form ─────────────────────────────────────────────────────────
        await emit({"type":"status","message":"Filling in your details…"})
        await page.wait_for_timeout(500)
        filled = await _fill_form(page, req.profile)

        # ── Assisted confirm: show the filled form, wait for one-tap approval ──
        ss = await _screenshot(page)
        await emit({
            "type": "input_needed", "field": "confirm_submit",
            "message": (f"Filled {filled} field(s). Review the screenshot — "
                        "tap Confirm to submit, or Cancel to finish manually."),
            "screenshot": ss,
        })
        answer = await wait_input(300)

        if answer and answer.strip().lower() in ("submit", "confirm", "yes", "y", "ok"):
            await emit({"type": "status", "message": "Submitting application…"})
            submitted = await _try_click_submit(page)
            await page.wait_for_timeout(1500)
            ss = await _screenshot(page)

            if submitted:
                # Record in tracker automatically (fresh DB session)
                try:
                    async with AsyncSessionLocal() as track_db:
                        dup = await track_db.execute(select(JobApplication).where(
                            JobApplication.user_id == user.id,
                            JobApplication.job_id == req.job_id))
                        if not dup.scalar_one_or_none():
                            track_db.add(JobApplication(
                                id=str(uuid.uuid4()), user_id=user.id, job_id=req.job_id,
                                company=req.company, role=req.role, job_url=req.job_url,
                                platform=portal, match_score=req.match_score,
                                status="applied", notes="Auto-submitted via Mithra AI"))
                            await track_db.commit()
                except Exception:
                    pass
                await emit({"type":"done","success":True,"fields_filled":filled,
                            "screenshot":ss,
                            "message":"✅ Application submitted and added to your tracker!",
                            "apply_url":req.job_url,"portal":portal.title()})
            else:
                await emit({"type":"done","success":False,"fields_filled":filled,
                            "screenshot":ss,
                            "message":"Couldn't find the submit button — finish manually with 'Open Application'.",
                            "apply_url":req.job_url,"portal":portal.title()})
        else:
            ss = await _screenshot(page)
            await emit({"type":"done","success":True,"fields_filled":filled,
                        "screenshot":ss,
                        "message":f"Auto-filled {filled} field(s). Use 'Open Application' to review and submit manually.",
                        "apply_url":req.job_url,"portal":portal.title()})

    except Exception as e:
        ss = await _screenshot(page) if browser else ""
        await emit({"type":"done","success":False,"fields_filled":0,
                    "screenshot":ss,"message":f"Error: {str(e)[:120]}",
                    "apply_url":req.job_url})
    finally:
        try: await browser.close()
        except: pass
        try: await pw_ctx.__aexit__(None, None, None)
        except: pass
        await asyncio.sleep(120)
        _sessions.pop(session_id, None)


# ── Campaign endpoints ─────────────────────────────────────────────────────────

@router.post("/campaign")
async def upsert_campaign(req: CampaignUpsertRequest,
                           db: AsyncSession = Depends(get_db),
                           current_user: User = Depends(get_current_user)):
    res = await db.execute(select(ApplyCampaign).where(ApplyCampaign.user_id == current_user.id))
    existing = res.scalar_one_or_none()
    criteria = req.dict()
    if existing:
        existing.name = f"{req.role} in {req.location}"; existing.criteria = criteria; existing.status = "active"
    else:
        db.add(ApplyCampaign(id=str(uuid.uuid4()), user_id=current_user.id,
                              name=f"{req.role} in {req.location}", criteria=criteria,
                              status="active", jobs_found=0, jobs_applied=0))
    await db.commit()
    return {"success": True}

@router.get("/campaign")
async def get_campaign(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    res = await db.execute(select(ApplyCampaign).where(ApplyCampaign.user_id == current_user.id))
    c = res.scalar_one_or_none()
    if not c: return {"campaign": None}
    return {"campaign": {"id":c.id,"name":c.name,"criteria":c.criteria,"status":c.status,
                         "jobs_found":c.jobs_found,"jobs_applied":c.jobs_applied}}

@router.delete("/campaign")
async def delete_campaign(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    res = await db.execute(select(ApplyCampaign).where(ApplyCampaign.user_id == current_user.id))
    c = res.scalar_one_or_none()
    if c: await db.delete(c); await db.commit()
    return {"success": True}


# ── Application endpoints ─────────────────────────────────────────────────────

@router.post("/mark-applied")
async def mark_applied(req: MarkAppliedRequest, db: AsyncSession = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    dup = await db.execute(select(JobApplication).where(
        JobApplication.user_id == current_user.id, JobApplication.job_id == req.job_id))
    if dup.scalar_one_or_none():
        return {"success": True, "duplicate": True}
    app = JobApplication(id=str(uuid.uuid4()), user_id=current_user.id, job_id=req.job_id,
                          company=req.company, role=req.role, job_url=req.job_url, platform=req.platform,
                          match_score=req.match_score, cover_letter=req.cover_letter, jd_snippet=req.jd_snippet,
                          status="applied", notes="Auto-submitted via Mithra AI" if req.auto_submitted else None)
    db.add(app)
    await db.execute(update(ApplyCampaign).where(ApplyCampaign.user_id == current_user.id)
                     .values(jobs_applied=ApplyCampaign.jobs_applied + 1))
    await db.commit()
    return {"success": True, "id": app.id}

@router.get("/applications")
async def get_applications(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    res = await db.execute(select(JobApplication).where(JobApplication.user_id == current_user.id)
                            .order_by(JobApplication.applied_at.desc()))
    apps = res.scalars().all()
    return {"applications": [{"id":a.id,"job_id":a.job_id,"company":a.company,"role":a.role,
                               "job_url":a.job_url,"platform":a.platform,"match_score":a.match_score,
                               "status":a.status,"cover_letter":a.cover_letter,
                               "applied_at":a.applied_at.isoformat() if a.applied_at else None,
                               "notes":a.notes} for a in apps]}

@router.patch("/applications/{app_id}/status")
async def update_status(app_id: str, req: UpdateStatusRequest,
                         db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    res = await db.execute(select(JobApplication).where(
        JobApplication.id == app_id, JobApplication.user_id == current_user.id))
    app = res.scalar_one_or_none()
    if not app: raise HTTPException(404)
    valid = {"applied","viewed","shortlisted","interview","offer","rejected"}
    if req.status not in valid: raise HTTPException(400, "Invalid status")
    app.status = req.status; await db.commit()
    return {"success": True}

@router.delete("/applications/{app_id}")
async def delete_application(app_id: str, db: AsyncSession = Depends(get_db),
                              current_user: User = Depends(get_current_user)):
    res = await db.execute(select(JobApplication).where(
        JobApplication.id == app_id, JobApplication.user_id == current_user.id))
    app = res.scalar_one_or_none()
    if not app: raise HTTPException(404)
    await db.delete(app); await db.commit()
    return {"success": True}


# ── Portal credentials ────────────────────────────────────────────────────────

@router.post("/credentials")
async def save_credentials(req: CredentialSaveRequest,
                            db: AsyncSession = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    allowed = {"linkedin","naukri","instahyre","indeed","glassdoor","wellfound","internshala"}
    if req.portal not in allowed: raise HTTPException(400, "Unsupported portal")

    res = await db.execute(select(PortalCredential).where(
        PortalCredential.user_id == current_user.id, PortalCredential.portal == req.portal))
    existing = res.scalar_one_or_none()

    enc = encrypt_pw(req.password)
    if existing:
        existing.username = req.username; existing.password_enc = enc
    else:
        db.add(PortalCredential(id=str(uuid.uuid4()), user_id=current_user.id,
                                 portal=req.portal, username=req.username, password_enc=enc))
    await db.commit()
    return {"success": True}

@router.get("/credentials")
async def get_credentials(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    res = await db.execute(select(PortalCredential).where(PortalCredential.user_id == current_user.id))
    rows = res.scalars().all()
    # Never return passwords
    return {"credentials": [{"portal":r.portal,"username":r.username} for r in rows]}

@router.delete("/credentials/{portal}")
async def delete_credentials(portal: str, db: AsyncSession = Depends(get_db),
                              current_user: User = Depends(get_current_user)):
    res = await db.execute(select(PortalCredential).where(
        PortalCredential.user_id == current_user.id, PortalCredential.portal == portal))
    row = res.scalar_one_or_none()
    if row: await db.delete(row); await db.commit()
    return {"success": True}


# ── SSE auto-submit ───────────────────────────────────────────────────────────

@router.post("/submit/start")
async def start_submit(req: AutoSubmitRequest,
                        db: AsyncSession = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    from services.credits import charge
    await charge(current_user, db, "auto_apply")
    sid = str(uuid.uuid4())
    _sessions[sid] = {
        "queue": asyncio.Queue(),
        "input_event": asyncio.Event(),
        "input_value": None,
        "user_id": current_user.id,
    }
    # Run Playwright in background — task opens its own DB sessions
    asyncio.create_task(_run_submit_session(sid, req, current_user))
    return {"session_id": sid}

@router.get("/submit/stream/{session_id}")
async def stream_submit(session_id: str, current_user: User = Depends(get_current_user)):
    session = _sessions.get(session_id)
    if not session or session["user_id"] != current_user.id:
        raise HTTPException(404, "Session not found")

    async def generate():
        q: asyncio.Queue = session["queue"]
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30)
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "done":
                    break
            except asyncio.TimeoutError:
                yield "data: {\"type\":\"keepalive\"}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no","Connection":"keep-alive"},
    )

@router.post("/submit/input/{session_id}")
async def provide_input(session_id: str, req: SessionInputRequest,
                         current_user: User = Depends(get_current_user)):
    session = _sessions.get(session_id)
    if not session or session["user_id"] != current_user.id:
        raise HTTPException(404)
    session["input_value"] = req.value
    session["input_event"].set()
    return {"ok": True}
