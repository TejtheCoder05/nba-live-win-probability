"""Shared reliability wrapper around ``nba_api`` endpoint calls.

Every request to stats.nba.com in this project goes through :func:`call_endpoint`.
That endpoint is unofficial and flaky: it times out regularly, occasionally
returns transient 5xx errors, and will throttle a client that hammers it. Rather
than scattering try/except and ``time.sleep`` across every module, all of that
policy lives here in one place:

* an explicit request timeout (``nba_api`` defaults to 30s but we set it openly)
* retries with exponential backoff plus jitter
* a polite minimum delay between consecutive requests
* logging of each attempt so failures are visible rather than silent

Phase 1 only makes two requests, so this is mild overkill today. It exists now
because the Phase 2 bulk downloader (thousands of games) needs exactly this
primitive, and building it once means the download logic can focus on caching
and resumption instead of re-inventing retry policy.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Any, TypeVar

import requests

logger = logging.getLogger(__name__)

EndpointT = TypeVar("EndpointT")

# --- Request policy defaults -------------------------------------------------
# Tuned to be conservative: we would much rather download slowly than get the
# client's IP temporarily blocked partway through a multi-season download.
DEFAULT_TIMEOUT = 60.0  # seconds to wait for a single HTTP response
DEFAULT_MAX_ATTEMPTS = 5  # total tries, including the first
DEFAULT_BACKOFF_BASE = 2.0  # seconds; doubles each retry
DEFAULT_BACKOFF_CAP = 60.0  # never sleep longer than this between retries
DEFAULT_REQUEST_SPACING = 0.6  # minimum gap between any two requests

# Exceptions worth retrying. These are transient network/server conditions,
# as opposed to e.g. a malformed GAME_ID, which will fail identically forever.
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.HTTPError,
    ValueError,  # nba_api raises this when it gets an unparseable/empty body
)

# Timestamp of the most recent request, used to enforce request spacing.
_last_request_time: float = 0.0


def _sleep_for_request_spacing(spacing: float) -> None:
    """Block until at least ``spacing`` seconds have passed since the last call."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if _last_request_time and elapsed < spacing:
        time.sleep(spacing - elapsed)
    _last_request_time = time.monotonic()


def _backoff_delay(attempt: int, base: float, cap: float) -> float:
    """Exponential backoff with jitter.

    ``attempt`` is 1-based. Jitter (a random 0-100% of the computed delay) keeps
    repeated failures from retrying in lockstep, which matters once the Phase 2
    downloader is running many requests in sequence.
    """
    raw = base * (2 ** (attempt - 1))
    return min(raw, cap) * (0.5 + random.random() * 0.5)


def call_endpoint(
    endpoint_cls: type[EndpointT],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_base: float = DEFAULT_BACKOFF_BASE,
    backoff_cap: float = DEFAULT_BACKOFF_CAP,
    request_spacing: float = DEFAULT_REQUEST_SPACING,
    **endpoint_kwargs: Any,
) -> EndpointT:
    """Instantiate an ``nba_api`` endpoint, retrying on transient failures.

    ``nba_api`` endpoints perform their HTTP request inside ``__init__``, so
    "calling the endpoint" and "constructing the object" are the same action.
    This returns the constructed endpoint object, leaving the caller to pull
    whichever named data set it wants off of it.

    Raises the final exception if every attempt fails.
    """
    label = f"{endpoint_cls.__name__}({endpoint_kwargs})"
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        _sleep_for_request_spacing(request_spacing)
        try:
            logger.info("Requesting %s (attempt %d/%d)", label, attempt, max_attempts)
            return endpoint_cls(timeout=timeout, **endpoint_kwargs)
        except RETRYABLE_EXCEPTIONS as exc:
            last_error = exc
            if attempt == max_attempts:
                break
            delay = _backoff_delay(attempt, backoff_base, backoff_cap)
            logger.warning(
                "%s failed (%s: %s); retrying in %.1fs",
                label,
                type(exc).__name__,
                exc,
                delay,
            )
            time.sleep(delay)

    logger.error("%s failed after %d attempts", label, max_attempts)
    raise RuntimeError(f"{label} failed after {max_attempts} attempts") from last_error
