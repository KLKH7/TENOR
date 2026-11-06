"""A progress heartbeat, a cooperative stop flag and a stall watchdog for long runs.

Code that makes progress calls :func:`beat` (``tenor.llm`` does so after every API reply,
retry and cache hit). Long loops call :func:`raise_if_stopping` between items.

Stopping is two-phase, so embedded databases (Graphiti's Kuzu) are closed cleanly whenever
possible:

1. **Ask.** A stall (no beat for ``stall_s``), SIGTERM or SIGINT calls :func:`request_stop`.
   Loops then raise :class:`StopRequested` at the next item boundary, the retry layer stops
   retrying, ``finally`` blocks close and checkpoint the database, and the CLI exits with
   :data:`STALL_EXIT_CODE` so a wrapper can restart and resume from ``progress.jsonl``.
2. **Force.** If the main thread is truly stuck (still no beat ``grace_s`` after the ask), the
   watchdog dumps every thread's stack, runs the :func:`on_forced_exit` hooks (Graphiti writes a
   ``FORCED_EXIT`` marker saying its write-ahead log may need recovery) and hard-exits with
   ``os._exit``, because a thread blocked in C code never sees ``sys.exit``. A second
   SIGTERM/SIGINT raises ``KeyboardInterrupt`` at once (``finally`` blocks still run).
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Iterator
from types import FrameType, TracebackType

logger = logging.getLogger("tenor.watchdog")

STALL_EXIT_CODE = 75  # EX_TEMPFAIL (sysexits.h): "temporary failure; try again"
# After asking a stalled run to stop, how long to wait for it to close cleanly before forcing:
# a thread stuck in an API read gets its timeout (<= 300 s, tenor.llm.resilience) plus 60 s.
GRACE_S = 360.0

_lock = threading.Lock()
_last_beat = time.monotonic()
_beats = 0
_stop = threading.Event()
_stop_reason = ""
_forced_exit_hooks: dict[str, Callable[[], None]] = {}


class StopRequested(Exception):
    """Raised at an item boundary after :func:`request_stop`: wind down cleanly, then exit 75."""


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


def request_stop(reason: str) -> None:
    """Ask the run to stop at the next safe point (idempotent; safe from any thread)."""
    global _stop_reason
    with _lock:
        if not _stop.is_set():
            _stop_reason = reason
            _stop.set()
    logger.warning("stop requested (%s): finishing the current item, then closing cleanly", reason)


def stop_requested() -> bool:
    return _stop.is_set()


def raise_if_stopping() -> None:
    """Call between work items: raises :class:`StopRequested` once a stop was requested."""
    if _stop.is_set():
        raise StopRequested(_stop_reason or "stop requested")


def reset_stop() -> None:
    """Clear the stop flag (a new run in the same process; tests)."""
    global _stop_reason
    with _lock:
        _stop.clear()
        _stop_reason = ""


def on_forced_exit(key: str, hook: Callable[[], None] | None) -> None:
    """Register (or with ``None`` remove) a hook run just before a forced hard exit.

    Hooks must be quick and must not touch state the stuck thread may hold (write a marker
    file; don't close a database another thread is writing).
    """
    with _lock:
        if hook is None:
            _forced_exit_hooks.pop(key, None)
        else:
            _forced_exit_hooks[key] = hook


def _run_forced_exit_hooks() -> None:
    with _lock:
        hooks = list(_forced_exit_hooks.items())
    for key, hook in hooks:
        try:
            hook()
        except Exception:  # one broken hook must not stop the others or the exit
            logger.exception("forced-exit hook %s failed", key)


def _hard_exit(code: int) -> None:  # pragma: no cover - replaced in tests
    sys.stderr.flush()
    os._exit(code)


class Watchdog:
    """Stop the run if :func:`beat` is not called for ``stall_s`` seconds (two-phase).

    Use as a context manager around the long loop. ``clock``, ``on_stall`` (the hard exit),
    ``grace_s`` and ``check_s`` are injectable so tests run in milliseconds.
    """

    def __init__(
        self,
        stall_s: float,
        *,
        what: str = "run",
        grace_s: float = GRACE_S,
        check_s: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_stall: Callable[[int], None] = _hard_exit,
    ) -> None:
        if stall_s <= 0:
            raise ValueError("stall_s must be positive")
        self.stall_s, self.what, self.grace_s = stall_s, what, grace_s
        self.check_s = check_s if check_s is not None else min(30.0, stall_s / 4)
        self._clock, self._on_stall = clock, on_stall
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self.asked_at: float | None = None  # when the stall was detected and a stop requested
        self.fired = False  # forced the hard exit

    def __enter__(self) -> Watchdog:
        beat()  # the clock starts now, not at import time
        self._thread = threading.Thread(target=self._watch, name="tenor-watchdog", daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self._done.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def check(self) -> bool:
        """One tick: ask for a stop on a stall; force the exit (return True) after the grace."""
        if self.fired:
            return False
        last, beats = last_beat()
        now = self._clock()
        idle = now - last
        if idle < self.stall_s:
            return False
        if self.asked_at is None:
            self.asked_at = now
            logger.critical(
                "STALL: %s made no progress for %.0f s (limit %.0f s, %d beats so far); "
                "asking it to stop cleanly, forcing exit %d in %.0f s if it does not",
                self.what,
                idle,
                self.stall_s,
                beats,
                STALL_EXIT_CODE,
                self.grace_s,
            )
            request_stop(f"stalled for {idle:.0f}s")
        if now - self.asked_at < self.grace_s:
            return False
        self.fired = True
        logger.critical("%s did not stop cleanly; forcing exit %d", self.what, STALL_EXIT_CODE)
        print(
            f"\n*** tenor watchdog: {self.what} stalled for {idle:.0f}s and did not stop "
            f"cleanly; forcing exit {STALL_EXIT_CODE} (see docs/engineering/HARDENING.md) ***",
            file=sys.stderr,
            flush=True,
        )
        # stderr may have no fd (e.g. captured under pytest): the stack dump is best-effort.
        with contextlib.suppress(AttributeError, ValueError, OSError):
            faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        _run_forced_exit_hooks()
        self._on_stall(STALL_EXIT_CODE)
        return True

    def _watch(self) -> None:
        while not self._done.wait(self.check_s):
            if self.check():
                return


@contextlib.contextmanager
def graceful_signals() -> Iterator[None]:
    """SIGTERM/SIGINT: the first asks for a clean stop; the second raises ``KeyboardInterrupt``.

    Python runs signal handlers in the main thread between bytecodes, never inside a C call, so
    even the forced path interrupts between two database statements and ``finally`` blocks run.
    A no-op outside the main thread.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def handler(signum: int, frame: FrameType | None) -> None:
        name = signal.Signals(signum).name
        if stop_requested():
            logger.critical("second %s: interrupting now", name)
            raise KeyboardInterrupt(name)
        request_stop(name)
        print(
            f"\n*** {name}: stopping after the current item (send again to interrupt now) ***",
            file=sys.stderr,
            flush=True,
        )

    previous = {s: signal.signal(s, handler) for s in (signal.SIGTERM, signal.SIGINT)}
    try:
        yield
    finally:
        for s, h in previous.items():
            signal.signal(s, h)
