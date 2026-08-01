"""
Mithra chat — FREE knowledge-base assistant. No Claude, no per-message API cost.
Answers from the curated Indian-jobs + Mithra KB, injects live user data, and
learns from 👍/👎 feedback. Streaming kept for frontend compatibility.
"""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import ChatKBEntry, ChatFeedback, JobApplication, SavedResume, User
from middleware.auth import get_optional_user, get_current_user
from services.kb_chatbot import answer as kb_answer, rebuild_from_db

router = APIRouter()

ADMIN_EMAILS = {"srinathreddy.ksr@gmail.com", "sri@mithraai.in"}


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    page_context: str = "dashboard"
    history: list[ChatMessage] = []
    user_profile: Optional[dict] = None
    resume_loaded: bool = False


async def _load_dynamic(db: AsyncSession):
    """Merge admin/learned KB entries from the DB into the in-memory index."""
    try:
        rows = (await db.execute(select(ChatKBEntry).where(ChatKBEntry.enabled == 1))).scalars().all()
        dynamic = [{
            "id": r.id, "triggers": r.triggers or [], "answer": r.answer,
            "category": r.category or "learned", "tags": r.tags or [], "boost": r.boost or 0.0,
        } for r in rows]
        rebuild_from_db(dynamic)
    except Exception:
        pass


async def _build_ctx(req: ChatRequest, user: Optional[User], db: AsyncSession) -> dict:
    ctx = {"name": (req.user_profile or {}).get("name") if req.user_profile else None,
           "resume_loaded": req.resume_loaded}
    if user:
        ctx["name"] = ctx["name"] or user.name
        ctx["plan"] = user.plan.value if hasattr(user.plan, "value") else str(user.plan or "free")
        try:
            from services.credits import ensure_period
            await ensure_period(user, db)
            ctx["credits"] = user.credits_balance
        except Exception:
            pass
        try:
            apps = (await db.execute(
                select(func.count()).select_from(JobApplication).where(JobApplication.user_id == user.id)
            )).scalar()
            ctx["apps"] = apps or 0
            res = (await db.execute(
                select(func.count()).select_from(SavedResume).where(SavedResume.user_id == user.id)
            )).scalar()
            ctx["resume_loaded"] = req.resume_loaded or (res or 0) > 0
        except Exception:
            pass
    return ctx


@router.post("/message")
async def chat_message(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_optional_user),
):
    """Non-streaming: returns {answer, entry_id, confidence, matched}. FREE — no credits."""
    await _load_dynamic(db)
    ctx = await _build_ctx(req, user, db)
    result = kb_answer(req.message, ctx)
    return result


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_optional_user),
):
    """Streaming for the existing frontend. Streams the KB answer word-by-word so it
    feels alive, then emits a metadata line the UI uses for the 👍/👎 buttons."""
    await _load_dynamic(db)
    ctx = await _build_ctx(req, user, db)
    result = kb_answer(req.message, ctx)
    text = result["answer"]

    async def generate():
        import asyncio
        # stream in small chunks for a natural typing feel
        words = text.split(" ")
        buf = ""
        for i, w in enumerate(words):
            buf += w + " "
            if i % 3 == 0 or i == len(words) - 1:
                yield f"data: {json.dumps({'text': buf})}\n\n"
                buf = ""
                await asyncio.sleep(0.015)
        # metadata for feedback UI (entry_id + confidence) and any orchestrator action
        meta = {"entry_id": result["entry_id"], "confidence": result["confidence"],
                "matched": result["matched"]}
        if result.get("action"):
            meta["action"] = result["action"]
        yield f"data: {json.dumps({'meta': meta})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


class FeedbackRequest(BaseModel):
    query: str
    answer: str = ""
    entry_id: str = ""
    helpful: bool
    confidence: float = 0.0


@router.post("/feedback")
async def chat_feedback(
    req: FeedbackRequest,
    db: AsyncSession = Depends(get_db),
    user=Depends(get_optional_user),
):
    """Record 👍/👎. Upvotes boost that KB entry so it surfaces more; downvotes are
    logged as gaps for the admin to fix. This is the learning loop."""
    fb = ChatFeedback(
        id=str(uuid.uuid4()),
        user_id=user.id if user else None,
        query=req.query[:1000],
        entry_id=req.entry_id or None,
        answer=req.answer[:2000],
        helpful=1 if req.helpful else 0,
        confidence=req.confidence,
    )
    db.add(fb)
    # Apply a small live boost/penalty to matched dynamic entries
    if req.entry_id and req.entry_id not in ("live", "greeting", "unknown", "empty", "gemini"):
        row = (await db.execute(select(ChatKBEntry).where(ChatKBEntry.id == req.entry_id))).scalar_one_or_none()
        if row:
            row.boost = (row.boost or 0.0) + (0.3 if req.helpful else -0.3)
    await db.commit()
    return {"ok": True}


# ── Admin: manage the KB + review gaps ────────────────────────────────────────

def _is_admin(user: Optional[User]) -> bool:
    return bool(user and user.email in ADMIN_EMAILS)


class KBEntryRequest(BaseModel):
    id: Optional[str] = None
    triggers: list[str]
    answer: str
    category: str = "learned"
    tags: list[str] = []


@router.get("/admin/gaps")
async def admin_gaps(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """Unanswered / downvoted questions — the queue of things to teach the bot."""
    if not _is_admin(user):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin only")
    rows = (await db.execute(
        select(ChatFeedback).where(
            (ChatFeedback.helpful == 0) | (ChatFeedback.entry_id == "unknown"),
            ChatFeedback.resolved == 0,
        ).order_by(desc(ChatFeedback.created_at)).limit(100)
    )).scalars().all()
    return {"gaps": [{"id": r.id, "query": r.query, "entry_id": r.entry_id,
                      "helpful": r.helpful, "created_at": r.created_at.isoformat()} for r in rows]}


@router.post("/admin/kb")
async def admin_upsert_kb(req: KBEntryRequest, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    """Teach the bot a new answer (or edit one). Takes effect immediately."""
    if not _is_admin(user):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin only")
    entry_id = req.id or f"learned_{uuid.uuid4().hex[:8]}"
    existing = (await db.execute(select(ChatKBEntry).where(ChatKBEntry.id == entry_id))).scalar_one_or_none()
    if existing:
        existing.triggers = req.triggers
        existing.answer = req.answer
        existing.category = req.category
        existing.tags = req.tags
    else:
        db.add(ChatKBEntry(id=entry_id, triggers=req.triggers, answer=req.answer,
                           category=req.category, tags=req.tags, boost=1.0, enabled=1))
    await db.commit()
    await _load_dynamic(db)
    return {"ok": True, "id": entry_id}


@router.post("/admin/gaps/{fb_id}/resolve")
async def admin_resolve_gap(fb_id: str, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    if not _is_admin(user):
        from fastapi import HTTPException
        raise HTTPException(403, "Admin only")
    row = (await db.execute(select(ChatFeedback).where(ChatFeedback.id == fb_id))).scalar_one_or_none()
    if row:
        row.resolved = 1
        await db.commit()
    return {"ok": True}
