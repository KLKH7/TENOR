"""Pad meetings to realistic length with fact-free filler turns.

Real meeting transcripts are mostly discussion that carries no extractable fact; a benchmark
without it would make retrieval and compression look far easier than they are. Filler turns are
inserted only *between* evidence groups (never between a question and its answer) and every
fact/commitment's evidence indices are remapped afterwards.

Filler is composed per meeting (``synth.filler``) and the amount scales with the calendar
slot. In ``--polish`` mode the LLM rewrites it into discussion
that fits the meeting, under a validator that forbids numbers, dates, names and commitments
(``synth.polish``).
"""

from __future__ import annotations

import random

from tenor.synth.filler import GROUP, FillerContext, turn
from tenor.synth.roster import ORG_BY_ID, OWNER_ID, PROJECT_BY_ID
from tenor.synth.world import Beat, Evidence, Meeting, World

EXTRAS_PREFIX = "v21-"  # == synth.extras.PREFIX (not imported: extras imports the renderer)

# Slot-scaled length: real remote meetings run ~3,500 words in a 30-minute slot (FIDELITY.md).
WORDS_PER_SLOT_MINUTE = 120


def _topics(meeting: Meeting, world: World) -> tuple[str, ...]:
    """Project/customer names this meeting's filler may mention (never people, never codes)."""
    ids = {f.subject for f in world.facts if any(e.meeting == meeting.key for e in f.evidence)}
    if meeting.customer:
        ids.add(meeting.customer)
    names: list[str] = []
    for i in sorted(ids):
        if i in PROJECT_BY_ID:
            p = PROJECT_BY_ID[i]
            names += [p.name, *(a for a in p.aliases if not any(ch.isdigit() for ch in a))]
        elif i in ORG_BY_ID:
            names.append(ORG_BY_ID[i].name)
    return tuple(names)


def _locked_gaps(meeting: Meeting, world: World) -> set[int]:
    """Gap ``g`` sits between beat g-1 and g; a gap inside an evidence group is locked."""
    locked: set[int] = set()
    groups: list[list[int]] = []
    for f in world.facts:
        groups.append([e.beat for e in f.evidence if e.meeting == meeting.key])
    for c in world.commitments:
        groups.append([e.beat for e in c.made_evidence if e.meeting == meeting.key])
        groups.append([e.beat for e in c.resolved_evidence if e.meeting == meeting.key])
    for g in groups:
        if len(g) > 1:
            locked.update(range(min(g) + 1, max(g) + 1))
    return locked


def _speakers(meeting: Meeting) -> list[str]:
    speakers = sorted({b.speaker for b in meeting.beats} | {OWNER_ID})
    return speakers if meeting.kind == "all_hands" else sorted(set(meeting.attendees))


def pad_world(world: World, seed: int, factor: float = 1.0) -> World:
    """Return a copy of ``world`` with filler beats inserted and evidence remapped."""
    rng = random.Random(f"{seed}:pad")
    world = world.model_copy(deep=True)
    remap: dict[str, dict[int, int]] = {}
    for meeting in world.meetings:
        # v2.1 extras meetings draw from their own stream, so v2 meetings keep their filler.
        own = meeting.key.startswith(EXTRAS_PREFIX)
        r = random.Random(f"{seed}:pad:{meeting.key}") if own else rng
        target = round(meeting.minutes * WORDS_PER_SLOT_MINUTE * factor)
        words = sum(len(b.text.split()) for b in meeting.beats)
        locked = _locked_gaps(meeting, world)
        # Never before the opener or after the closer.
        gaps = [g for g in range(1, len(meeting.beats)) if g not in locked]
        ctx = FillerContext(GROUP[meeting.kind], _topics(meeting, world))
        speakers = _speakers(meeting)
        inserts: dict[int, list[Beat]] = {}
        while words < target:
            gap = r.choice(gaps)
            who = OWNER_ID if r.random() < 0.3 else r.choice(speakers)
            text = turn(ctx, r)
            words += len(text.split())
            # Names are protected so ASR noise and passage cuts never split "Beacon Freight".
            protect = tuple(t for t in ctx.topics if t in text)
            inserts.setdefault(gap, []).append(
                Beat(speaker=who, text=text, protect=protect, filler=True)
            )
        new_beats: list[Beat] = []
        mapping: dict[int, int] = {}
        for i, beat in enumerate(meeting.beats):
            new_beats.extend(inserts.get(i, []))
            mapping[i] = len(new_beats)
            new_beats.append(beat)
        meeting.beats = new_beats
        remap[meeting.key] = mapping

    def fix(evs: list[Evidence]) -> list[Evidence]:
        return [Evidence(meeting=e.meeting, beat=remap[e.meeting][e.beat]) for e in evs]

    for f in world.facts:
        f.evidence = fix(f.evidence)
    for c in world.commitments:
        c.made_evidence = fix(c.made_evidence)
        c.resolved_evidence = fix(c.resolved_evidence)
    return world
