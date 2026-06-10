"""
Analytics — event tracking + admin dashboard stats for Mithra AI.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
from typing import Optional
import uuid

from db.database import get_db
from db.models import User, SavedResume, AdaptedResume, JobSearch, SavedJob, AnalyticsEvent
from middleware.auth import get_current_user

router = APIRouter()

ADMIN_EMAILS = [
    "srinathreddy.ksr@gmail.com",
    "sri@mithraai.in",
]


def require_admin(current_user: User):
    if current_user.email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")


# ── Public event tracking (no auth required) ──────────────────────────────────

class EventPayload(BaseModel):
    event: str
    page: Optional[str] = None
    feature: Optional[str] = None
    user_id: Optional[str] = None
    metadata: Optional[dict] = None


@router.post("/event")
async def track_event(
    payload: EventPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    row = AnalyticsEvent(
        id=str(uuid.uuid4()),
        event=payload.event,
        user_id=payload.user_id,
        page=payload.page,
        feature=payload.feature,
        metadata_json=payload.metadata or {},
        created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.commit()
    return {"ok": True}


# ── Admin dashboard overview ───────────────────────────────────────────────────

@router.get("/overview")
async def analytics_overview(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start  = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)

    total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0

    plan_res = await db.execute(select(User.plan, func.count()).group_by(User.plan))
    plan_counts = {row[0].value: row[1] for row in plan_res.fetchall()}

    signups_today = (await db.execute(select(func.count()).select_from(User).where(User.created_at >= today_start))).scalar() or 0
    signups_week  = (await db.execute(select(func.count()).select_from(User).where(User.created_at >= week_start))).scalar()  or 0
    signups_month = (await db.execute(select(func.count()).select_from(User).where(User.created_at >= month_start))).scalar() or 0

    google_users = (await db.execute(
        select(func.count()).select_from(User).where(User.google_id.isnot(None))
    )).scalar() or 0
    email_users = total_users - google_users

    resumes_built    = (await db.execute(select(func.count()).select_from(SavedResume).where(SavedResume.created_at >= month_start))).scalar() or 0
    resumes_adapted  = (await db.execute(select(func.count()).select_from(AdaptedResume).where(AdaptedResume.created_at >= month_start))).scalar() or 0
    job_searches_cnt = (await db.execute(select(func.count()).select_from(JobSearch).where(JobSearch.created_at >= month_start))).scalar() or 0

    ats_res = await db.execute(
        select(func.avg(AdaptedResume.ats_before), func.avg(AdaptedResume.ats_after))
        .where(AdaptedResume.created_at >= month_start, AdaptedResume.ats_before > 0)
    )
    ats_row = ats_res.fetchone()
    avg_ats_before = round(ats_row[0] or 0, 1)
    avg_ats_after  = round(ats_row[1] or 0, 1)

    # Daily signups — last 30 days
    daily_signups = []
    for i in range(30):
        day_start = today_start - timedelta(days=29 - i)
        day_end   = day_start + timedelta(days=1)
        cnt = (await db.execute(
            select(func.count()).select_from(User)
            .where(User.created_at >= day_start, User.created_at < day_end)
        )).scalar() or 0
        daily_signups.append({"date": day_start.strftime("%b %d"), "count": cnt})

    recent_res = await db.execute(
        select(User.id, User.name, User.email, User.plan, User.created_at, User.google_id)
        .order_by(User.created_at.desc())
        .limit(15)
    )
    recent_users = [
        {
            "id":     r[0],
            "name":   r[1] or "",
            "email":  r[2],
            "plan":   r[3].value,
            "joined": r[4].strftime("%b %d, %Y %H:%M") if r[4] else "",
            "method": "Google" if r[5] else "Email",
        }
        for r in recent_res.fetchall()
    ]

    total_events_30d = (await db.execute(
        select(func.count()).select_from(AnalyticsEvent).where(AnalyticsEvent.created_at >= month_start)
    )).scalar() or 0

    page_views_res = await db.execute(
        select(AnalyticsEvent.page, func.count().label("cnt"))
        .where(AnalyticsEvent.event == "page_view", AnalyticsEvent.created_at >= month_start, AnalyticsEvent.page.isnot(None))
        .group_by(AnalyticsEvent.page)
        .order_by(func.count().desc())
        .limit(12)
    )
    top_pages = [{"page": r[0], "views": r[1]} for r in page_views_res.fetchall()]

    feat_res = await db.execute(
        select(AnalyticsEvent.feature, func.count().label("cnt"))
        .where(AnalyticsEvent.event == "feature_use", AnalyticsEvent.created_at >= month_start, AnalyticsEvent.feature.isnot(None))
        .group_by(AnalyticsEvent.feature)
        .order_by(func.count().desc())
    )
    feature_event_counts = {r[0]: r[1] for r in feat_res.fetchall()}

    upgrade_clicks = (await db.execute(
        select(func.count()).select_from(AnalyticsEvent)
        .where(AnalyticsEvent.event == "upgrade_click", AnalyticsEvent.created_at >= month_start)
    )).scalar() or 0

    active_users_30d = (await db.execute(
        select(func.count(AnalyticsEvent.user_id.distinct()))
        .where(AnalyticsEvent.created_at >= month_start, AnalyticsEvent.user_id.isnot(None))
    )).scalar() or 0

    paid = plan_counts.get("pro", 0) + plan_counts.get("elite", 0)
    conv_rate = round(paid / total_users * 100, 1) if total_users > 0 else 0
    upgrade_conv = round(paid / upgrade_clicks * 100, 1) if upgrade_clicks > 0 else 0

    return {
        "summary": {
            "total_users":          total_users,
            "paid_users":           paid,
            "free_users":           plan_counts.get("free", 0),
            "conversion_rate":      f"{conv_rate}%",
            "signups_today":        signups_today,
            "signups_this_week":    signups_week,
            "signups_this_month":   signups_month,
            "active_users_30d":     active_users_30d,
            "total_events_30d":     total_events_30d,
            "upgrade_clicks_30d":   upgrade_clicks,
            "upgrade_conversion":   f"{upgrade_conv}%",
            "google_users":         google_users,
            "email_users":          email_users,
        },
        "plans": {
            "free":  plan_counts.get("free",  0),
            "pro":   plan_counts.get("pro",   0),
            "elite": plan_counts.get("elite", 0),
        },
        "feature_usage_30d": {
            "Resume Builder":  resumes_built,
            "Resume Adapter":  resumes_adapted,
            "Job Finder":      job_searches_cnt,
            **feature_event_counts,
        },
        "ats_improvement": {
            "avg_before": avg_ats_before,
            "avg_after":  avg_ats_after,
            "avg_lift":   round(avg_ats_after - avg_ats_before, 1),
        },
        "daily_signups":   daily_signups,
        "daily_signups_7d": daily_signups[-7:],  # backward compat
        "top_pages_30d":   top_pages,
        "recent_signups":  recent_users,
    }


# ── Admin user list ────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(
    search: str = "",
    plan: str = "",
    method: str = "",
    page: int = 1,
    per_page: int = 25,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)

    base = select(User)
    if search:
        base = base.where(or_(User.name.ilike(f"%{search}%"), User.email.ilike(f"%{search}%")))
    if plan:
        base = base.where(User.plan == plan)
    if method == "Google":
        base = base.where(User.google_id.isnot(None))
    elif method == "Email":
        base = base.where(User.google_id.is_(None))

    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    users = (await db.execute(
        base.order_by(User.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    )).scalars().all()

    result = []
    for u in users:
        resume_cnt = (await db.execute(
            select(func.count()).select_from(SavedResume).where(SavedResume.user_id == u.id)
        )).scalar() or 0
        adapt_cnt = (await db.execute(
            select(func.count()).select_from(AdaptedResume).where(AdaptedResume.user_id == u.id)
        )).scalar() or 0
        search_cnt = (await db.execute(
            select(func.count()).select_from(JobSearch).where(JobSearch.user_id == u.id)
        )).scalar() or 0
        event_cnt = (await db.execute(
            select(func.count()).select_from(AnalyticsEvent).where(AnalyticsEvent.user_id == u.id)
        )).scalar() or 0
        last_event_dt = (await db.execute(
            select(AnalyticsEvent.created_at)
            .where(AnalyticsEvent.user_id == u.id)
            .order_by(AnalyticsEvent.created_at.desc())
            .limit(1)
        )).scalar()

        result.append({
            "id":              u.id,
            "name":            u.name or "",
            "email":           u.email,
            "plan":            u.plan.value,
            "method":          "Google" if u.google_id else "Email",
            "joined":          u.created_at.strftime("%b %d, %Y") if u.created_at else "",
            "last_active":     last_event_dt.strftime("%b %d, %Y") if last_event_dt else (u.created_at.strftime("%b %d, %Y") if u.created_at else ""),
            "resumes_built":   resume_cnt,
            "resumes_adapted": adapt_cnt,
            "job_searches":    search_cnt,
            "total_events":    event_cnt,
        })

    return {"users": result, "total": total, "page": page, "per_page": per_page}


# ── Admin user journey drilldown ───────────────────────────────────────────────

@router.get("/user/{user_id}")
async def get_user_journey(
    user_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_admin(current_user)

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    resumes = [
        {
            "name": r.name,
            "template": r.template or "modern",
            "ats_score": round(r.ats_score or 0, 1),
            "date": r.created_at.strftime("%b %d, %Y") if r.created_at else "",
        }
        for r in (await db.execute(
            select(SavedResume).where(SavedResume.user_id == user_id).order_by(SavedResume.created_at.desc())
        )).scalars().all()
    ]

    adaptations = [
        {
            "company":   a.company or "",
            "role":      a.role or "",
            "ats_before": round(a.ats_before or 0, 1),
            "ats_after":  round(a.ats_after or 0, 1),
            "date": a.created_at.strftime("%b %d, %Y") if a.created_at else "",
        }
        for a in (await db.execute(
            select(AdaptedResume).where(AdaptedResume.user_id == user_id).order_by(AdaptedResume.created_at.desc())
        )).scalars().all()
    ]

    job_searches = [
        {"query": s.query, "location": s.location or "", "date": s.created_at.strftime("%b %d, %Y") if s.created_at else ""}
        for s in (await db.execute(
            select(JobSearch).where(JobSearch.user_id == user_id).order_by(JobSearch.created_at.desc()).limit(20)
        )).scalars().all()
    ]

    saved_jobs = [
        {"title": j.title, "company": j.company, "status": j.status or "bookmarked", "date": j.created_at.strftime("%b %d, %Y") if j.created_at else ""}
        for j in (await db.execute(
            select(SavedJob).where(SavedJob.user_id == user_id).order_by(SavedJob.created_at.desc()).limit(20)
        )).scalars().all()
    ]

    recent_events = [
        {"event": e.event, "page": e.page, "feature": e.feature, "date": e.created_at.strftime("%b %d %H:%M") if e.created_at else ""}
        for e in (await db.execute(
            select(AnalyticsEvent).where(AnalyticsEvent.user_id == user_id).order_by(AnalyticsEvent.created_at.desc()).limit(50)
        )).scalars().all()
    ]

    page_visits = [
        {"page": r[0], "count": r[1]}
        for r in (await db.execute(
            select(AnalyticsEvent.page, func.count().label("cnt"))
            .where(AnalyticsEvent.user_id == user_id, AnalyticsEvent.event == "page_view", AnalyticsEvent.page.isnot(None))
            .group_by(AnalyticsEvent.page)
            .order_by(func.count().desc())
        )).fetchall()
    ]

    feature_usage = [
        {"feature": r[0], "count": r[1]}
        for r in (await db.execute(
            select(AnalyticsEvent.feature, func.count().label("cnt"))
            .where(AnalyticsEvent.user_id == user_id, AnalyticsEvent.event == "feature_use", AnalyticsEvent.feature.isnot(None))
            .group_by(AnalyticsEvent.feature)
            .order_by(func.count().desc())
        )).fetchall()
    ]

    return {
        "user": {
            "id":           user.id,
            "name":         user.name or "",
            "email":        user.email,
            "plan":         user.plan.value,
            "method":       "Google" if user.google_id else "Email",
            "joined":       user.created_at.strftime("%b %d, %Y") if user.created_at else "",
            "referral_used": user.referral_code_used,
        },
        "resumes":       resumes,
        "adaptations":   adaptations,
        "job_searches":  job_searches,
        "saved_jobs":    saved_jobs,
        "page_visits":   page_visits,
        "feature_usage": feature_usage,
        "recent_events": recent_events,
        "summary": {
            "total_resumes":      len(resumes),
            "total_adaptations":  len(adaptations),
            "total_searches":     len(job_searches),
            "total_saved_jobs":   len(saved_jobs),
            "total_events":       len(recent_events),
            "pages_visited":      len(page_visits),
            "features_used":      len(feature_usage),
        },
    }
