"""Shared utilities for all game agents."""

import re
import sys

from agent_framework import AgentSession


def format_discussion_prompt(history: list[str], agent_name: str) -> str:
    """
    Format discussion history to encourage conversational responses.

    Separates the last message from earlier discussion so the agent
    knows exactly who just spoke and what they said — making it natural
    to respond TO that person rather than monologuing past them.
    """
    if not history:
        return (
            "Discussion:\nNobody has spoken yet. You are first.\n"
            "Pick someone by name and ask them a direct question, "
            "or throw out a concrete suspicion with a reason."
        )

    if len(history) == 1:
        last_speaker = _extract_name(history[0])
        if last_speaker == agent_name:
            return (
                f"Discussion:\n{history[0]}\n\n"
                f"^ That was you. Wait for others to respond, then react."
            )
        return (
            f"Discussion:\n{history[0]}\n\n"
            f"^ {last_speaker} just spoke. "
            f"Respond directly to what they said."
        )

    earlier = "\n".join(history[:-1])
    last = history[-1]
    last_speaker = _extract_name(last)

    if last_speaker == agent_name:
        # Find the last message from someone else
        other_msgs = [h for h in history if not h.startswith(f"{agent_name}:")]
        if other_msgs:
            respond_to = other_msgs[-1]
            respond_name = _extract_name(respond_to)
            return (
                f"Earlier discussion:\n{earlier}\n\n"
                f"LAST MESSAGE:\n{last}\n\n"
                f"^ That was you. Respond to what {respond_name} said earlier, "
                f"or address someone else in the room."
            )
        return (
            f"Full discussion:\n{chr(10).join(history)}\n\n"
            f"Address someone specific by name."
        )

    return (
        f"Earlier discussion:\n{earlier}\n\n"
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


def parse_reasoning_action(text: str) -> tuple[str, str]:
    """
    Splits the model output on ACTION:.
    Returns (reasoning_text, action_text).
    If no ACTION: marker found, returns ("", full_text).

    Uses the *last* ACTION: marker so that when the model mistakenly
    writes  ACTION: REASONING: <thoughts> ACTION: <real action>
    we still recover the real action from the final marker.

    If REASONING: leaks into the action section with no subsequent
    ACTION: marker, the action is treated as empty so the caller
    can retry.
    """
    text = text.strip()
    if "ACTION:" not in text:
        # Fallback: no marker - treat whole response as the action
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

    # If REASONING: still leaked into the action section (the model
    # wrote only one ACTION: followed by REASONING:), the real action
    # is missing.  Absorb the text into reasoning and return an empty
    # action so the retry logic can fire.
    if action.upper().startswith("REASONING:"):
        leaked = action[len("REASONING:"):].strip()
        reasoning = f"{reasoning} {leaked}".strip()
        action = ""

    return reasoning, action


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

    Corporate-speak enforcement is now handled by agent_middleware
    (agents/middleware.py) registered on the Agent — not here.

    Wraps the streaming call with error handling for common Azure
    Foundry issues such as missing model deployments (404).
    Retries up to _MAX_RETRIES times if the model returns a
    content-filter refusal or a corrupted response (e.g. REASONING
    leaked into the ACTION section with no real action text).
    """
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
