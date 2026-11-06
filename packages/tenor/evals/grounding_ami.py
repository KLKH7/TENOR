"""Validate the grounding checker against AMI's human annotations (known answers, not a judge).

AMI (CC BY 4.0) ships, per scenario meeting, a human abstractive summary and *summary links*:
for each summary sentence, the dialogue acts an annotator marked as its support. So:

* **Positives.** Every human summary sentence is true of its meeting: the checker should call
  it ``supported``, and the lines it cites should overlap the summary-linked dialogue acts
  (**evidence recall**; only where summary links exist).
* **Negatives.** Seeded, rule-based edits of those sentences are false by construction:
  a swapped role or person, a changed number, a negated decision, or a sentence injected from
  another meeting (cross-meeting bleed: same series, and another series). Each should come
  back ``unsupported`` or ``contradicted``.

Everything except the checker calls is deterministic and $0: the perturbation set, the
retrieval recall of the candidate lines against the summary links, the pre-check baseline and
the cost quote. Write-up: ``docs/method/GROUNDING_CHECKER.md``.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tenor.ami.corpus import (
    DEFAULT_ZIP,
    ROLE_NAMES,
    AmiMeeting,
    SummarySentence,
    load_meeting,
    meeting_note,
)
from tenor.core.models import MeetingNote
from tenor.evals import grounding as G
from tenor.evals.faithfulness import MONTHS, WEEKDAYS, Claim, rates, wilson

# Meetings with a human abstractive summary. IS1008a-d have summary links; IB4003 has none.
MEETINGS = ("IS1008a", "IS1008b", "IS1008c", "IS1008d", "IB4003")
DEV = ("IS1008a",)  # the only meeting looked at while writing rules or the prompt
# Donor meetings for the injection test (summaries only), by series.
SERIES = {"IS1008": ("IS1008a", "IS1008b", "IS1008c", "IS1008d"), "IB400": ("IB4003", "IB4005")}
INJECT_PER_MEETING = 5
FLAGGED = ("unsupported", "contradicted")
KINDS = (
    "original",
    "swap_role_or_person",
    "change_number",
    "negate",
    "inject_same_series",
    "inject_other_series",
)


def series(meeting_id: str) -> str:
    return next(s for s, ids in SERIES.items() if meeting_id in ids)


# -- perturbations (deterministic given the RNG) -----------------------------------------------

_ROLES = re.compile("|".join(rf"\b{r}\b" for r in ROLE_NAMES.values()), re.IGNORECASE)
_NUM_WORDS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty",
]  # fmt: skip
# "one" is left out: in summaries it is mostly a pronoun ("one of", "the one").
_NUMBER = re.compile(r"\b(\d+|" + "|".join(_NUM_WORDS[2:]) + r")\b", re.IGNORECASE)
_CAPITAL = re.compile(r"(?<!^)(?<![.!?;:]\s)\b([A-Z][a-z]{2,})\b")
_ROLE_WORDS = {w.lower() for r in ROLE_NAMES.values() for w in r.split()}
_NOT_PEOPLE = {*MONTHS, *WEEKDAYS, *_ROLE_WORDS, "lcd", "dvd", "the", "she", "he"}


def _same_case(new: str, old: str) -> str:
    return new.title() if old[:1].isupper() else new.lower()


def swap_role(text: str, rng: random.Random) -> str | None:
    """Replace the first scenario role named in ``text`` with one it doesn't name."""
    found = list(_ROLES.finditer(text))
    if not found:
        return None
    named = {m.group(0).lower() for m in found}
    choices = [r for r in ROLE_NAMES.values() if r.lower() not in named]
    m = found[0]
    new = _same_case(rng.choice(choices), m.group(0))
    return text[: m.start()] + new + text[m.end() :]


def people(sentences: Iterable[str], spoken: str) -> list[str]:
    """Capitalised mid-sentence words of a summary that are also said in the meeting:
    in practice, people's names (not roles, months, weekdays or acronyms)."""
    said = set(re.findall(r"[a-z]+", spoken.lower()))
    out = {
        m.group(1)
        for s in sentences
        for m in _CAPITAL.finditer(s)
        if m.group(1).lower() not in _NOT_PEOPLE and m.group(1).lower() in said
    }
    return sorted(out)


def swap_person(text: str, names: Sequence[str], rng: random.Random) -> str | None:
    """Replace the first known person's name in ``text`` with one from ``names`` that the
    sentence doesn't already mention."""
    for name in names:
        m = re.search(rf"\b{re.escape(name)}\b", text)
        others = [n for n in names if not re.search(rf"\b{re.escape(n)}\b", text)]
        if m and others:
            return text[: m.start()] + rng.choice(others) + text[m.end() :]
    return None


def change_number(text: str, rng: random.Random) -> str | None:
    """Change the first number (digits, or a word from "two" to "twenty") to another value."""
    m = _NUMBER.search(text)
    if not m:
        return None
    raw = m.group(1)
    value = int(raw) if raw.isdigit() else _NUM_WORDS.index(raw.lower())
    new_value = rng.choice([v for v in (value + 1, value + 2, value * 2) if v != value])
    if raw.isdigit() or new_value >= len(_NUM_WORDS):
        new = str(new_value)
    else:
        new = _same_case(_NUM_WORDS[new_value], raw)
    return text[: m.start()] + new + text[m.end() :]


# (pattern, replacement, priority): removing a "not" wins over adding one at the same place.
_NEGATIONS: list[tuple[re.Pattern[str], Callable[[re.Match[str]], str], int]] = [
    (
        re.compile(
            r"\b(will|is|are|was|were|can|should|must|could|would|did)( \w+ly)? not\b", re.I
        ),
        lambda m: m.group(1) + (m.group(2) or ""),
        0,
    ),
    (re.compile(r"\bwon't\b", re.I), lambda m: "will", 0),
    (
        re.compile(r"\b(decided|agreed)\b", re.I),
        lambda m: "did not " + m.group(1)[:-1],
        1,
    ),
    (
        re.compile(r"\b(will|is|are|was|were|can|should|must|could|would)\b", re.I),
        lambda m: f"{m.group(1)} not",
        2,
    ),
]
# Negations are made only before the first embedded clause: negating inside "whether X" or
# "the space that they are after" doesn't make the sentence false.
_EMBEDDED = re.compile(r"\b(whether|if|how|that|which|who|where)\b", re.I)


def negate(text: str) -> str | None:
    """Flip the polarity of the sentence's first main-clause decision or statement.

    Only before the first embedded clause ("whether", "that", "which", ...): negating a
    question or a relative clause doesn't reliably make the sentence false. "decided" becomes
    "did not decide"; "will" becomes "will not" and back ("would probably not" -> "would
    probably").
    """
    stop = _EMBEDDED.search(text)
    limit = stop.start() if stop else len(text)
    hits = [
        (m.start(), prio, m, fn)
        for pat, fn, prio in _NEGATIONS
        for m in pat.finditer(text)
        if m.start() < limit
    ]
    if not hits:
        return None
    _, _, m, fn = min(hits, key=lambda h: (h[0], h[1]))
    return text[: m.start()] + fn(m) + text[m.end() :]


# -- the item set --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Item:
    """One sentence given to the checker, with what it should get."""

    item_id: str  # "<meeting>.<kind>.<n>"
    meeting: str  # the meeting it is checked against
    kind: str  # one of KINDS
    section: str
    text: str
    source: str  # the human sentence it came from (== text for originals)
    source_meeting: str
    source_sentence_id: str
    links: tuple[int, ...]  # summary-linked utterance indices (originals in ``meeting`` only)
    split: str  # "dev" or "test"

    @property
    def should_flag(self) -> bool:
        return self.kind != "original"


@dataclass(frozen=True)
class Corpus:
    meetings: dict[str, AmiMeeting]  # checked meetings and donors
    notes: dict[str, MeetingNote]  # checked meetings only


def load(zip_path: Path = DEFAULT_ZIP) -> Corpus:
    ids = sorted({*MEETINGS, *(m for ids in SERIES.values() for m in ids)})
    meetings = {m: load_meeting(m, zip_path) for m in ids}
    return Corpus(meetings, {m: meeting_note(meetings[m]) for m in MEETINGS})


def _make(
    out: list[Item],
    meeting: str,
    kind: str,
    text: str,
    s: SummarySentence,
    src: str,
    links: tuple[int, ...] = (),
) -> None:
    n = sum(it.meeting == meeting and it.kind == kind for it in out) + 1
    split = "dev" if meeting in DEV else "test"
    out.append(
        Item(
            f"{meeting}.{kind}.{n:02d}",
            meeting,
            kind,
            s.section,
            text,
            s.text,
            src,
            s.sentence_id,
            links,
            split,
        )
    )


def items(corpus: Corpus, seed: int) -> list[Item]:
    """Originals plus every perturbation, deterministic in ``seed``."""
    out: list[Item] = []
    for mid in MEETINGS:
        m = corpus.meetings[mid]
        da_index = {a.da_id: i for i, a in enumerate(m.dialogue_acts)}
        spoken = " ".join(a.text for a in m.dialogue_acts)
        names = people((s.text for s in m.sentences), spoken)
        has_roles = any(s.role for s in m.speakers)
        for s in m.sentences:
            rng = random.Random(f"{seed}:{s.sentence_id}")
            links = tuple(sorted(da_index[d] for d in s.links if d in da_index))
            _make(out, mid, "original", s.text, s, mid, links)
            swapped = (swap_role(s.text, rng) if has_roles else None) or swap_person(
                s.text, names, rng
            )
            if swapped:
                _make(out, mid, "swap_role_or_person", swapped, s, mid)
            if changed := change_number(s.text, rng):
                _make(out, mid, "change_number", changed, s, mid)
            if s.section != "problems" and (negated := negate(s.text)):
                _make(out, mid, "negate", negated, s, mid)
        own = {s.text.lower() for s in m.sentences}
        for kind, same in (("inject_same_series", True), ("inject_other_series", False)):
            donors = [
                (d, s)
                for d, dm in sorted(corpus.meetings.items())
                if d != mid and (series(d) == series(mid)) == same
                for s in dm.sentences
                if s.text.lower() not in own
            ]
            rng = random.Random(f"{seed}:{mid}:{kind}")
            for d, s in rng.sample(donors, min(INJECT_PER_MEETING, len(donors))):
                _make(out, mid, kind, s.text, s, d)
    return out


def claims_for(group: Sequence[Item]) -> list[Claim]:
    """Items as checker claims: one claim per item, its section as the heading."""
    return [
        Claim(f"c{i + 1:03d}", it.section.capitalize(), "", it.text) for i, it in enumerate(group)
    ]


def groups(all_items: Sequence[Item]) -> dict[tuple[str, str], list[Item]]:
    """One checker call set per (meeting, kind): perturbations never share a call with the
    sentence they were made from, so the checker can't spot them by comparison."""
    out: dict[tuple[str, str], list[Item]] = {}
    for it in all_items:
        out.setdefault((it.meeting, it.kind), []).append(it)
    return out


# -- $0 measures ---------------------------------------------------------------------------------


def _near(cited: Iterable[int], gold: Iterable[int], window: int) -> bool:
    g = set(gold)
    return any(abs(c - x) <= window for c in cited for x in g)


def _rate(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": wilson(k, n)}


def retrieval_recall(
    corpus: Corpus, all_items: Sequence[Item], k: int, window: int
) -> dict[str, Any]:
    """How often the deterministic candidates (top-k, and widened) hit a summary-linked act."""
    originals = [it for it in all_items if it.kind == "original" and it.links]
    top = widened = near2 = 0
    for it in originals:
        note = corpus.notes[it.meeting]
        p = G.plan(note, claims_for([it]), k=k, window=window)
        cands = p.candidates["c001"]
        top += _near(cands, it.links, 0)
        widened += _near(p.excerpt, it.links, 0)
        near2 += _near(cands, it.links, 2)
    n = len(originals)
    return {
        "sentences_with_links": n,
        "top_k_hits_linked_act": _rate(top, n),
        "widened_hits_linked_act": _rate(widened, n),
        "top_k_within_2_acts": _rate(near2, n),
    }


def precheck_baseline(corpus: Corpus, all_items: Sequence[Item]) -> dict[str, Any]:
    """The $0 lexical pre-check alone: share of each kind it flags."""
    out: dict[str, Any] = {}
    for kind in KINDS:
        rs = [it for it in all_items if it.kind == kind]
        flagged = 0
        for it in rs:
            p = G.plan(corpus.notes[it.meeting], claims_for([it]), k=1, window=0)
            flagged += any(p.prechecks["c001"].values())
        out[kind] = _rate(flagged, len(rs))
    return out


def quote(corpus: Corpus, all_items: Sequence[Item], model: str) -> dict[str, Any]:
    """Offline cost estimate for the whole validation run (worst case: every claim escalates)."""
    calls: list[dict[str, Any]] = []
    for (mid, kind), group in sorted(groups(all_items).items()):
        q = G.quote(corpus.notes[mid], claims_for(group))
        calls.append(
            {
                "meeting": mid,
                "kind": kind,
                "claims": q.claims,
                "excerpt_tokens": q.excerpt_input,
                "full_tokens": q.full_input,
                "excerpt_lines": q.excerpt_lines,
                "transcript_lines": q.transcript_lines,
                "usd_worst": round(q.usd(model, 1.0), 4),
                "usd_no_escalation": round(q.usd(model, 0.0), 4),
            }
        )
    return {
        "model": model,
        "calls_max": 2 * len(calls),
        "usd_worst": round(sum(c["usd_worst"] for c in calls), 4),
        "usd_no_escalation": round(sum(c["usd_no_escalation"] for c in calls), 4),
        "by_group": calls,
    }


# -- scoring the checker's verdicts --------------------------------------------------------------


@dataclass(frozen=True)
class Scored:
    item: Item
    result: G.ClaimResult


def score(scored: Sequence[Scored], seed: int) -> dict[str, Any]:
    """Aggregate metrics over ``scored``, overall and per split."""
    out: dict[str, Any] = {}
    for split in ("all", "dev", "test"):
        rs = [s for s in scored if split == "all" or s.item.split == split]
        out[split] = _score_split(rs, seed)
    return out


def _flag(s: Scored) -> bool:
    return s.result.verdict in FLAGGED or s.result.verdict is None


def _score_split(scored: Sequence[Scored], seed: int) -> dict[str, Any]:
    orig = [s for s in scored if s.item.kind == "original"]
    supported = [s for s in orig if s.result.verdict == "supported"]
    linked = [s for s in supported if s.item.links]
    positives = {
        "labels": rates(s.result.verdict or "missing" for s in orig),
        "flagged_unsupported_or_contradicted": _rate(sum(map(_flag, orig)), len(orig)),
        "by_section": {
            sec: rates(s.result.verdict or "missing" for s in orig if s.item.section == sec)
            for sec in sorted({s.item.section for s in orig})
        },
        "evidence": {
            "supported_with_links": len(linked),
            "cited_overlaps_linked_act": _rate(
                sum(_near(s.result.lines, s.item.links, 0) for s in linked), len(linked)
            ),
            "cited_within_2_acts_of_linked": _rate(
                sum(_near(s.result.lines, s.item.links, 2) for s in linked), len(linked)
            ),
            "cited_lines_that_are_linked": _rate(
                sum(len(set(s.result.lines) & set(s.item.links)) for s in linked),
                sum(len(s.result.lines) for s in linked),
            ),
        },
    }
    by_id = {s.item.source_sentence_id + "@" + s.item.meeting: s for s in orig}
    negatives: dict[str, Any] = {}
    for kind in KINDS[1:]:
        neg = [s for s in scored if s.item.kind == kind]
        if not neg:
            continue
        # Paired precision: each perturbed item against one original of the same meeting
        # (its own source for edits; a seeded pick for injections), so classes are balanced.
        pairs: list[Scored] = []
        for s in neg:
            key = s.item.source_sentence_id + "@" + s.item.meeting
            if key in by_id:
                pairs.append(by_id[key])
            else:
                pool = [o for o in orig if o.item.meeting == s.item.meeting]
                if pool:
                    pairs.append(random.Random(f"{seed}:{s.item.item_id}").choice(pool))
        tp = sum(map(_flag, neg))
        fp = sum(map(_flag, pairs))
        tp_ns = sum(s.result.verdict != "supported" for s in neg)
        fp_ns = sum(s.result.verdict != "supported" for s in pairs)
        negatives[kind] = {
            "n": len(neg),
            "labels": dict(Counter(s.result.verdict or "missing" for s in neg)),
            "recall": _rate(tp, len(neg)),
            "precision_paired": _rate(tp, tp + fp),
            "recall_not_supported": _rate(tp_ns, len(neg)),
            "precision_not_supported_paired": _rate(tp_ns, tp_ns + fp_ns),
            "contradicted_cites_lines": _rate(
                sum(bool(s.result.lines) for s in neg if s.result.verdict == "contradicted"),
                sum(s.result.verdict == "contradicted" for s in neg),
            ),
        }
    return {
        "items": len(scored),
        "positives": positives,
        "negatives": negatives,
        "escalated_share": _rate(sum(s.result.decided_by == "full" for s in scored), len(scored)),
    }


def item_rows(scored: Sequence[Scored]) -> list[dict[str, Any]]:
    """Per-item output: AMI's own sentences (CC BY 4.0), perturbations, verdicts, line ids.

    The transcript quote is left out: line ids point into AMI's manual transcript instead.
    """
    rows = []
    for s in scored:
        r = s.result
        rows.append(
            {
                **{k: v for k, v in asdict(s.item).items() if k != "links"},
                "linked_lines": list(s.item.links),
                "verdict": r.verdict,
                "lines": r.lines,
                "reason": r.reason,
                "decided_by": r.decided_by,
                "excerpt_verdict": r.excerpt_verdict,
                "precheck": r.precheck,
            }
        )
    return rows


def kinds_count(all_items: Sequence[Item]) -> Mapping[str, Mapping[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for it in all_items:
        out.setdefault(it.kind, {}).setdefault(it.split, 0)
        out[it.kind][it.split] += 1
    return out
