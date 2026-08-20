"""
Provider-agnostic LLM service with automatic failover.

PRIMARY provider is chosen by env (LLM_PROVIDER, e.g. gemini) and serves every
call. If the primary fails with a rate-limit / quota / overload / transient error,
the call is transparently retried on the ANTHROPIC (Claude) FALLBACK, and the
admin is notified (throttled email + a persistent analytics_events row + a log
line). Users never see the failure.

Env:
  LLM_PROVIDER   = anthropic (default) | groq | gemini | deepseek | openai | openrouter
  LLM_API_KEY    = key for the chosen provider
  LLM_BASE_URL   = override base URL (optional)
  LLM_FAST_MODEL / LLM_SMART_MODEL = override tier models (optional)
  ANTHROPIC_API_KEY = enables the Claude fallback (used automatically when the
                      primary isn't anthropic and this key is present)
  LLM_DISABLE_FALLBACK = "1" to turn the fallback off
"""
import os
import time
import asyncio
from typing import AsyncIterator, Optional
from loguru import logger

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()

_PRESETS = {
    "groq":       {"base_url": "https://api.groq.com/openai/v1",
                   "fast": "llama-3.1-8b-instant", "smart": "llama-3.3-70b-versatile"},
    "gemini":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                   "fast": "gemini-flash-lite-latest", "smart": "gemini-3.5-flash"},
    "deepseek":   {"base_url": "https://api.deepseek.com",
                   "fast": "deepseek-chat", "smart": "deepseek-chat"},
    "openai":     {"base_url": "https://api.openai.com/v1",
                   "fast": "gpt-4o-mini", "smart": "gpt-4o"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",
                   "fast": "meta-llama/llama-3.3-70b-instruct",
                   "smart": "meta-llama/llama-3.3-70b-instruct"},
}

_ANTHROPIC_FAST = os.getenv("CLAUDE_FAST_MODEL", "claude-haiku-4-5-20251001")
_ANTHROPIC_SMART = os.getenv("CLAUDE_SMART_MODEL", "claude-sonnet-5")
_LEGACY = os.getenv("CLAUDE_MODEL", "")
if _LEGACY and "opus" not in _LEGACY:
    _ANTHROPIC_FAST = _LEGACY

_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))


class _Backend:
    """One place to call for completions/streams — either an OpenAI-compatible
    provider or Anthropic."""
    def __init__(self, kind: str, client, fast_model: str, smart_model: str, name: str):
        self.kind = kind          # "oai" | "anthropic"
        self.client = client
        self.fast = fast_model
        self.smart = smart_model
        self.name = name

    def model_for(self, tier: str) -> str:
        return self.smart if tier == "smart" else self.fast


def _make_anthropic_backend() -> Optional["_Backend"]:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    import anthropic
    return _Backend("anthropic", anthropic.AsyncAnthropic(api_key=key),
                    _ANTHROPIC_FAST, _ANTHROPIC_SMART, "anthropic")


def _make_oai_backend() -> "_Backend":
    from openai import AsyncOpenAI
    preset = _PRESETS.get(PROVIDER, _PRESETS["openai"])
    fast = os.getenv("LLM_FAST_MODEL", preset["fast"])
    smart = os.getenv("LLM_SMART_MODEL", preset["smart"])
    base = os.getenv("LLM_BASE_URL", preset["base_url"])
    key = os.getenv("LLM_API_KEY") or os.getenv(f"{PROVIDER.upper()}_API_KEY", "")
    return _Backend("oai", AsyncOpenAI(api_key=key, base_url=base), fast, smart, PROVIDER)


# Build primary + optional fallback.
if PROVIDER == "anthropic":
    PRIMARY = _make_anthropic_backend()
    FALLBACK = None
else:
    PRIMARY = _make_oai_backend()
    FALLBACK = None if os.getenv("LLM_DISABLE_FALLBACK") == "1" else _make_anthropic_backend()

logger.info(f"LLM primary = {PRIMARY.name} (fast={PRIMARY.fast}, smart={PRIMARY.smart}); "
            f"fallback = {FALLBACK.name if FALLBACK else 'none'}")


# ── failure classification ────────────────────────────────────────────────────
def _is_retriable(e: Exception) -> bool:
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    if status in (408, 409, 425, 429, 500, 502, 503, 504, 529):
        return True
    name = type(e).__name__.lower()
    if any(k in name for k in ("ratelimit", "timeout", "apiconnection", "overloaded",
                               "serviceunavailable", "internalserver", "apierror")):
        return True
    msg = str(e).lower()
    return any(k in msg for k in ("quota", "exhausted", "rate limit", "resource_exhausted",
                                  "overloaded", "unavailable", "try again"))


# ── admin notification (throttled, fire-and-forget) ───────────────────────────
_ADMIN_EMAIL = "srinathreddy.ksr@gmail.com"
_NOTIFY_EVERY = int(os.getenv("LLM_FALLBACK_NOTIFY_SECONDS", "1800"))  # ≤1 email / 30 min
_last_email_ts = 0.0
_fallback_count = 0
_bg_tasks: set = set()


def _notify_fallback(error: Exception, tier: str, fn: str) -> None:
    global _last_email_ts, _fallback_count
    _fallback_count += 1
    logger.warning(f"[LLM fallback] {PRIMARY.name} failed ({fn}/{tier}): {error!r} "
                   f"→ serving via {FALLBACK.name}. total={_fallback_count}")
    now = time.time()
    email_now = (now - _last_email_ts) >= _NOTIFY_EVERY
    if email_now:
        _last_email_ts = now
    try:
        t = asyncio.create_task(_notify_async(str(error)[:400], tier, fn, _fallback_count, email_now))
        _bg_tasks.add(t)
        t.add_done_callback(_bg_tasks.discard)
    except RuntimeError:
        pass  # no running loop


async def _notify_async(error: str, tier: str, fn: str, count: int, email_now: bool) -> None:
    # 1) persistent record the admin dashboard / SQL can read
    try:
        import uuid
        from db.database import AsyncSessionLocal
        from db.models import AnalyticsEvent
        async with AsyncSessionLocal() as s:
            s.add(AnalyticsEvent(id=str(uuid.uuid4()), event="llm_fallback", feature=fn,
                                 metadata_json={"provider": PRIMARY.name, "reason": error,
                                                "tier": tier, "count": count}))
            await s.commit()
    except Exception as e:
        logger.error(f"[LLM fallback] analytics event write failed: {e}")

    # 2) throttled email alert
    if not email_now:
        return
    try:
        from services.email_service import send_email
        html = f"""<div style="font-family:system-ui,sans-serif;color:#111">
          <h2>⚠️ Mithra AI — {PRIMARY.name.title()} limit hit, Claude fallback active</h2>
          <p><b>{PRIMARY.name}</b> returned an error, so requests are being served by <b>Claude (Anthropic)</b> instead. Users are unaffected.</p>
          <ul>
            <li><b>Reason:</b> {error}</li>
            <li><b>Where:</b> {fn} · {tier} tier</li>
            <li><b>Fallbacks since last restart:</b> {count}</li>
          </ul>
          <p>Most likely the {PRIMARY.name} free-tier rate/quota limit. Enabling billing on the {PRIMARY.name} project removes it. Claude keeps things running in the meantime.</p>
          <p style="color:#888;font-size:12px">You won't get another of these for {_NOTIFY_EVERY // 60} minutes even if it keeps happening.</p>
        </div>"""
        await send_email(_ADMIN_EMAIL, "⚠️ Mithra AI: Gemini limit hit — Claude fallback active", html)
        logger.info("[LLM fallback] admin email sent")
    except Exception as e:
        logger.error(f"[LLM fallback] admin email failed: {e}")


# ── backend call implementations ──────────────────────────────────────────────
def _cached_system(system: str) -> list[dict]:
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _oai_messages(system: str, messages: list[dict]) -> list[dict]:
    return [{"role": "system", "content": system}] + messages


async def _backend_complete(b: "_Backend", system, messages, max_tokens, tier) -> str:
    if b.kind == "anthropic":
        resp = await b.client.messages.create(
            model=b.model_for(tier), max_tokens=max_tokens,
            system=_cached_system(system), messages=messages)
        if not resp.content:
            return ""
        parts = [getattr(x, "text", "") for x in resp.content
                 if getattr(x, "type", None) == "text" or hasattr(x, "text")]
        return "".join(p for p in parts if p)
    resp = await b.client.chat.completions.create(
        model=b.model_for(tier), max_tokens=max_tokens, temperature=_TEMPERATURE,
        messages=_oai_messages(system, messages))
    return (resp.choices[0].message.content or "") if resp.choices else ""


async def _backend_stream(b: "_Backend", system, messages, max_tokens, tier) -> AsyncIterator[str]:
    if b.kind == "anthropic":
        async with b.client.messages.stream(
            model=b.model_for(tier), max_tokens=max_tokens,
            system=_cached_system(system), messages=messages) as stream:
            async for text in stream.text_stream:
                yield text
        return
    stream = await b.client.chat.completions.create(
        model=b.model_for(tier), max_tokens=max_tokens, temperature=_TEMPERATURE,
        messages=_oai_messages(system, messages), stream=True)
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


# ── Public API (unchanged signatures) ─────────────────────────────────────────
async def complete_claude(system: str, messages: list[dict], max_tokens: int = 4096, tier: str = "fast") -> str:
    try:
        return await _backend_complete(PRIMARY, system, messages, max_tokens, tier)
    except Exception as e:
        if FALLBACK and _is_retriable(e):
            _notify_fallback(e, tier, "complete")
            return await _backend_complete(FALLBACK, system, messages, max_tokens, tier)
        raise


async def stream_claude(system: str, messages: list[dict], max_tokens: int = 4096,
                        temperature: float = 1.0, tier: str = "fast") -> AsyncIterator[str]:
    # Try to start the primary stream; if it fails BEFORE any token, fall back.
    try:
        gen = _backend_stream(PRIMARY, system, messages, max_tokens, tier)
        first = await gen.__anext__()
    except StopAsyncIteration:
        return
    except Exception as e:
        if FALLBACK and _is_retriable(e):
            _notify_fallback(e, tier, "stream")
            async for text in _backend_stream(FALLBACK, system, messages, max_tokens, tier):
                yield text
            return
        raise
    yield first
    async for text in gen:
        yield text


async def complete_claude_json(system: str, messages: list[dict], max_tokens: int = 4096, tier: str = "fast") -> str:
    """Returns model response with JSON extracted — strips markdown code fences."""
    system_with_json = system + "\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown, no explanation, no code fences."
    raw = await complete_claude(system_with_json, messages, max_tokens, tier=tier)
    return _extract_json(raw)


def _extract_json(text: str) -> str:
    """Strip markdown code fences and extract the outermost JSON value.

    Whichever of '{' or '[' appears FIRST is the real start of the payload —
    always probing '{' first would grab the first object inside an array.
    """
    import re
    if not text:
        return ""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()

    obj_at = text.find("{")
    arr_at = text.find("[")
    candidates = [p for p in ((obj_at, "{", "}"), (arr_at, "[", "]")) if p[0] != -1]
    if not candidates:
        return text
    start, start_char, end_char = min(candidates, key=lambda p: p[0])

    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == start_char:
                depth += 1
            elif ch == end_char:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return text
