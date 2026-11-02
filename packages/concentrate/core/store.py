"""SQLite store for synced notes.

The raw wire JSON of each note is kept verbatim (lossless; lets us re-normalize after model
changes without re-syncing), alongside derived tables for querying: utterances, attendees,
folders. Timestamps are stored as UTC ISO-8601 strings so they sort lexicographically.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from concentrate.core import granola
from concentrate.core.models import MeetingNote, normalize_note

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    note_id TEXT PRIMARY KEY,
    title TEXT,
    owner_email TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    scheduled_start TEXT,
    calendar_event_id TEXT,
    raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS utterances (
    utterance_id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    speaker_name TEXT,
    text TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS utterances_note ON utterances(note_id, idx);
CREATE TABLE IF NOT EXISTS attendees (
    note_id TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    name TEXT,
    PRIMARY KEY (note_id, email)
);
CREATE TABLE IF NOT EXISTS folders (
    folder_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_folder_id TEXT,
    space_id TEXT
);
CREATE TABLE IF NOT EXISTS note_folders (
    note_id TEXT NOT NULL REFERENCES notes(note_id) ON DELETE CASCADE,
    folder_id TEXT NOT NULL,
    PRIMARY KEY (note_id, folder_id)
);
CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT PRIMARY KEY,
    high_watermark TEXT,
    last_run_at TEXT
);
CREATE TABLE IF NOT EXISTS pending_notes (
    source TEXT NOT NULL,
    note_id TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (source, note_id)
);
"""


def to_db_time(dt: datetime) -> str:
    """Serialize an aware datetime as a sortable UTC string."""
    return dt.astimezone(UTC).isoformat()


def from_db_time(s: str) -> datetime:
    return datetime.fromisoformat(s)


class NoteStore:
    """Persistence for normalized notes plus sync bookkeeping."""

    def __init__(self, path: Path | str) -> None:
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- notes ------------------------------------------------------------------------------

    def stored_updated_at(self, note_id: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT updated_at FROM notes WHERE note_id = ?", (note_id,)
        ).fetchone()
        return from_db_time(row[0]) if row else None

    def upsert_note(self, note: granola.Note) -> None:
        """Insert or fully replace a note and its derived rows, atomically."""
        m = normalize_note(note)
        with self.conn:
            self.conn.execute("DELETE FROM notes WHERE note_id = ?", (m.note_id,))
            self.conn.execute(
                "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    m.note_id,
                    m.title,
                    m.owner.email,
                    to_db_time(m.created_at),
                    to_db_time(m.updated_at),
                    to_db_time(m.deleted_at) if m.deleted_at else None,
                    to_db_time(m.scheduled_start) if m.scheduled_start else None,
                    m.calendar_event_id,
                    note.model_dump_json(exclude_unset=True),
                ),
            )
            self.conn.executemany(
                "INSERT INTO utterances VALUES (?,?,?,?,?,?,?,?)",
                [
                    (
                        u.utterance_id,
                        u.note_id,
                        u.index,
                        u.channel.value,
                        u.speaker_name,
                        u.text,
                        to_db_time(u.start_time),
                        to_db_time(u.end_time),
                    )
                    for u in m.utterances
                ],
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO attendees VALUES (?,?,?)",
                [(m.note_id, a.email, a.name) for a in m.attendees],
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO note_folders VALUES (?,?)",
                [(m.note_id, f.folder_id) for f in m.folders],
            )

    def mark_deleted(self, summary: granola.NoteSummary) -> None:
        """Record a soft-deleted note: keep the row, drop its transcript."""
        assert summary.deleted_at is not None
        with self.conn:
            self.conn.execute(
                "UPDATE notes SET deleted_at = ?, updated_at = ? WHERE note_id = ?",
                (to_db_time(summary.deleted_at), to_db_time(summary.updated_at), summary.id),
            )
            self.conn.execute("DELETE FROM utterances WHERE note_id = ?", (summary.id,))

    def get_note(self, note_id: str) -> MeetingNote | None:
        row = self.conn.execute(
            "SELECT raw_json FROM notes WHERE note_id = ?", (note_id,)
        ).fetchone()
        return normalize_note(granola.Note.model_validate_json(row[0])) if row else None

    def iter_notes(self, *, include_deleted: bool = False) -> Iterator[MeetingNote]:
        """Yield notes ordered by meeting time (scheduled start, else creation)."""
        where = "" if include_deleted else "WHERE deleted_at IS NULL"
        for (raw,) in self.conn.execute(
            f"SELECT raw_json FROM notes {where} "
            "ORDER BY COALESCE(scheduled_start, created_at), note_id"
        ):
            yield normalize_note(granola.Note.model_validate_json(raw))

    def upsert_folders(self, folders: list[granola.Folder]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM folders")
            self.conn.executemany(
                "INSERT INTO folders VALUES (?,?,?,?)",
                [(f.id, f.name, f.parent_folder_id, f.space_id) for f in folders],
            )

    def counts(self) -> dict[str, int]:
        tables = ("notes", "utterances", "attendees", "folders", "note_folders")
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}

    # -- sync bookkeeping -------------------------------------------------------------------

    def high_watermark(self, source: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT high_watermark FROM sync_state WHERE source = ?", (source,)
        ).fetchone()
        return from_db_time(row[0]) if row and row[0] else None

    def set_sync_state(self, source: str, watermark: datetime | None, run_at: datetime) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO sync_state VALUES (?,?,?) ON CONFLICT(source) DO UPDATE SET "
                "high_watermark = excluded.high_watermark, last_run_at = excluded.last_run_at",
                (source, to_db_time(watermark) if watermark else None, to_db_time(run_at)),
            )

    def pending(self, source: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT note_id FROM pending_notes WHERE source = ? ORDER BY note_id", (source,)
        )
        return [r[0] for r in rows]

    def add_pending(self, source: str, note_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO pending_notes VALUES (?,?,1) ON CONFLICT(source, note_id) "
                "DO UPDATE SET attempts = attempts + 1",
                (source, note_id),
            )

    def clear_pending(self, source: str, note_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM pending_notes WHERE source = ? AND note_id = ?", (source, note_id)
            )
