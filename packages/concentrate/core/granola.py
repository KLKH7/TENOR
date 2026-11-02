"""Wire models for the Granola public REST API (v1).

These mirror the OpenAPI schemas published at https://docs.granola.ai/api-reference/openapi.json
field-for-field, including nullability and camelCase pagination keys. They are used both to parse
live API responses and to validate fixtures (see tests/test_contract.py), so a fixture that
parses here is shaped exactly like the API. Provenance of every field: docs/SCHEMA_NOTES.md.

Unknown fields are ignored rather than rejected: the API evolves additively (v1.0 → v1.6 in
2026) and a new field must not break sync. The contract test separately checks that fixtures
contain no fields we don't model.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

NOTE_ID_PATTERN = r"^not_[a-zA-Z0-9]{14}$"
FOLDER_ID_PATTERN = r"^fol_[a-zA-Z0-9]{14}$"
SPACE_ID_PATTERN = r"^spa_[a-zA-Z0-9]{14}$"
ATTACHMENT_ID_PATTERN = r"^att_[a-zA-Z0-9]{14}$"

NoteId = Annotated[str, Field(pattern=NOTE_ID_PATTERN)]
FolderId = Annotated[str, Field(pattern=FOLDER_ID_PATTERN)]
SpaceId = Annotated[str, Field(pattern=SPACE_ID_PATTERN)]


class WireModel(BaseModel):
    """Base for all API models: immutable, tolerant of additive API changes."""

    model_config = ConfigDict(extra="ignore", frozen=True)


class User(WireModel):
    """A person reference (note owner or attendee)."""

    name: str | None
    email: str


class CalendarInvitee(WireModel):
    email: str


class CalendarEvent(WireModel):
    """Calendar event linked to a note. Note the API's British spelling ``organiser``."""

    event_title: str | None
    invitees: list[CalendarInvitee]
    organiser: str | None
    calendar_event_id: str | None
    scheduled_start_time: datetime | None
    scheduled_end_time: datetime | None


class Folder(WireModel):
    id: FolderId
    object: Literal["folder"]
    name: str
    parent_folder_id: FolderId | None
    space_id: SpaceId | None = None


class Space(WireModel):
    id: SpaceId
    object: Literal["space"]
    name: str


class NoteAttachment(WireModel):
    id: Annotated[str, Field(pattern=ATTACHMENT_ID_PATTERN)]
    object: Literal["attachment"]
    type: str  # e.g. "photo"; docs: clients should ignore unrecognised types
    url: str
    width: float
    height: float


class Speaker(WireModel):
    """Channel attribution for an utterance.

    ``source`` is the capture channel: ``microphone`` (the note owner's mic) or ``speaker``
    (system audio, i.e. everyone else on the call). The other fields are optional and, per the
    docs, usually absent: ``attribution`` is omitted when unknown, ``diarization_label`` is
    iOS-only, ``name`` only appears when the speaker was identified.
    """

    source: Literal["microphone", "speaker"]
    attribution: Literal["me", "them"] | None = None
    diarization_label: str | None = None
    name: str | None = None


class TranscriptItem(WireModel):
    """One transcript segment. The API gives no per-utterance ID; see core.models.Utterance."""

    speaker: Speaker
    text: str
    start_time: datetime
    end_time: datetime


class NoteSummary(WireModel):
    """Item of ``GET /v1/notes``."""

    id: NoteId
    object: Literal["note"]
    title: str | None
    owner: User
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


class Note(NoteSummary):
    """Response of ``GET /v1/notes/{note_id}``.

    ``private_notes_markdown`` holds the user's own typed notes and is only populated when the
    API key belongs to the note's creator. ``summary_markdown`` is Granola's AI-enhanced notes.
    ``transcript`` is null unless requested with ``include=transcript``.
    """

    web_url: str
    calendar_event: CalendarEvent | None
    attachments: list[NoteAttachment]
    attendees: list[User]
    folder_membership: list[Folder]
    space_membership: list[Space] = Field(default_factory=list)
    summary_text: str
    summary_markdown: str | None
    private_notes_text: str | None
    private_notes_markdown: str | None
    transcript: list[TranscriptItem] | None


class ListNotesOutput(WireModel):
    notes: list[NoteSummary]
    hasMore: bool  # camelCase: verbatim API field name
    cursor: str | None


class ListFoldersOutput(WireModel):
    folders: list[Folder]
    hasMore: bool
    cursor: str | None


class ListSpacesOutput(WireModel):
    spaces: list[Space]
    hasMore: bool
    cursor: str | None


class GetTranscriptOutput(WireModel):
    transcript: list[TranscriptItem]
    hasMore: bool
    cursor: str | None


# -- MCP variants -----------------------------------------------------------------------------
# Granola's MCP identifies notes by UUID (it accepts `not_` IDs as input but never returns them),
# and its folders carry their own IDs. These subclasses keep every other field and constraint of
# the public-API models, so normalization and storage are shared. See docs/SCHEMA_NOTES.md.

MCP_ID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
McpId = Annotated[str, Field(pattern=MCP_ID_PATTERN)]


class McpFolder(Folder):
    id: str  # MCP folder IDs are not `fol_` IDs
    parent_folder_id: str | None


class McpNoteSummary(NoteSummary):
    id: McpId


class McpNote(Note):
    id: McpId
    folder_membership: list[McpFolder]  # type: ignore[assignment]
