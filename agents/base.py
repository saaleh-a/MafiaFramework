"""Shared utilities for all game agents."""

import re
import sys

from agent_framework import AgentSession
from prompts.archetypes import CORPORATE_WORDS
from agents.middleware import _CORPORATE_THRESHOLD, CORPORATE_ENFORCEMENT_HINT


def format_discussion_prompt(history: list[str], agent_name: str) -> str:
    """
    Format discussion history to encourage conversational responses.

    ARCHITECTURE: The injected discussion_history contains ONLY other
    agents' messages from the current round's discussion. The agent's
    own prior turns are handled by InMemoryHistoryProvider and do not
    appear here. This avoids redundancy and context confusion.

    Separates the last message from earlier discussion so the agent
    knows exactly who just spoke and what they said — making it natural
    to respond TO that person rather than monologuing past them.
    """
    # Filter out this agent's own messages — their history provider
    # already supplies their own prior turns.
    others_history = [
        h for h in history if not h.startswith(f"{agent_name}:")
    ]

    if not others_history:
        return (
            "What others have said in this round's discussion:\n"
            "Nobody else has spoken yet. You are first.\n"
            "Pick someone by name and ask them a direct question, "
            "or throw out a concrete suspicion with a reason."
        )

    if len(others_history) == 1:
        last_speaker = _extract_name(others_history[0])
        return (
            f"What others have said in this round's discussion:\n"
            f"{others_history[0]}\n\n"
            f"^ {last_speaker} just spoke. "
            f"Respond directly to what they said."
        )

    earlier = "\n".join(others_history[:-1])
    last = others_history[-1]
    last_speaker = _extract_name(last)

    return (
        f"What others have said in this round's discussion:\n{earlier}\n\n"
        f"LAST MESSAGE (respond to this):\n{last}\n\n"
        f"^ {last_speaker} just said that. Talk TO them. "
        f"Agree, disagree, ask a follow-up, or challenge them directly."
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
        if prefix in full_text:
            return full_text.split(prefix, 1)[1].strip()
    return None


async def run_agent_stream(
    agent,
    prompt: str,
    session: AgentSession | None = None,
) -> tuple[str, str]:
    """
    Run an agent with streaming and return (reasoning, action).

    When *session* is provided, MAF persists all messages (inputs and
    outputs) in the session's in-memory history.  This gives the agent
    genuine memory of every prior turn — it can reference what it and
    others actually said instead of confabulating.

    Corporate-speak enforcement for streaming calls is handled inline
    here (the agent middleware can only enforce it for non-streaming
    calls because the ResponseStream text is not available until after
    the stream is consumed).

    Wraps the streaming call with error handling for common Azure
    Foundry issues such as missing model deployments (404).
    Retries up to _MAX_RETRIES times if the model returns a
    content-filter refusal or a corrupted response (e.g. REASONING
    leaked into the ACTION section with no real action text).
    """
    corporate_retried = False
    for attempt in range(_MAX_RETRIES + 1):
        try:
            full_text = ""
            async for chunk in agent.run(prompt, stream=True, session=session):
                if chunk.text:
                    full_text += chunk.text

            # If the response contains a refusal and we have retries left,
            # try again with the same prompt.
            if _contains_refusal(full_text) and attempt < _MAX_RETRIES:
                continue

            # Best-effort: strip any residual refusal fragments
            full_text = _strip_refusal(full_text)

            # Check for structured tool call results first
            tool_result = _extract_tool_result(full_text)
            if tool_result:
                reasoning, _ = parse_reasoning_action(full_text)
                return reasoning, tool_result

            reasoning, action = parse_reasoning_action(full_text)

            # If the action is empty (e.g. REASONING leaked into ACTION
            # with no real action), retry when possible.
            if not action.strip() and attempt < _MAX_RETRIES:
                continue

            # Corporate-speak enforcement for streaming: the middleware
            # cannot check streaming responses, so we do it here once.
            if (
                not corporate_retried
                and attempt < _MAX_RETRIES
                and _count_corporate(action) >= _CORPORATE_THRESHOLD
            ):
                corporate_retried = True
                prompt = CORPORATE_ENFORCEMENT_HINT + "\n\n" + prompt
                continue

            return reasoning, action
        except Exception as exc:
            _handle_api_error(exc)
            raise

    # Should not reach here, but satisfy the type checker:
    return "", ""


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
