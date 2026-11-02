"""Render world meetings into Granola API ``Note`` objects (transcript, summary, typed notes).

Faithful to what Granola actually produces (docs/SCHEMA_NOTES.md):

* utterances carry the capture channel: ``microphone`` for the note owner (and for everyone
  in an in-person meeting), ``speaker`` (system audio) for everyone else on a call. As in live
  transcripts (docs/PROBE_2026-11-02.md), Granola identifies *some* remote speakers by name:
  in most remote meetings a subset of "Them" speakers carry ``name``, and even those are
  unlabeled on a fraction of passages. The owner's microphone is never named;
* turns are cut into short, fragmentary passages (often 1-6 words), never inside a fact phrase;
* attendees without a calendar display name have ``name: null`` (the MCP shows their handle);
* in-person meetings recorded on iOS get ``diarization_label`` ("Speaker A"...);
* short backchannels from the other channel overlap long turns (crosstalk);
* ``summary_markdown`` is a lossy AI summary (~20% of points omitted); typed notes are sparse.

Notes are built as ``core.granola.Note`` models and serialized from them, so fixtures match the
wire schema by construction.
"""

from __future__ import annotations

import hashlib
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from concentrate.core import granola
from concentrate.core.models import utterance_id
from concentrate.synth.noise import BACKCHANNELS, LEAD_INS, NoiseRates, fragment, noisify
from concentrate.synth.roster import OWNER_ID, PEOPLE, person
from concentrate.synth.story import granola_id
from concentrate.synth.world import Meeting, World

FOLDERS: list[tuple[str, str | None]] = [
    ("Customers", None), ("Acmeway", "Customers"), ("Beacon Freight", "Customers"),
    ("Corvid Bank", "Customers"), ("Driftwood Retail", "Customers"), ("Engineering", None),
    ("Leadership", None), ("Design", None), ("Company", None), ("1:1s", None),
]  # fmt: skip
DIARIZED = {"w3-1on1-alex"}  # recorded on iPhone: diarization labels available
# Speaker identification and attendee naming, calibrated qualitatively on the live probe.
# Calibrated on the author's real meetings synced via the MCP (2026-11-02): 82% of "Them"
# passages named; remote passages median 5 words (p25 1, p75 17, p90 38); in-person passages
# median 25 words (p75 69). See docs/PROBE_2026-11-02.md.
NAMED_MEETING_RATE = 0.95  # remote meetings where Granola identifies "Them" speakers
IDENTIFIED_SPEAKER_RATE = 0.9  # per remote speaker, within such a meeting
UNLABELED_PASSAGE_RATE = 0.1  # identified speakers still appear as bare "System audio"
LEAD_IN_RATE = 0.2  # a turn opens with its own one-word passage ("So", "Yeah")
NULL_NAME_RATE = 0.25  # attendees with no display name on the calendar invite
LOSSY_SUMMARY_DROP = 0.2
TYPED_NOTES_RATE = 0.4

BeatMap = dict[tuple[str, int], list[str]]  # (meeting key, beat index) -> utterance IDs


@dataclass(frozen=True)
class GoldUtterance:
    """Ground truth for one rendered utterance: who spoke it (word counts) and its beats.

    Remote passages have exactly one speaker. In-person blocks merge consecutive turns, so they
    can carry several speakers; ``speaker`` is the one who spoke most of its words.
    """

    words_by_speaker: dict[str, int]
    beats: tuple[int, ...]  # beat indexes (empty for backchannels)

    @property
    def speaker(self) -> str:
        return max(sorted(self.words_by_speaker), key=lambda p: self.words_by_speaker[p])


GoldMap = dict[str, GoldUtterance]  # utterance ID -> gold


def _ts(dt: datetime) -> datetime:
    return dt.replace(microsecond=(dt.microsecond // 1000) * 1000)


def folder_list(scale: str = "base") -> list[tuple[str, str | None]]:
    if scale == "xl":
        from concentrate.synth.extend import XL_FOLDERS

        return [*FOLDERS, *XL_FOLDERS]
    return FOLDERS


def folder_models(seed: int, scale: str = "base") -> dict[str, granola.Folder]:
    folders = folder_list(scale)
    ids = {name: granola_id("fol", seed, name) for name, _ in folders}
    return {
        name: granola.Folder(
            id=ids[name],
            object="folder",
            name=name,
            parent_folder_id=ids[parent] if parent else None,
            space_id=None,
        )
        for name, parent in folders
    }


def folders_output(seed: int, scale: str = "base") -> granola.ListFoldersOutput:
    folders = sorted(folder_models(seed, scale).values(), key=lambda f: f.name)
    return granola.ListFoldersOutput(folders=folders, hasMore=False, cursor=None)


def _user(pid: str, rng: random.Random | None = None) -> granola.User:
    """Owner and most attendees have display names; some invites only carry an email."""
    p = person(pid)
    nameless = rng is not None and pid != OWNER_ID and rng.random() < NULL_NAME_RATE
    return granola.User(name=None if nameless else p.name, email=p.email)


# Granola-style summaries group points by topic, with progress and next steps at the end.
TOPIC = {
    "teams": "Northwind Teams launch", "falcon": "Falcon (payments migration)",
    "beacon": "Beacon onboarding", "lighthouse": "Lighthouse", "northwind": "Company metrics",
    "acmeway": "Acmeway", "beacon_freight": "Beacon Freight", "corvid": "Corvid Bank",
    "driftwood": "Driftwood Retail", "on_call": "Team", "release_testing": "Team",
    "hiring": "Hiring", "design_system": "Design system", "eng_manager_cover": "Team",
}  # fmt: skip
UPDATES, NEXT_STEPS = "Progress updates", "Next steps"


def _summary(meeting: Meeting, world: World, rng: random.Random) -> list[tuple[str, str]]:
    """(heading, bullet) pairs for this meeting, before lossy dropping."""
    points: list[tuple[str, str]] = []
    for f in world.facts:
        if f.evidence[0].meeting == meeting.key:  # first stated here (not a later restatement)
            if f.privacy and rng.random() < 0.5:
                continue
            points.append((TOPIC.get(f.subject, "Personal"), f.bullet))
    for c in world.commitments:
        who = person(c.owner).first
        if c.made_in == meeting.key:
            points.append((NEXT_STEPS, f"{who} to {c.task} by **{c.due:%A}**"))
        if any(e.meeting == meeting.key for e in c.resolved_evidence):
            if c.new_owner:
                line = f"**Reassigned:** {c.task} ({who} → {person(c.new_owner).first})"
            elif c.resolved_on and c.resolved_in == meeting.key:
                line = f"**Done:** {c.task} ({who})"
            else:
                line = f"**Slipped:** {c.task} ({who}, now due {c.new_due:%A})"
            points.append((UPDATES, line))
    return points


def _summary_markdown(points: list[tuple[str, str]], rng: random.Random) -> str:
    kept = [p for p in points if rng.random() >= LOSSY_SUMMARY_DROP]
    if not kept:
        return "### Discussion\n- General discussion; no decisions recorded."
    order: list[str] = []
    for h, _ in kept:
        if h not in order and h not in (UPDATES, NEXT_STEPS):
            order.append(h)
    order += [h for h in (UPDATES, NEXT_STEPS) if any(hh == h for hh, _ in kept)]
    blocks = []
    for h in order:
        bullets = "\n".join(f"- {b}" for hh, b in kept if hh == h)
        blocks.append(f"### {h}\n{bullets}")
    return "\n\n".join(blocks)


def _plain(markdown: str) -> str:
    return "\n".join(
        line.lstrip("#- ").replace("**", "").strip()
        for line in markdown.splitlines()
        if line.strip()
    )


def _typed_notes(points: list[tuple[str, str]], rng: random.Random) -> str | None:
    if not points or rng.random() >= TYPED_NOTES_RATE:
        return None
    chosen = rng.sample(points, min(len(points), rng.randint(1, 3)))
    lines = [
        " ".join(b.replace("*", "").split(";")[0].lower().replace(":", "").split()[:8])
        for _, b in chosen
    ]
    return "\n".join(lines)


def _merge_in_person(
    items: list[tuple[granola.TranscriptItem, int | None]],
    evidence: dict[int, list[int]],
    rng: random.Random,
) -> tuple[list[tuple[granola.TranscriptItem, int | None]], dict[int, list[int]], dict[int, int]]:
    """One shared microphone transcribes in long runs that span speakers (median ~25 words).

    Consecutive items merge until a block reaches a target length drawn log-uniformly from
    5-140 words. Evidence is remapped to the block that now contains each beat's segment.
    """
    merged: list[tuple[granola.TranscriptItem, int | None]] = []
    remap: dict[int, int] = {}
    target, words = 0.0, 0
    for k, (item, beat) in enumerate(items):
        n = len(item.text.split())
        if merged and words + n <= target:
            prev, first_beat = merged[-1]
            merged[-1] = (
                prev.model_copy(
                    update={"text": f"{prev.text} {item.text}", "end_time": item.end_time}
                ),
                first_beat if first_beat is not None else beat,
            )
            words += n
        else:
            merged.append((item, beat))
            target, words = 5 * 28 ** rng.random(), n
        remap[k] = len(merged) - 1
    new_evidence = {b: sorted({remap[k] for k in ks}) for b, ks in evidence.items()}
    return merged, new_evidence, remap


def render_meeting(
    meeting: Meeting,
    texts: list[str],
    world: World,
    seed: int,
    rates: NoiseRates,
    folders: dict[str, granola.Folder],
    gold_out: GoldMap | None = None,
) -> tuple[granola.Note, BeatMap]:
    """Render one meeting; return the note and the beat → utterance-ID map.

    If ``gold_out`` is given, it is filled with every utterance's true speaker and beats (used
    by the enhance evaluation). Recording gold draws no random numbers, so output is unchanged.
    """
    rng = random.Random(f"{seed}:render:{meeting.key}")
    owner = OWNER_ID
    labels: dict[str, str] = {}
    items: list[tuple[granola.TranscriptItem, int | None]] = []  # (item, beat index or None)
    spoken_by: list[tuple[str, int | None]] = []  # (true speaker, beat) parallel to ``items``
    t = meeting.start + timedelta(seconds=rng.uniform(20, 90))
    named = meeting.mode == "remote" and rng.random() < NAMED_MEETING_RATE
    identified = {
        pid
        for pid in sorted(meeting.attendees)
        if named and pid != owner and rng.random() < IDENTIFIED_SPEAKER_RATE
    }

    def speaker_for(pid: str) -> granola.Speaker:
        mic = meeting.mode == "in_person" or pid == owner
        extra: dict[str, str] = {}
        if meeting.key in DIARIZED:
            labels.setdefault(pid, f"Speaker {chr(ord('A') + len(labels))}")
            extra["diarization_label"] = labels[pid]
        if pid in identified and rng.random() >= UNLABELED_PASSAGE_RATE:
            extra["name"] = PEOPLE[pid].name
        return granola.Speaker(source="microphone" if mic else "speaker", **extra)

    evidence_segments: dict[int, list[int]] = {}
    for i, (beat, text) in enumerate(zip(meeting.beats, texts, strict=True)):
        protect = tuple(p for p in beat.protect if p in text)
        noisy = noisify(text, protect, rng, rates)
        segs = fragment(noisy, protect, rng) if meeting.mode == "remote" else [noisy]
        if meeting.mode == "remote" and rng.random() < LEAD_IN_RATE:
            segs = [rng.choice(LEAD_INS) + ".", *segs]
        for seg in segs:
            dur = max(0.8, len(seg.split()) / 2.6)
            item = granola.TranscriptItem(
                speaker=speaker_for(beat.speaker),
                text=seg,
                start_time=_ts(t),
                end_time=_ts(t + timedelta(seconds=dur)),
            )
            items.append((item, i))
            spoken_by.append((beat.speaker, i))
            if not protect or any(p in seg for p in protect):
                evidence_segments.setdefault(i, []).append(len(items) - 1)
            if meeting.mode == "remote" and len(seg.split()) > 6 and rng.random() < 0.25:
                other = rng.choice([a for a in meeting.attendees if a != beat.speaker])
                if (other == owner) != (beat.speaker == owner):  # only the other channel
                    bt = t + timedelta(seconds=dur * rng.uniform(0.5, 0.9))
                    items.append(
                        (
                            granola.TranscriptItem(
                                speaker=speaker_for(other),
                                text=rng.choice(BACKCHANNELS),
                                start_time=_ts(bt),
                                end_time=_ts(bt + timedelta(seconds=0.6)),
                            ),
                            None,
                        )
                    )
                    spoken_by.append((other, None))
            t += timedelta(seconds=dur + rng.uniform(0.2, 1.2))

    raw = [(pid, beat, item.text) for (item, _), (pid, beat) in zip(items, spoken_by, strict=True)]
    remap = {k: k for k in range(len(items))}
    if meeting.mode == "in_person":
        items, evidence_segments, remap = _merge_in_person(items, evidence_segments, rng)
    order = sorted(range(len(items)), key=lambda k: (items[k][0].start_time, k))
    position = {k: n for n, k in enumerate(order)}
    beat_map: BeatMap = {
        (meeting.key, i): [utterance_id(meeting.note_id, position[k]) for k in segs]
        for i, segs in evidence_segments.items()
    }
    if gold_out is not None:
        parts: dict[int, list[tuple[str, int | None, str]]] = {}
        for k, part in enumerate(raw):
            parts.setdefault(remap[k], []).append(part)
        for k, ps in parts.items():
            words: dict[str, int] = {}
            for pid, _, text in ps:
                words[pid] = words.get(pid, 0) + max(1, len(text.split()))
            beats = tuple(sorted({b for _, b, _ in ps if b is not None}))
            gold_out[utterance_id(meeting.note_id, position[k])] = GoldUtterance(words, beats)

    end = meeting.start + timedelta(minutes=meeting.minutes)
    points = _summary(meeting, world, rng)
    summary_md = _summary_markdown(points, rng)
    typed = _typed_notes(points, rng)
    event = None
    attendees: list[granola.User] = []
    if meeting.has_calendar_event:
        attendees = [_user(a, rng) for a in meeting.attendees]
        organiser = meeting.attendees[1] if meeting.kind == "customer" else owner
        event = granola.CalendarEvent(
            event_title=meeting.title,
            invitees=[granola.CalendarInvitee(email=person(a).email) for a in meeting.attendees],
            organiser=person(organiser).email,
            calendar_event_id=(
                hashlib.sha256(f"{seed}:{meeting.key}".encode()).hexdigest()[:26]
                + meeting.start.astimezone(UTC).strftime("_%Y%m%dT%H%M%SZ")
            ),
            scheduled_start_time=meeting.start,
            scheduled_end_time=end,
        )
    direct = folders[meeting.folder]
    membership = [direct]
    parent = direct.parent_folder_id
    while parent:
        f = next(f for f in folders.values() if f.id == parent)
        membership.append(f)
        parent = f.parent_folder_id

    note = granola.Note(
        id=meeting.note_id,
        object="note",
        title=meeting.title,
        owner=_user(owner),
        created_at=meeting.start.replace(second=0) + timedelta(seconds=12),
        updated_at=end + timedelta(minutes=rng.randint(1, 15)),
        deleted_at=None,
        web_url=f"https://notes.granola.ai/d/{uuid.UUID(hashlib.md5(meeting.note_id.encode()).hexdigest())}",
        calendar_event=event,
        attachments=[],
        attendees=attendees,
        folder_membership=membership,
        space_membership=[],
        summary_text=_plain(summary_md),
        summary_markdown=summary_md,
        private_notes_text=typed,
        private_notes_markdown=typed,
        transcript=[items[k][0] for k in order],
    )
    return note, beat_map
