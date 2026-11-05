"""Pair the same meeting across two products (e.g. a Granola note and a Wispr Flow recording).

Every cross-product comparison needs *identical meetings*: the same conversation, recorded by
both products at once. This module finds those pairs from what each product exposes:

1. **Time gate.** Start times (calendar start, else creation) within ``±window`` (10 min).
   Candidates outside it are never paired automatically.
2. **Score** = weighted mean of the signals both sides have (weights renormalised over the
   signals present):
   - transcript similarity (weight 0.6): containment of word 3-gram shingles, i.e. the share of
     the shorter transcript's shingles that also occur in the longer one. Two recordings of one
     conversation by different ASR systems share many exact 3-grams; different meetings rarely do;
   - attendees (0.2): Jaccard over emails if both sides have them, else over lower-cased names;
   - title (0.2): Jaccard over lower-cased word tokens.
3. **Assignment.** Greedy one-to-one in descending score (ties broken by ids, so the result is
   deterministic), keeping pairs with ``score >= threshold``.

A manual override file (``data/real/pairs.override.json``, private) wins over everything:
``pairs`` are forced, ``never`` pairs are excluded. See docs/benchmark/PRODUCTS.md.

The output names meeting IDs and scores only: no titles, names or text, so printing it is safe.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from concentrate.core.models import MeetingNote

DEFAULT_WINDOW = timedelta(minutes=10)
DEFAULT_THRESHOLD = 0.35
WEIGHTS = {"transcript": 0.6, "attendees": 0.2, "title": 0.2}
SHINGLE = 3
_WORD = re.compile(r"[a-z0-9']+")


class PairOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: str  # meeting ID in product A (e.g. Granola)
    b: str  # meeting ID in product B (e.g. Wispr)


class Overrides(BaseModel):
    """``data/real/pairs.override.json``: hand-checked corrections to the matcher."""

    model_config = ConfigDict(extra="forbid")
    pairs: list[PairOverride] = Field(default_factory=list)  # always pair these
    never: list[PairOverride] = Field(default_factory=list)  # never pair these

    @classmethod
    def load(cls, path: Path | None) -> Overrides:
        if path is None or not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text())


class Pair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: str
    b: str
    score: float
    signals: dict[str, float]  # transcript / attendees / title / start_delta_s
    method: str  # "auto" or "manual"


class Pairing(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_a: str
    product_b: str
    window_s: float
    threshold: float
    pairs: list[Pair]
    unpaired_a: list[str]
    unpaired_b: list[str]


def _start(n: MeetingNote) -> datetime:
    return n.scheduled_start or n.created_at


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def shingles(text: str, k: int = SHINGLE) -> set[tuple[str, ...]]:
    w = _words(text)
    return {tuple(w[i : i + k]) for i in range(len(w) - k + 1)}


def containment(a: set[tuple[str, ...]], b: set[tuple[str, ...]]) -> float | None:
    if not a or not b:
        return None
    return len(a & b) / min(len(a), len(b))


def jaccard(a: set[str], b: set[str]) -> float | None:
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


def _people(n: MeetingNote) -> tuple[set[str], set[str]]:
    people = [*n.attendees, n.owner]
    emails = {p.email.lower() for p in people if p.email}
    names = {p.name.lower() for p in people if p.name}
    return emails, names


def _transcript(n: MeetingNote) -> str:
    return " ".join(u.text for u in n.utterances)


def signals(a: MeetingNote, b: MeetingNote) -> dict[str, float | None]:
    """Each similarity signal in [0, 1], or None when either side lacks the data."""
    ea, na = _people(a)
    eb, nb = _people(b)
    people = jaccard(ea, eb) if ea and eb else jaccard(na, nb)
    return {
        "transcript": containment(shingles(_transcript(a)), shingles(_transcript(b))),
        "attendees": people,
        "title": jaccard(set(_words(a.title or "")), set(_words(b.title or ""))),
    }


def score(sig: dict[str, float | None]) -> float:
    present = {k: v for k, v in sig.items() if v is not None}
    if not present:
        return 0.0
    total = sum(WEIGHTS[k] for k in present)
    return sum(WEIGHTS[k] * v for k, v in present.items()) / total


def pair_meetings(
    a_notes: Iterable[MeetingNote],
    b_notes: Iterable[MeetingNote],
    *,
    product_a: str = "granola",
    product_b: str = "wispr",
    window: timedelta = DEFAULT_WINDOW,
    threshold: float = DEFAULT_THRESHOLD,
    overrides: Overrides | None = None,
) -> Pairing:
    """Pair meetings recorded by both products. Deterministic for the same inputs."""
    a_by = {n.note_id: n for n in a_notes}
    b_by = {n.note_id: n for n in b_notes}
    ov = overrides or Overrides()
    never = {(p.a, p.b) for p in ov.never}
    pairs: list[Pair] = []
    used_a: set[str] = set()
    used_b: set[str] = set()

    def make(a: MeetingNote, b: MeetingNote, method: str) -> Pair:
        sig = signals(a, b)
        delta = abs((_start(a) - _start(b)).total_seconds())
        shown = {k: round(v, 4) for k, v in sig.items() if v is not None}
        return Pair(
            a=a.note_id,
            b=b.note_id,
            score=round(score(sig), 4),
            signals={**shown, "start_delta_s": delta},
            method=method,
        )

    for p in ov.pairs:
        if p.a in a_by and p.b in b_by and p.a not in used_a and p.b not in used_b:
            pairs.append(make(a_by[p.a], b_by[p.b], "manual"))
            used_a.add(p.a)
            used_b.add(p.b)

    candidates: list[Pair] = []
    for a in sorted(a_by.values(), key=lambda n: n.note_id):
        if a.note_id in used_a:
            continue
        for b in sorted(b_by.values(), key=lambda n: n.note_id):
            if b.note_id in used_b or (a.note_id, b.note_id) in never:
                continue
            if abs(_start(a) - _start(b)) > window:
                continue
            c = make(a, b, "auto")
            if c.score >= threshold:
                candidates.append(c)
    for c in sorted(candidates, key=lambda c: (-c.score, c.a, c.b)):
        if c.a in used_a or c.b in used_b:
            continue
        pairs.append(c)
        used_a.add(c.a)
        used_b.add(c.b)

    pairs.sort(key=lambda p: (_start(a_by[p.a]), p.a))
    return Pairing(
        product_a=product_a,
        product_b=product_b,
        window_s=window.total_seconds(),
        threshold=threshold,
        pairs=pairs,
        unpaired_a=sorted(set(a_by) - used_a),
        unpaired_b=sorted(set(b_by) - used_b),
    )
