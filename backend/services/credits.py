"""
Credit system — every AI action costs credits; plans grant a monthly allowance.

Economics (verified against actual API costs post model-routing):
  Action costs are priced so the worst-case user (all credits on the most
  expensive action) still leaves ~45% margin on Pro and ~28% on Elite.

  Free  ₹0    →   30 cr/month
  Pro   ₹198  →  300 cr/month   (worst case: 12 adapts × ₹9 API = ₹108 cost)
  Elite ₹498  → 1000 cr/month   (worst case: 40 adapts × ₹9 API = ₹360 cost)

No rollover — balance resets to the plan allowance every 30 days (lazy, on read).
Top-up packs (pure ~70% margin): ₹99 → 120 cr, ₹199 → 280 cr.
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import User, CreditLedger

PLAN_ALLOWANCE = {"free": 30, "pro": 300, "elite": 1000}

CREDIT_COSTS = {
    "resume_adapt": 25,       # Sonnet adaptation + cached JD parse + company intel
    "resume_build": 15,       # AI extract / full rebuild (Haiku)
    "interview_session": 10,  # 7-question set (usually cache-hit = pure margin)
    "interview_feedback": 3,  # per answer scored
    "cover_letter": 5,
    "auto_apply": 8,          # Playwright session (compute + assistant loop)
    "pdf_download": 2,
    "job_search": 2,          # usually cache-hit = pure margin
    "chat_message": 1,
    "company_intel": 2,   # only on a cache MISS; cached lookups are free
    "resume_score": 0,        # rule-based — always free
}

TOPUP_PACKS = {
    "topup_99":  {"amount_inr": 99,  "credits": 120},
    "topup_199": {"amount_inr": 199, "credits": 280},
}

PERIOD_DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


async def ensure_period(user: User, db: AsyncSession) -> None:
    """Initialize or refresh the user's monthly allowance. Lazy — call before any read/charge."""
    plan = user.plan.value if hasattr(user.plan, "value") else str(user.plan or "free")
    allowance = PLAN_ALLOWANCE.get(plan, 30)
    now = _now()

    needs_reset = (
        user.credits_balance is None
        or user.credits_period_start is None
        or _naive(user.credits_period_start) + timedelta(days=PERIOD_DAYS) <= _naive(now)
    )
    if needs_reset:
        user.credits_balance = allowance
        user.credits_period_start = now
        db.add(CreditLedger(
            id=str(uuid.uuid4()), user_id=user.id, delta=allowance,
            reason="monthly_reset", balance_after=allowance,
        ))
        await db.commit()


async def charge(user: User, db: AsyncSession, action: str) -> int:
    """Deduct the action's cost. Raises 402 with top-up info when balance is short.
    Returns the new balance."""
    cost = CREDIT_COSTS.get(action, 0)
    if cost <= 0:
        return user.credits_balance or 0

    await ensure_period(user, db)

    if (user.credits_balance or 0) < cost:
        raise HTTPException(status_code=402, detail={
            "error": "insufficient_credits",
            "action": action,
            "cost": cost,
            "balance": user.credits_balance or 0,
            "topups": [
                {"id": k, "price_inr": v["amount_inr"], "credits": v["credits"]}
                for k, v in TOPUP_PACKS.items()
            ],
        })

    user.credits_balance -= cost
    db.add(CreditLedger(
        id=str(uuid.uuid4()), user_id=user.id, delta=-cost,
        reason=action, balance_after=user.credits_balance,
    ))
    await db.commit()
    return user.credits_balance


def charge_action(action: str):
    """Route dependency: charges logged-in users for the action; guests pass
    through uncharged (they're gated by frontend free limits instead).
    Raises 402 with top-up options when the balance is insufficient."""
    from middleware.auth import get_optional_user

    async def _dep(db: AsyncSession = Depends(get_db), user=Depends(get_optional_user)):
        if user is not None:
            await charge(user, db, action)
        return user
    return _dep


async def grant(user: User, db: AsyncSession, amount: int, reason: str) -> int:
    """Add credits (top-up purchase, referral bonus, admin adjustment)."""
    await ensure_period(user, db)
    user.credits_balance = (user.credits_balance or 0) + amount
    db.add(CreditLedger(
        id=str(uuid.uuid4()), user_id=user.id, delta=amount,
        reason=reason, balance_after=user.credits_balance,
    ))
    await db.commit()
    return user.credits_balance
