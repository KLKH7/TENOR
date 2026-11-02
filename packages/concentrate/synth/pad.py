"""Pad meetings to realistic length with fact-free filler turns.

Real meeting transcripts are mostly discussion that carries no extractable fact; a benchmark
without it would make retrieval and compression look far easier than they are. Filler turns are
inserted only *between* evidence groups (never between a question and its answer) and every
fact/commitment's evidence indices are remapped afterwards.

Template filler is generic on purpose. In ``--polish`` mode the LLM rewrites it into discussion
that fits the meeting, under a validator that forbids numbers, dates, names and commitments
(``synth.polish``).
"""

from __future__ import annotations

import random

from concentrate.synth.roster import OWNER_ID
from concentrate.synth.world import Beat, Evidence, Meeting, World

TARGET_TURNS = {
    "standup": 45, "one_on_one": 40, "customer": 55, "design_review": 55, "all_hands": 70,
    "planning": 50, "decision": 55, "sync": 40, "investor": 45, "review": 50,
}  # fmt: skip

_COMMON = [
    "Yeah, that makes sense to me.",
    "Sorry, can you say that again? You cut out for a second.",
    "Let me pull up the doc so we're all looking at the same thing.",
    "I think that's mostly right, but there's a nuance there.",
    "Can you share your screen?",
    "Okay, so where did we land on that last time?",
    "I'm not sure that's the right framing, honestly.",
    "Right, right. Keep going.",
    "That's fair. I hadn't thought about it that way.",
    "Do we have any data on that or is it gut feel?",
    "Let's not go down that rabbit hole right now.",
    "Can we park that and come back to it at the end?",
    "Hold on, my laptop is about to die, let me grab a charger.",
    "I think the question is really about scope.",
    "Yeah, I agree with that.",
    "Sorry, I was on mute. I was saying I agree.",
]
FILLER: dict[str, list[str]] = {
    "eng": [
        "The test suite has been pretty flaky this week, just flagging it.",
        "I spent most of yesterday in code review, there was a lot in the queue.",
        "Is the staging environment back up? It was down earlier.",
        "I paired on the migration stuff for a bit, it's gnarly but fine.",
        "I don't have any blockers really.",
        "The logs are super noisy right now, it's hard to find anything.",
        "We should probably write that down somewhere so we don't forget.",
        "I looked at the error dashboard and nothing scary.",
        "There's a lot of tech debt in that area, for what it's worth.",
        "Something to keep an eye on, anyway.",
    ],
    "customer": [
        "Thanks for the context, that's super helpful.",
        "How are your teams using it day to day right now?",
        "Our managers mostly live in email, if that helps.",
        "That's a common request from customers your size.",
        "Can you walk me through how that process works today?",
        "We've had some internal changes on our side, so bear with us.",
        "Honestly, the team likes it so far.",
        "Is there anyone else on your side we should loop in?",
        "That's really useful feedback, I'm writing it down.",
        "Our procurement process can be a bit slow, just so you know.",
    ],
    "design": [
        "The empty state still feels a bit cold to me.",
        "Can we try a version with less copy on that screen?",
        "I like the hierarchy, the primary action is much clearer now.",
        "The illustration style doesn't quite match the rest of the app.",
        "Users kept hesitating on that screen in the sessions.",
        "What happens on mobile? The layout looks tight.",
        "I'd want to see it with real data before we commit.",
        "The button labels could be more specific.",
        "That spacing feels off, but that's a polish thing.",
    ],
    "leadership": [
        "We need to be careful not to overcommit here.",
        "What's the downside if we're wrong about this?",
        "I'd rather make a call today than drag it out.",
        "Let's make sure sales and marketing hear this at the same time.",
        "I'm worried about how this lands with existing customers.",
        "The board will ask about this, so let's have a clear story.",
        "What do we need to believe for this to work?",
        "I don't love it, but I can live with it.",
        "We've been going back and forth on this for a while.",
    ],
    "one_on_one": [
        "How are you feeling about the workload generally?",
        "Honestly, it's been a busy couple of weeks.",
        "Anything I can do to unblock you?",
        "I've been thinking a lot about where I want to grow.",
        "The team dynamic feels good right now.",
        "I'd like more time for deep work if possible.",
        "That's good to hear.",
        "Let's make sure we write down goals for next quarter.",
    ],
    "all_hands": [
        "Welcome to everyone who joined recently.",
        "Shout out to the support team, they've been amazing.",
        "If you have questions, drop them in the chat.",
        "We'll share the slides afterwards.",
        "A few reminders on the office before we wrap.",
        "Thanks everyone for the hard work this month.",
        "Let's do a quick round of wins from each team.",
    ],
}
_GROUP = {
    "standup": "eng", "planning": "eng", "review": "eng", "customer": "customer",
    "design_review": "design", "decision": "leadership", "sync": "leadership",
    "investor": "leadership", "one_on_one": "one_on_one", "all_hands": "all_hands",
}  # fmt: skip


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
        target = round(TARGET_TURNS.get(meeting.kind, 45) * factor)
        need = max(0, target - len(meeting.beats))
        locked = _locked_gaps(meeting, world)
        # Never before the opener or after the closer.
        gaps = [g for g in range(1, len(meeting.beats)) if g not in locked]
        pool = FILLER[_GROUP[meeting.kind]] + _COMMON
        speakers = _speakers(meeting)
        inserts: dict[int, list[Beat]] = {}
        deck: list[str] = []
        for _ in range(need):
            if not deck:  # draw without replacement; reshuffle only when the pool runs out
                deck = rng.sample(pool, len(pool))
            gap = rng.choice(gaps)
            who = OWNER_ID if rng.random() < 0.3 else rng.choice(speakers)
            inserts.setdefault(gap, []).append(Beat(speaker=who, text=deck.pop(), filler=True))
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
