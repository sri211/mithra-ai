"""
Analytics — event tracking + admin dashboard stats for Mithra AI.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
from typing import Optional
import uuid

from db.database import get_db
from db.models import User, SavedResume, AdaptedResume, JobSearch, AnalyticsEvent
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
    """Fire-and-forget event tracking. Called from frontend analytics utility."""
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

    # ── User counts ──────────────────────────────────────────────────────────
    total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0

    plan_res = await db.execute(select(User.plan, func.count()).group_by(User.plan))
    plan_counts = {row[0].value: row[1] for row in plan_res.fetchall()}

    signups_today = (await db.execute(select(func.count()).select_from(User).where(User.created_at >= today_start))).scalar() or 0
    signups_week  = (await db.execute(select(func.count()).select_from(User).where(User.created_at >= week_start))).scalar()  or 0
    signups_month = (await db.execute(select(func.count()).select_from(User).where(User.created_at >= month_start))).scalar() or 0

    # ── Auth method breakdown (google vs email, 30d) ─────────────────────────
    google_users = (await db.execute(
        select(func.count()).select_from(User).where(User.google_id.isnot(None))
    )).scalar() or 0
    email_users = total_users - google_users

    # ── Feature usage from DB tables (30d) ───────────────────────────────────
    resumes_built    = (await db.execute(select(func.count()).select_from(SavedResume).where(SavedResume.created_at >= month_start))).scalar() or 0
    resumes_adapted  = (await db.execute(select(func.count()).select_from(AdaptedResume).where(AdaptedResume.created_at >= month_start))).scalar() or 0
    job_searches_cnt = (await db.execute(select(func.count()).select_from(JobSearch).where(JobSearch.created_at >= month_start))).scalar() or 0

    # ── Adapter ATS improvement (avg) ────────────────────────────────────────
    ats_res = await db.execute(
        select(func.avg(AdaptedResume.ats_before), func.avg(AdaptedResume.ats_after))
        .where(AdaptedResume.created_at >= month_start, AdaptedResume.ats_before > 0)
    )
    ats_row = ats_res.fetchone()
    avg_ats_before = round(ats_row[0] or 0, 1)
    avg_ats_after  = round(ats_row[1] or 0, 1)

    # ── Daily signups — last 7 days ───────────────────────────────────────────
    daily_signups = []
    for i in range(7):
        day_start = today_start - timedelta(days=6 - i)
        day_end   = day_start + timedelta(days=1)
        cnt = (await db.execute(
            select(func.count()).select_from(User)
            .where(User.created_at >= day_start, User.created_at < day_end)
        )).scalar() or 0
        daily_signups.append({"date": day_start.strftime("%b %d"), "count": cnt})

    # ── Recent signups ────────────────────────────────────────────────────────
    recent_res = await db.execute(
        select(User.name, User.email, User.plan, User.created_at, User.google_id)
        .order_by(User.created_at.desc())
        .limit(15)
    )
    recent_users = [
        {
            "name":   r[0],
            "email":  r[1],
            "plan":   r[2].value,
            "joined": r[3].strftime("%b %d, %Y %H:%M") if r[3] else "",
            "method": "Google" if r[4] else "Email",
        }
        for r in recent_res.fetchall()
    ]

    # ── Analytics event counts (30d) ──────────────────────────────────────────
    total_events_30d = (await db.execute(
        select(func.count()).select_from(AnalyticsEvent).where(AnalyticsEvent.created_at >= month_start)
    )).scalar() or 0

    # Page views by page (30d)
    page_views_res = await db.execute(
        select(AnalyticsEvent.page, func.count().label("cnt"))
        .where(AnalyticsEvent.event == "page_view", AnalyticsEvent.created_at >= month_start, AnalyticsEvent.page.isnot(None))
        .group_by(AnalyticsEvent.page)
        .order_by(func.count().desc())
        .limit(12)
    )
    top_pages = [{"page": r[0], "views": r[1]} for r in page_views_res.fetchall()]

    # Feature events by feature (30d)
    feat_res = await db.execute(
        select(AnalyticsEvent.feature, func.count().label("cnt"))
        .where(AnalyticsEvent.event == "feature_use", AnalyticsEvent.created_at >= month_start, AnalyticsEvent.feature.isnot(None))
        .group_by(AnalyticsEvent.feature)
        .order_by(func.count().desc())
    )
    feature_event_counts = {r[0]: r[1] for r in feat_res.fetchall()}

    # Upgrade clicks (30d)
    upgrade_clicks = (await db.execute(
        select(func.count()).select_from(AnalyticsEvent)
        .where(AnalyticsEvent.event == "upgrade_click", AnalyticsEvent.created_at >= month_start)
    )).scalar() or 0

    # Unique active users (30d) — users who fired at least one event
    active_users_res = await db.execute(
        select(func.count(AnalyticsEvent.user_id.distinct()))
        .where(AnalyticsEvent.created_at >= month_start, AnalyticsEvent.user_id.isnot(None))
    )
    active_users_30d = active_users_res.scalar() or 0

    # ── Conversion calculations ───────────────────────────────────────────────
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
            "Resume Builder":    resumes_built,
            "Resume Adapter":    resumes_adapted,
            "Job Finder":        job_searches_cnt,
            **feature_event_counts,
        },
        "ats_improvement": {
            "avg_before": avg_ats_before,
            "avg_after":  avg_ats_after,
            "avg_lift":   round(avg_ats_after - avg_ats_before, 1),
        },
        "daily_signups_7d": daily_signups,
        "top_pages_30d":    top_pages,
        "recent_signups":   recent_users,
    }
