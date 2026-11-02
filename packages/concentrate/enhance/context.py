"""Meeting context: attendee resolution and co-occurrence priors learned from earlier notes.

Everything here uses only what a deployment has: calendar attendees (some known only by an email
handle), platform-identified speaker names, and the directory. No ground truth.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from concentrate.core.models import Channel, MeetingNote, Person
from concentrate.enhance.directory import Directory
from concentrate.enhance.strings import fold, name_similarity

PLATFORM_NAME_MIN = 0.9  # fuzzy floor for a platform display name that isn't spelled exactly


def resolve_person(p: Person, directory: Directory) -> str | None:
    """Directory id for a calendar attendee: email first (works when ``name`` is null)."""
    hit = directory.by_email.get(p.email.lower())
    if hit is not None:
        return hit.id
    if p.name:
        return resolve_display_name(p.name, directory)
    return None


def resolve_display_name(name: str, directory: Directory) -> str | None:
    """Directory id for a full display name ("Sam Patel"), exact or near-exact; else ``None``."""
    exact = directory.by_full_name.get(fold(name))
    if exact and len(exact) == 1:
        return exact[0].id
    if exact:
        return None  # two people share the full name: ambiguous
    best, best_sim, runner_up = None, 0.0, 0.0
    for p in directory.people:
        sim = name_similarity(name, p.name)
        if sim > best_sim:
            best, best_sim, runner_up = p.id, sim, best_sim
        elif sim > runner_up:
            runner_up = sim
    if best is not None and best_sim >= PLATFORM_NAME_MIN and best_sim - runner_up > 0.03:
        return best
    return None


def attendee_ids(note: MeetingNote, directory: Directory) -> list[str]:
    """Owner first, then calendar attendees, resolved to directory ids (deduplicated)."""
    out: list[str] = []
    for p in (note.owner, *note.attendees):
        pid = resolve_person(p, directory)
        if pid is not None and pid not in out:
            out.append(pid)
    for email in note.invitee_emails:
        hit = directory.by_email.get(email.lower())
        if hit is not None and hit.id not in out:
            out.append(hit.id)
    return out


def series_key(note: MeetingNote) -> str:
    """Recurring meetings share a title ("Eng standup"); dates and numbers are stripped."""
    title = fold(note.title or note.event_title or "")
    return re.sub(r"[^a-z ]+", " ", title).strip()


@dataclass
class Priors:
    """Who-attends-with-whom and who-talks-how-much, accumulated over earlier meetings."""

    meetings: Counter[str] = field(default_factory=Counter)  # meetings attended per person
    pairs: Counter[tuple[str, str]] = field(default_factory=Counter)  # co-attendance
    series: dict[str, Counter[str]] = field(default_factory=dict)  # series -> attendance
    lines: Counter[str] = field(default_factory=Counter)  # identified lines spoken
    spoke_in: Counter[str] = field(default_factory=Counter)  # meetings with identified lines
    total_meetings: int = 0

    @classmethod
    def from_history(cls, history: Iterable[MeetingNote], directory: Directory) -> Priors:
        priors = cls()
        for note in history:
            priors.add(note, directory)
        return priors

    def add(self, note: MeetingNote, directory: Directory) -> None:
        """Fold one earlier meeting into the priors (attendance + platform-named lines)."""
        people = attendee_ids(note, directory)
        owner = resolve_person(note.owner, directory)
        self.total_meetings += 1
        for a in people:
            self.meetings[a] += 1
            for b in people:
                if a != b:
                    self.pairs[(a, b)] += 1
        self.series.setdefault(series_key(note), Counter()).update(people)
        remote = any(u.channel is Channel.THEM for u in note.utterances)
        spoke: set[str] = set()
        for u in note.utterances:
            pid: str | None = None
            if u.channel is Channel.THEM and u.speaker_name:
                pid = resolve_display_name(u.speaker_name, directory)
            elif u.channel is Channel.ME and remote:
                pid = owner
            if pid is not None:
                self.lines[pid] += 1
                spoke.add(pid)
        for pid in spoke:
            self.spoke_in[pid] += 1

    def cooccurrence(self, pid: str, others: Sequence[str]) -> float:
        """Average share of ``pid``'s meetings that each of ``others`` also attended (0..1)."""
        others = [o for o in others if o != pid]
        if not others or not self.meetings[pid]:
            return 0.0
        return sum(self.pairs[(pid, o)] for o in others) / (len(others) * self.meetings[pid])

    def talkativeness(self, pid: str) -> float:
        """Identified lines per meeting attended, shrunk towards the population mean and
        square-rooted: a deliberately gentle speaking-volume prior (newcomers aren't silent)."""
        attended = sum(self.meetings.values())
        mean = (sum(self.lines.values()) + 5.0) / (attended + 1.0)
        rate = (self.lines[pid] + 2.0 * mean) / (self.meetings[pid] + 2.0)
        return float(rate**0.5)

    def series_share(self, key: str, pid: str) -> float:
        """Share of past meetings in this series that ``pid`` attended (0..1)."""
        c = self.series.get(key)
        if not c:
            return 0.0
        n = max(c.values())
        return c[pid] / n if n else 0.0
