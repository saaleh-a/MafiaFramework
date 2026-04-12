"""Shared utilities for all game agents."""

import re
import sys
import logging

from agent_framework import AgentSession
from prompts.archetypes import CORPORATE_WORDS
from agents.middleware import (
    _CORPORATE_THRESHOLD,
    CORPORATE_ENFORCEMENT_HINT,
    SessionHealthMonitor,
    _extract_history_from_session,
    _is_session_expired_error,
    _refresh_session,
    _session_refresh_registry,
    _summarize_history,
)
from agents.rate_limiter import rate_limited_call, _is_rate_limit_error, _is_timeout_error
from config.settings import MAFIA_ENABLE_STREAMING_FALLBACK

logger = logging.getLogger(__name__)


# Runtime reminder appended to every discussion prompt (Symptom A fix).
# DISCUSSION_RULES covers this in the system prompt but agents still open
# with vote declarations — a per-call reminder eliminates the gap.
_VOTE_BAN_REMINDER = (
    "\nREMINDER: DISCUSSION phase — NOT the vote phase. "
    "Do NOT open with 'I'm voting X', 'I vote X', or any vote declaration. "
    "Your response must be conversational argument, challenge, or question."
)


def format_discussion_prompt(history: list[str], agent_name: str) -> str:
    """
    Format discussion history to encourage conversational responses.

    ARCHITECTURE: Only messages that appeared AFTER the agent's own most
    recent contribution are injected. Messages before that are already
    stored in InMemoryHistoryProvider from the previous call — re-injecting
    them causes other agents' statements to appear twice in context (Symptom E).

    Separates the last message from earlier discussion so the agent
    knows exactly who just spoke and what they said — making it natural
    to respond TO that person rather than monologuing past them.
    """
    # Find the index of the agent's most recent contribution.
    # Everything at or before that index is already in InMemoryHistoryProvider.
    last_agent_idx = -1
    for i, h in enumerate(history):
        if h.startswith(f"{agent_name}:"):
            last_agent_idx = i

    # Only inject messages that arrived after the agent's last turn.
    new_messages = history[last_agent_idx + 1:]
    others_history = [h for h in new_messages if not h.startswith(f"{agent_name}:")]

    if not others_history:
        return (
            "What others have said in this round's discussion:\n"
            "Nobody else has spoken yet. You are first.\n"
            "Pick someone by name and ask them a direct question, "
            "or throw out a concrete suspicion with a reason."
            + _VOTE_BAN_REMINDER
        )

    if len(others_history) == 1:
        last_speaker = _extract_name(others_history[0])
        return (
            f"What others have said in this round's discussion:\n"
            f"{others_history[0]}\n\n"
            f"^ {last_speaker} just spoke. "
            f"Respond directly to what they said."
            + _VOTE_BAN_REMINDER
        )

    earlier = "\n".join(others_history[:-1])
    last = others_history[-1]
    last_speaker = _extract_name(last)

    return (
        f"What others have said in this round's discussion:\n{earlier}\n\n"
        f"LAST MESSAGE (respond to this):\n{last}\n\n"
        f"^ {last_speaker} just said that. Talk TO them. "
        f"Agree, disagree, ask a follow-up, or challenge them directly."
        + _VOTE_BAN_REMINDER
    )


def format_vote_prompt(
    public_state_summary: str,
    history: list[str],
    agent_name: str,
    targets: list[str],
    *,
    private_context: str = "",
) -> str:
    """Build a stricter day-vote prompt that strongly prefers exact output."""
    discussion_text = "\n".join(history) if history else "No prior discussion."
    private_block = f"{private_context.strip()}\n\n" if private_context.strip() else ""
    return (
        f"{public_state_summary}\n\n"
        f"{private_block}"
        f"Full discussion:\n{discussion_text}\n\n"
        f"DAY VOTE. You are {agent_name}. You CANNOT vote for yourself.\n"
        f"Valid targets: {', '.join(targets)}\n"
        "This is the vote phase, not discussion. Make one final elimination choice now.\n"
        "Do NOT continue the discussion. Do NOT ask questions.\n"
        "Preferred: call the cast_vote tool with target=<exact valid name>.\n"
        "If you do not use the tool, your FINAL line MUST be exactly:\n"
        "ACTION: VOTE: <exact name from valid targets>\n"
        "The ACTION line must contain exactly one player name and no other names.\n"
        "Do NOT add punctuation, explanations, quotes, or extra words after the name.\n"
        "Bad: ACTION: I vote Bob because..., ACTION: Bob?, ACTION: cast_vote on Bob.\n"
        "Good: ACTION: VOTE: Bob"
    )


def _extract_name(line: str) -> str:
    """Extract speaker name from a 'Name: message' line."""
    if ":" in line:
        return line.split(":", 1)[0].strip()
    return "Someone"

# Patterns that indicate a content-filter refusal from the model
_REFUSAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"I'm sorry,?\s*but I cannot assist", re.IGNORECASE),
    re.compile(r"I cannot assist with that request", re.IGNORECASE),
    re.compile(r"I'm not able to help with that", re.IGNORECASE),
    re.compile(r"I can't assist with that", re.IGNORECASE),
    re.compile(r"I'm unable to (?:assist|help)", re.IGNORECASE),
]

_MAX_RETRIES = 2


def _contains_refusal(text: str) -> bool:
    """Return True if *text* contains a content-filter refusal phrase."""
    return any(p.search(text) for p in _REFUSAL_PATTERNS)


def _strip_refusal(text: str) -> str:
    """Remove refusal phrases from *text* (best-effort cleanup)."""
    for p in _REFUSAL_PATTERNS:
        text = p.sub("", text)
    return text.strip()


def _count_corporate(text: str) -> int:
    """Count how many corporate-speak words appear in *text*."""
    text_lower = text.lower()
    return sum(1 for w in CORPORATE_WORDS if w in text_lower)


def parse_reasoning_action(text: str) -> tuple[str, str]:
    """
    Splits the model output on ACTION:.
    Returns (reasoning_text, action_text).
    If no ACTION: marker found, returns ("", full_text).

    Uses rsplit("ACTION:", 1) so that when the model mistakenly
    writes  ACTION: REASONING: <thoughts> ACTION: <real action>
    we still recover the real action from the final marker.

    Any "REASONING:" strings found inside the action block are
    recursively stripped to prevent history pollution.
    """
    text = text.strip()
    if "ACTION:" not in text:
        # No ACTION: marker at all.
        # If REASONING: is present, the model produced reasoning without
        # a decision.  Treat this as a failed parse: return the reasoning
        # content in the reasoning slot and an empty action so that the
        # retry / fallback logic in the caller fires.
        if re.search(r"REASONING:", text, re.IGNORECASE):
            reasoning = re.sub(r"REASONING:", " ", text, flags=re.IGNORECASE)
            reasoning = " ".join(reasoning.split())
            return reasoning, ""
        # Fallback: no markers at all — treat whole response as the action
        return "", text

    # Split on the LAST ACTION: marker
    parts     = text.rsplit("ACTION:", 1)
    raw_front = parts[0]
    action    = parts[1].strip()

    # Collect reasoning from everything before the last ACTION:,
    # stripping any stray REASONING: / ACTION: markers
    reasoning = raw_front
    for marker in ("REASONING:", "ACTION:"):
        reasoning = reasoning.replace(marker, " ")
    reasoning = " ".join(reasoning.split())  # collapse whitespace

    # Recursively strip ALL "REASONING:" markers from the action block
    # to prevent history pollution.  If the action is *entirely*
    # REASONING: content (starts with the marker), absorb into
    # reasoning and return empty action so the retry logic fires.
    if action.upper().startswith("REASONING:"):
        leaked = action[len("REASONING:"):].strip()
        reasoning = f"{reasoning} {leaked}".strip()
        action = ""
    else:
        # Strip any embedded REASONING: markers inside the action
        cleaned = _recursive_strip_marker(action, "REASONING:")
        action = cleaned.strip()

    return reasoning, action


def _recursive_strip_marker(text: str, marker: str) -> str:
    """Remove all occurrences of *marker* (case-insensitive) from *text*."""
    pattern = re.compile(re.escape(marker), re.IGNORECASE)
    cleaned = pattern.sub(" ", text)
    # Collapse resulting multi-spaces
    return " ".join(cleaned.split())


def _extract_tool_result(full_text: str) -> str | None:
    """
    Extract a tool call result from the response text.
    Tool results appear as VOTE: or TARGET: from our @tool functions.
    """
    for prefix in ("VOTE:", "TARGET:"):
        match = re.search(rf"{prefix}\s*(\w+)", full_text, re.IGNORECASE)
        if match:
            return f"{prefix} {match.group(1)}"

    # Streaming sometimes surfaces a textual tool trace instead of the tool
    # return value, e.g. `functions.cast_vote {"target":"Bob", ...}`.
    tool_trace_patterns = [
        (
            re.compile(
                r"(?:functions\.)?cast_vote\b.*?[\"']target[\"']\s*:\s*[\"'](?P<target>\w+)[\"']",
                re.IGNORECASE | re.DOTALL,
            ),
            "VOTE:",
        ),
        (
            re.compile(
                r"(?:functions\.)?choose_target\b.*?[\"']target[\"']\s*:\s*[\"'](?P<target>\w+)[\"']",
                re.IGNORECASE | re.DOTALL,
            ),
            "TARGET:",
        ),
        (
            re.compile(r"\bcast_vote\s+on\s+(?P<target>\w+)\b", re.IGNORECASE),
            "VOTE:",
        ),
        (
            re.compile(r"\bchoose_target\s+on\s+(?P<target>\w+)\b", re.IGNORECASE),
            "TARGET:",
        ),
    ]
    for pattern, prefix in tool_trace_patterns:
        match = pattern.search(full_text)
        if match:
            return f"{prefix} {match.group('target')}"
    return None


def _append_unique_segment(segments: list[str], value: str) -> None:
    """Append a non-empty text segment once, preserving order."""
    cleaned = value.strip()
    if cleaned and cleaned not in segments:
        segments.append(cleaned)


def _extract_structured_tool_content(content) -> str | None:
    """Recover vote/target outputs from structured tool call/result content."""
    tool_name = (getattr(content, "name", None) or getattr(content, "tool_name", None) or "").strip()
    prefix = None
    if tool_name.endswith("cast_vote"):
        prefix = "VOTE:"
    elif tool_name.endswith("choose_target"):
        prefix = "TARGET:"

    if prefix:
        try:
            arguments = content.parse_arguments()
        except Exception:
            arguments = None
        if isinstance(arguments, dict):
            target = arguments.get("target")
            if isinstance(target, str) and target.strip():
                return f"{prefix} {target.strip()}"

    result = getattr(content, "result", None)
    if isinstance(result, str) and result.strip():
        normalized = _extract_tool_result(result)
        return normalized or result.strip()

    for item in getattr(content, "items", None) or []:
        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            normalized = _extract_tool_result(text)
            return normalized or text.strip()

    return None


def _serialize_agent_response(response) -> str:
    """
    Convert AgentResponse content into a parseable text form.

    ``AgentResponse.text`` only includes plain text content. Tool calls/results
    live in structured ``function_call`` / ``function_result`` contents, so vote
    or target actions can look blank unless we serialize those explicitly.
    """
    segments: list[str] = []
    response_text = getattr(response, "text", None)
    if isinstance(response_text, str):
        _append_unique_segment(segments, response_text)

    for message in getattr(response, "messages", []) or []:
        for content in getattr(message, "contents", []) or []:
            content_type = getattr(content, "type", None)
            if content_type == "text_reasoning":
                text = getattr(content, "text", None)
                if isinstance(text, str):
                    _append_unique_segment(segments, f"REASONING: {text}")
                continue

            if content_type in {"function_call", "function_result"}:
                structured = _extract_structured_tool_content(content)
                if structured:
                    _append_unique_segment(segments, structured)
                continue

            if content_type == "text":
                text = getattr(content, "text", None)
                if isinstance(text, str):
                    _append_unique_segment(segments, text)

    return "\n".join(segments)


async def run_agent_stream(
    agent,
    prompt: str,
    session: AgentSession | None = None,
    *,
    player_name: str = "unknown",
    prefer_non_stream: bool = False,
) -> tuple[str, str, AgentSession | None]:
    """
    Run an agent with streaming and return (reasoning, action, session).

    The third element is the refreshed AgentSession if ResilientSession-
    Middleware rebuilt the session due to a ``previous_response_id not found``
    error, otherwise None. Callers MUST update self.session when non-None
    so the next call does not re-trigger a recovery.

    When *session* is provided, MAF persists all messages (inputs and
    outputs) in the session's in-memory history.  This gives the agent
    genuine memory of every prior turn — it can reference what it and
    others actually said instead of confabulating.

    Corporate-speak enforcement for streaming calls is handled inline
    here (the agent middleware can only enforce it for non-streaming
    calls because the ResponseStream text is not available until after
    the stream is consumed).

    Wraps the call with error handling for common Azure
    Foundry issues such as missing model deployments (404).
    Retries up to _MAX_RETRIES times if the model returns a
    content-filter refusal or a corrupted response (e.g. REASONING
    leaked into the ACTION section with no real action text).

    Rate limiting: all API calls go through the global semaphore via
    rate_limited_call(), which handles 429 backoff automatically.
    """
    if player_name == "unknown":
        player_name = getattr(agent, "name", None) or player_name

    # Capture the session id before the call so we can look up any
    # replacement that ResilientSessionMiddleware registered.
    original_session_id: str | None = (
        session.session_id if session is not None else None
    )
    refreshed_session: AgentSession | None = None

    def _get_refreshed() -> AgentSession | None:
        """Pop and return a refreshed session if middleware replaced it."""
        nonlocal refreshed_session
        if refreshed_session is not None:
            latest = refreshed_session
            refreshed_session = None
            return latest
        if original_session_id is None:
            return None
        return _session_refresh_registry.pop(original_session_id, None)

    async def _do_stream_call() -> str:
        """Single streaming API call — returns the concatenated text."""
        chunks: list[str] = []
        async for chunk in agent.run(prompt, stream=True, session=session):
            if chunk.text:
                chunks.append(chunk.text)
        return "".join(chunks)

    async def _do_non_stream_call() -> str:
        """Non-streaming fallback — returns the response text."""
        result = await agent.run(prompt, stream=False, session=session)
        return _serialize_agent_response(result)

    def _parse_response_text(full_text: str) -> tuple[str, str]:
        """Normalize a raw model response into (reasoning, action)."""
        full_text = _strip_refusal(full_text)
        tool_result = _extract_tool_result(full_text)
        if tool_result:
            reasoning, _ = parse_reasoning_action(full_text)
            return reasoning, tool_result
        return parse_reasoning_action(full_text)

    async def _try_non_stream_recovery(
        reason: str,
    ) -> tuple[str, str, AgentSession | None] | None:
        """Attempt one non-stream fallback and return a parsed result on success."""
        if not MAFIA_ENABLE_STREAMING_FALLBACK:
            return None
        logger.info("[%s] %s — retrying non-streaming", player_name, reason)
        try:
            full_text = await rate_limited_call(
                _do_non_stream_call,
                player_name=player_name,
            )
            reasoning, action = _parse_response_text(full_text)
            if action.strip():
                return reasoning, action, _get_refreshed()
        except Exception:
            return None
        return None

    corporate_retried = False
    for attempt in range(_MAX_RETRIES + 1):
        try:
            call_factory = _do_non_stream_call if prefer_non_stream else _do_stream_call
            full_text = await rate_limited_call(
                call_factory,
                player_name=player_name,
            )

            # If the response contains a refusal and we have retries left,
            # try again with the same prompt.
            if _contains_refusal(full_text) and attempt < _MAX_RETRIES:
                continue

            reasoning, action = _parse_response_text(full_text)

            # If the action is empty (e.g. REASONING leaked into ACTION
            # with no real action), retry when possible.
            if not action.strip() and attempt < _MAX_RETRIES:
                continue
            if not action.strip():
                fallback = await _try_non_stream_recovery(
                    "Empty or malformed streaming action",
                )
                if fallback is not None:
                    return fallback

            # Corporate-speak enforcement for streaming: the middleware
            # cannot check streaming responses, so we do it here once.
            if (
                not prefer_non_stream
                and not corporate_retried
                and attempt < _MAX_RETRIES
                and _count_corporate(action) >= _CORPORATE_THRESHOLD
            ):
                corporate_retried = True
                prompt = CORPORATE_ENFORCEMENT_HINT + "\n\n" + prompt
                continue

            return reasoning, action, _get_refreshed()
        except Exception as exc:
            if session is not None and _is_session_expired_error(exc):
                logger.warning(
                    "[%s] Session expired during %s call; rebuilding locally",
                    player_name,
                    "non-stream" if prefer_non_stream else "streaming",
                )
                history_summary = _summarize_history(
                    _extract_history_from_session(session),
                )
                new_session = _refresh_session(session, history_summary)
                refreshed_session = new_session
                SessionHealthMonitor.remove(session.session_id)
                SessionHealthMonitor.touch(new_session.session_id)
                session = new_session
                if not prefer_non_stream and attempt >= _MAX_RETRIES:
                    fallback = await _try_non_stream_recovery(
                        "Session expired after streaming retries",
                    )
                    if fallback is not None:
                        return fallback
                continue

            # Streaming fallback: retry as non-streaming on rate-limit
            if (
                not prefer_non_stream
                and MAFIA_ENABLE_STREAMING_FALLBACK
                and (_is_rate_limit_error(exc) or _is_timeout_error(exc))
                and attempt < _MAX_RETRIES
            ):
                logger.info(
                    "[%s] Streaming failed, retrying non-streaming: %s",
                    player_name, exc,
                )
                try:
                    fallback = await _try_non_stream_recovery(str(exc))
                    if fallback is not None:
                        return fallback
                except Exception:
                    pass  # Fall through to normal error handling

            _handle_api_error(exc)
            raise

    # Should not reach here, but satisfy the type checker:
    return "", "", _get_refreshed()


def _handle_api_error(exc: Exception) -> None:
    """Print a user-friendly diagnostic for known API errors."""
    msg = str(exc)
    if "DeploymentNotFound" in msg or "does not exist" in msg:
        print(
            "\n\033[91m\033[1m"
            "ERROR: Model deployment not found.\033[0m\n"
            "The Azure AI Foundry deployment name in your configuration "
            "does not match any active deployment in your project.\n\n"
            "How to fix:\n"
            "  1. Open Azure AI Foundry and check your deployed model names.\n"
            "  2. Set FOUNDRY_MODEL in your\n"
            "     .env file to match an active deployment name.\n"
            "  3. If you just created a deployment, wait ~5 minutes and retry.\n"
            "  4. Run  python check.py  to verify connectivity.\n",
            file=sys.stderr,
        )
