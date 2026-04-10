"""
agents/middleware.py
---------------------
MAF middleware for the Mafia game agents.

Uses the framework's native @agent_middleware decorator instead of
manual retry loops in run_agent_stream().

CorporateSpeakMiddleware:
    After agent execution, checks the ACTION section for boardroom
    vocabulary. If too many corporate words are found, modifies the
    prompt and re-invokes the agent — all within the middleware pipeline.

    Note: In streaming mode, the result is a ResponseStream that hasn't
    been consumed yet, so the middleware skips the check. For streaming
    calls, corporate-speak enforcement is handled inline by
    run_agent_stream() in agents/base.py.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_framework import AgentContext, AgentResponse, Message, agent_middleware

# Import the canonical CORPORATE_WORDS from archetypes to avoid duplication.
from prompts.archetypes import CORPORATE_WORDS

_CORPORATE_THRESHOLD = 3


def _count_corporate(text: str) -> int:
    text_lower = text.lower()
    return sum(1 for w in CORPORATE_WORDS if w in text_lower)


def _extract_action(text: str) -> str:
    """Extract the ACTION section from REASONING/ACTION formatted text."""
    if "ACTION:" not in text:
        return text
    return text.rsplit("ACTION:", 1)[1].strip()


@agent_middleware
async def corporate_speak_middleware(
    context: AgentContext,
    call_next: Callable[[], Awaitable[None]],
) -> None:
    """
    Agent middleware that enforces slang over corporate-speak.

    Runs after the agent produces a response. If the ACTION section
    contains 3+ corporate words, appends a slang hint to the messages
    and re-invokes the pipeline once.

    In streaming mode the result is a ResponseStream that hasn't been
    consumed yet, so the check is skipped here (handled by
    run_agent_stream instead).
    """
    await call_next()

    # Streaming: result is a ResponseStream, not AgentResponse.
    # The text hasn't been consumed yet so we can't inspect it.
    if context.stream:
        return

    # Non-streaming: result is an AgentResponse
    result = context.result
    if not isinstance(result, AgentResponse):
        return

    response_text = result.text or ""
    action_text = _extract_action(response_text)

    if _count_corporate(action_text) < _CORPORATE_THRESHOLD:
        return

    # Re-invoke with a slang enforcement hint
    context.messages.append(
        Message(
            role="user",
            contents=[
                "⚠ YOUR LAST RESPONSE SOUNDED LIKE A CORPORATE MEMO. "
                "Rewrite using slang. Short words. Road logic. "
                "You are in a pub argument, not a boardroom."
            ],
        ),
    )
    await call_next()
