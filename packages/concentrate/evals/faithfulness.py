"""The **notes faithfulness** track: are a system's meeting notes grounded in the transcript?

Product-neutral. The input is a :class:`NotesSystem`: for each meeting, its transcript and
calendar context (a :class:`MeetingNote`) plus *that system's* notes for the meeting, as
markdown. Each entry comes in through a small adapter: :func:`granola_system` (Granola's AI
notes, ``summary_markdown`` from the private real sync) or :func:`northwind_system` (the
synthetic generator's summaries, a baseline). Any other notetaker, or our own summariser,
needs only an adapter that returns ``(MeetingNote, notes markdown)`` pairs.

The output is per-claim labels and aggregate metrics:

* notes are split into atomic claims (one per bullet or sentence; headings and lead-in labels
  are dropped but kept as context);
* a cheap model labels every claim against the meeting's full transcript, in **one** call per
  meeting with the transcript in a cached system prompt:
  ``supported`` (must cite lines), ``inferred`` (a plausible reading not said outright),
  ``unsupported`` or ``contradicted``;
* a stronger model re-labels a seeded 30% (agreement as Cohen's kappa) and, as the *strict*
  pass, every claim;
* a deterministic pre-check flags numbers, dates and names a claim has but its transcript lacks;
* a deterministic **cross-meeting bleed** heuristic flags ungrounded claims whose distinctive
  terms are missing from their own meeting but present in another of the same corpus's
  meetings (needs human confirmation).

For a private system (real meetings) per-claim output goes only to ``data/real/``; committed
files hold aggregates. Write-up: ``docs/benchmark/NOTES_FAITHFULNESS.md``.
"""

from __future__ import annotations

import math
import random
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from concentrate.core.models import Channel, MeetingNote, Utterance
from concentrate.core.store import NoteStore
from concentrate.evals.judge_check import cohen_kappa
from concentrate.llm.client import LLMRequest, load_prompt

PROMPT_ID = "notes_audit.v1"
CHECK_MODEL = "claude-haiku-5-5"
RECHECK_MODEL = "claude-sonnet-5-5"
RECHECK_FRACTION = 0.3

# The calls a run makes: the primary check (cheap model, every claim), the 30% re-check
# (stronger model) and the strict pass (stronger model, every claim).
Kind = Literal["check", "recheck", "strict"]

Label = Literal["supported", "inferred", "unsupported", "contradicted"]
LABELS: tuple[Label, ...] = ("supported", "inferred", "unsupported", "contradicted")
ClaimType = Literal[
    "decision",
    "action_item",
    "number_date",
    "attribution",
    "opinion_sentiment",
    "generalisation",
    "fact",
]
CLAIM_TYPES: tuple[ClaimType, ...] = (
    "decision",
    "action_item",
    "number_date",
    "attribution",
    "opinion_sentiment",
    "generalisation",
    "fact",
)

# -- 1. claims -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """One atomic claim from a note's AI notes."""

    claim_id: str  # "c001", unique within a meeting
    section: str  # nearest heading above it ("" if none)
    context: str  # lead-in label it sits under ("" if none)
    text: str


@dataclass(frozen=True)
class Split:
    claims: tuple[Claim, ...]
    headings: int
    lead_ins: int


_HEADING = re.compile(r"^\s*#{1,6}\s+(.*)$")
_ITEM = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+(.*)$")
_BOLD_ONLY = re.compile(r"^\*\*([^*]+)\*\*:?$")
# A sentence ends at . ! or ? followed by whitespace and an upper-case letter or digit.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_ABBREV = re.compile(r"\b(?:e\.g|i\.e|etc|vs|approx|incl|Mr|Ms|Mrs|Dr|St)\.$", re.IGNORECASE)


def strip_markdown(text: str) -> str:
    """Drop emphasis, inline code and link targets; collapse whitespace."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"(?<!\w)[*_](\S[^*_]*\S|\S)[*_](?!\w)", r"\1", text)
    return " ".join(text.split())


def sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping common abbreviations ("e.g.") intact."""
    parts: list[str] = []
    for piece in _SENTENCE_END.split(text):
        if parts and _ABBREV.search(parts[-1]):
            parts[-1] = f"{parts[-1]} {piece}"
        else:
            parts.append(piece)
    return [p.strip() for p in parts if p.strip()]


def split_claims(markdown: str) -> Split:
    """Split notes markdown into atomic claims: one per bullet, numbered item or sentence.

    Headings are dropped but remembered as each claim's ``section``. A *lead-in* (an item that
    is all bold, or whose text ends with ``:``) is a label, not a claim: it is dropped and
    becomes the ``context`` of the lines under it, until the next item at its depth or
    shallower, or the next heading. Items and plain paragraphs with several sentences give one
    claim per sentence.
    """
    claims: list[Claim] = []
    section = ""
    lead: tuple[int, str] | None = None  # (indent of the lead-in, its text)
    headings = lead_ins = 0
    for raw in markdown.splitlines():
        if not raw.strip():
            continue
        h = _HEADING.match(raw)
        if h:
            section, lead, headings = strip_markdown(h.group(1)), None, headings + 1
            continue
        m = _ITEM.match(raw)
        indent = len(m.group(1)) if m else len(raw) - len(raw.lstrip())
        body = (m.group(2) if m else raw).strip()
        if m and lead is not None and indent <= lead[0]:
            lead = None
        bold = _BOLD_ONLY.match(body)
        if bold or body.endswith(":"):
            label = strip_markdown(bold.group(1) if bold else body).rstrip(":").strip()
            lead, lead_ins = (indent, label), lead_ins + 1
            continue
        context = lead[1] if lead else ""
        for s in sentences(strip_markdown(body)):
            claims.append(Claim(f"c{len(claims) + 1:03d}", section, context, s))
    return Split(tuple(claims), headings, lead_ins)


# -- 2a. deterministic pre-check -----------------------------------------------------------------

_UNITS = {
    w: i
    for i, w in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
        ]
    )
}
_TENS = {
    w: 10 * (i + 2)
    for i, w in enumerate(
        ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
    )
}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000, "billion": 1_000_000_000}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
    "eighth": 8, "ninth": 9, "tenth": 10,
}  # fmt: skip
MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_MONTH_ABBR = {m[:3]: m for m in MONTHS} | {"sept": "september"}
_WORD = re.compile(r"[A-Za-z][A-Za-z'\u2019\-]*")
_NUMBER = re.compile(
    r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kKmM%]|bn|st|nd|rd|th)?(?![\w])"
)
# Capitalised words that are not names when they appear mid-sentence.
_NOT_NAMES = frozenset(
    [
        "i",
        "i'm",
        "i've",
        "i'd",
        "i'll",
        "ok",
        "okay",
        "ai",
        "api",
        "apis",
        "ui",
        "ux",
        "faq",
        "tbd",
        "etc",
        "eta",
        "q",
        "a",
        "me",
        "them",
        "we",
        "us",
        "mcp",
        "llm",
        "llms",
        "pm",
        "ceo",
        "cto",
        "cfo",
        "vp",
        "hr",
        "pr",
        "prs",
        "mvp",
        "roi",
        "kpi",
        "kpis",
        "saas",
        "b2b",
        "b2c",
        "url",
        "sdk",
    ]
)


def _number_values(text: str) -> set[float]:
    """Every numeric value written in ``text``, as digits or as spelled-out words."""
    values: set[float] = set()
    for m in _NUMBER.finditer(text):
        v = float(m.group(1).replace(",", ""))
        values.add(v)
        suffix = (m.group(2) or "").lower()
        if suffix == "k":
            values.add(v * 1000)
        elif suffix == "m":
            values.add(v * 1_000_000)
        elif suffix == "bn":
            values.add(v * 1_000_000_000)
    total = current = 0.0
    in_number = False
    for w in [w.lower() for w in _WORD.findall(text)] + [""]:
        if w in _UNITS or w in _TENS:
            current += _UNITS.get(w, 0) + _TENS.get(w, 0)
            in_number = True
            values.add(_UNITS.get(w, 0) + _TENS.get(w, 0))
        elif w in _SCALES and in_number:
            current = max(current, 1) * _SCALES[w]
            if _SCALES[w] >= 1000:
                total, current = total + current, 0.0
        elif w in _ORDINALS:
            values.add(_ORDINALS[w])
        elif w == "and" and in_number:
            continue
        else:
            if in_number:
                values.add(total + current)
            total = current = 0.0
            in_number = False
    return values


def _digit_values(claim: str) -> list[float]:
    """Numbers a claim writes in digits ("Q2" is a quarter, not a number), with k/m/bn read
    as their own value: a match on either the bare or the scaled value counts as spoken."""
    claim = re.sub(r"\b[Qq][1-4]\b", "", claim)
    return [float(m.group(1).replace(",", "")) for m in _NUMBER.finditer(claim)]


def _date_words(text: str) -> set[str]:
    out: set[str] = set()
    for w in _WORD.findall(text.lower()):
        w = w.rstrip(".")
        if w in MONTHS or w in WEEKDAYS:
            out.add(w)
        elif w in _MONTH_ABBR:
            out.add(_MONTH_ABBR[w])
        elif w.endswith("s") and w[:-1] in WEEKDAYS:
            out.add(w[:-1])
    out.discard("may")  # "may" is far more often the verb; a spelled date still has its number
    return out


def _name_candidates(claim: str) -> set[str]:
    """Capitalised words that are not the first word of a sentence or clause, nor a date.

    Hyphenated compounds are split and only their capitalised parts kept ("AI-generated"
    gives nothing, "Acme-style" gives "acme").
    """
    out: set[str] = set()
    for sentence in re.split(r"[.!?:;(]\s+|\s[-\u2013\u2014]\s|^", claim):
        words = _WORD.findall(sentence)
        for w in [part for word in words[1:] for part in word.split("-") if part]:
            low = w.lower().strip("'\u2019")
            if not w[0].isupper() or low in _NOT_NAMES or low in MONTHS or low in WEEKDAYS:
                continue
            if low in _MONTH_ABBR or len(low) < 2:
                continue
            out.add(low.removesuffix("'s").removesuffix("\u2019s"))
    return out


@dataclass(frozen=True)
class Precheck:
    """Tokens of a claim that its meeting's transcript never mentions."""

    numbers: tuple[str, ...] = ()
    dates: tuple[str, ...] = ()
    names: tuple[str, ...] = ()  # in neither the transcript nor the calendar context
    names_calendar_only: tuple[str, ...] = ()  # in the calendar context but not spoken

    @property
    def flagged(self) -> bool:
        return bool(self.numbers or self.dates or self.names)


def _fmt(v: float) -> str:
    return str(int(v)) if v == int(v) else str(v)


def precheck(claim: str, transcript: str, context: str = "") -> Precheck:
    """Flag numbers, dates and names in ``claim`` that ``transcript`` never mentions.

    ``context`` is the calendar context the notes generator also sees (title, attendee names):
    a name found only there is reported separately, not flagged. Matching is literal after
    lower-casing (numbers also match their spelled-out form), so a misheard name or an
    unusual way of saying a number gives a false flag; it is a cheap signal, not a verdict.
    """
    spoken = set(_WORD.findall(transcript.lower()))
    spoken |= {w.removesuffix("'s") for w in spoken}
    ctx = {w.lower() for w in _WORD.findall(context)}
    spoken_numbers = _number_values(transcript)
    numbers = sorted({_fmt(v) for v in _digit_values(claim) if v not in spoken_numbers}, key=float)
    dates = sorted(_date_words(claim) - _date_words(transcript))
    names: list[str] = []
    calendar_only: list[str] = []
    for n in sorted(_name_candidates(claim)):
        parts = [p for p in re.split(r"['\u2019]", n) if p]
        if all(p in spoken for p in parts):
            continue
        (calendar_only if all(p in ctx for p in parts) else names).append(n)
    return Precheck(tuple(numbers), tuple(dates), tuple(names), tuple(calendar_only))


# -- 2b. the checker call ------------------------------------------------------------------------


def speaker(u: Utterance) -> str:
    who = "Me" if u.channel == Channel.ME else "Them"
    return f"{who} ({u.speaker_name})" if u.speaker_name else who


def transcript_block(note: MeetingNote) -> str:
    """The transcript as ``[index] Speaker: text`` lines (index = Utterance.index)."""
    return "\n".join(f"[{u.index}] {speaker(u)}: {u.text}" for u in note.utterances)


def calendar_context(note: MeetingNote) -> str:
    """What the notes generator also knows besides the audio: title, date, owner, attendees."""
    people = sorted({p.name or p.email.split("@")[0] for p in note.attendees} - {note.owner.name})
    when = (note.scheduled_start or note.created_at).date().isoformat()
    return (
        f"Title: {note.title or note.event_title or '(none)'}\nDate: {when}\n"
        f"Note owner (Me): {note.owner.name or 'unknown'}\n"
        f"Invited: {', '.join(p for p in people if p) or 'none listed'}"
    )


class ClaimVerdict(BaseModel):
    claim_id: str
    reason: str
    label: Label
    claim_type: ClaimType
    lines: list[int]


class AuditReply(BaseModel):
    verdicts: list[ClaimVerdict]


# Strict structured-output schema (the API needs ``additionalProperties: false`` everywhere).
AUDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "label": {"type": "string", "enum": list(LABELS)},
                    "claim_type": {"type": "string", "enum": list(CLAIM_TYPES)},
                    "lines": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["claim_id", "reason", "label", "claim_type", "lines"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def claims_message(claims: Sequence[Claim]) -> str:
    """The user message listing claims: ``<id> [<section > lead-in>]: <text>`` per line."""
    rows = []
    for c in claims:
        where = " > ".join(p for p in (c.section, c.context) if p)
        rows.append(f"{c.claim_id} [{where or 'top'}]: {c.text}")
    return "Claims:\n" + "\n".join(rows)


def check_request(note: MeetingNote, claims: Sequence[Claim]) -> LLMRequest:
    """One call per meeting: transcript in the (cached) system prompt, all claims in the user."""
    system = (
        f"{load_prompt(PROMPT_ID)}\n\n<calendar>\n{calendar_context(note)}\n</calendar>\n\n"
        f"<transcript>\n{transcript_block(note)}\n</transcript>"
    )
    return LLMRequest(
        stage="faithfulness.check",
        prompt_id=PROMPT_ID,
        system=system,
        user=claims_message(claims),
        schema=AUDIT_SCHEMA,
        max_tokens=4000 + 250 * len(claims),
        effort="low",
        cache_system=True,
    )


# -- records, sampling, aggregation --------------------------------------------------------------


@dataclass
class ClaimRecord:
    """Everything known about one claim (private: lives only under data/real/)."""

    meeting: str  # "M1".."M4": an anonymous index, never the note ID
    claim: Claim
    precheck: Precheck
    label: Label | None = None
    claim_type: ClaimType | None = None
    lines: list[int] = field(default_factory=list)
    reason: str = ""
    uncited_support: bool = False  # said "supported" without a valid line: relabelled
    recheck_label: Label | None = None
    recheck_type: ClaimType | None = None
    strict_label: Label | None = None
    strict_type: ClaimType | None = None
    strict_lines: list[int] = field(default_factory=list)
    strict_reason: str = ""
    # Cross-meeting bleed heuristic: other meeting label -> distinctive terms of this claim
    # found there but not in this claim's own meeting.
    cross_meeting: dict[str, list[str]] = field(default_factory=dict)

    @property
    def ungrounded_by_any(self) -> bool:
        """Some checker did not call it supported."""
        return any(
            x not in (None, "supported")
            for x in (self.label, self.recheck_label, self.strict_label)
        )

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["precheck"]["flagged"] = self.precheck.flagged
        return d


def apply_verdicts(
    records: Sequence[ClaimRecord], reply: AuditReply, n_lines: int, kind: Kind = "check"
) -> list[str]:
    """Attach ``reply`` to ``records`` (matched on claim id); return the ids it left out.

    A primary ``supported`` verdict without any valid line index is relabelled
    ``unsupported`` (Granola's own rule: only claim what you can point to) and flagged.
    """
    by_id = {r.claim.claim_id: r for r in records}
    seen: set[str] = set()
    for v in reply.verdicts:
        r = by_id.get(v.claim_id)
        if r is None or v.claim_id in seen:
            continue
        seen.add(v.claim_id)
        lines = sorted({i for i in v.lines if 0 <= i < n_lines})
        if kind == "recheck":
            r.recheck_label, r.recheck_type = v.label, v.claim_type
            continue
        if kind == "strict":
            r.strict_label, r.strict_type = v.label, v.claim_type
            r.strict_lines, r.strict_reason = lines, v.reason
            continue
        r.label, r.claim_type, r.lines, r.reason = v.label, v.claim_type, lines, v.reason
        if v.label == "supported" and not lines:
            r.label, r.uncited_support = "unsupported", True
    return [cid for cid in by_id if cid not in seen]


def recheck_sample(records: Sequence[ClaimRecord], seed: int) -> list[ClaimRecord]:
    """A seeded random ``RECHECK_FRACTION`` of claims (rounded up), in input order."""
    k = math.ceil(RECHECK_FRACTION * len(records))
    picked = set(random.Random(seed).sample(range(len(records)), k))
    return [r for i, r in enumerate(records) if i in picked]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for ``k`` of ``n``."""
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, round(c - h, 4)), min(1.0, round(c + h, 4)))


def rates(labels: Iterable[str]) -> dict[str, Any]:
    """Count, rate and 95% Wilson CI per label, plus ``ungrounded`` (= not supported)."""
    c = Counter(labels)
    n = sum(c.values())
    out: dict[str, Any] = {"n": n}
    for lab in (*LABELS, "ungrounded"):
        k = n - c["supported"] if lab == "ungrounded" else c[lab]
        out[lab] = {"k": k, "rate": round(k / n, 4) if n else None, "ci95": wilson(k, n)}
    return out


def aggregate(records: Sequence[ClaimRecord], split_counts: Mapping[str, Split]) -> dict[str, Any]:
    """The committable aggregate: counts, rates and CIs only (no claim text, no IDs)."""
    labelled = [r for r in records if r.label is not None]
    by_meeting: dict[str, Any] = {}
    for m, sp in split_counts.items():
        rs = [r for r in labelled if r.meeting == m]
        by_meeting[m] = {
            "claims": len(sp.claims),
            "headings_dropped": sp.headings,
            "lead_ins_dropped": sp.lead_ins,
            "labels": dict(Counter(r.label for r in rs)),
        }
    by_type: dict[str, Any] = {}
    for t in CLAIM_TYPES:
        rs = [r for r in labelled if r.claim_type == t]
        if rs:
            by_type[t] = rates(r.label for r in rs if r.label)
    ranked = sorted(
        ((t, v["ungrounded"]["rate"], v["n"]) for t, v in by_type.items()),
        key=lambda x: (-x[1], -x[2]),
    )
    flagged = [r for r in labelled if r.precheck.flagged]
    pre: dict[str, Any] = {
        "numbers_checked": sum(len(_digit_values(r.claim.text)) for r in labelled),
        "date_words_checked": sum(len(_date_words(r.claim.text)) for r in labelled),
        "name_candidates_checked": sum(len(_name_candidates(r.claim.text)) for r in labelled),
        "claims_flagged": len(flagged),
        "flagged_numbers": sum(bool(r.precheck.numbers) for r in labelled),
        "flagged_dates": sum(bool(r.precheck.dates) for r in labelled),
        "flagged_names": sum(bool(r.precheck.names) for r in labelled),
        "claims_with_calendar_only_names": sum(
            bool(r.precheck.names_calendar_only) for r in labelled
        ),
        "llm_labels_of_flagged": dict(Counter(r.label for r in flagged)),
        "flagged_but_llm_supported": sum(r.label == "supported" for r in flagged),
    }
    sample = [r for r in labelled if r.recheck_label]
    strict = [r for r in labelled if r.strict_label]
    both = [r for r in strict if r.recheck_label]
    reliability = {
        "check_vs_recheck_sample": compare(
            [r.label for r in sample], [r.recheck_label for r in sample]
        ),
        "check_vs_strict_all": compare([r.label for r in strict], [r.strict_label for r in strict]),
        "strict_test_retest_on_sample": compare(
            [r.strict_label for r in both], [r.recheck_label for r in both]
        ),
        "type_agreement_check_vs_strict": round(
            sum(r.claim_type == r.strict_type for r in strict) / len(strict), 4
        )
        if strict
        else None,
    }
    strict_by_type: dict[str, Any] = {}
    for t in CLAIM_TYPES:  # grouped by the strict model's own type, so it stands alone
        labs = [r.strict_label for r in strict if r.strict_type == t]
        if labs:
            strict_by_type[t] = rates(x for x in labs if x)
    return {
        "claims_total": len(records),
        "claims_labelled": len(labelled),
        "claims_missing_verdict": len(records) - len(labelled),
        "uncited_support_relabelled": sum(r.uncited_support for r in records),
        "by_meeting": by_meeting,
        "overall": rates(r.label for r in labelled if r.label),
        "by_type": by_type,
        "types_ranked_by_ungrounded": [
            {"type": t, "ungrounded_rate": rate, "n": n} for t, rate, n in ranked
        ],
        "precheck": pre,
        "cross_meeting_bleed": bleed_aggregate(labelled),
        "reliability": reliability,
        "strict": {
            "n": len(strict),
            "overall": rates(r.strict_label for r in strict if r.strict_label),
            "by_type": strict_by_type,
            "llm_labels_of_flagged": dict(Counter(r.strict_label for r in flagged)),
        },
    }


def compare(a: Sequence[str | None], b: Sequence[str | None]) -> dict[str, Any]:
    """Agreement of two label lists: raw, Cohen's kappa (4-way and supported-vs-not), and
    the confusion counts ``first->second``. Empty lists give ``{"n": 0}``."""
    x = [v or "missing" for v in a]
    y = [v or "missing" for v in b]
    if not x:
        return {"n": 0}

    def binary(v: list[str]) -> list[str]:
        return ["supported" if s == "supported" else "ungrounded" for s in v]

    return {
        "n": len(x),
        "agreement_4way": round(sum(p == q for p, q in zip(x, y, strict=True)) / len(x), 4),
        "kappa_4way": round(cohen_kappa(x, y), 4),
        "kappa_supported_vs_not": round(cohen_kappa(binary(x), binary(y)), 4),
        "confusion": {
            f"{p}->{q}": k for (p, q), k in sorted(Counter(zip(x, y, strict=True)).items())
        },
        "first_rates": rates(x),
        "second_rates": rates(y),
    }


# -- private spot-check list ---------------------------------------------------------------------

_STOP = frozenset(
    [
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "at",
        "by",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "it",
        "this",
        "that",
        "they",
        "them",
        "we",
        "us",
        "our",
        "you",
        "i",
        "he",
        "she",
        "his",
        "her",
        "their",
        "as",
        "but",
        "not",
        "no",
        "so",
        "if",
        "then",
        "than",
        "into",
        "about",
        "will",
        "would",
        "can",
        "could",
        "should",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "more",
        "most",
        "also",
        "just",
        "very",
    ]
)


def content_terms(text: str, *, stemmed: bool = False) -> set[str]:
    """Lower-cased words of three letters or more, minus stop words; optionally stemmed."""
    words = {w for w in (x.lower() for x in _WORD.findall(text)) if w not in _STOP and len(w) > 2}
    return {_stem(w) for w in words} if stemmed else words


def nearest_lines(
    claim: str,
    utterances: Sequence[Utterance],
    k: int = 3,
    *,
    weights: Mapping[str, float] | None = None,
    stemmed: bool = False,
) -> list[int]:
    """Indices of the ``k`` transcript lines sharing the most content words with ``claim``.

    Each shared term counts 1, or its entry in ``weights`` (e.g. an IDF) when given; terms
    missing from ``weights`` count 0. ``stemmed`` compares crude stems ("decided" = "decide").
    Ties go to the earlier line, so the result is deterministic. Lines sharing nothing are
    never returned.
    """
    want = content_terms(claim, stemmed=stemmed)

    def score(u: Utterance) -> float:
        shared = want & content_terms(u.text, stemmed=stemmed)
        return sum(weights.get(t, 0.0) for t in shared) if weights is not None else len(shared)

    scored = [(score(u), -u.index) for u in utterances]
    best = sorted((s for s in scored if s[0] > 0), reverse=True)[:k]
    return sorted(-i for _, i in best)


def spotcheck_markdown(
    records: Sequence[ClaimRecord], notes: Mapping[str, MeetingNote], seed: int
) -> str:
    """Private hand-check list: every non-supported claim with the lines nearest to it."""
    out = [
        "# Notes faithfulness: spot-check list (PRIVATE: never commit)",
        "",
        "Every claim that any checker did not label `supported`, with the transcript lines "
        "the checkers cited and the lines sharing the most words with it. Mark each: agree / "
        "disagree + the label you would give. You are the ground truth.",
        "",
        f"seed {seed}; checker {CHECK_MODEL}; re-check {RECHECK_MODEL}; prompt {PROMPT_ID}",
        "",
    ]
    for r in records:
        if {r.label, r.recheck_label, r.strict_label} <= {None, "supported"}:
            continue
        note = notes[r.meeting]
        cited = set(r.lines) | set(r.strict_lines)
        lines = sorted(cited | set(nearest_lines(r.claim.text, note.utterances)))
        where = " > ".join(p for p in (r.claim.section, r.claim.context) if p)
        flags = asdict(r.precheck)
        flag_txt = ", ".join(f"{k}: {list(v)}" for k, v in flags.items() if v)
        out += [
            f"## {r.meeting} {r.claim.claim_id}: check **{r.label}** ({r.claim_type})",
            "",
            f"- section: {where or 'top'}",
            f"- claim: {r.claim.text}",
            f"- checker reason: {r.reason}",
            f"- strict ({RECHECK_MODEL}): **{r.strict_label}**: {r.strict_reason}",
            f"- re-check: {r.recheck_label or 'not sampled'}",
            f"- pre-check: {flag_txt or 'nothing flagged'}",
            f"- cross-meeting bleed (heuristic): {r.cross_meeting or 'none'}",
            f"- cited lines: check {r.lines or 'none'}; strict {r.strict_lines or 'none'}",
            "",
        ]
        out += [
            f"    [{u.index}] {speaker(u)}: {u.text}" for u in note.utterances if u.index in lines
        ]
        out += ["", "- [ ] agree  - [ ] disagree, I'd say: ____", ""]
    return "\n".join(out) + "\n"


# -- the run -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NotesSystem:
    """One benchmark entry: a system's notes for a set of meetings.

    ``entries`` are the audited meetings, as ``(meeting, notes markdown)``; ``pool`` is every
    meeting of the same corpus/account (transcript + notes), which the cross-meeting bleed
    heuristic searches. ``private`` systems write per-claim output only under data/real/.
    """

    name: str  # e.g. "granola" or "northwind-synth"
    description: str
    private: bool
    entries: tuple[tuple[MeetingNote, str], ...]
    pool: tuple[tuple[MeetingNote, str], ...]


def granola_system(db: Path) -> NotesSystem:
    """Granola's AI notes (``summary_markdown``) for every synced meeting with a transcript."""
    notes = [n for n in NoteStore(db).iter_notes() if n.utterances]
    pairs = tuple(
        (n, n.summary_markdown or "") for n in notes if (n.summary_markdown or "").strip()
    )
    return NotesSystem(
        "granola",
        "Granola AI notes (summary_markdown) via Granola's official read-only MCP",
        True,
        pairs,
        pairs,
    )


def northwind_system(notes: Sequence[MeetingNote], n: int, seed: int) -> NotesSystem:
    """The synthetic generator's summaries for ``n`` seeded-random Northwind meetings.

    A baseline, not a notetaker: the generator writes each summary bullet from a planted fact
    that its transcript also states, so it should score near 100% supported. It tests the
    checker (false "ungrounded" rate) more than the notes.
    """
    pool = tuple((m, m.summary_markdown or "") for m in notes if m.utterances)
    picked = sorted(random.Random(seed).sample(range(len(pool)), min(n, len(pool))))
    return NotesSystem(
        "northwind-synth",
        "Synthetic Northwind generator summaries (summary_markdown), data/fixtures/northwind",
        False,
        tuple(pool[i] for i in picked),
        pool,
    )


@dataclass
class Meeting:
    """One audited meeting under its anonymous label."""

    label: str  # "M1".."Mn", in entry order
    note: MeetingNote
    notes_markdown: str
    split: Split
    records: list[ClaimRecord]


def prepare(system: NotesSystem) -> list[Meeting]:
    """Split each entry's notes into claims; run the pre-check and the bleed heuristic."""
    out: list[Meeting] = []
    for note, markdown in system.entries:
        label = f"M{len(out) + 1}"
        sp = split_claims(markdown)
        spoken = " ".join(u.text for u in note.utterances)
        ctx = calendar_context(note)
        others = {
            f"P{i + 1}": " ".join(u.text for u in other.utterances) + " " + md
            for i, (other, md) in enumerate(system.pool)
            if other.note_id != note.note_id
        }
        recs = []
        for c in sp.claims:
            r = ClaimRecord(label, c, precheck(c.text, spoken, ctx))
            r.cross_meeting = cross_meeting_terms(c.text, f"{spoken} {ctx}", others)
            recs.append(r)
        out.append(Meeting(label, note, markdown, sp, recs))
    return out


# -- cross-meeting bleed (deterministic heuristic) -------------------------------------------


def _stem(word: str) -> str:
    """A crude suffix strip so "decided" / "decides" / "decision" don't count as distinct."""
    w = word.lower().strip("'\u2019")
    for suf in ("ings", "ing", "ions", "ion", "ed", "es", "s", "ly"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _vocab(text: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(text)} | {_fmt(v) for v in _number_values(text)}


def distinctive_terms(claim: str) -> dict[str, str]:
    """Candidate distinctive terms of a claim, term -> kind ("number", "name" or "word").

    Numbers written in digits, capitalised mid-sentence words, and lower-case content words of
    six letters or more (stemmed). Which of the words are actually *rare* is decided against
    the corpus in :func:`cross_meeting_terms`.
    """
    out: dict[str, str] = {}
    for w in _WORD.findall(claim):
        low = w.lower()
        if len(low) >= 6 and low not in _STOP and w[0].islower():
            out[_stem(low)] = "word"
    for n in _name_candidates(claim):
        out[_stem(n)] = "name"
    for v in _digit_values(claim):
        out[_fmt(v)] = "number"
    return out


def cross_meeting_terms(
    claim: str, own: str, others: Mapping[str, str], rare_share: float = 0.25
) -> dict[str, list[str]]:
    """Distinctive terms of ``claim`` absent from ``own`` but present in some ``others`` text.

    ``own`` is the claim's meeting transcript and calendar context; ``others`` maps each
    other meeting of the corpus to its transcript + notes. Names and numbers count wherever
    they appear; a plain word counts only if it is rare: found in at most ``rare_share`` of
    the other meetings (at least one). Returns ``{other label: [terms]}``; empty means no
    sign of bleed. A heuristic: a word can be absent from its own meeting just because it was
    paraphrased, and present elsewhere by coincidence.
    """
    own_vocab = _vocab(own)
    vocabs = {k: _vocab(v) for k, v in others.items()}
    cap = max(1, math.floor(rare_share * len(vocabs)))
    found: dict[str, list[str]] = {}
    for term, kind in sorted(distinctive_terms(claim).items()):
        if term in own_vocab:
            continue
        where = [k for k, vocab in vocabs.items() if term in vocab]
        if not where or (kind == "word" and len(where) > cap):
            continue
        for k in where:
            found.setdefault(k, []).append(f"{kind}:{term}")
    return found


def bleed_aggregate(records: Sequence[ClaimRecord]) -> dict[str, Any]:
    """Count claims flagged "possibly sourced from another meeting" (a heuristic).

    *Strong* flags rest on a name or number found only in another meeting; the rest rest only
    on a rare word, which in a small corpus is mostly paraphrase vocabulary.
    """
    ungrounded = [r for r in records if r.ungrounded_by_any]

    def flagged(rs: Sequence[ClaimRecord], test: Any = None) -> dict[str, Any]:
        k = sum(bool(r.cross_meeting) and (test is None or test(r)) for r in rs)
        return {
            "n": len(rs),
            "k": k,
            "rate": round(k / len(rs), 4) if rs else None,
            "ci95": wilson(k, len(rs)),
        }

    def strong(r: ClaimRecord) -> bool:
        return any(not t.startswith("word:") for v in r.cross_meeting.values() for t in v)

    kinds = Counter(
        t.split(":", 1)[0]
        for r in ungrounded
        for t in {t for v in r.cross_meeting.values() for t in v}
    )
    return {
        "heuristic": "deterministic term match; needs human confirmation",
        "ungrounded_claims": flagged(ungrounded),
        "ungrounded_claims_strong": flagged(ungrounded, strong),
        "all_claims_baseline": flagged(records),
        "all_claims_baseline_strong": flagged(records, strong),
        "term_kinds_in_flagged_ungrounded": dict(kinds),
    }


def requests(
    meetings: Sequence[Meeting], seed: int, *, strict: bool = True
) -> list[tuple[Meeting, LLMRequest, list[ClaimRecord], Kind]]:
    """Every call the audit makes: (meeting, request, its claims, kind), one per meeting and
    kind. The re-check sample is drawn over all claims at once, then grouped by meeting."""
    sampled = {id(r) for r in recheck_sample([r for m in meetings for r in m.records], seed)}
    out: list[tuple[Meeting, LLMRequest, list[ClaimRecord], Kind]] = []
    kinds: tuple[Kind, ...] = ("check", "recheck", "strict") if strict else ("check", "recheck")
    for kind in kinds:
        for m in meetings:
            picked = [r for r in m.records if kind != "recheck" or id(r) in sampled]
            if picked:
                req = check_request(m.note, [r.claim for r in picked])
                req = LLMRequest(**{**asdict(req), "stage": f"faithfulness.{kind}"})
                out.append((m, req, picked, kind))
    return out


def private_texts(meetings: Sequence[Meeting]) -> list[str]:
    """Strings that must never reach a committed file: claims, titles, names, transcript."""
    out: list[str] = []
    for m in meetings:
        n = m.note
        out += [r.claim.text for r in m.records] + [c for c in (n.title, n.event_title) if c]
        out += [p.name for p in (n.owner, *n.attendees) if p.name]
        out += [r.reason for r in m.records if r.reason]
        out += [t for r in m.records for v in r.cross_meeting.values() for t in v]
    return out
