"""``enhance()``: one MeetingNote (+ directory + earlier notes) → an ``EnhancedNote``. No LLM."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from concentrate.core.models import Channel, MeetingNote, Utterance
from concentrate.enhance.context import Priors, attendee_ids, resolve_person
from concentrate.enhance.directory import Directory
from concentrate.enhance.mentions import Gazetteer, MentionLinker, lowercase_vocabulary
from concentrate.enhance.models import EnhancedNote, MentionLink
from concentrate.enhance.speakers import MIN_CONFIDENCE, SpeakerResolver

_GAZETTEERS: dict[int, tuple[Directory, Gazetteer]] = {}


def _gazetteer(directory: Directory) -> Gazetteer:
    hit = _GAZETTEERS.get(id(directory))
    if hit is None or hit[0] is not directory:
        hit = (directory, Gazetteer(directory))
        _GAZETTEERS[id(directory)] = hit
    return hit[1]


def enhance(
    note: MeetingNote,
    directory: Directory,
    history: Sequence[MeetingNote] = (),
    *,
    priors: Priors | None = None,
    min_confidence: float = MIN_CONFIDENCE,
) -> EnhancedNote:
    """Resolve the speaker of every utterance and link person/org/project mentions.

    ``history`` holds earlier notes (for co-attendance and talkativeness priors). Callers that
    process many notes in order can pass precomputed ``priors`` instead (see ``enhance_all``).
    """
    if priors is None:
        priors = Priors.from_history(history, directory)
    gz = _gazetteer(directory)
    attendees = attendee_ids(note, directory)
    owner = resolve_person(note.owner, directory)
    linker = MentionLinker(gz, attendees, priors, lowercase_vocabulary(note))
    mentions: list[MentionLink] = []
    for u in note.utterances:
        mentions += linker.link_utterance(u)
    title_people = _title_people(linker, note)
    resolver = SpeakerResolver(
        note, directory, attendees, owner, priors, mentions, title_people, min_confidence
    )
    speakers = resolver.resolve()
    used = set(attendees)
    for r in speakers.values():
        used.update(r.candidates)
    used.update(m.entity_id for m in mentions if m.kind == "person" and m.entity_id)
    return EnhancedNote(
        note_id=note.note_id,
        speakers=speakers,
        mentions=mentions,
        attendee_ids=attendees,
        labels={pid: directory.label(pid) for pid in sorted(used) if pid in directory.by_id},
    )


def _title_people(linker: MentionLinker, note: MeetingNote) -> list[str]:
    """People named in the title ("Sam / Priya"); an ambiguous name nominates every match."""
    out: list[str] = []
    for m in linker.find(note.title or note.event_title or ""):
        if m.kind != "person":
            continue
        if m.entity_id is not None:
            out.append(m.entity_id)
        elif ": " in m.reason:
            names = m.reason.split(": ", 1)[1].split(" ", 1)[0]
            out += [p for p in names.split("|") if p in linker.gz.directory.by_id]
    return out


def enhance_all(notes: Iterable[MeetingNote], directory: Directory) -> dict[str, EnhancedNote]:
    """Enhance notes in chronological order, each with the notes before it as history."""
    ordered = sorted(notes, key=lambda n: (n.scheduled_start or n.created_at, n.note_id))
    priors = Priors()
    out: dict[str, EnhancedNote] = {}
    for note in ordered:
        out[note.note_id] = enhance(note, directory, priors=priors)
        priors.add(note, directory)
    return out


def speaker_label(u: Utterance, e: EnhancedNote) -> str:
    """'Sam Patel (Backend Engineer)', or 'Them (? Sam Patel|Sam Okafor)' when unresolved."""
    r = e.speakers.get(u.utterance_id)
    channel = "Me" if u.channel is Channel.ME else "Them"
    if r is None:
        return channel
    if r.person_id is not None:
        return e.labels.get(r.person_id, r.person_id)
    if not r.candidates:
        return f"{channel} (?)"
    names = [e.labels.get(c, c).split(" (")[0] for c in r.candidates]
    return f"{channel} (? {'|'.join(names)})"


def enhanced_line(u: Utterance, e: EnhancedNote) -> str:
    """Agent-native transcript line: ``[id] Sam Patel (Backend Engineer): text``.

    Unresolved speakers read ``[id] Them (? Sam Patel|Sam Okafor): text``.
    """
    return f"[{u.utterance_id}] {speaker_label(u, e)}: {u.text}"
