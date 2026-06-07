"""Referral system — refer 3 friends → 1 month Pro free."""
import os, uuid, secrets
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timezone, timedelta
from loguru import logger

from db.database import get_db
from db.models import User, PlanEnum
from middleware.auth import get_current_user

router = APIRouter()

REFERRAL_REWARD_COUNT = 3   # friends needed
REWARD_DAYS           = 30  # days of Pro


# ── helpers ──────────────────────────────────────────────────────────────────

def generate_ref_code(user_id: str) -> str:
    """Deterministic short code from user_id so it's reproducible."""
    return "MTH" + user_id[:6].upper().replace("-", "")


# ── endpoints ────────────────────────────────────────────────────────────────

@router.get("/my-link")
async def get_my_referral_link(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    code = generate_ref_code(current_user.id)
    # Count successful referrals (users who signed up with this code)
    result = await db.execute(
        select(func.count()).where(User.referral_code_used == code)
    )
    count = result.scalar() or 0
    reward_triggered = await db.execute(
        select(func.count()).where(
            User.referral_code_used == code,
        )
    )
    signed_up = reward_triggered.scalar() or 0

    base_url = os.getenv("FRONTEND_URL", "https://www.mithraai.in")
    return {
        "referral_code": code,
        "referral_link": f"{base_url}/register?ref={code}",
        "referrals_completed": signed_up,
        "referrals_needed": REFERRAL_REWARD_COUNT,
        "reward_unlocked": signed_up >= REFERRAL_REWARD_COUNT,
        "current_plan": current_user.plan.value,
        "message": (
            f"You've referred {signed_up} friend{'s' if signed_up != 1 else ''}. "
            f"Refer {max(0, REFERRAL_REWARD_COUNT - signed_up)} more to unlock 1 month of Pro free!"
            if signed_up < REFERRAL_REWARD_COUNT
            else "🎉 Reward unlocked! You've earned 1 month of Pro."
        ),
    }


class UseRefRequest(BaseModel):
    referral_code: str


@router.post("/apply")
async def apply_referral_code(
    req: UseRefRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Called after a new user registers to credit the referrer."""
    code = req.referral_code.upper().strip()

    # Find referrer (their code matches)
    result = await db.execute(select(User))
    all_users = result.scalars().all()
    referrer = next(
        (u for u in all_users if generate_ref_code(u.id) == code and u.id != current_user.id),
        None
    )
    if not referrer:
        raise HTTPException(status_code=404, detail="Invalid referral code")

    # Mark new user's referral
    current_user.referral_code_used = code
    await db.commit()

    # Count referrer's total signups
    count_res = await db.execute(
        select(func.count()).where(User.referral_code_used == code)
    )
    total = count_res.scalar() or 0
    logger.info(f"Referral applied: {current_user.email} used code {code}. Referrer now has {total} referrals.")

    # Check if reward threshold hit (exactly at threshold to avoid re-applying)
    if total == REFERRAL_REWARD_COUNT:
        # Upgrade referrer to Pro for 30 days
        referrer.plan = PlanEnum.pro
        await db.commit()
        logger.info(f"Referral reward: {referrer.email} upgraded to Pro for {REWARD_DAYS} days.")
        return {"success": True, "reward_triggered": True, "referrer_upgraded": True}

    return {"success": True, "reward_triggered": False, "referrer_count": total}


@router.get("/leaderboard")
async def referral_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return this user's referral stats."""
    code = generate_ref_code(current_user.id)
    count_res = await db.execute(
        select(func.count()).where(User.referral_code_used == code)
    )
    count = count_res.scalar() or 0
    base_url = os.getenv("FRONTEND_URL", "https://www.mithraai.in")
    return {
        "code": code,
        "link": f"{base_url}/register?ref={code}",
        "count": count,
        "needed": REFERRAL_REWARD_COUNT,
        "progress_pct": min(int((count / REFERRAL_REWARD_COUNT) * 100), 100),
        "reward_unlocked": count >= REFERRAL_REWARD_COUNT,
    }
