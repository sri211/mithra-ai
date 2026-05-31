from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from loguru import logger
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

from routers import chat, resume, jobs, apply, network, interview, tracker
from db.database import init_db

async def ensure_playwright_browsers():
    """Install Playwright Chromium browser if not already installed."""
    try:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium", "--with-deps"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            logger.info("Playwright Chromium ready")
        else:
            logger.warning(f"Playwright install: {result.stderr[:200]}")
    except Exception as e:
        logger.warning(f"Could not install Playwright browsers: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Mithra AI Backend...")
    await init_db()
    asyncio.create_task(ensure_playwright_browsers())
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
    allow_origins=[os.getenv("FRONTEND_URL", "http://localhost:3000"), "http://localhost:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api/chat", tags=["Mithra Chat"])
app.include_router(resume.router, prefix="/api/resume", tags=["Resume"])
app.include_router(jobs.router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(apply.router, prefix="/api/apply", tags=["Apply"])
app.include_router(network.router, prefix="/api/network", tags=["Network"])
app.include_router(interview.router, prefix="/api/interview", tags=["Interview"])
app.include_router(tracker.router, prefix="/api/tracker", tags=["Tracker"])

@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "Mithra AI"}
