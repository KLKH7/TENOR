"""Keeping Graphiti's embedded Kuzu database safe across kills, stalls and restarts.

Kuzu writes changes to a write-ahead log (``<db>.wal``) and folds them into the main file at a
checkpoint (or a clean close). A process killed mid-write can leave a WAL whose replay crashes
the *next* process natively ("Fatal Python error: Bus error"), which no ``try`` can catch. So:

* :func:`close_kuzu` waits for in-flight queries, runs ``CHECKPOINT`` and closes: after it the
  WAL is gone and the main file is self-contained.
* :func:`check_openable` runs before opening a graph that was not closed cleanly (a non-empty
  WAL, or the watchdog's ``FORCED_EXIT`` marker). It replays the WAL in a *child process*: a
  native crash kills only the child, and we fail with :class:`GraphStoreError` explaining the
  recovery steps instead of dying with a bus error on every start.
* :func:`reconcile` drops ``progress.jsonl`` rows whose episode is not in the graph (e.g. the
  WAL holding them was set aside), so a resume redoes exactly the missing episodes.

Recovery steps (also in docs/engineering/HARDENING.md, "Recovering a Graphiti store"): stop the
build; move
``graph.kuzu.wal`` aside (e.g. ``graph.kuzu.wal.broken``; never delete it); rerun the same
command. The resume reconciles ``progress.jsonl`` against the episodes in the main file and
rebuilds only the lost ones (their earlier spend is reported as ``orphaned_cost_usd``).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FORCED_EXIT_MARKER = "FORCED_EXIT"
ORPHANED_FILE = "progress.orphaned.jsonl"
RECOVERY_DOC = "docs/engineering/HARDENING.md#recovering-a-graphiti-store"
PROBE_TIMEOUT_S = 600.0

# Opening replays the WAL; the count proves the replayed database answers queries.
PROBE_CODE = (
    "import sys, kuzu\n"
    "db = kuzu.Database(sys.argv[1])\n"
    "conn = kuzu.Connection(db)\n"
    "conn.execute('MATCH (n:Episodic) RETURN count(n)')\n"
    "conn.close(); db.close()\n"
)

Probe = Callable[[Path], str | None]


class GraphStoreError(RuntimeError):
    """The graph store cannot be opened safely; the message says how to recover."""


def wal_path(db: Path) -> Path:
    return db.with_name(db.name + ".wal")


def needs_probe(db: Path) -> bool:
    """True if ``db`` was not closed cleanly: a non-empty WAL or a forced-exit marker."""
    wal = wal_path(db)
    return (wal.exists() and wal.stat().st_size > 0) or (db.parent / FORCED_EXIT_MARKER).exists()


def probe_kuzu(
    db: Path, *, code: str = PROBE_CODE, timeout_s: float = PROBE_TIMEOUT_S
) -> str | None:
    """Open ``db`` (replaying its WAL) in a child process. ``None`` if fine, else what failed."""
    try:
        done = subprocess.run(
            [sys.executable, "-c", code, str(db)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return f"opening took longer than {timeout_s:.0f}s"
    if done.returncode == 0:
        return None
    tail = (done.stderr or "").strip().splitlines()[-3:]
    if done.returncode < 0:
        how = f"crashed with signal {done.returncode * -1}"
        try:
            import signal

            how = f"crashed with {signal.Signals(-done.returncode).name}"
        except ValueError:
            pass
    else:
        how = f"failed (exit {done.returncode})"
    return how + (": " + " | ".join(tail) if tail else "")


def check_openable(db: Path, probe: Probe = probe_kuzu) -> None:
    """Raise :class:`GraphStoreError` if opening ``db`` would fail or crash the process."""
    if not db.exists() or not needs_probe(db):
        return
    marker = db.parent / FORCED_EXIT_MARKER
    logger.warning("%s was not closed cleanly; test-opening it in a child process", db)
    problem = probe(db)
    if problem is not None:
        raise GraphStoreError(
            f"Graphiti store {db} cannot be opened: replaying its write-ahead log {problem}.\n"
            f"Recovery ({RECOVERY_DOC}):\n"
            f"  1. make sure no build is running on it;\n"
            f"  2. mv {wal_path(db)} {wal_path(db)}.broken   (keep it; don't delete);\n"
            f"  3. rerun the same command: it resumes from the episodes in the main file and "
            f"rebuilds the rest (progress.jsonl is reconciled automatically)."
        )
    marker.unlink(missing_ok=True)


def write_forced_exit_marker(db: Path, why: str = "the stall watchdog forced an exit") -> None:
    """Note that ``db`` may not have been closed cleanly (read by :func:`needs_probe`)."""
    (db.parent / FORCED_EXIT_MARKER).write_text(
        f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')}: {why} while {db.name} was open; its WAL may "
        f"need recovery. The next open test-opens it first. See {RECOVERY_DOC}.\n"
    )


def close_kuzu(driver: Any) -> None:
    """Wait for in-flight queries, checkpoint (fold the WAL into the main file) and close.

    ``driver`` is graphiti's ``KuzuDriver`` (``.db``: ``kuzu.Database``, ``.client``:
    ``kuzu.AsyncConnection``). Graphiti's own ``close`` is a no-op that relies on GC.
    """
    import kuzu

    client, db = driver.client, driver.db
    client.executor.shutdown(wait=True)  # let queries already in the thread pool finish
    client.close()
    conn = kuzu.Connection(db)
    try:
        conn.execute("CHECKPOINT")
    finally:
        conn.close()
        db.close()


def reconcile(
    progress_path: Path, rows: list[dict[str, Any]], present: Iterable[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split ``rows`` into (kept, orphaned) by whether their ``uuid`` is in the graph.

    Orphaned rows are appended to ``progress.orphaned.jsonl`` (their spend was real) and
    ``progress_path`` is rewritten atomically with the kept rows only.
    """
    have = set(present)
    kept = [r for r in rows if r.get("uuid") in have]
    orphaned = [r for r in rows if r.get("uuid") not in have]
    if not orphaned:
        return kept, orphaned
    logger.warning(
        "%s: %d of %d progress rows have no episode in the graph (lost with its WAL?); "
        "they will be rebuilt",
        progress_path,
        len(orphaned),
        len(rows),
    )
    with (progress_path.parent / ORPHANED_FILE).open("a") as fh:
        for r in orphaned:
            fh.write(json.dumps(r) + "\n")
    tmp = progress_path.with_name(progress_path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in kept))
    os.replace(tmp, progress_path)  # atomic: a kill now leaves the old or the new file whole
    return kept, orphaned
