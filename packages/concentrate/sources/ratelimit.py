"""Client-side rate limiting for the Granola public API.

Granola documents two limits: a burst of 25 requests per 5 seconds and a sustained 5 req/s.
We enforce both, conservatively, with two independent constraints that must *both* admit a
request before it is sent:

* a token bucket (capacity 25, refill 5 tokens/s) — the classic burst + sustained-rate model;
* a sliding-window log (at most 25 requests in any 5-second window).

The token bucket alone would allow ~50 requests in the first 5 s (25 burst + 25 refilled),
which a strict server-side "25 per 5 s window" would reject. The sliding window alone already
caps the long-run rate at 5 req/s. Combining them costs nothing and is safe under either
reading of the docs. See docs/engineering/DESIGN.md ("Rate limiting").

Time is injected through :class:`Clock` so tests run on a simulated clock.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

# Float tolerance for time comparisons. Without it a residual wait smaller than the clock's
# float resolution (t + eps == t) would make ``acquire`` spin forever.
_EPS = 1e-9


class Clock(Protocol):
    """Monotonic time source with a sleep primitive."""

    def now(self) -> float:
        """Return monotonic seconds."""
        ...

    def sleep(self, seconds: float) -> None:
        """Block for ``seconds``."""
        ...


class SystemClock:
    """Real wall-clock implementation of :class:`Clock`."""

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


@dataclass
class FakeClock:
    """Deterministic clock for tests: ``sleep`` advances time instantly and is recorded."""

    t: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            self.sleeps.append(seconds)
            self.t += seconds


class RateLimiter:
    """Token bucket + sliding window limiter. Call :meth:`acquire` before every request."""

    def __init__(
        self,
        clock: Clock,
        *,
        burst: int = 25,
        window_s: float = 5.0,
        rate_per_s: float = 5.0,
    ) -> None:
        self._clock = clock
        self._capacity = float(burst)
        self._rate = rate_per_s
        self._window_s = window_s
        self._window_max = burst
        self._tokens = float(burst)
        self._last = clock.now()
        self._sent: deque[float] = deque()

    def _refill(self, now: float) -> None:
        self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
        self._last = now
        while self._sent and now - self._sent[0] >= self._window_s - _EPS:
            self._sent.popleft()

    def _wait_time(self, now: float) -> float:
        bucket_wait = 0.0 if self._tokens >= 1.0 - _EPS else (1.0 - self._tokens) / self._rate
        window_wait = 0.0
        if len(self._sent) >= self._window_max:
            window_wait = self._sent[0] + self._window_s - now
        return max(bucket_wait, window_wait)

    def acquire(self) -> float:
        """Block until a request may be sent, consume one slot, and return seconds waited."""
        waited = 0.0
        while True:
            now = self._clock.now()
            self._refill(now)
            wait = self._wait_time(now)
            if wait <= _EPS:
                self._tokens = max(0.0, self._tokens - 1.0)
                self._sent.append(now)
                return waited
            self._clock.sleep(wait)
            waited += wait

    def penalize(self, seconds: float) -> None:
        """Back off after a server 429: drain the bucket and pause for ``seconds``."""
        self._tokens = 0.0
        self._clock.sleep(seconds)
        self._last = self._clock.now()
