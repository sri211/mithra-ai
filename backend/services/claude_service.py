import anthropic
import os
from typing import AsyncIterator
from loguru import logger

CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-4-7")

client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


async def stream_claude(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
    temperature: float = 1.0,
) -> AsyncIterator[str]:
    async with client.messages.stream(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield text


async def complete_claude(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
) -> str:
    response = await client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    return response.content[0].text


async def complete_claude_json(
    system: str,
    messages: list[dict],
    max_tokens: int = 4096,
) -> str:
    """Returns Claude response with JSON extracted — strips markdown code fences."""
    system_with_json = system + "\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown, no explanation, no code fences."
    raw = await complete_claude(system_with_json, messages, max_tokens)
    return _extract_json(raw)


def _extract_json(text: str) -> str:
    """Strip markdown code fences and extract the first valid JSON object/array."""
    import re
    # Remove ```json ... ``` or ``` ... ``` wrappers
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```$", "", text.strip(), flags=re.MULTILINE)
    text = text.strip()
    # Find the outermost { } or [ ] block
    for start_char, end_char in [('{', '}'), ('[', ']')]:
        start = text.find(start_char)
        if start == -1:
            continue
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
            if not in_string:
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        return text[start:i+1]
    return text
