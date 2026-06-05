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
        "price_inr": 49900,  # paise (₹499)
        "price_display": "₹499/month",
        "features": [
            "Unlimited resume adaptations",
            "All templates + PDF export",
            "Full network (10 contacts)",
            "Interview prep module",
            "Job tracker",
        ],
    },
    "elite": {
        "name": "Elite",
        "price_inr": 99900,  # paise (₹999)
        "price_display": "₹999/month",
        "features": [
            "Everything in Pro",
            "Auto-apply access",
            "Priority AI (Claude Opus)",
            "1-on-1 Mithra career coaching",
            "LinkedIn profile review",
        ],
    },
}


class CreateOrderRequest(BaseModel):
    plan: str  # "pro" or "elite"


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
            "features": [
                "5 resume adaptations/month",
                "10 job searches/day",
                "3 templates",
                "Basic network (5 contacts)",
            ],
        },
        **PLANS,
    }


@router.post("/create-order")
async def create_order(
    req: CreateOrderRequest,
    current_user: User = Depends(get_current_user),
):
    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    if not RAZORPAY_KEY_ID or not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=503, detail="Payment gateway not configured")

    try:
        import razorpay
        client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        plan_data = PLANS[req.plan]
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

    if req.plan not in PLANS:
        raise HTTPException(status_code=400, detail="Invalid plan")

    # Upgrade user plan
    result = await db.execute(select(User).where(User.id == current_user.id))
    user = result.scalar_one_or_none()
    if user:
        user.plan = PlanEnum(req.plan)
        await db.commit()
        logger.info(f"User {user.email} upgraded to {req.plan}")

    return {"success": True, "plan": req.plan}
