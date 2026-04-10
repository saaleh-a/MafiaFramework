"""
agents/rate_limiter.py
-----------------------
Two-tier rate limiting and exponential backoff for Azure Foundry API calls.

Global tier: process-wide asyncio.Semaphore limiting concurrent API calls.
Phase tier:  per-phase semaphore in the orchestrator (see engine/orchestrator.py).

Exponential backoff with full jitter prevents thundering-herd on recovery
from 429 (Too Many Requests) errors.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

from config.settings import (
    MAFIA_MAX_CONCURRENT_CALLS,
    MAFIA_RATE_LIMIT_RETRIES,
    MAFIA_BACKOFF_BASE_DELAY,
)

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------ #
#  Global semaphore — limits concurrent API calls process-wide         #
# ------------------------------------------------------------------ #

_global_semaphore: asyncio.Semaphore | None = None


def get_global_semaphore() -> asyncio.Semaphore:
    """
    Return (and lazily create) the process-wide API-call semaphore.

    Lazy creation is required because asyncio.Semaphore is bound to the
    running event loop; importing the module at load-time would fail if
    no loop is active yet.
    """
    global _global_semaphore
    if _global_semaphore is None:
        _global_semaphore = asyncio.Semaphore(MAFIA_MAX_CONCURRENT_CALLS)
    return _global_semaphore


# ------------------------------------------------------------------ #
#  Error classification                                                #
# ------------------------------------------------------------------ #

def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True if *exc* is a 429 Too Many Requests error."""
    msg = str(exc).lower()
    if "429" in msg or "too many requests" in msg or "rate limit" in msg:
        return True
    # Check for openai-style status_code attribute
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return status == 429


def _is_server_error(exc: Exception) -> bool:
    """Return True if *exc* is a 5xx server error (fail fast)."""
    msg = str(exc)
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and 500 <= status < 600:
        return True
    # Heuristic for string messages
    for code in ("500", "502", "503", "504"):
        if code in msg:
            return True
    return False


def _is_timeout_error(exc: Exception) -> bool:
    """Return True if *exc* is a timeout/connection error."""
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True
    msg = str(exc).lower()
    return "timeout" in msg or "timed out" in msg


# ------------------------------------------------------------------ #
#  Backoff calculation                                                 #
# ------------------------------------------------------------------ #

def _backoff_delay(attempt: int) -> float:
    """
    Exponential backoff with full jitter.

    Base delay 1s, multiplier 2x per attempt, cap at 8s.
    Full jitter: add random 0–0.5s to prevent thundering herd.
    """
    base = MAFIA_BACKOFF_BASE_DELAY
    delay = min(base * (2 ** attempt), 8.0)
    jitter = random.uniform(0, 0.5)
    return delay + jitter


# ------------------------------------------------------------------ #
#  Retry wrapper                                                       #
# ------------------------------------------------------------------ #

# Per-player retry counters for observability
_retry_counters: dict[str, int] = {}


def get_retry_stats() -> dict[str, int]:
    """Return a copy of per-player retry counters."""
    return dict(_retry_counters)


async def rate_limited_call(
    coro_factory: "Callable[[], Awaitable]",
    *,
    player_name: str = "unknown",
):
    """
    Execute *coro_factory()* with rate limiting and exponential backoff.

    *coro_factory* is a zero-argument callable that returns an awaitable.
    It is called fresh on each attempt (retries create a new coroutine).

    Behaviour per error type:
      - 429 (rate limit): retry up to MAFIA_RATE_LIMIT_RETRIES times
      - 5xx (server):     fail immediately (no retry)
      - timeout:          retry once
      - other:            propagate immediately

    Returns the result of a successful call.
    Raises the last exception if all retries are exhausted.
    """
    max_retries = MAFIA_RATE_LIMIT_RETRIES
    sem = get_global_semaphore()
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            async with sem:
                return await coro_factory()
        except Exception as exc:
            last_exc = exc

            if _is_server_error(exc):
                logger.warning(
                    "[%s] Server error (5xx) — failing fast: %s",
                    player_name, exc,
                )
                raise

            if _is_timeout_error(exc):
                if attempt >= 1:
                    logger.warning(
                        "[%s] Timeout on retry — failing: %s",
                        player_name, exc,
                    )
                    raise
                delay = _backoff_delay(attempt)
                logger.info(
                    "[%s] Timeout — retrying once after %.1fs",
                    player_name, delay,
                )
                _retry_counters[player_name] = _retry_counters.get(player_name, 0) + 1
                await asyncio.sleep(delay)
                continue

            if _is_rate_limit_error(exc):
                if attempt >= max_retries:
                    logger.warning(
                        "[%s] Rate limit — max retries (%d) exhausted",
                        player_name, max_retries,
                    )
                    raise
                delay = _backoff_delay(attempt)
                logger.info(
                    "[%s] Rate limited (429) — retry %d/%d after %.1fs",
                    player_name, attempt + 1, max_retries, delay,
                )
                _retry_counters[player_name] = _retry_counters.get(player_name, 0) + 1
                await asyncio.sleep(delay)
                continue

            # Unknown error — propagate
            raise

    # Should not reach here, but satisfy the type checker
    if last_exc:
        raise last_exc
    return None
