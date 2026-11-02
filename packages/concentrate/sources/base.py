"""The ``Source`` protocol: the only way Concentrate reads Granola data."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from concentrate.core import granola


class SourceError(Exception):
    """Base error for source failures."""


class NoteNotReady(SourceError):
    """A note returned 404 after retries.

    Granola returns 404 for notes that are still processing (no summary/transcript yet). The
    API cannot distinguish that from a deleted or inaccessible note, so callers should treat
    this as "try again on a later sync", not as a deletion.
    """

    def __init__(self, note_id: str) -> None:
        super().__init__(f"note {note_id} not available (still processing, deleted, or no access)")
        self.note_id = note_id


class Source(Protocol):
    """Read-only access to Granola notes and folders, in the API's wire shapes."""

    name: str

    def list_notes(
        self,
        *,
        updated_after: datetime | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        folder_id: str | None = None,
    ) -> Iterator[granola.NoteSummary]:
        """Yield note summaries matching the filters, across all pages. Order is unspecified."""
        ...

    def get_note(self, note_id: str) -> granola.Note:
        """Return the full note with ``transcript`` populated (``[]`` if none).

        Raises :class:`NoteNotReady` if the note is unavailable after retries.
        """
        ...

    def list_folders(self) -> Iterator[granola.Folder]:
        """Yield every folder visible to the caller, across all pages."""
        ...
