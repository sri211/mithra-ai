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
                   "nlogin","session/new","authwall"]
    title_hints = ["sign in","log in","login","join now","sign up to"]
    if any(h in url for h in login_hints) or any(h in title for h in title_hints):
        return "login"
    # Login MODAL OVERLAYS — portals show these on job pages without changing the URL
    # (LinkedIn contextual sign-in modal was the main miss)
    overlay_selectors = [
        ".sign-in-modal", ".contextual-sign-in-modal", "[data-test-modal] .sign-in-form",
        "div.authwall", "#organic-div form.login-form",
        "button.sign-in-modal__outlet-btn",
    ]
    for sel in overlay_selectors:
        try:
            if await page.locator(sel).first.is_visible(timeout=300):
                return "login"
        except Exception:
            pass
    try:
        # Text-based overlay detection: a visible "Sign in" dialog over the page
        dialog = page.locator("div[role='dialog'], section[role='dialog']").first
        if await dialog.is_visible(timeout=300):
            txt = (await dialog.inner_text(timeout=500)).lower()
            if "sign in" in txt or "join now" in txt or "continue with google" in txt:
                return "login"
    except Exception:
        pass
    try:
        if await page.locator("iframe[src*='recaptcha'],iframe[src*='captcha']").count():
            return "captcha"
    except: pass
    return ""


async def _dismiss_overlays(page):
    """Close cookie banners and dismissible modals that block interaction."""
    for sel in [
        "button:has-text('Accept')", "button:has-text('Accept all')",
        "button[aria-label='Dismiss']", "button.modal__dismiss",
        "icon[type='cancel-icon']", "button:has-text('✕')",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=250):
                await el.click(timeout=800)
                await page.wait_for_timeout(400)
        except Exception:
            continue


async def _count_empty_required(page) -> list[str]:
    """Names/placeholders of visible required inputs still empty after fill."""
    empties: list[str] = []
    try:
        inputs = page.locator("input[required]:visible, textarea[required]:visible")
        n = min(await inputs.count(), 12)
        for i in range(n):
            el = inputs.nth(i)
            try:
                if not (await el.input_value(timeout=300)).strip():
                    label = (await el.get_attribute("placeholder") or
                             await el.get_attribute("name") or
                             await el.get_attribute("aria-label") or "field")
                    empties.append(label[:40])
            except Exception:
                continue
    except Exception:
        pass
    return empties

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

        # ═══ AGENT LOOP: plan → act → verify, until applied or genuinely blocked ═══
        # (login walls — including modal overlays — are handled inside the loop)
        async def record_in_tracker():
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

        async def login_with_otp() -> str:
            """Login; if OTP needed, ask the user and continue. Returns final state."""
            pw_dec = decrypt_pw(cred.password_enc)
            result = await _try_portal_login(page, portal, cred.username, pw_dec)
            if result == "otp":
                ss2 = await _screenshot(page)
                await emit({"type":"input_needed","field":"otp",
                            "message":f"{portal.title()} sent an OTP to your email/phone — enter it to continue:",
                            "screenshot":ss2})
                otp = await wait_input(240)
                if not otp:
                    return "otp_timeout"
                await _fill_otp(page, otp)
                await page.wait_for_timeout(3500)
                # Verify OTP actually worked
                if await _detect_blocker(page) == "login":
                    return "failed"
                return "success"
            return result

        applied_clicked = False
        filled_total = 0
        login_attempts = 0
        missing_asked = 0
        confirm_shown = False

        for step in range(1, 9):
            await _dismiss_overlays(page)
            state = await _detect_blocker(page)
            ss = await _screenshot(page)
            await emit({"type":"screenshot","data":ss})

            # ── Login wall (incl. modal overlays) ────────────────────────────
            if state == "login":
                if not cred:
                    await emit({"type":"done","success":False,"screenshot":ss,
                                "needs_credentials":portal,
                                "message":f"{portal.title()} requires login. Add your credentials, then retry.",
                                "apply_url":req.job_url})
                    return
                if login_attempts >= 2:
                    await emit({"type":"done","success":False,"screenshot":ss,
                                "needs_credentials":portal,
                                "message":f"Login to {portal.title()} keeps failing — check your credentials.",
                                "apply_url":req.job_url})
                    return
                login_attempts += 1
                await emit({"type":"status","message":f"Step {step}: logging in to {portal.title()}…"})
                lr = await login_with_otp()
                if lr == "success":
                    await emit({"type":"status","message":"Logged in ✓ — returning to the job…"})
                    try: await page.goto(req.job_url, wait_until="domcontentloaded", timeout=22000)
                    except Exception: pass
                    await page.wait_for_timeout(2000)
                    continue
                if lr == "otp_timeout":
                    await emit({"type":"done","success":False,"screenshot":ss,
                                "message":"OTP timed out. Retry auto-apply when you have the code handy.",
                                "apply_url":req.job_url})
                    return
                if lr == "captcha":
                    await emit({"type":"done","success":False,"screenshot":ss,
                                "message":f"{portal.title()} demanded a CAPTCHA at login — apply manually via 'Open Application'.",
                                "apply_url":req.job_url})
                    return
                continue  # failed → loop retries once more

            if state == "captcha":
                await emit({"type":"done","success":False,"screenshot":ss,
                            "message":"CAPTCHA on this page — finish manually via 'Open Application'.",
                            "apply_url":req.job_url})
                return

            # ── Reach the application form ───────────────────────────────────
            if not applied_clicked:
                await emit({"type":"status","message":f"Step {step}: opening the application form…"})
                clicked = await _try_click_apply(page)
                applied_clicked = True
                if clicked:
                    await page.wait_for_timeout(1500)
                    continue  # re-detect: apply click often triggers a login wall

            # ── Fill + verify ────────────────────────────────────────────────
            await emit({"type":"status","message":f"Step {step}: filling your details…"})
            filled_total += await _fill_form(page, req.profile)
            empties = await _count_empty_required(page)

            if empties and missing_asked < 2:
                missing_asked += 1
                field_name = empties[0]
                ss = await _screenshot(page)
                await emit({"type":"input_needed","field":"missing_info",
                            "message":f"The form needs '{field_name}' which isn't in your profile — type it below:",
                            "screenshot":ss})
                answer = await wait_input(240)
                if answer:
                    try:
                        el = page.locator("input[required]:visible, textarea[required]:visible").first
                        # fill the first still-empty required field
                        inputs = page.locator("input[required]:visible, textarea[required]:visible")
                        for i in range(min(await inputs.count(), 12)):
                            cand = inputs.nth(i)
                            if not (await cand.input_value(timeout=300)).strip():
                                await cand.fill(answer)
                                filled_total += 1
                                break
                    except Exception:
                        pass
                    continue  # re-verify

            # ── Confirm & submit ─────────────────────────────────────────────
            if not confirm_shown:
                confirm_shown = True
                ss = await _screenshot(page)
                await emit({
                    "type": "input_needed", "field": "confirm_submit",
                    "message": (f"Filled {filled_total} field(s)"
                                + (f" — {len(empties)} field(s) may still need attention" if empties else "")
                                + ". Review the screenshot and tap Confirm to submit."),
                    "screenshot": ss,
                })
                answer = await wait_input(300)
                if not (answer and answer.strip().lower() in ("submit", "confirm", "yes", "y", "ok")):
                    ss = await _screenshot(page)
                    await emit({"type":"done","success":True,"fields_filled":filled_total,
                                "screenshot":ss,
                                "message":f"Auto-filled {filled_total} field(s). Finish and submit via 'Open Application'.",
                                "apply_url":req.job_url,"portal":portal.title()})
                    return

            await emit({"type":"status","message":f"Step {step}: submitting…"})
            submitted = await _try_click_submit(page)
            await page.wait_for_timeout(2000)

            if submitted:
                # Verify submission actually landed (confirmation text or form gone)
                confirmed = False
                try:
                    body_text = (await page.locator("body").inner_text(timeout=1500)).lower()
                    if any(t in body_text for t in ["application sent", "application submitted",
                                                     "successfully applied", "thank you for applying",
                                                     "application received", "applied successfully"]):
                        confirmed = True
                except Exception:
                    pass
                if not confirmed:
                    # Form gone = likely submitted
                    try:
                        confirmed = (await _count_empty_required(page)) == [] and \
                                    not await page.locator("button[type='submit']:visible").first.is_visible(timeout=500)
                    except Exception:
                        confirmed = True  # can't verify — trust the click
                ss = await _screenshot(page)
                await record_in_tracker()
                await emit({"type":"done","success":True,"fields_filled":filled_total,
                            "screenshot":ss,
                            "message":("✅ Application submitted and added to your Tracker!"
                                       if confirmed else
                                       "Submitted (couldn't fully verify) — added to your Tracker; double-check via 'Open Application'."),
                            "apply_url":req.job_url,"portal":portal.title()})
                return
            else:
                # No submit button — maybe a multi-step form advanced, loop once more
                if step >= 7:
                    ss = await _screenshot(page)
                    await emit({"type":"done","success":False,"fields_filled":filled_total,
                                "screenshot":ss,
                                "message":"Couldn't find the submit button — finish manually with 'Open Application'.",
                                "apply_url":req.job_url,"portal":portal.title()})
                    return
                continue

        # Loop exhausted
        ss = await _screenshot(page)
        await emit({"type":"done","success":False,"fields_filled":filled_total,
                    "screenshot":ss,
                    "message":"This portal's flow is unusual — finish manually via 'Open Application'.",
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
