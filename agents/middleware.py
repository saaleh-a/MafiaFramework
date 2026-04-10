"""
agents/middleware.py
---------------------
MAF middleware for the Mafia game agents.

Uses the framework's native @agent_middleware decorator and class-based
AgentMiddleware for cross-cutting concerns.

CorporateSpeakMiddleware:
    After agent execution, checks the ACTION section for boardroom
    vocabulary. If too many corporate words are found, modifies the
    prompt and re-invokes the agent — all within the middleware pipeline.

    Note: In streaming mode, the result is a ResponseStream that hasn't
    been consumed yet, so the middleware skips the check. For streaming
    calls, corporate-speak enforcement is handled inline by
    run_agent_stream() in agents/base.py.

ReasoningActionMiddleware:
    Parses REASONING:/ACTION: split from every agent response and stores
    the parsed values on context.metadata so the orchestrator can read
    them cleanly. Handles empty-action fallback.

BeliefUpdateMiddleware:
    Extracts BELIEF_UPDATE tags from the reasoning text and stores the
    parsed updates on context.metadata for the orchestrator to apply.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent_framework import (
    AgentContext, AgentMiddleware, AgentResponse, Message, agent_middleware,
)

# Import the canonical CORPORATE_WORDS from archetypes to avoid duplication.
from prompts.archetypes import CORPORATE_WORDS

_CORPORATE_THRESHOLD = 3

# The slang enforcement hint, shared with run_agent_stream (agents/base.py)
# for the streaming fallback.
CORPORATE_ENFORCEMENT_HINT = (
    "⚠ YOUR LAST RESPONSE SOUNDED LIKE A CORPORATE MEMO. "
    "Rewrite using slang. Short words. Road logic. "
    "You are in a pub argument, not a boardroom."
)


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
            contents=[CORPORATE_ENFORCEMENT_HINT],
        ),
    )
    await call_next()


# ------------------------------------------------------------------ #
#  ReasoningActionMiddleware                                            #
# ------------------------------------------------------------------ #

class ReasoningActionMiddleware(AgentMiddleware):
    """
    Parses REASONING:/ACTION: split from every agent response.

    After call_next(), inspects context.result.text, runs the standard
    parse_reasoning_action() split, and stores the parsed values on
    context.metadata["reasoning"] and context.metadata["action"].

    Skipped for streaming calls (handled by run_agent_stream).
    """

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        await call_next()

        if context.stream:
            return

        result = context.result
        if not isinstance(result, AgentResponse):
            return

        from agents.base import parse_reasoning_action

        response_text = result.text or ""
        reasoning, action = parse_reasoning_action(response_text)
        context.metadata["reasoning"] = reasoning
        context.metadata["action"] = action


# ------------------------------------------------------------------ #
#  BeliefUpdateMiddleware                                               #
# ------------------------------------------------------------------ #

class BeliefUpdateMiddleware(AgentMiddleware):
    """
    Extracts BELIEF_UPDATE tags from the reasoning text after every
    agent call and stores the updates on context.metadata.

    After call_next(), parses BELIEF_UPDATE lines from the reasoning
    portion of the response and stores them as a dict on
    context.metadata["belief_updates"].

    Skipped for streaming calls (handled inline by the orchestrator).
    """

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        await call_next()

        if context.stream:
            return

        result = context.result
        if not isinstance(result, AgentResponse):
            return

        from agents.base import parse_reasoning_action
        from agents.belief_state import parse_belief_updates

        response_text = result.text or ""
        reasoning, _ = parse_reasoning_action(response_text)
        updates = parse_belief_updates(reasoning) if reasoning else {}
        context.metadata["belief_updates"] = updates
