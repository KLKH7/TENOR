"""Transcript fallback: raw transcript passages for what the write-time memory never stored.

Concentrate answers from extracted facts plus the lines they cite. On real meetings that loses the
long tail ("who proposed X?", a passing date): the details were never extracted
(docs/REAL_SLICE.md). The fallback retrieves transcript passages at question time, using b0's
15-utterance chunks and the same BM25 + embedding ``HybridIndex``, and keeps every line ID so
answers still cite lines.

Rules the passages obey:
- only meetings held on or before the question's as-of date;
- never a retired meeting (``retire_after_days``): retired transcripts stay unreadable;
- a fixed token budget (best-ranked chunks first, then shown in meeting/line order).

Speaker labels: Enhance's resolved names can be wrong on real data, so each line shows the raw
channel label Granola gives (``Me`` = the note owner's microphone, ``Them`` = system audio, plus any
name Granola itself attached) and, only when it adds something, Enhance's guess marked as a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from concentrate.agents import corpus as cv
from concentrate.agents.retrieval import Embedder, HybridIndex
from concentrate.core.models import MeetingNote, Utterance
from concentrate.llm.client import estimate_tokens

FALLBACK_MODES = ("off", "on_abstain", "always")
DEFAULT_FALLBACK_TOKENS = 3_500

# The answer said the material doesn't cover the question (with or without setting abstain).
_NOT_COVERED = re.compile(
    r"\b(?:do(?:es)?|did)(?:n['\u2019]t| not)\s+(?:\w+\s+){0,3}"
    r"(?:mention|cover|record|contain|include|say|state|specify|show|indicate)"
    r"|\bno (?:record|mention|information|indication)\b(?! of (?:a |any )?follow)"
    r"|\bnot (?:mentioned|recorded|covered|stated|specified|in the (?:material|memory))\b"
    r"|\b(?:isn't|is not|aren't|are not) (?:in|covered|recorded|mentioned)\b"
    r"|\bcan(?:no|')t (?:find|tell|determine|say)\b",
    re.IGNORECASE,
)


def says_not_covered(answer: str, abstained: bool) -> bool:
    """True if a memory-only answer abstained or said the material doesn't contain the answer.
    "No record of a follow-up" is a real commitment-status answer (answer.v1 asks for it), not a
    gap, so it doesn't count."""
    return abstained or bool(_NOT_COVERED.search(answer))


def resolved_labels(l1_lines: dict[str, str]) -> dict[str, str]:
    """Utterance ID -> Enhance's speaker label, parsed from L1 lines ``[id] Label: text``."""
    out = {}
    for uid, text in l1_lines.items():
        body = text.split("] ", 1)[-1]
        if ": " in body:
            out[uid] = body.split(": ", 1)[0]
    return out


def _day(m: MeetingNote) -> str:
    return (m.scheduled_start or m.created_at).date().isoformat()


@dataclass
class Passages:
    text: str  # rendered block ("" when nothing qualifies)
    line_ids: list[str] = field(default_factory=list)
    meetings: list[str] = field(default_factory=list)
    tokens: int = 0


class TranscriptFallback:
    """Hybrid index over every meeting's transcript chunks (built once, after ingest)."""

    def __init__(
        self,
        notes: list[MeetingNote],
        embedder: Embedder | None = None,
        resolved: dict[str, str] | None = None,
    ) -> None:
        """``resolved`` maps utterance ID -> Enhance's speaker label (from the L1 lines)."""
        self.notes = {m.note_id: m for m in notes}
        self.resolved = resolved or {}
        self.chunks = [c for m in cv.by_date(notes) for c in cv.chunks(m)]
        docs = [f"{self.notes[c.note_id].title}\n{c.text}" for c in self.chunks]
        self.index = HybridIndex(docs, embedder) if docs else None

    def line(self, u: Utterance) -> str:
        raw = cv.speaker_label(u)
        guess = self.resolved.get(u.utterance_id, "")
        label = raw
        if guess and guess not in raw and not guess.startswith(("Me", "Them")):
            label = f"{raw}, Enhance guess: {guess}"
        return f"[{u.utterance_id}] {label}: {u.text}"

    def passages(
        self,
        query: str,
        as_of: date,
        *,
        budget_tokens: int = DEFAULT_FALLBACK_TOKENS,
        retire_after_days: int | None = None,
        skip_lines: set[str] | None = None,
    ) -> Passages:
        """Best chunks for ``query`` from readable meetings, up to ``budget_tokens``."""
        if self.index is None or budget_tokens <= 0:
            return Passages("")
        day = as_of.isoformat()
        cutoff = (
            (as_of - timedelta(days=retire_after_days)).isoformat()
            if retire_after_days is not None
            else None
        )
        skip = skip_lines or set()
        picked: list[tuple[int, list[Utterance]]] = []
        used = 0
        for i in self.index.search(query, k=len(self.chunks)):
            c = self.chunks[i]
            d = _day(self.notes[c.note_id])
            if d > day or (cutoff is not None and d < cutoff):
                continue
            us = [u for u in c.utterances if u.utterance_id not in skip]
            if not us:
                continue
            cost = estimate_tokens("\n".join(self.line(u) for u in us))
            if used + cost > budget_tokens:
                if used:
                    break
                continue  # one oversized chunk: try the next one
            picked.append((i, us))
            used += cost
        if not picked:
            return Passages("")
        picked.sort(key=lambda p: p[0])  # chunks are stored in meeting-date, line order
        blocks: dict[str, list[str]] = {}
        ids: list[str] = []
        for i, us in picked:
            nid = self.chunks[i].note_id
            blocks.setdefault(nid, []).extend(self.line(u) for u in us)
            ids += [u.utterance_id for u in us]
        body = "\n\n".join(
            f"### {self.notes[n].title} [{n}] {_day(self.notes[n])}\n" + "\n".join(ls)
            for n, ls in blocks.items()
        )
        text = (
            "<transcript_passages>\n"
            "Raw transcript passages retrieved for this question (search, not verified facts). "
            'Speaker labels are channels: "Me" is the note owner\'s microphone (in-person '
            'meetings put everyone on it); "Them" is system audio (anyone else on the call). '
            'A name after "Enhance guess:" is an automatic attribution that can be wrong; '
            "say who said something only when the words or the channel make it clear.\n\n"
            f"{body}\n</transcript_passages>"
        )
        return Passages(text, ids, list(blocks), estimate_tokens(text))
