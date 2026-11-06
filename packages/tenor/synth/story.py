"""Authoring helpers for the scenario: a builder that keeps facts, beats and evidence in sync.

``Story`` owns the seeded RNG. Every random choice in world construction goes through it, in a
fixed order, so the same seed yields the same world.
"""

from __future__ import annotations

import hashlib
import random
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from tenor.synth.roster import OWNER_ID, person
from tenor.synth.world import (
    Beat,
    Commitment,
    Evidence,
    Fact,
    Meeting,
    MeetingMode,
    Outcome,
    Platform,
    PrivacyClass,
)

START = date(2026, 8, 31)  # Monday of week 1
_ALNUM = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def day(week: int, weekday: int) -> date:
    """Date of ``weekday`` (0=Mon) in benchmark ``week`` (1-based)."""
    return START + timedelta(days=7 * (week - 1) + weekday)


def granola_id(prefix: str, seed: int, key: str) -> str:
    """Deterministic Granola-shaped ID: ``prefix_`` + 14 alphanumerics."""
    digest = int.from_bytes(hashlib.sha256(f"{seed}:{prefix}:{key}".encode()).digest(), "big")
    chars = []
    for _ in range(14):
        digest, r = divmod(digest, len(_ALNUM))
        chars.append(_ALNUM[r])
    return f"{prefix}_{''.join(chars)}"


class M:
    """Mutable handle on a meeting under construction."""

    def __init__(self, meeting: Meeting) -> None:
        self.m = meeting

    @property
    def key(self) -> str:
        return self.m.key

    @property
    def day(self) -> date:
        return self.m.day

    def say(
        self,
        who: str,
        text: str,
        *protect: str,
        privacy: PrivacyClass | None = None,
    ) -> int:
        """Append a beat; ``protect`` spans must occur verbatim in ``text``."""
        for span in protect:
            if span not in text:
                raise ValueError(f"protected span {span!r} not in beat {text!r} ({self.key})")
        if who not in self.m.attendees and who != OWNER_ID:
            raise ValueError(f"{who} speaks in {self.key} but is not an attendee")
        self.m.beats.append(Beat(speaker=who, text=text, protect=protect, privacy=privacy))
        return len(self.m.beats) - 1

    def tag(self, beat: int, fact_id: str) -> None:
        b = self.m.beats[beat]
        self.m.beats[beat] = b.model_copy(update={"facts": (*b.facts, fact_id)})


class Story:
    """Collects meetings, facts and commitments; enforces cross-references."""

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self.rng = random.Random(seed)
        self.meetings: list[M] = []
        self.facts: list[Fact] = []
        self.commitments: list[Commitment] = []
        self._handles: dict[str, M] = {}

    # -- meetings ---------------------------------------------------------------------------

    def meeting(
        self,
        key: str,
        title: str,
        kind: str,
        week: int,
        weekday: int,
        hour: float,
        minutes: int,
        attendees: list[str],
        *,
        folder: str,
        mode: MeetingMode = "remote",
        platform: Platform | None = None,
        customer: str | None = None,
        calendar: bool = True,
    ) -> M:
        if key in self._handles:
            raise ValueError(f"duplicate meeting key {key}")
        start = datetime.combine(
            day(week, weekday),
            time(int(hour), round((hour % 1) * 60)),
            tzinfo=UTC,
        )
        # Small seeded jitter: real meetings rarely start exactly on the hour.
        start += timedelta(minutes=self.rng.choice([0, 0, 1, 2, 3]))
        meeting = Meeting(
            key=key,
            note_id=granola_id("not", self.seed, key),
            title=title,
            kind=kind,
            week=week,
            start=start,
            minutes=minutes,
            attendees=[OWNER_ID, *[a for a in attendees if a != OWNER_ID]],
            mode=mode,
            platform=platform or ("in_person" if mode == "in_person" else "zoom"),
            folder=folder,
            customer=customer,
            has_calendar_event=calendar,
        )
        handle = M(meeting)
        self.meetings.append(handle)
        self._handles[key] = handle
        return handle

    def handle(self, key: str) -> M:
        return self._handles[key]

    # -- facts ------------------------------------------------------------------------------

    def current(self, subject: str, predicate: str) -> Fact | None:
        live = [
            f for f in self.facts
            if f.subject == subject and f.predicate == predicate and f.valid_to is None
        ]  # fmt: skip
        return live[-1] if live else None

    def fact(
        self,
        m: M,
        beats: list[int],
        *,
        kind: str,
        subject: str,
        predicate: str,
        value: str,
        value_norm: str,
        spoken: tuple[str, ...],
        bullet: str,
        reason: str | None = None,
        reason_spoken: tuple[str, ...] = (),
        privacy: PrivacyClass | None = None,
    ) -> Fact:
        """Register a fact stated in ``beats`` of ``m``; supersede the current version if any."""
        text = " ".join(m.m.beats[b].text for b in beats)
        for span in (*spoken, *reason_spoken):
            if span not in text:
                raise ValueError(f"fact span {span!r} not stated in {m.key} beats {beats}")
            if not any(span in p for b in beats for p in m.m.beats[b].protect):
                raise ValueError(f"fact span {span!r} in {m.key} is not protected")
        previous = self.current(subject, predicate)
        fid = f"f{len(self.facts) + 1:03d}"
        if previous is not None:
            if previous.value_norm == value_norm:
                raise ValueError(f"{fid} restates {previous.id}; use confirm()")
            previous.valid_to = m.day
        fact = Fact(
            id=fid,
            kind=kind,
            subject=subject,
            predicate=predicate,
            value=value,
            value_norm=value_norm,
            spoken=spoken,
            valid_from=m.day,
            evidence=[Evidence(meeting=m.key, beat=b) for b in beats],
            supersedes=previous.id if previous else None,
            reason=reason,
            reason_spoken=reason_spoken,
            privacy=privacy,
            bullet=bullet,
        )
        self.facts.append(fact)
        for b in beats:
            m.tag(b, fid)
        return fact

    def confirm(self, fact: Fact, m: M, beats: list[int]) -> None:
        """Record that ``fact`` is restated later (adds evidence, no new version)."""
        fact.evidence.extend(Evidence(meeting=m.key, beat=b) for b in beats)
        for b in beats:
            m.tag(b, fact.id)

    # -- commitments ------------------------------------------------------------------------

    def commit(
        self,
        m: M,
        beats: list[int],
        *,
        owner: str,
        task: str,
        task_spoken: str,
        due: date,
        outcome: Outcome,
    ) -> Commitment:
        """Register a commitment; its resolution is attached later with :meth:`resolve`."""
        cid = f"c{len(self.commitments) + 1:03d}"
        c = Commitment(
            id=cid,
            owner=owner,
            task=task,
            task_spoken=task_spoken,
            due=due,
            made_in=m.key,
            made_evidence=[Evidence(meeting=m.key, beat=b) for b in beats],
            outcome=outcome,
        )
        self.commitments.append(c)
        for b in beats:
            m.tag(b, cid)
        return c

    def resolve(self, c: Commitment, m: M, beats: list[int], **update: Any) -> None:
        """Attach evidence of what happened to ``c`` (completion, slip, reassignment)."""
        c.resolved_evidence.extend(Evidence(meeting=m.key, beat=b) for b in beats)
        c.resolved_in = m.key
        for k, v in update.items():
            setattr(c, k, v)
        for b in beats:
            m.tag(b, c.id)

    # -- misc -------------------------------------------------------------------------------

    def pick(self, options: list[str]) -> str:
        return self.rng.choice(options)

    def first(self, pid: str) -> str:
        return person(pid).first
