"""Crash-safe reading of the append-only ``progress.jsonl`` logs that make builds resumable.

A build appends one JSON line per finished item and flushes. If the process is killed (by the
stall watchdog, ``kill -9`` or a power cut) mid-write, the file can end in a torn, partial line.
Reading it naively would raise forever, and appending after it would glue the next row onto the
fragment. :func:`read_progress` drops a torn *last* line and truncates the file back to the end of
the last complete row, so the next append starts on a clean line. A corrupt line anywhere else is
real damage and raises.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def read_progress(path: Path) -> list[dict[str, Any]]:
    """The complete rows of ``path`` (``[]`` if missing), repairing a torn final line in place.

    Each row is written as one ``line + "\n"``; an interrupted write leaves a prefix of it, so
    only the bytes after the last newline can be torn.
    """
    if not path.exists():
        return []
    data = path.read_bytes()
    *complete, tail = data.split(b"\n")
    rows: list[dict[str, Any]] = []
    for n, raw in enumerate(complete, start=1):
        if not raw.strip():
            continue
        try:
            rows.append(json.loads(raw))
        except json.JSONDecodeError:
            raise ValueError(
                f"{path}: corrupt row {n} (a complete line, so not a torn write)"
            ) from None
    if not tail.strip():
        return rows
    try:
        rows.append(json.loads(tail))  # a whole row whose newline never reached the disk
        with path.open("ab") as fh:
            fh.write(b"\n")
    except json.JSONDecodeError:
        logger.warning("%s: dropping a torn final row (%d bytes)", path, len(tail))
        with path.open("r+b") as fh:
            fh.truncate(len(data) - len(tail))
    return rows
