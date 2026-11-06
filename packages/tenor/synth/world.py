"""Ground-truth world model for the synthetic benchmark.

The world is built *before* any text is rendered: a list of meetings made of **beats** (one
speaker turn each, in clean spoken form) and a timeline of **facts** and **commitments** that
point at the beats that state them. Rendering (noise, LLM polish, Granola JSON) maps beats to
utterances, so every fact keeps exact gold utterance IDs.

A beat's ``protect`` spans are the words that carry a fact (e.g. "twenty five dollars"). They
are exempt from injected ASR noise and must survive LLM polish verbatim; this is what lets the
question validator prove each gold answer is stated in its gold evidence.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PrivacyClass = Literal["health", "family", "compensation"]
MeetingMode = Literal["remote", "in_person"]
Platform = Literal["zoom", "meet", "in_person"]
Outcome = Literal["kept", "slipped", "dropped", "reassigned", "open"]


class Beat(BaseModel):
    """One speaker turn, before noise or polish."""

    model_config = ConfigDict(frozen=True)

    speaker: str  # person id
    text: str
    protect: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    privacy: PrivacyClass | None = None
    filler: bool = False  # topical padding: carries no facts, rewritten freely by polish


class Evidence(BaseModel):
    model_config = ConfigDict(frozen=True)

    meeting: str  # meeting key
    beat: int


class Fact(BaseModel):
    """An atomic, time-scoped statement about an entity."""

    id: str
    kind: str  # decision | metric | date | ownership | request | deal | attribute | status
    subject: str  # entity id (person, org or project)
    predicate: str
    value: str  # human-readable answer, e.g. "$25 per seat per month"
    value_norm: str  # normalized value for exact match, e.g. "25" / "2026-10-28" / "tomas"
    spoken: tuple[str, ...]  # protected spans that state the value in the transcript
    valid_from: date
    valid_to: date | None = None
    evidence: list[Evidence]
    supersedes: str | None = None
    reason: str | None = None  # why it changed (for decision-change questions)
    reason_spoken: tuple[str, ...] = ()
    privacy: PrivacyClass | None = None
    bullet: str  # how an AI meeting summary would phrase it


class Commitment(BaseModel):
    """Someone said they would do something by a date; the world decides what happened."""

    id: str
    owner: str
    task: str  # e.g. "finish the PAY-212 cutover script"
    task_spoken: str  # protected span used in the transcript
    due: date
    made_in: str
    made_evidence: list[Evidence]
    outcome: Outcome
    resolved_in: str | None = None
    resolved_on: date | None = None
    resolved_evidence: list[Evidence] = Field(default_factory=list)
    new_owner: str | None = None
    new_due: date | None = None


class SummarySection(BaseModel):
    heading: str
    bullets: list[str]


class Meeting(BaseModel):
    key: str  # stable slug, e.g. "w2-pricing-review"
    note_id: str
    title: str
    kind: str  # standup | one_on_one | customer | design_review | all_hands | ...
    week: int
    start: datetime
    minutes: int
    attendees: list[str]
    mode: MeetingMode = "remote"
    platform: Platform = "zoom"
    folder: str
    customer: str | None = None
    has_calendar_event: bool = True
    beats: list[Beat] = Field(default_factory=list)
    summary: list[SummarySection] = Field(default_factory=list)
    typed_notes: list[str] = Field(default_factory=list)

    @property
    def day(self) -> date:
        return self.start.date()


class Room(BaseModel):
    """A conference room on a call: one system-audio channel carrying several in-room voices."""

    name: str  # how Granola labels the channel, e.g. "Room 4B"
    email: str  # the calendar resource invited to the meeting
    members: list[str]  # person ids physically in the room (ground truth only)


class ExtrasTruth(BaseModel):
    """Ground truth for the v2.1 phenomena (docs/archive/northwind/V2_EXTRAS.md); None in v2."""

    rooms: dict[str, Room] = Field(default_factory=dict)  # meeting key -> room on that call
    retracted: list[str] = Field(default_factory=list)  # fact ids that were misstatements
    implicit: list[str] = Field(default_factory=list)  # commitments made without "I'll ... by"
    out_of_band: list[str] = Field(default_factory=list)  # resolved outside any meeting


class World(BaseModel):
    seed: int
    owner: str
    start: date
    meetings: list[Meeting]
    facts: list[Fact]
    commitments: list[Commitment]
    extras: ExtrasTruth | None = None

    def meeting(self, key: str) -> Meeting:
        return next(m for m in self.meetings if m.key == key)

    def facts_about(self, subject: str, predicate: str) -> list[Fact]:
        """All versions of (subject, predicate), oldest first."""
        return sorted(
            (f for f in self.facts if f.subject == subject and f.predicate == predicate),
            key=lambda f: f.valid_from,
        )
