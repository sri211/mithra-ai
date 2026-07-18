"""
Claude service with cost-optimised model routing.

Tiers:
  - FAST  (default): Haiku 4.5 — extraction, scoring, ranking, question gen, chat.
  - SMART: Sonnet — only where users judge writing quality (resume adaptation, cover letters).

Every helper takes tier="fast"|"smart". Existing callers default to fast.
System prompts are sent with cache_control so repeated calls hit Anthropic's
prompt cache (90% input discount when the prompt is long enough to cache).
"""
import anthropic
import os
from typing import AsyncIterator
from loguru import logger

FAST_MODEL = os.getenv("CLAUDE_FAST_MODEL", "claude-haiku-4-5-20251001")
SMART_MODEL = os.getenv("CLAUDE_SMART_MODEL", "claude-sonnet-5")
# Legacy env override — if CLAUDE_MODEL is set explicitly it wins for fast tier
_LEGACY = os.getenv("CLAUDE_MODEL", "")
if _LEGACY and "opus" not in _LEGACY:
    FAST_MODEL = _LEGACY

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def _model_for(tier: str) -> str:
    return SMART_MODEL if tier == "smart" else FAST_MODEL


def _cached_system(system: str) -> list[dict]:
    """Wrap system prompt with cache_control — cached when long enough, ignored otherwise."""
    return [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]


async def stream_claude(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 1.0,
    tier: str = "fast",
) -> AsyncIterator[str]:
    async with client.messages.stream(
        model=_model_for(tier),
        max_tokens=max_tokens,
        system=_cached_system(system),
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def complete_claude(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    tier: str = "fast",
) -> str:
    response = await client.messages.create(
        model=_model_for(tier),
        max_tokens=max_tokens,
        system=_cached_system(system),
        messages=messages,
    )
    # Concatenate ALL text blocks. The response may lead with a thinking block
    # (content[0] has no `.text`), so reading only content[0] would drop the
    # actual answer — this silently broke the resume adaptor.
    if not response.content:
        return ""
    parts = [getattr(b, "text", "") for b in response.content
             if getattr(b, "type", None) == "text" or hasattr(b, "text")]
    return "".join(p for p in parts if p)


async def complete_claude_json(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    tier: str = "fast",
) -> str:
    """Returns Claude response with JSON extracted — strips markdown code fences."""
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
    # Remove ```json ... ``` or ``` ... ``` wrappers
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
