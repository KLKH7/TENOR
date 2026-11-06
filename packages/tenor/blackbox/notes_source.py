"""Product-neutral input for the notes-faithfulness track: one meeting's notes + a transcript.

The track asks, for any notetaker: is every line of its notes backed by what was said? The
judge needs only two things, so every product reduces to the same :class:`NotesCase`:

* ``notes``: the product's own written output for the meeting (Granola: the AI-enhanced
  ``summary_markdown``; Wispr Flow: the Flow Summary, plus optionally its markdown notes);
* ``transcript``: numbered lines (``<meeting_id>:<index>``) to check the notes against.

On paired meetings (:mod:`.pairing`) the fair comparison checks *both* products' notes against
the *same* reference transcript, so a product isn't rewarded for a transcript that dropped the
words its notes got wrong. :func:`paired_cases` does that; the reference is chosen explicitly.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from tenor.core.models import MeetingNote

NotesKind = Literal["summary", "notes", "summary+notes"]


class TranscriptLine(BaseModel):
    model_config = ConfigDict(frozen=True)
    line_id: str
    speaker: str  # name if known, else "Me" / "Them"
    text: str


class NotesCase(BaseModel):
    """One (product, meeting) notes-faithfulness case. PRIVATE when built from real meetings."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    product: str
    meeting_id: str  # the product's own meeting ID
    notes_kind: NotesKind
    notes: str
    transcript_source: str  # product whose transcript the notes are checked against
    transcript_meeting_id: str
    transcript: tuple[TranscriptLine, ...]


def transcript_lines(note: MeetingNote) -> tuple[TranscriptLine, ...]:
    return tuple(
        TranscriptLine(
            line_id=u.utterance_id,
            speaker=u.speaker_name or ("Me" if u.channel.value == "me" else "Them"),
            text=u.text,
        )
        for u in note.utterances
    )


def product_notes(note: MeetingNote, kind: NotesKind = "summary") -> str:
    """The product-written text for ``kind``. Granola's typed notes are the *user's* writing,
    so for Granola use ``summary``; Wispr's ``notes`` are its own generated notes body."""
    summary = note.summary_markdown or note.summary_text
    notes = note.private_notes_markdown or ""
    if kind == "summary":
        return summary
    if kind == "notes":
        return notes
    return "\n\n".join(t for t in (summary, notes) if t)


def notes_case(
    product: str,
    note: MeetingNote,
    *,
    kind: NotesKind = "summary",
    reference: tuple[str, MeetingNote] | None = None,
) -> NotesCase:
    """A case checking ``note``'s notes against ``reference`` (default: its own transcript)."""
    ref_product, ref = reference or (product, note)
    return NotesCase(
        product=product,
        meeting_id=note.note_id,
        notes_kind=kind,
        notes=product_notes(note, kind),
        transcript_source=ref_product,
        transcript_meeting_id=ref.note_id,
        transcript=transcript_lines(ref),
    )


def paired_cases(
    a: tuple[str, MeetingNote, NotesKind],
    b: tuple[str, MeetingNote, NotesKind],
    *,
    reference: Literal["a", "b"] = "a",
) -> tuple[NotesCase, NotesCase]:
    """Both products' notes for one paired meeting, against one shared reference transcript."""
    ref = (a[0], a[1]) if reference == "a" else (b[0], b[1])
    return (
        notes_case(a[0], a[1], kind=a[2], reference=ref),
        notes_case(b[0], b[1], kind=b[2], reference=ref),
    )
