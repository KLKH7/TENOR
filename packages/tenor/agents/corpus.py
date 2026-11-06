"""Text views of the note store that baselines put in front of the model.

All views come from normalized ``MeetingNote``s, so they work the same for fixtures, the REST
API and the MCP. Transcript lines carry their utterance ID (``[not_x:0012]``) so systems can cite
the exact line; summaries are cited by note ID (``[not_x]``) since they have no line IDs.
"""

from __future__ import annotations

from dataclasses import dataclass

from tenor.core.models import Channel, MeetingNote, Utterance

CHUNK_UTTERANCES = 15  # transcript chunk size for agentic search/reads (MCP-style chunks)


def speaker_label(u: Utterance) -> str:
    base = "Me" if u.channel is Channel.ME else "Them"
    if u.speaker_name:
        return f"{base} ({u.speaker_name})"
    if u.diarization_label:
        return f"{base} ({u.diarization_label})"
    return base


def header(m: MeetingNote) -> str:
    when = (m.scheduled_start or m.created_at).strftime("%a %Y-%m-%d %H:%M UTC")
    people = ", ".join(f"{p.name or p.email} <{p.email}>" for p in m.attendees) or "unknown"
    folders = ", ".join(f.name for f in m.folders) or "none"
    return f"# {m.title} [{m.note_id}]\nWhen: {when}\nAttendees: {people}\nFolders: {folders}"


def summary_view(m: MeetingNote) -> str:
    return (m.summary_markdown or m.summary_text or "(no summary)").strip()


def transcript_lines(utterances: tuple[Utterance, ...] | list[Utterance]) -> str:
    return "\n".join(f"[{u.utterance_id}] {speaker_label(u)}: {u.text}" for u in utterances)


def full_view(m: MeetingNote) -> str:
    """Header + AI summary + typed notes + full transcript."""
    parts = [header(m), "## Summary", summary_view(m)]
    if m.private_notes_markdown:
        parts += ["## My notes", m.private_notes_markdown]
    parts += ["## Transcript", transcript_lines(m.utterances) or "(no transcript)"]
    return "\n".join(parts)


def summary_only_view(m: MeetingNote) -> str:
    parts = [header(m), "## Summary", summary_view(m)]
    if m.private_notes_markdown:
        parts += ["## My notes", m.private_notes_markdown]
    return "\n".join(parts)


@dataclass(frozen=True)
class Chunk:
    note_id: str
    chunk_id: int
    utterances: tuple[Utterance, ...]

    @property
    def text(self) -> str:
        return " ".join(u.text for u in self.utterances)


def chunks(m: MeetingNote) -> list[Chunk]:
    u = m.utterances
    return [
        Chunk(m.note_id, i // CHUNK_UTTERANCES, u[i : i + CHUNK_UTTERANCES])
        for i in range(0, len(u), CHUNK_UTTERANCES)
    ]


def by_date(notes: list[MeetingNote]) -> list[MeetingNote]:
    return sorted(notes, key=lambda m: (m.scheduled_start or m.created_at, m.note_id))
