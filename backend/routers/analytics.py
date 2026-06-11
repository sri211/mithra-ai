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


# ── Engagement deep-dive ───────────────────────────────────────────────────────

@router.get("/engagement")
async def analytics_engagement(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Activation rates, retention buckets, power users, at-risk, activity feed."""
    require_admin(current_user)

    now = datetime.now(timezone.utc)

    total_users = (await db.execute(select(func.count()).select_from(User))).scalar() or 0

    # ── Unique user sets per feature (for adoption rates) ────────────────────
    def ids(rows):
        return set(r[0] for r in rows if r[0])

    resume_uids = ids((await db.execute(select(SavedResume.user_id.distinct()))).fetchall())
    adapt_uids  = ids((await db.execute(select(AdaptedResume.user_id.distinct()))).fetchall())
    search_uids = ids((await db.execute(select(JobSearch.user_id.distinct()))).fetchall())
    job_uids    = ids((await db.execute(select(SavedJob.user_id.distinct()))).fetchall())

    upgrade_uids = ids((await db.execute(
        select(AnalyticsEvent.user_id.distinct())
        .where(AnalyticsEvent.event == "upgrade_click", AnalyticsEvent.user_id.isnot(None))
    )).fetchall())

    paid_uids = ids((await db.execute(
        select(User.id).where(User.plan.in_(["pro", "elite"]))
    )).fetchall())

    all_activated = resume_uids | adapt_uids | search_uids | job_uids
    activated_count = len(all_activated)

    def rate(n):
        return round(n / total_users * 100, 1) if total_users > 0 else 0.0

    feature_adoption = {
        "Built a Resume":   {"count": len(resume_uids),   "rate": rate(len(resume_uids))},
        "Adapted a Resume": {"count": len(adapt_uids),    "rate": rate(len(adapt_uids))},
        "Searched Jobs":    {"count": len(search_uids),   "rate": rate(len(search_uids))},
        "Saved a Job":      {"count": len(job_uids),      "rate": rate(len(job_uids))},
        "Clicked Upgrade":  {"count": len(upgrade_uids),  "rate": rate(len(upgrade_uids))},
        "Paid for a Plan":  {"count": len(paid_uids),     "rate": rate(len(paid_uids))},
    }

    # ── Retention buckets ────────────────────────────────────────────────────
    retention = {}
    for days in [7, 14, 30, 60, 90]:
        cutoff = now - timedelta(days=days)
        ev_uids = ids((await db.execute(
            select(AnalyticsEvent.user_id.distinct())
            .where(AnalyticsEvent.created_at >= cutoff, AnalyticsEvent.user_id.isnot(None))
        )).fetchall())
        r_uids = ids((await db.execute(select(SavedResume.user_id.distinct()).where(SavedResume.created_at >= cutoff))).fetchall())
        a_uids = ids((await db.execute(select(AdaptedResume.user_id.distinct()).where(AdaptedResume.created_at >= cutoff))).fetchall())
        s_uids = ids((await db.execute(select(JobSearch.user_id.distinct()).where(JobSearch.created_at >= cutoff))).fetchall())
        active_n = len(ev_uids | r_uids | a_uids | s_uids)
        retention[f"{days}d"] = {"count": active_n, "rate": rate(active_n)}

    # ── At-risk users (signed up >7d, never activated) ───────────────────────
    week_ago = now - timedelta(days=7)
    old_uids = ids((await db.execute(select(User.id).where(User.created_at < week_ago))).fetchall())
    at_risk_ids = old_uids - all_activated
    at_risk_examples = []
    if at_risk_ids:
        rows = (await db.execute(
            select(User.id, User.name, User.email, User.created_at, User.plan)
            .where(User.id.in_(list(at_risk_ids)[:30]))
            .order_by(User.created_at.desc())
            .limit(10)
        )).fetchall()
        for r in rows:
            days_since = (now - r[3].replace(tzinfo=timezone.utc)).days if r[3] else 0
            at_risk_examples.append({"id": r[0], "name": r[1] or "", "email": r[2], "days_since_signup": days_since, "plan": r[4].value})

    # ── Power users (top 10 by events) ───────────────────────────────────────
    top_ev_map = {r[0]: r[1] for r in (await db.execute(
        select(AnalyticsEvent.user_id, func.count().label("cnt"))
        .where(AnalyticsEvent.user_id.isnot(None))
        .group_by(AnalyticsEvent.user_id)
        .order_by(func.count().desc())
        .limit(10)
    )).fetchall()}

    power_users = []
    if top_ev_map:
        urows = (await db.execute(
            select(User.id, User.name, User.email, User.plan).where(User.id.in_(list(top_ev_map.keys())))
        )).fetchall()
        umap = {r[0]: r for r in urows}
        for uid, ev_cnt in sorted(top_ev_map.items(), key=lambda x: -x[1]):
            u = umap.get(uid)
            if not u: continue
            r_cnt = (await db.execute(select(func.count()).select_from(SavedResume).where(SavedResume.user_id == uid))).scalar() or 0
            a_cnt = (await db.execute(select(func.count()).select_from(AdaptedResume).where(AdaptedResume.user_id == uid))).scalar() or 0
            power_users.append({"id": uid, "name": u[1] or "", "email": u[2], "plan": u[3].value, "total_events": ev_cnt, "resumes": r_cnt, "adaptations": a_cnt})

    # ── Usage depth ───────────────────────────────────────────────────────────
    total_resumes     = (await db.execute(select(func.count()).select_from(SavedResume))).scalar() or 0
    total_adaptations = (await db.execute(select(func.count()).select_from(AdaptedResume))).scalar() or 0
    total_searches    = (await db.execute(select(func.count()).select_from(JobSearch))).scalar() or 0
    total_saved_jobs  = (await db.execute(select(func.count()).select_from(SavedJob))).scalar() or 0

    # ── Template popularity ───────────────────────────────────────────────────
    templates = {r[0]: r[1] for r in (await db.execute(
        select(SavedResume.template, func.count().label("cnt"))
        .where(SavedResume.template.isnot(None))
        .group_by(SavedResume.template)
        .order_by(func.count().desc())
    )).fetchall()}

    # ── Job status pipeline ───────────────────────────────────────────────────
    job_statuses = {(r[0] or "bookmarked"): r[1] for r in (await db.execute(
        select(SavedJob.status, func.count().label("cnt"))
        .group_by(SavedJob.status)
        .order_by(func.count().desc())
    )).fetchall()}

    # ── Top search queries ────────────────────────────────────────────────────
    top_searches = [{"query": r[0], "count": r[1]} for r in (await db.execute(
        select(JobSearch.query, func.count().label("cnt"))
        .group_by(JobSearch.query)
        .order_by(func.count().desc())
        .limit(10)
    )).fetchall()]

    # ── Recent activity feed (with user names) ────────────────────────────────
    ev_rows = (await db.execute(
        select(AnalyticsEvent.user_id, AnalyticsEvent.event, AnalyticsEvent.page, AnalyticsEvent.feature, AnalyticsEvent.created_at)
        .where(AnalyticsEvent.user_id.isnot(None))
        .order_by(AnalyticsEvent.created_at.desc())
        .limit(25)
    )).fetchall()

    recent_activity = []
    if ev_rows:
        ev_uid_list = list({r[0] for r in ev_rows})
        umap2 = {r[0]: r for r in (await db.execute(
            select(User.id, User.name, User.email).where(User.id.in_(ev_uid_list))
        )).fetchall()}
        for r in ev_rows:
            u = umap2.get(r[0])
            recent_activity.append({
                "user_id":    r[0],
                "user_name":  u[1] if u else "",
                "user_email": u[2] if u else "",
                "event":   r[1],
                "page":    r[2],
                "feature": r[3],
                "date":    r[4].strftime("%b %d %H:%M") if r[4] else "",
            })

    all_user_ids = ids((await db.execute(select(User.id))).fetchall())
    never_activated_count = max(len(all_user_ids) - activated_count, 0)

    return {
        "activation": {
            "total_users":       total_users,
            "activated_count":   activated_count,
            "activation_rate":   rate(activated_count),
            "never_activated":   never_activated_count,
            "feature_adoption":  feature_adoption,
        },
        "retention":   retention,
        "at_risk": {
            "count":    len(at_risk_ids),
            "rate":     rate(len(at_risk_ids)),
            "examples": at_risk_examples,
        },
        "power_users":  power_users,
        "usage_depth": {
            "total_resumes":             total_resumes,
            "total_adaptations":         total_adaptations,
            "total_searches":            total_searches,
            "total_saved_jobs":          total_saved_jobs,
            "avg_resumes_per_user":      round(total_resumes / total_users, 2) if total_users else 0,
            "avg_adaptations_per_user":  round(total_adaptations / total_users, 2) if total_users else 0,
            "avg_searches_per_user":     round(total_searches / total_users, 2) if total_users else 0,
        },
        "templates":       templates,
        "job_statuses":    job_statuses,
        "top_searches":    top_searches,
        "recent_activity": recent_activity,
    }


# ── Revenue & cost analysis ────────────────────────────────────────────────────

@router.get("/revenue")
async def analytics_revenue(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """MRR/ARR snapshot, cost estimates, monthly trend, cohort new revenue."""
    require_admin(current_user)
    from calendar import monthrange

    now = datetime.now(timezone.utc)

    plan_res = await db.execute(select(User.plan, func.count()).group_by(User.plan))
    plan_counts = {r[0].value: r[1] for r in plan_res.fetchall()}

    pro_n   = plan_counts.get("pro", 0)
    elite_n = plan_counts.get("elite", 0)
    paid_n  = pro_n + elite_n
    total_n = sum(plan_counts.values())

    mrr = (pro_n * 198) + (elite_n * 498)
    arr = mrr * 12

    # Feature usage for cost estimation (all-time)
    total_resumes     = (await db.execute(select(func.count()).select_from(SavedResume))).scalar() or 0
    total_adaptations = (await db.execute(select(func.count()).select_from(AdaptedResume))).scalar() or 0
    total_searches    = (await db.execute(select(func.count()).select_from(JobSearch))).scalar() or 0

    # ₹ cost estimates per operation (Claude API + infra estimate)
    COST_RESUME  = 2.5   # ₹ per resume build
    COST_ADAPT   = 8.0   # ₹ per adaptation (longer LLM context)
    COST_SEARCH  = 0.5   # ₹ per job search (API + compute)

    resume_cost = total_resumes * COST_RESUME
    adapt_cost  = total_adaptations * COST_ADAPT
    search_cost = total_searches * COST_SEARCH
    total_cost  = resume_cost + adapt_cost + search_cost
    net_margin  = mrr - total_cost
    margin_rate = round(net_margin / mrr * 100, 1) if mrr > 0 else 0.0

    # ── Cohort windows: new paid users in period ──────────────────────────────
    cohorts = {}
    for days in [7, 30, 60, 90]:
        cutoff = now - timedelta(days=days)
        new_pro = (await db.execute(
            select(func.count()).select_from(User).where(User.plan == "pro", User.created_at >= cutoff)
        )).scalar() or 0
        new_elite = (await db.execute(
            select(func.count()).select_from(User).where(User.plan == "elite", User.created_at >= cutoff)
        )).scalar() or 0
        cohorts[f"{days}d"] = {
            "new_pro": new_pro, "new_elite": new_elite,
            "new_paid": new_pro + new_elite,
            "new_revenue": (new_pro * 198) + (new_elite * 498),
        }

    # ── Monthly signup revenue trend (last 6 months) ──────────────────────────
    monthly_trend = []
    for i in range(5, -1, -1):
        raw_month = now.month - i
        if raw_month <= 0:
            month_num = raw_month + 12
            year = now.year - 1
        else:
            month_num = raw_month
            year = now.year
        _, last_day = monthrange(year, month_num)
        m_start = datetime(year, month_num, 1, tzinfo=timezone.utc)
        m_end   = datetime(year, month_num, last_day, 23, 59, 59, tzinfo=timezone.utc)
        pr = (await db.execute(
            select(User.plan, func.count()).select_from(User)
            .where(User.created_at >= m_start, User.created_at <= m_end, User.plan != "free")
            .group_by(User.plan)
        )).fetchall()
        mp = {r[0].value: r[1] for r in pr}
        monthly_trend.append({
            "month":   m_start.strftime("%b %Y"),
            "revenue": (mp.get("pro", 0) * 198) + (mp.get("elite", 0) * 498),
            "new_pro":   mp.get("pro", 0),
            "new_elite": mp.get("elite", 0),
        })

    return {
        "snapshot": {
            "mrr":        mrr,
            "arr":        arr,
            "paid_users": paid_n,
            "total_users": total_n,
            "arpu_paid":  round(mrr / paid_n, 2) if paid_n else 0,
            "plan_breakdown": {
                "pro":   {"users": pro_n,   "monthly": pro_n * 198,   "share_pct": round(pro_n * 198 / mrr * 100, 1) if mrr else 0},
                "elite": {"users": elite_n, "monthly": elite_n * 498, "share_pct": round(elite_n * 498 / mrr * 100, 1) if mrr else 0},
            },
        },
        "cost_estimates": {
            "note": "Estimated LLM/API costs per operation (all-time cumulative)",
            "resume_builds": {"count": total_resumes,     "unit_cost_inr": COST_RESUME, "total": round(resume_cost)},
            "adaptations":   {"count": total_adaptations, "unit_cost_inr": COST_ADAPT,  "total": round(adapt_cost)},
            "job_searches":  {"count": total_searches,    "unit_cost_inr": COST_SEARCH, "total": round(search_cost)},
            "total_estimated": round(total_cost),
            "net_margin":      round(net_margin),
            "margin_rate":     margin_rate,
        },
        "cohorts":       cohorts,
        "monthly_trend": monthly_trend,
    }
