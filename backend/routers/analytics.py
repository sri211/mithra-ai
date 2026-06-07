"""
Analytics endpoint — admin stats for the Mithra AI dashboard.
Returns user counts, plan distribution, signups over time, feature usage.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text
from datetime import datetime, timezone, timedelta
from loguru import logger

from db.database import get_db
from db.models import User, PlanEnum, SavedResume, AdaptedResume, JobSearch
from middleware.auth import get_current_user

router = APIRouter()

ADMIN_EMAILS = [
    "srinathreddy.ksr@gmail.com",
    "sri@mithraai.in",
]


def require_admin(current_user: User):
    if current_user.email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")


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

    # Total users
    total_res = await db.execute(select(func.count()).select_from(User))
    total_users = total_res.scalar() or 0

    # Users by plan
    plan_res = await db.execute(
        select(User.plan, func.count()).group_by(User.plan)
    )
    plan_counts = {row[0].value: row[1] for row in plan_res.fetchall()}

    # Signups today / this week / this month
    today_res  = await db.execute(select(func.count()).select_from(User).where(User.created_at >= today_start))
    week_res   = await db.execute(select(func.count()).select_from(User).where(User.created_at >= week_start))
    month_res  = await db.execute(select(func.count()).select_from(User).where(User.created_at >= month_start))
    signups_today = today_res.scalar() or 0
    signups_week  = week_res.scalar()  or 0
    signups_month = month_res.scalar() or 0

    # Feature usage (30 days)
    resume_saves = await db.execute(select(func.count()).select_from(SavedResume).where(SavedResume.created_at >= month_start))
    adaptations  = await db.execute(select(func.count()).select_from(AdaptedResume).where(AdaptedResume.created_at >= month_start))
    job_searches = await db.execute(select(func.count()).select_from(JobSearch).where(JobSearch.created_at >= month_start))

    # Recent signups (last 7 days per day)
    daily_signups = []
    for i in range(7):
        day_start = today_start - timedelta(days=6 - i)
        day_end   = day_start + timedelta(days=1)
        day_res   = await db.execute(
            select(func.count()).select_from(User).where(
                User.created_at >= day_start,
                User.created_at < day_end,
            )
        )
        daily_signups.append({
            "date": day_start.strftime("%b %d"),
            "count": day_res.scalar() or 0,
        })

    # Last 10 signups
    recent_res = await db.execute(
        select(User.name, User.email, User.plan, User.created_at)
        .order_by(User.created_at.desc())
        .limit(10)
    )
    recent_users = [
        {"name": r[0], "email": r[1], "plan": r[2].value, "joined": r[3].strftime("%Y-%m-%d %H:%M") if r[3] else ""}
        for r in recent_res.fetchall()
    ]

    # Conversion rate (paid / total)
    paid = plan_counts.get("pro", 0) + plan_counts.get("elite", 0)
    conversion_rate = round(paid / total_users * 100, 1) if total_users > 0 else 0

    return {
        "summary": {
            "total_users":      total_users,
            "paid_users":       paid,
            "conversion_rate":  f"{conversion_rate}%",
            "signups_today":    signups_today,
            "signups_this_week":signups_week,
            "signups_this_month": signups_month,
        },
        "plans": {
            "free":  plan_counts.get("free",  0),
            "pro":   plan_counts.get("pro",   0),
            "elite": plan_counts.get("elite", 0),
        },
        "feature_usage_30d": {
            "resumes_built":     resume_saves.scalar() or 0,
            "resumes_adapted":   adaptations.scalar()  or 0,
            "job_searches":      job_searches.scalar() or 0,
        },
        "daily_signups_7d": daily_signups,
        "recent_signups":   recent_users,
    }
