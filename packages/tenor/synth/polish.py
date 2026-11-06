"""Hybrid rendering: an LLM rewrites wording; a validator guarantees the plot survives.

One LLM call per meeting rewrites every beat. Each rewritten beat is accepted only if it passes
:func:`check_turn`; otherwise the template text is kept for that beat. Acceptance statistics
are reported so the dataset documents how much of it is LLM-written.

Rules enforced on *fact* beats: every protected span appears verbatim; no new numbers; no
person names, weekdays or months that weren't in the template. On *filler* beats: no numbers,
dates, names, commitments, or entities other than those the meeting's fact beats mention.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict

from tenor.core.spoken import spoken_numbers
from tenor.llm.client import LLMClient, LLMRequest, estimate_tokens, load_prompt
from tenor.synth.roster import ORGS, PEOPLE, PROJECTS
from tenor.synth.world import Beat, Meeting

PROMPT_ID = "synth_polish.v1"

_CALENDAR = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|"
    r"april|june|july|august|september|october|november|december)\b",  # not "may": a verb
    re.I,
)
_COMMIT = re.compile(r"\b(i'll|i will|we'll|we will|gonna|decided|decision)\b", re.I)
_NAME_TOKENS = sorted(
    {part for p in PEOPLE.values() for part in p.name.split()}
    | {v for p in PEOPLE.values() for v in p.asr_variants},
    key=len,
    reverse=True,
)
_ENTITY_TOKENS = sorted(
    {o.name for o in ORGS} | {p.name for p in PROJECTS} | {"PAY"}, key=len, reverse=True
)


def _has_token(text: str, token: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", text) is not None


def _names_in(text: str) -> set[str]:
    return {n for n in _NAME_TOKENS if _has_token(text, n)}


def _calendar_words(text: str) -> set[str]:
    return {m.lower() for m in _CALENDAR.findall(text)}


def check_turn(original: Beat, new: str, meeting_entities: set[str]) -> str | None:
    """Return a rejection reason, or None if ``new`` is an acceptable rewrite of ``original``."""
    if not new.strip():
        return "empty"
    if original.filler:
        if spoken_numbers(new) and not set(spoken_numbers(new)) <= {1}:
            return "filler has numbers"
        if _calendar_words(new):
            return "filler has dates"
        if _names_in(new):
            return "filler has names"
        if _COMMIT.search(new):
            return "filler has commitment/decision language"
        extra = {e for e in _ENTITY_TOKENS if _has_token(new, e)} - meeting_entities
        if extra:
            return f"filler mentions entities not in this meeting: {sorted(extra)}"
        return None
    for span in original.protect:
        if span not in new:
            return f"dropped protected span: {span!r}"
    if not set(spoken_numbers(new)) <= set(spoken_numbers(original.text)) | {1}:
        return "introduced new numbers"
    if not _names_in(new) <= _names_in(original.text):
        return "introduced new names"
    if not _calendar_words(new) <= _calendar_words(original.text):
        return "introduced new dates"
    return None


class _Turn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    i: int
    text: str


class _Polished(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turns: list[_Turn]


SCHEMA = {
    "type": "object",
    "properties": {
        "turns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"i": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["i", "text"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["turns"],
    "additionalProperties": False,
}


def meeting_entities(meeting: Meeting) -> set[str]:
    text = " ".join(b.text for b in meeting.beats if not b.filler)
    return {e for e in _ENTITY_TOKENS if _has_token(text, e)}


def build_request(meeting: Meeting) -> LLMRequest:
    """The polish request for one meeting (deterministic, so it caches)."""
    attendees = [
        f"{PEOPLE[a].first} ({PEOPLE[a].role}, {PEOPLE[a].org})" for a in meeting.attendees
    ]
    turns = [
        {
            "i": i,
            "speaker": PEOPLE[b.speaker].first,
            "kind": "filler" if b.filler else "fact",
            "text": b.text,
            **({"keep": list(b.protect)} if b.protect else {}),
        }
        for i, b in enumerate(meeting.beats)
    ]
    user = json.dumps(
        {"title": meeting.title, "kind": meeting.kind, "attendees": attendees, "turns": turns},
        ensure_ascii=False,
        indent=1,
    )
    return LLMRequest(
        stage="synth.polish",
        prompt_id=PROMPT_ID,
        system=load_prompt(PROMPT_ID),
        user=user,
        schema=SCHEMA,
        max_tokens=16000,
        effort="low",
    )


class PolishStats(BaseModel):
    turns: int = 0
    accepted: int = 0
    rejected: dict[str, int] = {}
    missing: int = 0


def polish_meeting(meeting: Meeting, client: LLMClient, stats: PolishStats) -> list[str]:
    """Return final clean text per beat: the LLM rewrite where valid, else the template."""
    result = client.structured(build_request(meeting), _Polished)
    by_i = {t.i: t.text.strip() for t in result.turns}
    entities = meeting_entities(meeting)
    out: list[str] = []
    for i, beat in enumerate(meeting.beats):
        stats.turns += 1
        new = by_i.get(i)
        if new is None:
            stats.missing += 1
            out.append(beat.text)
            continue
        reason = check_turn(beat, new, entities)
        if reason is None:
            stats.accepted += 1
            out.append(new)
        else:
            key = reason.split(":")[0]
            stats.rejected[key] = stats.rejected.get(key, 0) + 1
            out.append(beat.text)
    return out


def estimate_cost_tokens(meetings: Iterable[Meeting]) -> tuple[int, int]:
    """(input, output) token estimate for polishing ``meetings`` (offline heuristic)."""
    tin = tout = 0
    for m in meetings:
        req = build_request(m)
        tin += estimate_tokens(req.system) + estimate_tokens(req.user)
        tout += estimate_tokens(" ".join(b.text for b in m.beats)) * 13 // 10 + 20 * len(m.beats)
    return tin, tout
