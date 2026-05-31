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
    """Returns Claude response — caller must parse JSON."""
    system_with_json = system + "\n\nIMPORTANT: Respond ONLY with valid JSON. No markdown, no explanation."
    return await complete_claude(system_with_json, messages, max_tokens)
