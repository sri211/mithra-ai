from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from loguru import logger
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

from routers import chat, resume, jobs, apply, network, interview, tracker, auto_apply
from routers import auth as auth_router
from routers import user_data, payments, referral, analytics, extension
from db.database import init_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Mithra AI Backend...")
    await init_db()
    rapidapi_key = os.getenv("RAPIDAPI_KEY", "")
    if rapidapi_key:
        logger.info("JSearch API (RapidAPI) is ENABLED — real job listings active")
    else:
        logger.warning("JSearch API not configured (RAPIDAPI_KEY missing) — using Claude-generated jobs")
    yield
    logger.info("Shutting down Mithra AI Backend...")

app = FastAPI(
    title="Mithra AI API",
    description="AI-powered job search platform backend",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        os.getenv("FRONTEND_URL", "http://localhost:3000"),
        "http://localhost:3001",
        "https://www.mithraai.in",
        "https://mithraai.in",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Core feature routers ──────────────────────────────────────────────────────
app.include_router(chat.router, prefix="/api/chat", tags=["Mithra Chat"])
app.include_router(resume.router, prefix="/api/resume", tags=["Resume"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(apply.router, prefix="/api/apply", tags=["Apply"])
app.include_router(network.router, prefix="/api/network", tags=["Network"])
app.include_router(interview.router, prefix="/api/interview", tags=["Interview"])
app.include_router(tracker.router, prefix="/api/tracker", tags=["Tracker"])
app.include_router(auto_apply.router, prefix="/api/auto-apply", tags=["Auto Apply"])

# ── Auth / User / Payments routers ────────────────────────────────────────────
app.include_router(auth_router.router, prefix="/api/auth", tags=["Auth"])
app.include_router(user_data.router, prefix="/api/user", tags=["User Data"])
app.include_router(payments.router, prefix="/api/payments", tags=["Payments"])
app.include_router(referral.router, prefix="/api/referral", tags=["Referral"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])
app.include_router(extension.router, prefix="/api/extension", tags=["Browser Extension"])

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "Mithra AI"}
