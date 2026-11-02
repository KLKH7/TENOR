"""Timeouts and retries for every Anthropic API call Concentrate makes.

Why this exists: the SDK's defaults are a 600 s read timeout and 2 retries (we used to pass
``max_retries=4``), and its retries log only at INFO. A connection that dies silently (no FIN,
no RST) could therefore block a call for up to 5 x 600 s = 50 minutes with nothing printed:
the likely cause of three ingest builds freezing at 0% CPU on 2026-11-02. See ``docs/HARDENING.md``.

The policy here:

* **Timeouts** (:func:`timeout_for`): connect fast (10 s); the read timeout scales with
  ``max_tokens`` (120-300 s) because a non-streaming call sends no bytes until the whole reply
  is generated.
* **Retries** (:func:`call_with_retries` / :func:`acall_with_retries`): our own loop, with the
  SDK's built-in retries switched off (``max_retries=0``) so the two never multiply. Retries
  timeouts, dropped connections, 408/409/429/5xx/529; never 400/401/403/404/413 (retrying a bad
  request cannot help). Exponential backoff with full jitter, honouring ``Retry-After``, under a
  per-call wall-clock deadline. Every retry is logged at WARNING so a stall is never silent.

Idempotency: a retried Messages call is safe for *our* state because nothing is cached or
accounted until a complete reply arrives. It is not free: the server may have generated (and
billed) the attempt we gave up on. That unseen spend cannot be measured client-side, so the
ledger records only replies we received; the WARNING log lines count abandoned attempts.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

import httpx
import httpx2  # the HTTP library the anthropic SDK (>= 2.x) is built on

from concentrate.watchdog import beat

logger = logging.getLogger("concentrate.llm")

T = TypeVar("T")

CONNECT_TIMEOUT_S = 10.0
WRITE_TIMEOUT_S = 60.0
POOL_TIMEOUT_S = 30.0
MIN_READ_TIMEOUT_S = 120.0
MAX_READ_TIMEOUT_S = 300.0
# Non-streaming: the first response byte arrives only after the last output token, so the read
# timeout must cover the whole generation. Measured on our cache (2026-11-02, 461 timed calls):
# p99 latency 12 s, max 32 s, max output 2.4k tokens, slowest throughput ~39 tok/s. We budget
# 60 s of queueing plus 1/60 s per allowed output token, clamped to [120, 300] s: a 16k-token call
# gets 300 s, room for ~9k output tokens at the slowest rate seen (4x the largest reply).
READ_BASE_S = 60.0
READ_S_PER_TOKEN = 1 / 60

# Status codes worth retrying: timeout, lock conflict, rate limit, server errors, overloaded.
RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


def timeout_for(max_tokens: int) -> httpx2.Timeout:
    """An explicit SDK timeout for a non-streaming call allowed ``max_tokens`` of output."""
    read = READ_BASE_S + max_tokens * READ_S_PER_TOKEN
    read = min(MAX_READ_TIMEOUT_S, max(MIN_READ_TIMEOUT_S, read))
    return httpx2.Timeout(
        read, connect=CONNECT_TIMEOUT_S, write=WRITE_TIMEOUT_S, pool=POOL_TIMEOUT_S
    )


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to try one API call. Defaults bound a dead call to ~``deadline_s``."""

    max_attempts: int = 5  # first try + 4 retries
    base_s: float = 2.0  # backoff before retry n is uniform(0, min(cap, base * 2**n))
    cap_s: float = 60.0
    deadline_s: float = 1200.0  # no retry would start after this much wall time

    def backoff(self, retry: int, rng: random.Random) -> float:
        """Full-jitter exponential backoff before retry number ``retry`` (0-based)."""
        return rng.uniform(0.0, min(self.cap_s, self.base_s * (2**retry)))


DEFAULT_POLICY = RetryPolicy()


def status_of(exc: BaseException) -> int | None:
    """The HTTP status carried by an SDK error, if any."""
    status = getattr(exc, "status_code", None)
    return int(status) if isinstance(status, int) else None


def is_retryable(exc: BaseException) -> bool:
    """True for transient failures: timeouts, dropped connections and retryable statuses."""
    try:
        import anthropic
    except ImportError:  # pragma: no cover - the SDK is a hard dependency
        anthropic = None  # type: ignore[assignment]
    if anthropic is not None and isinstance(
        exc, anthropic.APITimeoutError | anthropic.APIConnectionError
    ):
        return True  # APITimeoutError subclasses APIConnectionError; both are transient
    if isinstance(
        exc,
        httpx.TimeoutException
        | httpx.TransportError
        | httpx2.TimeoutException
        | httpx2.TransportError
        | TimeoutError,
    ):
        return True
    status = status_of(exc)
    return status is not None and status in RETRY_STATUS


def retry_after_s(exc: BaseException) -> float | None:
    """Seconds the server asked us to wait (``retry-after-ms`` / ``retry-after``), if given."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    for name, scale in (("retry-after-ms", 0.001), ("retry-after", 1.0)):
        value = headers.get(name)
        if value is None:
            continue
        try:
            return max(0.0, float(value) * scale)
        except ValueError:
            continue  # an HTTP-date Retry-After: fall back to our own backoff
    return None


def _next_delay(exc: BaseException, retry: int, policy: RetryPolicy, rng: random.Random) -> float:
    hinted = retry_after_s(exc)
    if hinted is not None:
        return min(hinted, policy.cap_s)
    return policy.backoff(retry, rng)


def _give_up(
    exc: BaseException, attempt: int, elapsed: float, delay: float, p: RetryPolicy
) -> bool:
    return not is_retryable(exc) or attempt + 1 >= p.max_attempts or elapsed + delay > p.deadline_s


def call_with_retries(
    fn: Callable[[], T],
    *,
    what: str,
    policy: RetryPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    rng: random.Random | None = None,
) -> T:
    """Call ``fn()``; on a transient error back off and retry under ``policy``.

    ``fn`` must have no side effects on our state before it returns (true of an SDK call), so
    a failed attempt leaves nothing to undo. The final error is re-raised unchanged.
    ``sleep`` defaults to ``time.sleep``, looked up at call time (patchable in tests).
    """
    sleep = sleep or time.sleep
    rng = rng or random.Random()
    start = clock()
    for attempt in range(policy.max_attempts):
        try:
            result = fn()
            beat()
            return result
        except Exception as exc:
            delay = _next_delay(exc, attempt, policy, rng)
            if _give_up(exc, attempt, clock() - start, delay, policy):
                raise
            _log_retry(what, exc, attempt, policy, delay)
            beat()  # alive and handling it; a dead network ends at the deadline, not here
            sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


async def acall_with_retries(
    fn: Callable[[], Awaitable[T]],
    *,
    what: str,
    policy: RetryPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    clock: Callable[[], float] = time.monotonic,
    rng: random.Random | None = None,
) -> T:
    """Async twin of :func:`call_with_retries` (Graphiti's client is async)."""
    sleep = sleep or asyncio.sleep
    rng = rng or random.Random()
    start = clock()
    for attempt in range(policy.max_attempts):
        try:
            result = await fn()
            beat()
            return result
        except Exception as exc:
            delay = _next_delay(exc, attempt, policy, rng)
            if _give_up(exc, attempt, clock() - start, delay, policy):
                raise
            _log_retry(what, exc, attempt, policy, delay)
            beat()
            await sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


def _log_retry(what: str, exc: BaseException, attempt: int, p: RetryPolicy, delay: float) -> None:
    logger.warning(
        "%s failed (%s%s); retry %d/%d in %.1fs",
        what,
        type(exc).__name__,
        f" {status_of(exc)}" if status_of(exc) is not None else "",
        attempt + 1,
        p.max_attempts - 1,
        delay,
    )


def anthropic_client(max_tokens: int = 16000, **kwargs: Any) -> Any:
    """A sync ``anthropic.Anthropic`` with explicit timeouts and SDK retries off.

    Retries happen in :func:`call_with_retries` around each call instead. Extra ``kwargs``
    (e.g. ``http_client`` in tests) are passed through.
    """
    import anthropic

    return anthropic.Anthropic(timeout=timeout_for(max_tokens), max_retries=0, **kwargs)


def async_anthropic_client(max_tokens: int = 16000, **kwargs: Any) -> Any:
    """Async twin of :func:`anthropic_client`."""
    import anthropic

    return anthropic.AsyncAnthropic(timeout=timeout_for(max_tokens), max_retries=0, **kwargs)
