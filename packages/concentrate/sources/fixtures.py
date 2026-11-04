"""``FixtureSource``: serves Granola data from local JSON in the exact API response shapes.

Directory layout::

    <root>/folders.json          # body of GET /v1/folders (single page: hasMore=false)
    <root>/notes/<note_id>.json  # body of GET /v1/notes/{note_id}?include=transcript

Filters reproduce the documented API semantics (``folder_id`` includes child folders). Where the
docs are silent (inclusive vs. exclusive date bounds) we follow the choices recorded in
docs/engineering/SCHEMA_NOTES.md so both sources behave the same.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from concentrate.core import granola
from concentrate.sources.base import NoteNotReady


class FixtureSource:
    """Implements :class:`concentrate.sources.base.Source` over a fixture directory."""

    name = "fixtures"

    def __init__(self, root: Path) -> None:
        self.root = root
        if not (root / "notes").is_dir():
            raise FileNotFoundError(f"no notes/ directory under {root}")

    def _note_paths(self) -> list[Path]:
        return sorted((self.root / "notes").glob("*.json"))

    def _descendants(self, folder_id: str) -> set[str]:
        children: dict[str | None, list[str]] = {}
        for f in self.list_folders():
            children.setdefault(f.parent_folder_id, []).append(f.id)
        found, stack = {folder_id}, [folder_id]
        while stack:
            for child in children.get(stack.pop(), []):
                if child not in found:
                    found.add(child)
                    stack.append(child)
        return found

    def list_notes(
        self,
        *,
        updated_after: datetime | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        folder_id: str | None = None,
    ) -> Iterator[granola.NoteSummary]:
        """Yield summaries of fixture notes matching the filters."""
        folder_ids = self._descendants(folder_id) if folder_id else None
        for path in self._note_paths():
            note = granola.Note.model_validate_json(path.read_bytes())
            if updated_after and not note.updated_at > updated_after:
                continue
            if created_after and not note.created_at > created_after:
                continue
            if created_before and not note.created_at < created_before:
                continue
            if folder_ids is not None and not folder_ids & {f.id for f in note.folder_membership}:
                continue
            yield granola.NoteSummary.model_validate(
                note.model_dump(include=set(granola.NoteSummary.model_fields))
            )

    def get_note(self, note_id: str) -> granola.Note:
        """Return the stored note (transcript ``[]`` if the fixture has none)."""
        path = self.root / "notes" / f"{note_id}.json"
        if not path.exists():
            raise NoteNotReady(note_id)
        note = granola.Note.model_validate_json(path.read_bytes())
        return note if note.transcript is not None else note.model_copy(update={"transcript": []})

    def list_folders(self) -> Iterator[granola.Folder]:
        """Yield folders from ``folders.json`` (empty if absent)."""
        path = self.root / "folders.json"
        if path.exists():
            yield from granola.ListFoldersOutput.model_validate_json(path.read_bytes()).folders
