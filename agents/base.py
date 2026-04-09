"""Shared utilities for all game agents."""


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
