import os
import hmac
import hashlib
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger

from db.database import get_db
from db.models import User, PlanEnum
from middleware.auth import get_current_user

router = APIRouter()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

PLANS = {
    "pro": {
        "name": "Pro",
        "price_inr": 19800,  # paise (₹198)
        "price_display": "₹198/month",
        "credits": 300,
        "features": [
            "300 credits every month",
            "~12 resume adaptations or mix freely",
            "All templates + PDF export",
            "Full network (10 contacts)",
            "Interview prep + job tracker",
        ],
    },
    "elite": {
        "name": "Elite",
        "price_inr": 49800,  # paise (₹498)
        "price_display": "₹498/month",
        "credits": 1000,
        "features": [
            "1,000 credits every month",
            "Everything in Pro",
            "Auto-apply access",
            "Priority support",
            "LinkedIn profile review",
        ],
    },
}

# One-time credit top-up packs — usable on any plan, no expiry within the month
TOPUPS = {
    "topup_99":  {"name": "120 Credits",  "price_inr": 9900,  "credits": 120},
    "topup_199": {"name": "280 Credits", "price_inr": 19900, "credits": 280},
}


class CreateOrderRequest(BaseModel):
    plan: str  # "pro" | "elite" | "topup_99" | "topup_199"


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    plan: str


@router.get("/plans")
async def get_plans():
    return {
        "free": {
            "name": "Free",
            "price_display": "₹0/month",
            "credits": 30,
            "features": [
                "30 credits every month",
                "Resume score always free",
                "3 templates",
                "Basic network (5 contacts)",
            ],
        },
        **PLANS,
        "topups": TOPUPS,
    }


@router.post("/create-order")
async def create_order(
    req: CreateOrderRequest,
    current_user: User = Depends(get_current_user),
):
    if req.plan not in PLANS and req.plan not in TOPUPS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")

    try:
        import razorpay
        client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        plan_data = PLANS.get(req.plan) or TOPUPS[req.plan]
        order = client.order.create({
            "amount": plan_data["price_inr"],
            "currency": "INR",
            "receipt": f"mithra_{current_user.id[:8]}_{req.plan}",
            "notes": {
                "user_id": current_user.id,
                "plan": req.plan,
            },
        })
        return {
            "order_id": order["id"],
            "amount": order["amount"],
            "currency": order["currency"],
            "key_id": RAZORPAY_KEY_ID,
            "plan": req.plan,
            "user_name": current_user.name,
            "user_email": current_user.email,
        }
    except Exception as e:
        logger.error(f"Razorpay order creation failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to create payment order")


@router.post("/verify")
async def verify_payment(
    req: VerifyPaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")

    # Verify Razorpay signature
    expected_signature = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        f"{req.razorpay_order_id}|{req.razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_signature, req.razorpay_signature):
        raise HTTPException(status_code=400, detail="Invalid payment signature")

    if req.plan not in PLANS and req.plan not in TOPUPS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    from services.credits import grant, PLAN_ALLOWANCE

    if req.plan in TOPUPS:
        # Credit top-up pack — add credits, plan unchanged
        pack = TOPUPS[req.plan]
        balance = await grant(user, db, pack["credits"], req.plan)
        logger.info(f"User {user.email} bought {req.plan}: +{pack['credits']} credits (balance {balance})")
        return {"success": True, "plan": req.plan, "credits_added": pack["credits"], "balance": balance}

    # Plan upgrade — set plan and immediately grant this month's allowance
    user.plan = PlanEnum(req.plan)
    allowance = PLAN_ALLOWANCE.get(req.plan, 30)
    user.credits_balance = allowance
    from datetime import datetime, timezone
    user.credits_period_start = datetime.now(timezone.utc)
    await db.commit()
    logger.info(f"User {user.email} upgraded to {req.plan} with {allowance} credits")
    return {"success": True, "plan": req.plan, "credits_added": allowance}
