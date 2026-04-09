"""Shared utilities for all game agents."""

import re
import sys

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
    Also strips any REASONING: marker that leaks into the action section.
    """
    text = text.strip()
    if "ACTION:" in text:
        parts    = text.split("ACTION:", 1)
        reasoning = parts[0].replace("REASONING:", "").strip()
        action    = parts[1].strip()
        # Strip REASONING: that leaked into the action section
        if action.startswith("REASONING:"):
            action = action.split("REASONING:", 1)[1].strip()
        return reasoning, action
    # Fallback: no marker - treat whole response as the action
    return "", text


async def run_agent_stream(agent, prompt: str) -> tuple[str, str]:
    """
    Run an agent with streaming and return (reasoning, action).

    Wraps the streaming call with error handling for common Azure
    Foundry issues such as missing model deployments (404).
    Retries up to _MAX_RETRIES times if the model returns a
    content-filter refusal.
    """
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            full_text = ""
            async for chunk in agent.run(prompt, stream=True):
                if chunk.text:
                    full_text += chunk.text

            # If the response contains a refusal and we have retries left,
            # try again with the same prompt.
            if _contains_refusal(full_text) and attempt < _MAX_RETRIES:
                continue

            # Best-effort: strip any residual refusal fragments
            full_text = _strip_refusal(full_text)
            return parse_reasoning_action(full_text)
        except Exception as exc:
            last_exc = exc
            _handle_api_error(exc)
            raise

    # Should not reach here, but just in case:
    if last_exc:
        raise last_exc
    return parse_reasoning_action(_strip_refusal(full_text))


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
            "  2. Set FOUNDRY_MODEL (and optionally FOUNDRY_MODEL_4O) in your\n"
            "     .env file to match an active deployment name.\n"
            "  3. If you just created a deployment, wait ~5 minutes and retry.\n"
            "  4. Run  python check.py  to verify connectivity.\n",
            file=sys.stderr,
        )
