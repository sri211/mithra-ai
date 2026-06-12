import os
import uuid
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger

from db.database import get_db
from db.models import User, PlanEnum
from services.auth_service import (
    hash_password, verify_password,
    create_jwt, create_refresh_token, verify_refresh_token, generate_user_id,
)
from middleware.auth import get_current_user

router = APIRouter()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")


# ── Schemas ──────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleAuthRequest(BaseModel):
    id_token: str


class GoogleAccessTokenRequest(BaseModel):
    access_token: str


class RefreshRequest(BaseModel):
    refresh_token: str


class AuthResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: dict


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "plan": user.plan.value if user.plan else "free",
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=AuthResponse)
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    user = User(
        id=generate_user_id(),
        email=req.email,
        name=req.name,
        hashed_password=hash_password(req.password),
        plan=PlanEnum.free,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info(f"New user registered: {user.email}")

    access = create_jwt(user.id, user.email, user.plan.value)
    refresh = create_refresh_token(user.id)
    return AuthResponse(access_token=access, refresh_token=refresh, user=_user_dict(user))


@router.post("/login", response_model=AuthResponse)
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.email == req.email))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user.hashed_password:
        # Account was created via Google — guide user to the right flow
        raise HTTPException(status_code=401, detail="This account was created with Google. Please use 'Sign in with Google' instead.")
    if not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    access = create_jwt(user.id, user.email, user.plan.value)
    refresh = create_refresh_token(user.id)
    return AuthResponse(access_token=access, refresh_token=refresh, user=_user_dict(user))


@router.post("/google", response_model=AuthResponse)
async def google_auth(req: GoogleAuthRequest, db: AsyncSession = Depends(get_db)):
    # Verify Google id_token
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://oauth2.googleapis.com/tokeninfo?id_token={req.id_token}",
                timeout=10,
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid Google token")

        google_data = resp.json()
        if "error" in google_data:
            raise HTTPException(status_code=401, detail="Invalid Google token")

        # Optionally verify audience
        if GOOGLE_CLIENT_ID and google_data.get("aud") != GOOGLE_CLIENT_ID:
            raise HTTPException(status_code=401, detail="Google token audience mismatch")

        google_id = google_data.get("sub")
        email = google_data.get("email")
        name = google_data.get("name", email.split("@")[0] if email else "User")

        if not google_id or not email:
            raise HTTPException(status_code=400, detail="Could not extract user info from Google token")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Google auth error: {e}")
        raise HTTPException(status_code=500, detail="Google verification failed")

    # Find or create user
    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if not user:
        # Check by email
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.google_id = google_id
            await db.commit()
            await db.refresh(user)
        else:
            user = User(
                id=generate_user_id(),
                email=email,
                name=name,
                google_id=google_id,
                plan=PlanEnum.free,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            logger.info(f"New Google user: {email}")

    access = create_jwt(user.id, user.email, user.plan.value)
    refresh = create_refresh_token(user.id)
    return AuthResponse(access_token=access, refresh_token=refresh, user=_user_dict(user))


@router.post("/google-access-token", response_model=AuthResponse)
async def google_access_token_auth(req: GoogleAccessTokenRequest, db: AsyncSession = Depends(get_db)):
    """OAuth2 access_token flow — fallback for browsers that block One Tap (third-party cookies)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {req.access_token}"},
                timeout=10,
            )
        if resp.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid Google access token")

        info = resp.json()
        google_id = info.get("sub")
        email = info.get("email")
        name = info.get("name", email.split("@")[0] if email else "User")

        if not google_id or not email:
            raise HTTPException(status_code=400, detail="Could not extract user info from Google token")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Google access token auth error: {e}")
        raise HTTPException(status_code=500, detail="Google verification failed")

    result = await db.execute(select(User).where(User.google_id == google_id))
    user = result.scalar_one_or_none()

    if not user:
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()
        if user:
            user.google_id = google_id
            await db.commit()
            await db.refresh(user)
        else:
            user = User(
                id=generate_user_id(),
                email=email,
                name=name,
                google_id=google_id,
                plan=PlanEnum.free,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            logger.info(f"New Google user (access token flow): {email}")

    access = create_jwt(user.id, user.email, user.plan.value)
    refresh = create_refresh_token(user.id)
    return AuthResponse(access_token=access, refresh_token=refresh, user=_user_dict(user))


@router.get("/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return _user_dict(current_user)


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(req: RefreshRequest, db: AsyncSession = Depends(get_db)):
    user_id = verify_refresh_token(req.refresh_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    access = create_jwt(user.id, user.email, user.plan.value)
    refresh = create_refresh_token(user.id)
    return AuthResponse(access_token=access, refresh_token=refresh, user=_user_dict(user))
