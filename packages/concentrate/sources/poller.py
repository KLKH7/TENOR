"""Incremental sync: poll a :class:`Source` with ``updated_after`` and normalize into SQLite.

Design (docs/DESIGN.md, "Sync"):

* The watermark is the max ``updated_at`` *observed from the server*, never our wall clock,
  so client/server clock skew can't make us skip notes.
* Each poll re-scans an overlap window before the watermark (default 10 min). This absorbs
  same-second updates and the unknown inclusive/exclusive semantics of ``updated_after``.
  Re-scanned notes whose ``updated_at`` hasn't changed are skipped without a Get Note call.
* Notes that 404 after retries (still processing) go to ``pending_notes`` and are retried on
  every subsequent sync, independent of the watermark.
* Results never depend on list order (the API doesn't document one).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from concentrate.core.store import NoteStore
from concentrate.sources.base import NoteNotReady, Source

DEFAULT_OVERLAP = timedelta(minutes=10)


@dataclass
class SyncReport:
    listed: int = 0
    fetched: int = 0
    unchanged: int = 0
    deleted: int = 0
    pending: list[str] = field(default_factory=list)
    recovered: int = 0
    watermark: datetime | None = None


def sync(
    source: Source,
    store: NoteStore,
    *,
    full: bool = False,
    overlap: timedelta = DEFAULT_OVERLAP,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> SyncReport:
    """Bring ``store`` up to date with ``source``. ``full=True`` ignores the watermark."""
    report = SyncReport()
    store.upsert_folders(list(source.list_folders()))

    previous = None if full else store.high_watermark(source.name)
    since = previous - overlap if previous else None
    watermark = previous

    def fetch(note_id: str) -> bool:
        try:
            note = source.get_note(note_id)
        except NoteNotReady:
            store.add_pending(source.name, note_id)
            report.pending.append(note_id)
            return False
        store.upsert_note(note)
        store.clear_pending(source.name, note_id)
        report.fetched += 1
        return True

    seen: set[str] = set()
    for summary in source.list_notes(updated_after=since):
        report.listed += 1
        seen.add(summary.id)
        watermark = max(watermark, summary.updated_at) if watermark else summary.updated_at
        if summary.deleted_at is not None:
            if store.stored_updated_at(summary.id) is not None:
                store.mark_deleted(summary)
                report.deleted += 1
            continue
        if store.stored_updated_at(summary.id) == summary.updated_at:
            report.unchanged += 1
            continue
        fetch(summary.id)

    for note_id in store.pending(source.name):
        if note_id not in seen and fetch(note_id):
            report.recovered += 1

    report.watermark = watermark
    store.set_sync_state(source.name, watermark, now())
    return report
