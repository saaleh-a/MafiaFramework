import os
from dotenv import load_dotenv

load_dotenv()

# Env vars matching the MAF FoundryChatClient conventions
FOUNDRY_PROJECT_ENDPOINT: str = os.environ.get("FOUNDRY_PROJECT_ENDPOINT", "")
FOUNDRY_MODEL: str = os.environ.get("FOUNDRY_MODEL", "gpt-4o-mini")

# ------------------------------------------------------------------ #
#  Rate-limiting & resilience configuration                            #
# ------------------------------------------------------------------ #

def _int_env(key: str, default: int, max_val: int) -> int:
    """Read an integer env var, clamped to [1, max_val]."""
    raw = os.environ.get(key, "")
    if not raw:
        return default
    try:
        return max(1, min(int(raw), max_val))
    except ValueError:
        return default


def _float_env(key: str, default: float) -> float:
    """Read a float env var with fallback."""
    raw = os.environ.get(key, "")
    if not raw:
        return default
    try:
        return max(0.1, float(raw))
    except ValueError:
        return default


# Maximum concurrent API calls (global semaphore)
MAFIA_MAX_CONCURRENT_CALLS: int = _int_env("MAFIA_MAX_CONCURRENT_CALLS", 5, 10)

# Number of retries for rate-limited (429) API calls
MAFIA_RATE_LIMIT_RETRIES: int = _int_env("MAFIA_RATE_LIMIT_RETRIES", 3, 5)

# Base delay (seconds) for exponential backoff
MAFIA_BACKOFF_BASE_DELAY: float = _float_env("MAFIA_BACKOFF_BASE_DELAY", 1.0)

# If true, retry failed streaming calls as non-streaming
MAFIA_ENABLE_STREAMING_FALLBACK: bool = (
    os.environ.get("MAFIA_ENABLE_STREAMING_FALLBACK", "").lower() in ("1", "true", "yes")
)
