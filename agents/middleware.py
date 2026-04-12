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

ResilientSessionMiddleware:
    Catches ``previous_response_not_found`` errors from Azure Foundry
    when the server-side session TTL expires during rate-limit delays.
    Recovers by extracting conversation history from
    InMemoryHistoryProvider, creating a fresh AgentSession, injecting
    a compressed summary of the history, and retrying the call.

RateLimitMiddleware:
    Intercepts 429 errors with exponential backoff and proactively
    refreshes the session before the server-side TTL expires.

SessionHealthMonitor:
    Tracks per-session last-call timestamps and proactively refreshes
    sessions that have been idle longer than the configured threshold.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from agent_framework import (
    AgentContext, AgentMiddleware, AgentResponse, AgentSession,
    InMemoryHistoryProvider, Message, agent_middleware,
)
# Import the canonical CORPORATE_WORDS from archetypes to avoid duplication.
from prompts.archetypes import CORPORATE_WORDS

logger = logging.getLogger(__name__)

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


# ------------------------------------------------------------------ #
#  Session Health Monitor                                               #
# ------------------------------------------------------------------ #

class SessionHealthMonitor:
    """
    Tracks per-session last-call timestamps for proactive refresh.

    Shared across middleware instances — one global tracker for all
    sessions in the process. Thread-safe for asyncio (single-threaded).
    """

    _timestamps: dict[str, float] = {}

    @classmethod
    def touch(cls, session_id: str) -> None:
        """Record that a call was just made on this session."""
        cls._timestamps[session_id] = time.monotonic()

    @classmethod
    def idle_seconds(cls, session_id: str) -> float:
        """Return seconds since last call on this session."""
        last = cls._timestamps.get(session_id)
        if last is None:
            return 0.0
        return time.monotonic() - last

    @classmethod
    def remove(cls, session_id: str) -> None:
        """Remove tracking for a session (after refresh)."""
        cls._timestamps.pop(session_id, None)


# ------------------------------------------------------------------ #
#  Helpers                                                              #
# ------------------------------------------------------------------ #

def _is_session_expired_error(exc: Exception) -> bool:
    """Return True if *exc* is a previous_response_id not found error.

    Azure returns the error as the string ``"previous_response_id not found"``
    (with spaces and the word "id"). The underscore form
    ``"previous_response_not_found"`` is kept for any SDK wrappers that
    normalise the message.
    """
    msg = str(exc).lower()
    return (
        "previous_response_not_found" in msg
        or ("previous_response" in msg and "not found" in msg)
    )


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if *exc* is a 429 Too Many Requests error."""
    msg = str(exc).lower()
    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status == 429


def _summarize_history(messages: list[Message], max_messages: int = 10) -> str:
    """
    Build a compact text summary from the most recent messages.

    Extracts the text content from each Message and produces a
    compressed conversation summary suitable for injecting into a
    fresh session's context.
    """
    recent = messages[-max_messages:] if len(messages) > max_messages else messages
    lines: list[str] = []
    for msg in recent:
        role = msg.role or "unknown"
        parts: list[str] = []
        for content in (msg.contents or []):
            text = getattr(content, "text", None) or str(content)
            if text:
                parts.append(text)
        if parts:
            combined = " ".join(parts)
            # Truncate individual messages to keep summary compact
            if len(combined) > 200:
                combined = combined[:197] + "..."
            lines.append(f"[{role}]: {combined}")
    return "\n".join(lines)


def _extract_history_from_session(session: AgentSession) -> list[Message]:
    """
    Extract stored messages from a session's InMemoryHistoryProvider state.

    InMemoryHistoryProvider stores messages under state["history"]["messages"].
    """
    if session is None:
        return []
    # InMemoryHistoryProvider uses source_id as the key in session.state
    history_state = session.state.get("history", {})
    messages = history_state.get("messages", [])
    return list(messages)


def _refresh_session(
    old_session: AgentSession,
    history_summary: str,
) -> AgentSession:
    """
    Create a fresh AgentSession and transfer state from the old session.

    Copies all provider state (belief, memory, etc.) from the old session
    to the new one. Clears the old InMemoryHistoryProvider messages and
    injects a summary message so the agent retains conversation context.
    """
    new_session = AgentSession()

    # Transfer all state from old session to new (belief, memory, etc.)
    for key, value in old_session.state.items():
        if key == "history":
            # Reset history with a summary message instead of full history
            new_state: dict = {}
            if history_summary:
                summary_msg = Message(
                    role="user",
                    contents=[
                        f"[CONVERSATION SUMMARY — session refreshed]\n"
                        f"{history_summary}"
                    ],
                )
                new_state["messages"] = [summary_msg]
            else:
                new_state["messages"] = []
            new_session.state[key] = new_state
        else:
            # Shallow copy dicts, direct copy everything else
            if isinstance(value, dict):
                new_session.state[key] = dict(value)
            else:
                new_session.state[key] = value

    return new_session


# Maps old session_id → refreshed AgentSession after a recovery.
# run_agent_stream pops entries here to propagate the new session back
# to the agent wrapper so subsequent calls don't re-trigger recovery.
_session_refresh_registry: dict[str, "AgentSession"] = {}


# ------------------------------------------------------------------ #
#  ResilientSessionMiddleware                                           #
# ------------------------------------------------------------------ #

class ResilientSessionMiddleware(AgentMiddleware):
    """
    Catches ``previous_response_not_found`` errors and rebuilds the
    session from the local InMemoryHistoryProvider cache.

    Behaviour:
    1. Wraps call_next() in a try/except for ChatClientException
    2. On ``previous_response_not_found``:
       - Extract conversation history from InMemoryHistoryProvider
       - Create a fresh AgentSession with transferred state
       - Inject a compressed history summary into the new session
       - Replace context.session and retry call_next()
    3. All other exceptions propagate normally

    This middleware MUST be registered FIRST in the middleware chain
    (outermost) so it wraps all other middleware.
    """

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        try:
            await call_next()
        except Exception as exc:
            if not _is_session_expired_error(exc):
                raise

            session = context.session
            agent_name = getattr(context, "agent_name", None) or "unknown"
            logger.warning(
                "[%s] Session expired (previous_response_not_found) — "
                "rebuilding from InMemoryHistoryProvider cache",
                agent_name,
            )

            # Extract history from the expired session
            old_messages = _extract_history_from_session(session)
            history_summary = _summarize_history(old_messages)

            # Create fresh session with transferred state
            new_session = _refresh_session(session, history_summary)

            # Update the context to use the new session
            context.session = new_session

            # Register the replacement so run_agent_stream can update
            # the agent wrapper's self.session after this call returns.
            if session:
                _session_refresh_registry[session.session_id] = new_session

            # Track the new session
            SessionHealthMonitor.touch(new_session.session_id)
            if session:
                SessionHealthMonitor.remove(session.session_id)

            # Store recovery metadata for observability
            context.metadata["session_recovered"] = True
            context.metadata["recovered_message_count"] = len(old_messages)

            # Retry with the fresh session
            logger.info(
                "[%s] Session rebuilt with %d messages summarized — retrying",
                agent_name, len(old_messages),
            )
            await call_next()


# ------------------------------------------------------------------ #
#  RateLimitMiddleware                                                  #
# ------------------------------------------------------------------ #

class RateLimitMiddleware(AgentMiddleware):
    """
    Intercepts 429 (Too Many Requests) errors with exponential backoff,
    keeping the same session and previous_response_id chain.

    If the cumulative retry delay exceeds SESSION_REFRESH_THRESHOLD,
    proactively refreshes the session via ResilientSessionMiddleware's
    helpers BEFORE the server-side TTL expires.

    Key insight: prevent expiration rather than recover from it.
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        refresh_threshold: float | None = None,
    ) -> None:
        from config.settings import MAFIA_SESSION_REFRESH_THRESHOLD
        self._max_retries = max_retries
        self._base_delay = base_delay
        self._refresh_threshold = (
            refresh_threshold
            if refresh_threshold is not None
            else MAFIA_SESSION_REFRESH_THRESHOLD
        )

    async def process(
        self,
        context: AgentContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        import asyncio
        import random

        cumulative_delay = 0.0
        last_exc: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                # Pre-call: check if session is idle too long
                session = context.session
                if session and attempt == 0:
                    from config.settings import MAFIA_SESSION_IDLE_THRESHOLD
                    idle = SessionHealthMonitor.idle_seconds(session.session_id)
                    if idle > MAFIA_SESSION_IDLE_THRESHOLD:
                        agent_name = getattr(context, "agent_name", None) or "unknown"
                        logger.info(
                            "[%s] Session idle %.1fs > threshold %.1fs — "
                            "proactive refresh",
                            agent_name, idle, MAFIA_SESSION_IDLE_THRESHOLD,
                        )
                        old_messages = _extract_history_from_session(session)
                        summary = _summarize_history(old_messages)
                        new_session = _refresh_session(session, summary)
                        context.session = new_session
                        SessionHealthMonitor.remove(session.session_id)
                        SessionHealthMonitor.touch(new_session.session_id)
                        context.metadata["session_proactive_refresh"] = True

                await call_next()

                # Success — record the timestamp
                if context.session:
                    SessionHealthMonitor.touch(context.session.session_id)
                return

            except Exception as exc:
                if not _is_rate_limit_error(exc):
                    raise

                last_exc = exc

                if attempt >= self._max_retries:
                    raise

                # Exponential backoff with jitter
                delay = min(self._base_delay * (2 ** attempt), 8.0)
                jitter = random.uniform(0, 0.5)
                delay += jitter
                cumulative_delay += delay

                agent_name = getattr(context, "agent_name", None) or "unknown"
                logger.info(
                    "[%s] Rate limited (429) — retry %d/%d after %.1fs "
                    "(cumulative: %.1fs)",
                    agent_name, attempt + 1, self._max_retries,
                    delay, cumulative_delay,
                )

                # If cumulative delay exceeds threshold, proactively
                # refresh the session before the TTL expires
                session = context.session
                if session and cumulative_delay > self._refresh_threshold:
                    logger.info(
                        "[%s] Cumulative delay %.1fs exceeds threshold "
                        "%.1fs — proactive session refresh",
                        agent_name, cumulative_delay,
                        self._refresh_threshold,
                    )
                    old_messages = _extract_history_from_session(session)
                    summary = _summarize_history(old_messages)
                    new_session = _refresh_session(session, summary)
                    context.session = new_session
                    SessionHealthMonitor.remove(session.session_id)
                    SessionHealthMonitor.touch(new_session.session_id)
                    context.metadata["session_refreshed_during_backoff"] = True
                    # Reset cumulative since we have a fresh session
                    cumulative_delay = 0.0

                await asyncio.sleep(delay)

        # Should not reach here
        if last_exc:
            raise last_exc
