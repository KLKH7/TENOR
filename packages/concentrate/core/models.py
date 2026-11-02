"""Normalized, source-independent domain models.

Wire models (``core.granola``) are converted here once, at the source boundary. Downstream
packages only see these types, so they never depend on API quirks (camelCase pagination,
``organiser`` spelling, transcript fetched inline vs. paginated).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from concentrate.core import granola


class Channel(StrEnum):
    """Who an utterance is attributed to. Granola only knows the capture channel."""

    ME = "me"  # microphone: the note owner
    THEM = "them"  # system audio: everyone else on the call, undifferentiated


class Person(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str | None
    email: str


class Utterance(BaseModel):
    """One transcript segment with a stable, synthesized ID.

    The API provides no utterance IDs, so we assign ``{note_id}:{index:04d}`` in API order.
    Citations and gold evidence throughout the project use this ID.
    """

    model_config = ConfigDict(frozen=True)

    utterance_id: str
    note_id: str
    index: int
    channel: Channel
    speaker_name: str | None
    diarization_label: str | None
    text: str
    start_time: datetime
    end_time: datetime


class FolderRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    folder_id: str
    name: str
    parent_folder_id: str | None
    space_id: str | None


class MeetingNote(BaseModel):
    """A Granola note: typed notes + AI summary + transcript + calendar context."""

    model_config = ConfigDict(frozen=True)

    note_id: str
    title: str | None
    owner: Person
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None
    web_url: str
    event_title: str | None
    calendar_event_id: str | None
    scheduled_start: datetime | None
    scheduled_end: datetime | None
    organiser_email: str | None
    invitee_emails: tuple[str, ...]
    attendees: tuple[Person, ...]
    folders: tuple[FolderRef, ...]
    space_ids: tuple[str, ...]
    summary_markdown: str | None
    summary_text: str
    private_notes_markdown: str | None
    utterances: tuple[Utterance, ...]


def utterance_id(note_id: str, index: int) -> str:
    """Return the canonical utterance ID for the ``index``-th transcript item of a note."""
    return f"{note_id}:{index:04d}"


def channel_of(speaker: granola.Speaker) -> Channel:
    """Map Granola's speaker info to a channel; explicit ``attribution`` wins over ``source``."""
    if speaker.attribution is not None:
        return Channel(speaker.attribution)
    return Channel.ME if speaker.source == "microphone" else Channel.THEM


def _folder(f: granola.Folder) -> FolderRef:
    return FolderRef(
        folder_id=f.id, name=f.name, parent_folder_id=f.parent_folder_id, space_id=f.space_id
    )


def normalize_note(note: granola.Note) -> MeetingNote:
    """Convert a wire ``Note`` (with transcript populated, possibly empty) to a ``MeetingNote``."""
    event = note.calendar_event
    utterances = tuple(
        Utterance(
            utterance_id=utterance_id(note.id, i),
            note_id=note.id,
            index=i,
            channel=channel_of(item.speaker),
            speaker_name=item.speaker.name,
            diarization_label=item.speaker.diarization_label,
            text=item.text,
            start_time=item.start_time,
            end_time=item.end_time,
        )
        for i, item in enumerate(note.transcript or [])
    )
    return MeetingNote(
        note_id=note.id,
        title=note.title,
        owner=Person(name=note.owner.name, email=note.owner.email),
        created_at=note.created_at,
        updated_at=note.updated_at,
        deleted_at=note.deleted_at,
        web_url=note.web_url,
        event_title=event.event_title if event else None,
        calendar_event_id=event.calendar_event_id if event else None,
        scheduled_start=event.scheduled_start_time if event else None,
        scheduled_end=event.scheduled_end_time if event else None,
        organiser_email=event.organiser if event else None,
        invitee_emails=tuple(i.email for i in event.invitees) if event else (),
        attendees=tuple(Person(name=a.name, email=a.email) for a in note.attendees),
        folders=tuple(_folder(f) for f in note.folder_membership),
        space_ids=tuple(s.id for s in note.space_membership),
        summary_markdown=note.summary_markdown,
        summary_text=note.summary_text,
        private_notes_markdown=note.private_notes_markdown,
        utterances=utterances,
    )
