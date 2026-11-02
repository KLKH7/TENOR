"""A progress heartbeat and a stall watchdog for long runs (``concentrate ingest`` / ``eval``).

Code that makes progress calls :func:`beat` (``concentrate.llm`` does so after every API reply,
retry and cache hit). A :class:`Watchdog` thread checks how long it has been since the last beat;
past ``stall_s`` it logs loudly, dumps every thread's stack to stderr (so we can see *where* it
hung), and hard-exits with :data:`STALL_EXIT_CODE` so a wrapper (``until ...; do ...; done``,
systemd, a shell loop) can restart it. Builds resume from ``progress.jsonl``, so a restart
loses at most the item in flight.

``os._exit`` is deliberate: the main thread is typically blocked inside a socket read, where
``sys.exit`` from another thread would never be seen.
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from types import TracebackType

logger = logging.getLogger("concentrate.watchdog")

STALL_EXIT_CODE = 75  # EX_TEMPFAIL (sysexits.h): "temporary failure; try again"

_lock = threading.Lock()
_last_beat = time.monotonic()
_beats = 0


def beat() -> None:
    """Record that the process made progress just now. Cheap; safe from any thread."""
    global _last_beat, _beats
    with _lock:
        _last_beat = time.monotonic()
        _beats += 1


def last_beat() -> tuple[float, int]:
    """``(monotonic time of the last beat, number of beats so far)``."""
    with _lock:
        return _last_beat, _beats


def _hard_exit(code: int) -> None:  # pragma: no cover - replaced in tests
    sys.stderr.flush()
    os._exit(code)


class Watchdog:
    """Exit the process if :func:`beat` is not called for ``stall_s`` seconds.

    Use as a context manager around the long loop. ``clock``, ``on_stall`` and ``check_s`` are
    injectable so tests run in milliseconds without killing the test process.
    """

    def __init__(
        self,
        stall_s: float,
        *,
        what: str = "run",
        check_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_stall: Callable[[int], None] = _hard_exit,
    ) -> None:
        if stall_s <= 0:
            raise ValueError("stall_s must be positive")
        self.stall_s, self.what = stall_s, what
        self.check_s = check_s if check_s is not None else min(30.0, stall_s / 4)
        self._clock, self._on_stall = clock, on_stall
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.fired = False

    def __enter__(self) -> Watchdog:
        beat()  # the clock starts now, not at import time
        self._thread = threading.Thread(
            target=self._watch, name="concentrate-watchdog", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def check(self) -> bool:
        """One watchdog tick: fire (and return True) if the run has stalled."""
        last, beats = last_beat()
        idle = self._clock() - last
        if idle < self.stall_s or self.fired:
            return False
        self.fired = True
        logger.critical(
            "STALL: %s made no progress for %.0f s (limit %.0f s, %d beats so far); "
            "dumping stacks and exiting %d so a wrapper can restart and resume",
            self.what,
            idle,
            self.stall_s,
            beats,
            STALL_EXIT_CODE,
        )
        print(
            f"\n*** concentrate watchdog: {self.what} stalled for {idle:.0f}s; "
            f"exiting {STALL_EXIT_CODE} ***",
            file=sys.stderr,
            flush=True,
        )
        # stderr may have no fd (e.g. captured under pytest): the stack dump is best-effort.
        with contextlib.suppress(AttributeError, ValueError, OSError):
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        self._on_stall(STALL_EXIT_CODE)
        return True

    def _watch(self) -> None:
        while not self._stop.wait(self.check_s):
            if self.check():
                return
