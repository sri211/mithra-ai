import uuid
import base64
import asyncio
import json
import os
import html as _html
import tempfile
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
    resume: dict = {}  # full resume JSON — used to generate the PDF attachment + cover text

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

async def _safe(coro, secs: float = 20.0, default=None):
    """Run any Playwright coroutine under a HARD asyncio timeout. Guarantees no
    single browser op can ever freeze the agent loop (root cause of 8-min hangs)."""
    try:
        return await asyncio.wait_for(coro, timeout=secs)
    except (asyncio.TimeoutError, Exception):
        return default


async def _captcha_is_blocking(page) -> bool:
    """True only for a VISIBLE captcha challenge the user must solve.
    Nearly every site embeds an invisible reCAPTCHA for form protection — that is
    NOT a blocker and must be ignored (this false-positive killed real applications)."""
    # Visible reCAPTCHA checkbox / challenge, or hCaptcha, sized and on-screen
    candidates = [
        "iframe[title*='recaptcha' i]",
        "iframe[src*='recaptcha/api2/anchor']",
        "iframe[src*='hcaptcha']",
        "div.g-recaptcha:visible", "div.h-captcha:visible",
        "iframe[src*='recaptcha/api2/bframe']",  # the image-grid challenge popup
    ]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=250):
                box = await el.bounding_box()
                if box and box["width"] > 60 and box["height"] > 40:
                    return True
        except Exception:
            continue
    return False


async def _login_wall_present(page) -> bool:
    """URL-level or full-page login (not a dismissible modal)."""
    url = page.url.lower()
    try: title = (await page.title()).lower()
    except: title = ""
    login_hints = ["/login","/signin","/sign-in","/auth/","accounts.google.com",
                   "login.microsoftonline","checkpoint/challenge","uas/login",
                   "nlogin","session/new","authwall","/register","/signup"]
    if any(h in url for h in login_hints):
        return True
    title_hints = ["sign in","log in","login","join now","sign up to","register"]
    if any(h in title for h in title_hints):
        # Confirm there's actually a password field (title alone is unreliable)
        try:
            if await page.locator("input[type='password']:visible").first.is_visible(timeout=400):
                return True
        except Exception:
            pass
    return False


async def _detect_blocker(page) -> str:
    """Returns '' | 'login' | 'captcha'. Called AFTER _dismiss_overlays, so any
    modal that could be closed is already gone by the time this runs."""
    if await _login_wall_present(page):
        return "login"
    # A login/signup MODAL still visible after dismissal attempts = real wall
    for sel in [".sign-in-modal", ".contextual-sign-in-modal", "div.authwall",
                "#organic-div form.login-form"]:
        try:
            if await page.locator(sel).first.is_visible(timeout=250):
                # Only a wall if it contains auth inputs
                if await page.locator("input[type='password']:visible, input[type='email']:visible").first.is_visible(timeout=300):
                    return "login"
        except Exception:
            pass
    if await _captcha_is_blocking(page):
        return "captcha"
    return ""


async def _dismiss_overlays(page):
    """Close cookie/consent banners AND dismissible signup/login modals.

    Many job boards (Jobaaj, Instahyre, etc.) pop a 'Sign up' modal over the job
    that has a close (×) button — closing it reveals the real Apply button. We
    always TRY to close first; only an undismissable auth wall counts as a blocker.
    """
    # 1. Consent / cookie accept (main frame)
    for sel in [
        "button:has-text('Accept all')", "button:has-text('Accept All')",
        "button:has-text('Accept')", "button:has-text('AGREE')",
        "button:has-text('Agree')", "button:has-text('I agree')",
        "button:has-text('Got it')", "button:has-text('Allow all')",
        "button[mode='primary']", "#onetrust-accept-btn-handler",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=200):
                await el.click(timeout=800); await page.wait_for_timeout(350); break
        except Exception:
            continue

    # 2. Consent inside CMP iframes (TCF frameworks)
    try:
        for frame in page.frames[1:6]:
            for sel in ["button:has-text('Accept')", "button:has-text('AGREE')", "button:has-text('Consent')"]:
                try:
                    el = frame.locator(sel).first
                    if await el.is_visible(timeout=150):
                        await el.click(timeout=800); await page.wait_for_timeout(350); break
                except Exception:
                    continue
    except Exception:
        pass

    # 3. Close dismissible signup/login modals (the × / close button)
    for sel in [
        "div[role='dialog'] button[aria-label*='close' i]",
        "div[role='dialog'] button[aria-label*='dismiss' i]",
        "[class*='modal'] button[aria-label*='close' i]",
        "button.modal__dismiss", "button.close", "button.modal-close",
        ".modal button:has-text('✕')", ".modal button:has-text('×')",
        "[class*='popup'] [class*='close']", "svg[aria-label*='close' i]",
        "button:has-text('Maybe later')", "button:has-text('Skip')",
        "button:has-text('Continue without')", "button:has-text('Not now')",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=200):
                await el.click(timeout=800); await page.wait_for_timeout(400)
        except Exception:
            continue

    # 4. Escape key as a last-resort modal closer
    try:
        if await page.locator("div[role='dialog']:visible, [class*='modal']:visible").first.is_visible(timeout=200):
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(300)
    except Exception:
        pass


LOGINNABLE_PORTALS = {"linkedin", "naukri", "instahyre"}


async def _is_logged_out(page, portal: str) -> bool:
    """True when the page shows a signed-out state (Sign in / Join now / authwall).
    Works even when there's no password field yet (LinkedIn public job view)."""
    # Logged-IN indicators — if present, we're authenticated, bail early
    logged_in_markers = {
        "linkedin": ["img.global-nav__me-photo", ".global-nav__me", "div.global-nav__me"],
        "naukri": [".nI-gNb-drawer__icon", ".view-profile-wrapper", "img.nI-gNb-menuIcon__pic"],
        "instahyre": [".navbar-profile", "a[href*='logout']"],
    }.get(portal, [])
    for sel in logged_in_markers:
        try:
            if await page.locator(sel).first.is_visible(timeout=300):
                return False
        except Exception:
            pass
    # Signed-OUT affordances
    signout_selectors = [
        "a[href*='/login']", "a[href*='signin' i]",
        "button:has-text('Sign in')", "a:has-text('Sign in')",
        "button:has-text('Join now')", "a:has-text('Join now')",
        ".authwall", ".sign-in-form", "a.nav__button-secondary",
        "div.nI-gNb-log-reg",  # naukri logged-out nav
    ]
    for sel in signout_selectors:
        try:
            if await page.locator(sel).first.is_visible(timeout=300):
                return True
        except Exception:
            pass
    return False


async def _on_application_form(page) -> bool:
    """True only when we're genuinely on a job APPLICATION form — prevents the
    agent from filling random inputs on a listings/search page and then falsely
    claiming 'ready to submit'."""
    # Strongest signal: a resume/file upload field
    try:
        if await page.locator("input[type='file']").first.is_visible(timeout=400):
            return True
    except Exception:
        pass
    # A form region containing an email OR phone input AND an apply/submit button
    try:
        has_contact = await page.locator(
            "input[type='email']:visible, input[type='tel']:visible, "
            "input[name*='phone' i]:visible, input[placeholder*='email' i]:visible"
        ).first.is_visible(timeout=400)
    except Exception:
        has_contact = False
    try:
        has_submit = await page.locator(
            "button:has-text('Submit application'), button:has-text('Submit Application'), "
            "button:has-text('Send application'), button[type='submit']:visible"
        ).first.is_visible(timeout=400)
    except Exception:
        has_submit = False
    if has_contact and has_submit:
        return True
    # Application-specific page text near a form
    try:
        body = (await page.locator("body").inner_text(timeout=1000)).lower()
        markers = ["fill out this form", "expected salary", "notice period",
                   "upload a new resume", "upload resume", "cover letter",
                   "years of experience", "message for recruiter"]
        if sum(1 for m in markers if m in body) >= 2:
            return True
    except Exception:
        pass
    return False


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
          'input[placeholder*="email" i]','input[placeholder*="mail" i]',
          'input[autocomplete="email"]'], email),
        (['input[type="tel"]','input[name="phone"]','input[name="mobile"]',
          'input[id*="phone" i]','input[placeholder*="phone" i]',
          'input[placeholder*="mobile" i]'], phone),
        (['input[name="location"]','input[name="city"]','input[id*="location" i]',
          'input[placeholder*="location" i]','input[placeholder*="city" i]'], loc),
        (['input[name="linkedin"]','input[id*="linkedin" i]',
          'input[placeholder*="linkedin" i]'], li),
    ]
    # First + last name split for portals that separate them
    parts = name.split()
    first = parts[0] if parts else ""
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    groups += [
        (['input[name*="first" i]','input[id*="first" i]','input[placeholder*="first name" i]'], first),
        (['input[name*="last" i]','input[id*="last" i]','input[placeholder*="last name" i]'], last),
    ]

    filled = 0
    for selectors, value in groups:
        if not value: continue
        for sel in selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=400) and await el.is_enabled(timeout=400):
                    cur = ""
                    try: cur = (await el.input_value(timeout=300)).strip()
                    except Exception: pass
                    if cur:  # already populated — don't overwrite
                        break
                    # fill() with a hard timeout — .type() could stall for 30s per field
                    await el.fill(value, timeout=2500)
                    filled += 1; break
            except: continue
    return filled


async def _fill_named_field(page, field_hint: str, value: str) -> bool:
    """Fill a specific field identified by its label/placeholder/name hint."""
    hint = (field_hint or "").lower()[:30]
    selectors = [
        f'input[placeholder*="{hint}" i]', f'input[name*="{hint}" i]',
        f'input[aria-label*="{hint}" i]', f'textarea[placeholder*="{hint}" i]',
    ]
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=400):
                await el.fill(value, timeout=2500)
                return True
        except Exception:
            continue
    # Fallback: first still-empty required field
    try:
        inputs = page.locator("input[required]:visible, textarea[required]:visible")
        for i in range(min(await inputs.count(), 12)):
            cand = inputs.nth(i)
            try:
                if not (await cand.input_value(timeout=300)).strip():
                    await cand.fill(value, timeout=2500)
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def _fill_custom_dropdowns(page, profile: dict, resume: dict) -> int:
    """Handle React/styled dropdowns that are NOT native <select> — click to open,
    then click the best-matching option. Covers Qualification, Location, Department,
    Notice Period, Experience, etc. seen on Jobaaj/Instahyre/Foundit."""
    filled = 0
    resume = resume or {}
    edu = (resume.get("education") or [{}])
    degree = (edu[0].get("degree") if edu else "") or ""
    loc = profile.get("location", "") or ""
    role = (resume.get("personal", {}) or {}).get("title", "") or profile.get("role", "")

    # label-substring → preferred option keyword(s)
    intents = [
        (["qualification", "education", "degree"], [degree, "bachelor", "graduate", "b.tech", "any graduate"]),
        (["location", "city"], [loc, "bangalore", "bengaluru"]),
        (["department", "function", "category"], [role, "sales", "marketing", "operations", "general"]),
        (["notice", "availability"], ["immediate", "15 days", "30 days", "1 month"]),
        (["experience", "total exp"], ["3", "2-5", "mid"]),
    ]

    # Custom-dropdown triggers: elements that visually look like selects
    trigger_selectors = [
        "[class*='select']:visible", "[class*='dropdown']:visible",
        "[role='combobox']:visible", "div[class*='control']:visible",
        "button[aria-haspopup='listbox']:visible",
    ]
    try:
        seen_labels = set()
        for tsel in trigger_selectors:
            triggers = page.locator(tsel)
            tcount = min(await triggers.count(), 10)
            for i in range(tcount):
                trig = triggers.nth(i)
                try:
                    if not await trig.is_visible(timeout=200):
                        continue
                    # Read nearby text to infer what this dropdown is for
                    context_txt = ""
                    try:
                        context_txt = (await trig.evaluate(
                            "el => (el.closest('div')?.innerText || '').slice(0,80)"
                        ) or "").lower()
                    except Exception:
                        pass
                    if context_txt in seen_labels:
                        continue
                    seen_labels.add(context_txt)

                    match = None
                    for labels, options in intents:
                        if any(l in context_txt for l in labels):
                            match = options
                            break
                    if not match:
                        continue

                    # Open the dropdown
                    await trig.click(timeout=1500)
                    await page.wait_for_timeout(500)

                    # Click the first option matching our preferred keywords
                    picked = False
                    for kw in [m for m in match if m]:
                        opt = page.locator(
                            f"[role='option']:has-text('{kw}'), li:has-text('{kw}'), "
                            f"[class*='option']:has-text('{kw}')"
                        ).first
                        try:
                            if await opt.is_visible(timeout=600):
                                await opt.click(timeout=1200)
                                filled += 1
                                picked = True
                                await page.wait_for_timeout(300)
                                break
                        except Exception:
                            continue
                    # If nothing matched, pick the first real option so validation passes
                    if not picked:
                        opt = page.locator("[role='option'], li[class*='option'], [class*='option']").first
                        try:
                            if await opt.is_visible(timeout=500):
                                await opt.click(timeout=1200)
                                filled += 1
                                await page.wait_for_timeout(300)
                        except Exception:
                            # close the dropdown so it doesn't block other fields
                            await page.keyboard.press("Escape")
                except Exception:
                    continue
    except Exception:
        pass
    return filled


async def _generate_resume_pdf(context, resume: dict, name: str) -> str:
    """Render the resume JSON to a simple PDF and return the temp file path.
    Used to attach to portal file-upload fields. Returns '' on failure."""
    if not resume or not resume.get("personal"):
        return ""
    try:
        p = resume.get("personal", {})
        def esc(x): return _html.escape(str(x or ""))
        exp_html = ""
        for e in resume.get("experience", [])[:6]:
            bullets = "".join(f"<li>{esc(b)}</li>" for b in (e.get("bullets") or [])[:5])
            exp_html += (f"<div class='role'><b>{esc(e.get('role'))}</b> — {esc(e.get('company'))}"
                         f" <span class='dt'>({esc(e.get('start'))}–{'Present' if e.get('current') else esc(e.get('end'))})</span>"
                         f"<ul>{bullets}</ul></div>")
        edu_html = "".join(
            f"<div>{esc(ed.get('degree'))} {esc(ed.get('field'))}, {esc(ed.get('institution'))} "
            f"<span class='dt'>({esc(ed.get('start'))}–{esc(ed.get('end'))})</span></div>"
            for ed in resume.get("education", [])[:4])
        sk = resume.get("skills", {})
        skills = sk.get("technical", []) + sk.get("soft", []) if isinstance(sk, dict) else (sk if isinstance(sk, list) else [])
        skills_html = ", ".join(esc(s) for s in skills[:25])
        doc = f"""<html><head><meta charset='utf-8'><style>
        body{{font-family:Georgia,serif;color:#111;padding:40px;line-height:1.5;font-size:12px}}
        h1{{font-size:22px;margin:0}} h2{{font-size:13px;border-bottom:1px solid #999;margin:16px 0 6px;text-transform:uppercase;letter-spacing:1px}}
        .contact{{color:#555;font-size:11px;margin:4px 0 12px}} .role{{margin-bottom:10px}} .dt{{color:#777;font-weight:normal}}
        ul{{margin:4px 0 0 18px}} li{{margin:2px 0}}</style></head><body>
        <h1>{esc(p.get('name') or name)}</h1>
        <div class='contact'>{esc(p.get('title'))} · {esc(p.get('email'))} · {esc(p.get('phone'))} · {esc(p.get('location'))}</div>
        {f"<h2>Summary</h2><div>{esc(resume.get('summary'))}</div>" if resume.get('summary') else ""}
        <h2>Experience</h2>{exp_html}
        {f"<h2>Education</h2>{edu_html}" if edu_html else ""}
        {f"<h2>Skills</h2><div>{skills_html}</div>" if skills_html else ""}
        </body></html>"""
        pg = await context.new_page()
        await pg.set_content(doc, wait_until="load")
        fd, path = tempfile.mkstemp(suffix=".pdf", prefix="mithra_resume_")
        os.close(fd)
        await pg.pdf(path=path, format="A4", print_background=True,
                     margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"})
        await pg.close()
        return path
    except Exception:
        return ""


async def _upload_resume(page, pdf_path: str) -> bool:
    """Attach the generated resume PDF to any file input on the page."""
    if not pdf_path:
        return False
    try:
        inputs = page.locator("input[type='file']")
        n = await inputs.count()
        for i in range(min(n, 4)):
            el = inputs.nth(i)
            try:
                # File inputs are often hidden behind a styled button — set anyway
                await el.set_input_files(pdf_path, timeout=3000)
                await page.wait_for_timeout(1200)
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


async def _fill_choices(page, profile: dict) -> int:
    """Handle dropdowns, radios and checkboxes with sensible defaults so the form
    passes validation. Conservative: only touches clearly-safe controls."""
    filled = 0
    yes_words = ["yes", "i agree", "agree", "authorized", "authorised", "available", "willing"]

    # Native <select> — pick the first meaningful non-placeholder option
    try:
        selects = page.locator("select:visible")
        for i in range(min(await selects.count(), 8)):
            sel = selects.nth(i)
            try:
                val = await sel.input_value(timeout=300)
                if val:  # already chosen
                    continue
                options = sel.locator("option")
                oc = await options.count()
                for j in range(oc):
                    otext = (await options.nth(j).inner_text(timeout=200)).strip().lower()
                    oval = await options.nth(j).get_attribute("value")
                    if not oval or otext in ("", "select", "-- select --", "choose", "please select"):
                        continue
                    await sel.select_option(index=j, timeout=1500)
                    filled += 1
                    break
            except Exception:
                continue
    except Exception:
        pass

    # Consent / agreement checkboxes — tick required ones
    try:
        boxes = page.locator("input[type='checkbox']:visible")
        for i in range(min(await boxes.count(), 6)):
            box = boxes.nth(i)
            try:
                required = await box.get_attribute("required")
                if required is not None and not await box.is_checked():
                    await box.check(timeout=1200)
                    filled += 1
            except Exception:
                continue
    except Exception:
        pass

    # Yes/No radio groups — answer affirmatively, EXCEPT "fresher?" which should
    # be "No" for anyone with work experience (Jobaaj defaults it wrongly to Yes)
    is_experienced = bool(profile.get("_has_experience"))
    try:
        radios = page.locator("input[type='radio']:visible")
        rc = await radios.count()
        seen_names = set()
        for i in range(min(rc, 12)):
            r = radios.nth(i)
            try:
                nm = await r.get_attribute("name")
                if nm in seen_names:
                    continue
                label = ((await r.get_attribute("value")) or "").lower()
                aria = ((await r.get_attribute("aria-label")) or "").lower()
                # Read the group's question text to detect "fresher"
                q = ""
                try:
                    q = (await r.evaluate("el => (el.closest('div')?.innerText||'').slice(0,60)") or "").lower()
                except Exception:
                    pass
                want_no = ("fresher" in q or "fresher" in aria) and is_experienced
                want = "no" if want_no else None
                pick = False
                if want == "no":
                    pick = ("no" == label or "false" == label or "no" in aria)
                else:
                    pick = any(w in label or w in aria for w in yes_words)
                if pick and not await r.is_checked():
                    await r.check(timeout=1000)
                    filled += 1
                    if nm: seen_names.add(nm)
            except Exception:
                continue
    except Exception:
        pass

    return filled

async def _try_portal_login(page, portal: str, username: str, password: str) -> str:
    """Returns 'success' | 'otp' | 'captcha' | 'failed'"""
    try:
        if portal == "linkedin":
            await page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)
            # If LinkedIn shows a social-login-only page, try to reveal the email form
            try:
                if not await page.locator("#username, input[name='session_key']").first.is_visible(timeout=1500):
                    for rev in ["a:has-text('Sign in with email')", "button:has-text('Sign in with email')",
                                "a:has-text('email')", "a[href*='login']"]:
                        try:
                            el = page.locator(rev).first
                            if await el.is_visible(timeout=600):
                                await el.click(timeout=1500); await page.wait_for_timeout(1200); break
                        except Exception:
                            continue
            except Exception:
                pass
            # Fill username/password (multiple selector variants)
            u_ok = p_ok = False
            for sel in ["#username", "input[name='session_key']", "input[autocomplete='username']"]:
                try:
                    await page.fill(sel, username, timeout=2500); u_ok = True; break
                except Exception:
                    continue
            for sel in ["#password", "input[name='session_password']", "input[type='password']"]:
                try:
                    await page.fill(sel, password, timeout=2500); p_ok = True; break
                except Exception:
                    continue
            if not (u_ok and p_ok):
                # No password form offered — LinkedIn is serving a social-only / anti-bot page
                return "blocked"
            for sel in ["button[type='submit']", "button[data-litms-control-urn*='login']", "button:has-text('Sign in')"]:
                try:
                    await page.click(sel, timeout=3000); break
                except Exception:
                    continue
            await page.wait_for_timeout(4500)
            url = page.url.lower()
            if "/feed" in url or "/jobs" in url or "/in/" in url or "linkedin.com/checkpoint/lg/login-submit" not in url and "login" not in url:
                return "success"
            if "checkpoint" in url or "challenge" in url or "verification" in url or "add-phone" in url:
                return "otp"
            if "captcha" in url:
                return "captcha"
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
        # Discard any stale input from a previous (possibly timed-out) prompt
        evt.clear()
        session["input_value"] = None
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
    resume_pdf = ""

    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox","--disable-setuid-sandbox",
                  "--disable-dev-shm-usage","--disable-gpu",
                  "--disable-blink-features=AutomationControlled",
                  # Memory-slimming — Chrome was being OOM-killed on this 3.7GB box
                  "--js-flags=--max-old-space-size=384",
                  "--disable-extensions","--disable-background-networking",
                  "--disable-background-timer-throttling","--disable-renderer-backgrounding",
                  "--memory-pressure-off","--disable-features=site-per-process,TranslateUI",
                  "--blink-settings=imagesEnabled=false"],
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

        # BLOCK consent/CMP and tracker scripts at the network level so the
        # "This site asks for consent to use your data" popup never renders —
        # a cross-origin CMP iframe was hanging the whole session uncancellably.
        _BLOCK_HOSTS = (
            "fundingchoicesmessages.google.com", "funding-choices",
            "cookiebot.com", "cookie-cdn", "onetrust.com", "cookielaw.org",
            "quantcast", "consensu.org", "cmp.", "usercentrics",
            "consent.", "privacy-mgmt", "sourcepoint", "trustarc",
            "doubleclick.net", "googletagmanager.com",
        )
        async def _route(route):
            try:
                req_ = route.request
                url_l = req_.url.lower()
                # Block consent/CMP/trackers AND heavy media (images/video/fonts)
                # to cut Chrome's memory footprint — we only need the form's text/inputs.
                if any(h in url_l for h in _BLOCK_HOSTS) or req_.resource_type in ("image", "media", "font"):
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                try: await route.continue_()
                except Exception: pass
        try:
            await context.route("**/*", _route)
        except Exception:
            pass

        page = await context.new_page()
        # Cap ALL Playwright operations so nothing can wait the default 30s —
        # combined with _safe() this makes an uncancellable hang impossible.
        page.set_default_timeout(8000)
        page.set_default_navigation_timeout(25000)
        portal = detect_portal(req.job_url)

        # Pre-generate the resume PDF once, for any file-upload field we meet
        try:
            resume_pdf = await _generate_resume_pdf(context, req.resume, req.profile.get("name", ""))
        except Exception:
            resume_pdf = ""

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
        resume_uploaded = False
        form_retry_done = False
        did_login = False

        from loguru import logger as _log
        for step in range(1, 9):
            # Instant liveness check — if Chrome was OOM-killed, is_connected() is
            # False immediately (no CDP call), so we abort cleanly instead of
            # hanging on every subsequent Playwright call until the watchdog.
            if not browser.is_connected():
                _log.warning(f"[auto-apply {session_id[:8]}] browser died (likely OOM) at step {step}")
                await emit({"type":"done","success":False,"fields_filled":filled_total,
                            "message":"The apply session ran out of resources — please retry (server just got more memory headroom).",
                            "apply_url":req.job_url,"portal":portal.title()})
                return
            await _safe(_dismiss_overlays(page), 25, None)
            state = await _safe(_detect_blocker(page), 15, "")
            # Proactive login: we have credentials for a loginnable portal and the
            # page is showing a signed-out state (e.g. LinkedIn public job view with
            # a 'Sign in' button but no password field). Log in BEFORE giving up.
            if (state != "login" and cred and not did_login
                    and portal in LOGINNABLE_PORTALS and login_attempts < 2):
                if await _safe(_is_logged_out(page, portal), 8, False):
                    state = "login"
            _log.info(f"[auto-apply {session_id[:8]}] step {step}: state={state or 'ok'} applied_clicked={applied_clicked} filled={filled_total} did_login={did_login}")
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
                    did_login = True
                    applied_clicked = False  # re-find Apply now that we're authenticated
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
                if lr == "blocked":
                    await emit({"type":"done","success":False,"screenshot":ss,
                                "message":(f"{portal.title()} is blocking automated sign-in from our server "
                                           "(it only offered social login). This is their anti-bot protection — "
                                           "please apply while signed in on your own browser via 'Open Application'."),
                                "apply_url":req.job_url,"portal":portal.title()})
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
                clicked = await _safe(_try_click_apply(page), 20, False)
                applied_clicked = True
                if clicked:
                    await page.wait_for_timeout(1500)
                    continue  # re-detect: apply click often triggers a login wall

            # ── Gate: are we ACTUALLY on an application form? ────────────────
            # Prevents filling random inputs on a listings/search page and then
            # falsely claiming "ready to submit".
            on_form = await _safe(_on_application_form(page), 10, False)
            if not on_form:
                # One more attempt to reach the form (a second Apply/Apply-Now click)
                if not form_retry_done:
                    form_retry_done = True
                    await emit({"type":"status","message":f"Step {step}: locating the application form…"})
                    await _safe(_try_click_apply(page), 15, False)
                    await page.wait_for_timeout(1800)
                    continue
                # Genuinely no reachable form — honest handoff, no false success
                ss = await _screenshot(page)
                need_login = not cred and portal != "other"
                await emit({
                    "type": "done", "success": False, "fields_filled": filled_total,
                    "screenshot": ss,
                    **({"needs_credentials": portal} if need_login else {}),
                    "message": (
                        f"This {portal.title()} job needs you to sign in before the application form opens. "
                        f"Add your {portal.title()} credentials above, then retry."
                        if need_login else
                        "Couldn't reach an application form on this portal — it likely requires signing in "
                        "or applying on their site. Use 'Open Application' to finish."
                    ),
                    "apply_url": req.job_url, "portal": portal.title(),
                })
                return

            # ── Fill + verify (every op time-boxed so nothing can freeze) ─────
            await emit({"type":"status","message":f"Step {step}: filling your details…"})
            filled_total += await _safe(_fill_form(page, req.profile), 45, 0) or 0
            # Attach resume to any file-upload field
            if resume_pdf and not resume_uploaded:
                if await _safe(_upload_resume(page, resume_pdf), 30, False):
                    resume_uploaded = True
                    filled_total += 1
                    await emit({"type":"status","message":"Attached your resume ✓"})
            # Native + custom dropdowns, consent checkboxes, yes/no radios
            filled_total += await _safe(_fill_choices(page, req.profile), 45, 0) or 0
            filled_total += await _safe(_fill_custom_dropdowns(page, req.profile, req.resume), 45, 0) or 0
            empties = await _safe(_count_empty_required(page), 15, []) or []

            if empties and missing_asked < 3:
                missing_asked += 1
                field_name = empties[0]
                ss = await _screenshot(page)
                await emit({"type":"input_needed","field":"missing_info",
                            "message":f"The form needs '{field_name}' — type the value below and I'll fill it:",
                            "screenshot":ss})
                answer = await wait_input(240)
                if answer:
                    await _safe(_fill_named_field(page, field_name, answer), 15, None)
                    filled_total += 1
                    continue  # re-verify
                # No answer within timeout — stop asking, move to confirm/handoff
                missing_asked = 3

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
            submitted = await _safe(_try_click_submit(page), 20, False)
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

    except asyncio.CancelledError:
        # Watchdog cancelled us — the wrapper emits the timeout message
        raise
    except Exception as e:
        from loguru import logger
        logger.error(f"[auto-apply {session_id[:8]}] session error: {e!r}")
        ss = await _screenshot(page) if browser else ""
        await emit({"type":"done","success":False,"fields_filled":0,
                    "screenshot":ss,"message":f"Error: {str(e)[:120]}",
                    "apply_url":req.job_url})
    finally:
        try: await browser.close()
        except: pass
        try: await pw_ctx.__aexit__(None, None, None)
        except: pass
        try:
            if resume_pdf and os.path.exists(resume_pdf):
                os.remove(resume_pdf)
        except Exception: pass


async def _run_with_watchdog(session_id: str, req: AutoSubmitRequest, user: User):
    """Hard 8-minute ceiling on any auto-apply session — a stuck Playwright call
    can never leave the user staring at a frozen status again."""
    from loguru import logger
    try:
        await asyncio.wait_for(_run_submit_session(session_id, req, user), timeout=480)
    except asyncio.TimeoutError:
        logger.warning(f"[auto-apply {session_id[:8]}] watchdog fired — session killed at 8 min")
        session = _sessions.get(session_id)
        if session:
            await session["queue"].put({
                "type": "done", "success": False,
                "message": "This session took too long and was stopped — finish manually via 'Open Application'.",
                "apply_url": req.job_url,
            })
    except Exception as e:
        logger.error(f"[auto-apply {session_id[:8]}] wrapper error: {e!r}")
    finally:
        await asyncio.sleep(120)  # let the SSE reader drain, then free the session
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
    # Run Playwright in background under an 8-minute watchdog
    asyncio.create_task(_run_with_watchdog(sid, req, current_user))
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
