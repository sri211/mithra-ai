"""
Provider-agnostic LLM service with cost-optimised model routing.

Historically this called Anthropic Claude directly. It now routes through ANY
OpenAI-compatible provider (Groq, Google Gemini, DeepSeek, OpenAI, OpenRouter…)
OR Anthropic, selected purely by environment variables — so the whole app can
switch to a cheaper model without touching a single feature file.

Tiers (unchanged for callers):
  - FAST  (default): cheap/quick — extraction, scoring, ranking, question gen, chat.
  - SMART: best-quality — resume adaptation, cover letters.

Env:
  LLM_PROVIDER   = anthropic (default) | groq | gemini | deepseek | openai | openrouter
  LLM_API_KEY    = key for the chosen provider (falls back to ANTHROPIC_API_KEY for anthropic)
  LLM_BASE_URL   = override the provider's base URL (optional)
  LLM_FAST_MODEL / LLM_SMART_MODEL = override the tier models (optional)

Anthropic remains the default so nothing changes until LLM_PROVIDER is set.
"""
import os
from typing import AsyncIterator
from loguru import logger

PROVIDER = os.getenv("LLM_PROVIDER", "anthropic").lower().strip()

# Sensible base URL + default models per provider. Overridable via env.
_PRESETS = {
    "groq":       {"base_url": "https://api.groq.com/openai/v1",
                   "fast": "llama-3.1-8b-instant", "smart": "llama-3.3-70b-versatile"},
    "gemini":     {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
                   "fast": "gemini-2.0-flash", "smart": "gemini-2.5-flash"},
    "deepseek":   {"base_url": "https://api.deepseek.com",
                   "fast": "deepseek-chat", "smart": "deepseek-chat"},
    "openai":     {"base_url": "https://api.openai.com/v1",
                   "fast": "gpt-4o-mini", "smart": "gpt-4o"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1",
                   "fast": "meta-llama/llama-3.3-70b-instruct",
                   "smart": "meta-llama/llama-3.3-70b-instruct"},
}

_IS_ANTHROPIC = PROVIDER == "anthropic"

# ── Anthropic path (default) ──────────────────────────────────────────────────
if _IS_ANTHROPIC:
    import anthropic
    FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "claude-haiku-4-5-20251001")
    SMART_MODEL = os.getenv("CLAUDE_SMART_MODEL", "claude-sonnet-5")
    _LEGACY = os.getenv("CLAUDE_MODEL", "")
    if _LEGACY and "opus" not in _LEGACY:
        FAST_MODEL = _LEGACY
    _anthropic_client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    _oai_client = None
else:
    from openai import AsyncOpenAI
    _preset = _PRESETS.get(PROVIDER, _PRESETS["openai"])
    FAST_MODEL = os.getenv("LLM_FAST_MODEL", _preset["fast"])
    SMART_MODEL = os.getenv("LLM_SMART_MODEL", _preset["smart"])
    _base_url = os.getenv("LLM_BASE_URL", _preset["base_url"])
    _api_key = os.getenv("LLM_API_KEY") or os.getenv(f"{PROVIDER.upper()}_API_KEY", "")
    _oai_client = AsyncOpenAI(api_key=_api_key, base_url=_base_url)
    _anthropic_client = None
    logger.info(f"LLM provider = {PROVIDER} (fast={FAST_MODEL}, smart={SMART_MODEL})")

_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.7"))


def _model_for(tier: str) -> str:
    return SMART_MODEL if tier == "smart" else FAST_MODEL


def _cached_system(system: str) -> list[dict]:
    """Wrap system prompt with cache_control — cached when long enough, ignored otherwise."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


def _oai_messages(system: str, messages: list[dict]) -> list[dict]:
    return [{"role": "system", "content": system}] + messages


# ── Public API (same signatures as before) ────────────────────────────────────

async def stream_claude(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 1.0,
    tier: str = "fast",
) -> AsyncIterator[str]:
    if _IS_ANTHROPIC:
        async with _anthropic_client.messages.stream(
            model=_model_for(tier), max_tokens=max_tokens,
            system=_cached_system(system), messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
        return
    # OpenAI-compatible providers
    stream = await _oai_client.chat.completions.create(
        model=_model_for(tier), max_tokens=max_tokens, temperature=_TEMPERATURE,
        messages=_oai_messages(system, messages), stream=True,
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta.content if chunk.choices else None
        if delta:
            yield delta


async def complete_claude(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    tier: str = "fast",
) -> str:
    if _IS_ANTHROPIC:
        response = await _anthropic_client.messages.create(
            model=_model_for(tier), max_tokens=max_tokens,
            system=_cached_system(system), messages=messages,
        )
        # Concatenate ALL text blocks — the response may lead with a thinking
        # block (content[0] has no `.text`), so reading only content[0] drops the
        # actual answer (this silently broke the resume adaptor once).
        if not response.content:
            return ""
        parts = [getattr(b, "text", "") for b in response.content
                 if getattr(b, "type", None) == "text" or hasattr(b, "text")]
        return "".join(p for p in parts if p)
    # OpenAI-compatible providers
    resp = await _oai_client.chat.completions.create(
        model=_model_for(tier), max_tokens=max_tokens, temperature=_TEMPERATURE,
        messages=_oai_messages(system, messages),
    )
    return (resp.choices[0].message.content or "") if resp.choices else ""


async def complete_claude_json(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    tier: str = "fast",
) -> str:
    """Returns model response with JSON extracted — strips markdown code fences."""
    system_with_json = system + "\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown, no explanation, no code fences."
    raw = await complete_claude(system_with_json, messages, max_tokens, tier=tier)
    return _extract_json(raw)


def _extract_json(text: str) -> str:
    """Strip markdown code fences and extract the outermost JSON value.

    IMPORTANT: whichever of '{' or '[' appears FIRST is the real start of the
    payload. Always probing '{' first would grab the first object *inside* an
    array (e.g. `[{...},{...}]` → `{...}`), silently turning a list of results
    into a single dict — which callers then read as "empty".
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
