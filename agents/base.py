"""Shared utilities for all game agents."""

import sys


def parse_reasoning_action(text: str) -> tuple[str, str]:
    """
    Splits the model output on ACTION:.
    Returns (reasoning_text, action_text).
    If no ACTION: marker found, returns ("", full_text).
    """
    text = text.strip()
    if "ACTION:" in text:
        parts    = text.split("ACTION:", 1)
        reasoning = parts[0].replace("REASONING:", "").strip()
        action    = parts[1].strip()
        return reasoning, action
    # Fallback: no marker - treat whole response as the action
    return "", text


async def run_agent_stream(agent, prompt: str) -> tuple[str, str]:
    """
    Run an agent with streaming and return (reasoning, action).

    Wraps the streaming call with error handling for common Azure
    Foundry issues such as missing model deployments (404).
    """
    try:
        full_text = ""
        async for chunk in agent.run(prompt, stream=True):
            if chunk.text:
                full_text += chunk.text
        return parse_reasoning_action(full_text)
    except Exception as exc:
        _handle_api_error(exc)
        raise


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
