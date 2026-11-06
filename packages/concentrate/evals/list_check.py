"""E1: list then check, on AMI list questions (docs/tracks/ANSWERS.md, "E1").

Does listing every item from the transcript, then checking each item against its quote, make
list answers more complete without making them wrong? Four conditions, each reading only the
question's own meeting:

* ``N``: Granola's notes only (``answer.v1``, the deletion run's ``notes_only``; replays from
  its cache).
* ``L``: list every item from Granola's transcript. Map over chunks (``enumerate.v1``: each
  candidate with a verbatim quote and its line numbers; the speaker label is read off the cited
  line), then merge duplicates (``enumerate_merge.v1``). The answer is every item.
* ``LD``: L, then a separate verifier call per item (``verify_item.v1``: the item against its
  quoted lines plus a little context). Unsupported items are dropped.
* ``LM``: the same checks; unsupported items are kept and marked "unconfirmed".

LD and LM are deterministic renderings of L's items and verdicts, so they are scored from L's
item-level judgments (one judge call for L, not three): their differences from L are then the
filter's alone, not judge noise.

**Scoring** is item level (``judge_items.v1`` on Sonnet). Gold items are AMI's human DECISIONS /
ACTIONS / PROBLEMS sentences behind the question (one item per sentence) plus the speaker
roster. The judge maps each answer item to the gold items it states and also checks it against
AMI's reference transcript, so a true item the gold left out is not counted wrong. Recall =
gold items covered; precision strict (gold only) and lenient (gold or transcript); F1 of each.
"""

from __future__ import annotations

import json
import random
import re
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from concentrate.ami.questions import AmiQuestion
from concentrate.core.models import Channel, MeetingNote
from concentrate.evals.stats import binom_two_sided_p
from concentrate.llm.client import Ledger, LLMClient, LLMRequest, cost_usd, load_prompt

LIST_TYPES = ("decision", "action_what", "problem")
N, L, LD, LM = "N", "L", "LD", "LM"
CONDITIONS: tuple[str, ...] = (N, L, LD, LM)
LABELS = {
    N: "N: Granola notes only",
    L: "L: list every item (transcript, chunked)",
    LD: "LD: list, check, drop unsupported",
    LM: "LM: list, check, mark unsupported 'unconfirmed'",
}
PAIRS: tuple[tuple[str, str], ...] = ((N, L), (L, LD), (L, LM), (LD, LM))
METRICS = ("recall", "precision_gold", "precision_any", "f1_gold", "f1_any")

ENUMERATE_PROMPT = "enumerate.v1"
MERGE_PROMPT = "enumerate_merge.v1"
VERIFY_PROMPT = "verify_item.v2"  # v1 rejected items whose owner the lines could not show (pilot)
JUDGE_PROMPT = "judge_items.v1"
ANSWER_PROMPT = "answer.v1"  # N, via deletion_ami's notes_only
PROMPTS = {
    N: [ANSWER_PROMPT],
    L: [ENUMERATE_PROMPT, MERGE_PROMPT],
    LD: [ENUMERATE_PROMPT, MERGE_PROMPT, VERIFY_PROMPT],
    LM: [ENUMERATE_PROMPT, MERGE_PROMPT, VERIFY_PROMPT],
    "judge": [JUDGE_PROMPT],
}

JUDGE_MODEL = "claude-sonnet-5-5"  # the deletion run's judge
CHUNK_CHARS = 12000  # ~3k tokens of transcript per map call
OVERLAP_LINES = 3  # lines repeated at each chunk boundary, so an item isn't cut in half
CONTEXT_LINES = 2  # lines either side of the cited ones, for the verifier
UNCONFIRMED = "(unconfirmed)"
PILOT_N = 3
PILOT_MAX_USD = 0.50
MARGIN = 1.5
OUT_DIR = Path("data/ami/results/e1")  # gitignored: rows hold transcript quotes


@dataclass(frozen=True)
class Assumed:
    """ASSUMED for the quote only (before any call exists); the pilot's ledger replaces them.
    Pairs are (expected, high)."""

    map_out: tuple[float, float] = (500.0, 1200.0)  # map output tokens, thinking included
    cands_per_chunk: tuple[float, float] = (4.0, 8.0)
    cand_tokens: int = 70  # one candidate as the merge call reads it
    merge_out_per_cand: tuple[float, float] = (40.0, 80.0)
    items_per_q: tuple[float, float] = (5.0, 10.0)  # after merging
    verify_context: int = 350  # item + quote + cited lines with context
    verify_out: tuple[float, float] = (150.0, 350.0)
    answer_item_tokens: int = 40  # one answer item as the judge reads it
    judge_out_per_item: tuple[float, float] = (70.0, 140.0)
    judge_out_base: tuple[float, float] = (100.0, 300.0)
    overhead: int = 250  # structured-output overhead per call (deletion_ami's measurement)
    answer_out: tuple[float, float] = (261.0, 456.0)  # deletion_ami.ANSWER_OUT (answer.v1)


ASSUMED = Assumed()


# -- transcript lines and chunks -------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One transcript line as the reader sees it: ``[L0042 Them 03:12] text``."""

    index: int
    speaker: str  # "Me" / "Them" (Granola's audio channel), or a role in AMI's reference
    start: str  # mm:ss from the first line
    text: str

    def render(self) -> str:
        return f"[L{self.index:04d} {self.speaker} {self.start}] {self.text}"


def _mmss(delta: timedelta) -> str:
    s = max(0, int(delta.total_seconds()))
    return f"{s // 60:02d}:{s % 60:02d}"


def transcript_lines(note: MeetingNote, speaker: Callable[[Any], str] | None = None) -> list[Line]:
    """``note``'s utterances as :class:`Line` s, in index order. The speaker is Granola's
    channel label (Me = microphone, Them = system audio) unless ``speaker`` says otherwise."""
    utts = sorted(note.utterances, key=lambda u: u.index)
    if not utts:
        return []
    t0 = utts[0].start_time

    def channel(u: Any) -> str:
        return "Me" if u.channel is Channel.ME else "Them"

    who = speaker or channel
    return [
        Line(i, who(u), _mmss(u.start_time - t0) if u.start_time and t0 else "", u.text)
        for i, u in enumerate(utts)
    ]


def merge_turns(lines: Sequence[Line]) -> list[Line]:
    """Consecutive lines by the same speaker joined into one (first line's index and start).
    For AMI's reference transcript, whose dialogue acts are short: same words, fewer tokens."""
    out: list[Line] = []
    for x in lines:
        if out and out[-1].speaker == x.speaker:
            prev = out[-1]
            out[-1] = Line(prev.index, prev.speaker, prev.start, f"{prev.text} {x.text}")
        else:
            out.append(x)
    return out


def chunk_lines(
    lines: Sequence[Line], max_chars: int = CHUNK_CHARS, overlap: int = OVERLAP_LINES
) -> list[list[Line]]:
    """Consecutive runs of lines of at most ``max_chars`` rendered characters (a longer single
    line is its own chunk); each chunk after the first repeats the previous one's last
    ``overlap`` lines. Deterministic; every line is in at least one chunk."""
    chunks: list[list[Line]] = []
    cur: list[Line] = []
    size = 0
    for line in lines:
        n = len(line.render()) + 1
        if cur and size + n > max_chars:
            chunks.append(cur)
            cur = cur[-overlap:] if overlap and len(cur) > overlap else []
            size = sum(len(x.render()) + 1 for x in cur)
        cur.append(line)
        size += n
    if cur and (not chunks or cur[-1] is not chunks[-1][-1]):
        chunks.append(cur)
    return chunks


_NON_WORD = re.compile(r"[^a-z0-9']+")


def normalize(text: str) -> str:
    """Lower case, punctuation to spaces, whitespace collapsed (for quote matching)."""
    return " ".join(_NON_WORD.sub(" ", text.lower().replace("\u2019", "'")).split())


def quote_found(quote: str, lines: Sequence[Line]) -> bool:
    """Whether ``quote`` occurs in ``lines`` (normalized; may run across adjacent lines)."""
    q = normalize(quote)
    return bool(q) and q in normalize(" ".join(x.text for x in lines))


# -- the list step (map, merge) --------------------------------------------------------------


class _Cand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item: str
    quote: str
    lines: list[int]


class EnumerateOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[_Cand]


ENUMERATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "quote": {"type": "string"},
                    "lines": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["item", "quote", "lines"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Candidate:
    """One map-step item. ``speaker`` is the label of its first cited line (read off the
    transcript, not asked of the model); ``found`` = the quote occurs in the transcript."""

    item: str
    quote: str
    lines: tuple[int, ...]
    speaker: str
    chunk: int
    found: bool


def enumerate_request(question: str, chunk: Sequence[Line], part: int, parts: int) -> LLMRequest:
    head = f"Question: {question}\n\nTranscript part {part + 1} of {parts}"
    if chunk:
        head += f" (lines L{chunk[0].index:04d}-L{chunk[-1].index:04d})"
    body = "\n".join(x.render() for x in chunk)
    return LLMRequest(
        stage="e1.enumerate",
        prompt_id=ENUMERATE_PROMPT,
        system=load_prompt(ENUMERATE_PROMPT),
        user=f"{head}:\n\n{body}",
        schema=ENUMERATE_SCHEMA,
        max_tokens=8000,
        effort="low",
    )


def candidates(
    out: EnumerateOut, chunk: Sequence[Line], part: int, transcript: Sequence[Line]
) -> list[Candidate]:
    """Map-step output as :class:`Candidate` s. Line numbers outside the chunk are dropped (a
    candidate citing none keeps an empty tuple and the label "?")."""
    inside = {x.index: x for x in chunk}
    out_cands = []
    for c in out.items:
        lines = tuple(dict.fromkeys(i for i in c.lines if i in inside))
        speaker = inside[lines[0]].speaker if lines else "?"
        out_cands.append(
            Candidate(
                c.item.strip(),
                c.quote.strip(),
                lines,
                speaker,
                part,
                quote_found(c.quote, transcript),
            )
        )
    return out_cands


def exact_dedupe(cands: Sequence[Candidate]) -> list[Candidate]:
    """Drop exact repeats from chunk overlap: same normalized quote and same cited lines, or
    same normalized item text. The first occurrence is kept."""
    seen_q: set[tuple[str, tuple[int, ...]]] = set()
    seen_t: set[str] = set()
    out = []
    for c in cands:
        kq, kt = (normalize(c.quote), c.lines), normalize(c.item)
        if kq in seen_q or kt in seen_t:
            continue
        seen_q.add(kq)
        seen_t.add(kt)
        out.append(c)
    return out


class _Group(BaseModel):
    model_config = ConfigDict(extra="forbid")
    members: list[int]
    item: str


class MergeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    groups: list[_Group]


MERGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "members": {"type": "array", "items": {"type": "integer"}},
                    "item": {"type": "string"},
                },
                "required": ["members", "item"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["groups"],
    "additionalProperties": False,
}


def merge_request(question: str, cands: Sequence[Candidate]) -> LLMRequest:
    listing = "\n".join(f'{i}. {c.item} (quote: "{c.quote}")' for i, c in enumerate(cands))
    return LLMRequest(
        stage="e1.merge",
        prompt_id=MERGE_PROMPT,
        system=load_prompt(MERGE_PROMPT),
        user=f"Question: {question}\n\nCandidates:\n{listing}",
        schema=MERGE_SCHEMA,
        max_tokens=8000,
        effort="low",
    )


@dataclass(frozen=True)
class Item:
    """A merged item: what the L/LD/LM answers list. Its quote and lines are its first
    member's (the verifier checks those); ``quotes`` keeps every member's."""

    id: str  # "I1", "I2", ... in first-member order
    text: str
    quote: str
    lines: tuple[int, ...]
    speaker: str
    found: bool
    members: tuple[int, ...]
    quotes: tuple[str, ...]


def apply_groups(
    cands: Sequence[Candidate], groups: Iterable[tuple[Sequence[int], str]]
) -> list[Item]:
    """Turn merge groups into items, repairing the partition: unknown indices are ignored, an
    index already used keeps its first group, empty groups vanish, and any candidate left out
    becomes its own item (the merge may not drop anything). Items are ordered by first member."""
    used: set[int] = set()
    kept: list[tuple[tuple[int, ...], str]] = []
    for members, text in groups:
        ms = tuple(i for i in dict.fromkeys(members) if 0 <= i < len(cands) and i not in used)
        if not ms:
            continue
        used.update(ms)
        kept.append((tuple(sorted(ms)), text.strip() or cands[min(ms)].item))
    kept += [((i,), cands[i].item) for i in range(len(cands)) if i not in used]
    kept.sort(key=lambda g: g[0][0])
    items = []
    for n, (ms, text) in enumerate(kept, 1):
        first = cands[ms[0]]
        items.append(
            Item(
                id=f"I{n}",
                text=text,
                quote=first.quote,
                lines=first.lines,
                speaker=first.speaker,
                found=first.found,
                members=ms,
                quotes=tuple(cands[i].quote for i in ms),
            )
        )
    return items


# -- the check step --------------------------------------------------------------------------


class VerifyOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasoning: str
    verdict: Literal["supported", "not_supported"]


VERIFY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reasoning": {"type": "string"},
        "verdict": {"type": "string", "enum": ["supported", "not_supported"]},
    },
    "required": ["reasoning", "verdict"],
    "additionalProperties": False,
}


def context_lines(
    item: Item, transcript: Sequence[Line], context: int = CONTEXT_LINES
) -> list[Line]:
    """The cited lines plus ``context`` lines either side of each, in order."""
    wanted: set[int] = set()
    for i in item.lines:
        wanted.update(range(i - context, i + context + 1))
    return [x for x in transcript if x.index in wanted]


def verify_request(question: str, item: Item, transcript: Sequence[Line]) -> LLMRequest:
    shown = context_lines(item, transcript)
    cited = ", ".join(f"L{i:04d}" for i in item.lines) or "none"
    body = "\n".join(x.render() for x in shown) or "(no lines cited)"
    return LLMRequest(
        stage="e1.verify",
        prompt_id=VERIFY_PROMPT,
        system=load_prompt(VERIFY_PROMPT),
        user=(
            f'Question: {question}\n\nItem: {item.text}\n\nQuote: "{item.quote}"\n\n'
            f"Cited lines: {cited}. The lines with context:\n{body}"
        ),
        schema=VERIFY_SCHEMA,
        max_tokens=4000,
        effort="low",
    )


def render_list(
    items: Sequence[Item], confirmed: Mapping[str, bool] | None = None, *, drop: bool = False
) -> str:
    """An L / LD / LM answer: one bullet per item. With ``confirmed``, unconfirmed items are
    dropped (``drop``: LD) or marked :data:`UNCONFIRMED` (LM)."""
    out = []
    for it in items:
        ok = True if confirmed is None else confirmed.get(it.id, False)
        if drop and not ok:
            continue
        out.append(f"- {it.text}" + ("" if ok else f" {UNCONFIRMED}"))
    return "\n".join(out) or "(no items found)"


def kept_ids(items: Sequence[Item], condition: str, confirmed: Mapping[str, bool]) -> list[str]:
    """Which of L's items a condition's answer lists (LD drops the unconfirmed ones)."""
    if condition == LD:
        return [it.id for it in items if confirmed.get(it.id, False)]
    return [it.id for it in items]


# -- the judge -------------------------------------------------------------------------------


class JudgedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    gold: list[str]
    in_transcript: bool
    reason: str


class JudgeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_items: list[JudgedItem]


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "gold": {"type": "array", "items": {"type": "string"}},
                    "in_transcript": {"type": "boolean"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "text", "gold", "in_transcript", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answer_items"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Gold:
    """A question's gold: one item per AMI summary sentence, plus the speaker roster."""

    items: tuple[tuple[str, str], ...]  # ("G1", sentence), ...
    roster: str = ""

    @property
    def ids(self) -> list[str]:
        return [g for g, _ in self.items]

    def render(self) -> str:
        body = "\n".join(f"{g}. {t}" for g, t in self.items)
        return f"{body}\n\n{self.roster}" if self.roster else body


def gold_for(q: AmiQuestion, sentences: Mapping[str, str], roster: str = "") -> Gold:
    """``q``'s gold items, in the question's own sentence order. ``sentences``: AMI sentence
    id -> text for the question's meetings."""
    return Gold(
        tuple((f"G{i}", sentences[s]) for i, s in enumerate(q.gold_sentence_ids, 1)),
        roster,
    )


def judge_answer_block(answer: str | Sequence[tuple[str, str]]) -> str:
    """Free text as is; a list as numbered items ``A1. text``."""
    if isinstance(answer, str):
        return f"System answer (free text):\n{answer}"
    if not answer:
        return "System answer: (no items)"
    return "System answer (numbered items):\n" + "\n".join(f"{i}. {t}" for i, t in answer)


def judge_request(
    question: str, gold: Gold, reference: Sequence[Line], answer: str | Sequence[tuple[str, str]]
) -> LLMRequest:
    return LLMRequest(
        stage="e1.judge",
        prompt_id=JUDGE_PROMPT,
        system=load_prompt(JUDGE_PROMPT),
        user=judge_context(question, gold, reference) + "\n\n" + judge_answer_block(answer),
        schema=JUDGE_SCHEMA,
        max_tokens=8000,
        effort="medium",
    )


def judge_context(question: str, gold: Gold, reference: Sequence[Line]) -> str:
    """Everything the judge reads except the answer (exactly countable before answers exist)."""
    ref = "\n".join(x.render() for x in reference)
    return (
        f"Question: {question}\n\nGold items:\n{gold.render()}\n\n"
        f"<reference_transcript>\n{ref}\n</reference_transcript>"
    )


class JudgeParseError(ValueError):
    pass


def parse_mapping(
    out: JudgeOut, gold_ids: Sequence[str], answer_ids: Sequence[str] | None
) -> list[JudgedItem]:
    """Validate the judge's item mapping. Gold IDs it invents are dropped (and duplicates
    collapsed). For a numbered answer (``answer_ids``) the judge must return exactly those IDs:
    a missing or extra ID raises :class:`JudgeParseError`, order follows ``answer_ids``. Free
    text: the judge's own items, renumbered A1.. in its order."""
    valid = set(gold_ids)
    fixed = [
        it.model_copy(update={"gold": [g for g in dict.fromkeys(it.gold) if g in valid]})
        for it in out.answer_items
    ]
    if answer_ids is None:
        return [it.model_copy(update={"id": f"A{i}"}) for i, it in enumerate(fixed, 1)]
    by_id: dict[str, JudgedItem] = {}
    for it in fixed:
        if it.id in by_id:
            raise JudgeParseError(f"judge returned {it.id} twice")
        by_id[it.id] = it
    if set(by_id) != set(answer_ids):
        missing = sorted(set(answer_ids) - set(by_id))
        extra = sorted(set(by_id) - set(answer_ids))
        raise JudgeParseError(f"judge items mismatch: missing {missing}, extra {extra}")
    return [by_id[i] for i in answer_ids]


# -- scoring ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Score:
    """Item-level scores of one answer. Precision is None for an answer with no items."""

    n_items: int
    n_gold: int
    covered: int  # gold items stated by at least one answer item
    gold_hits: int  # answer items that state a gold item
    any_hits: int  # answer items that state a gold item or are true per the transcript

    @property
    def recall(self) -> float:
        return self.covered / self.n_gold if self.n_gold else 0.0

    @property
    def precision_gold(self) -> float | None:
        return self.gold_hits / self.n_items if self.n_items else None

    @property
    def precision_any(self) -> float | None:
        return self.any_hits / self.n_items if self.n_items else None

    @property
    def f1_gold(self) -> float:
        return _f1(self.precision_gold, self.recall)

    @property
    def f1_any(self) -> float:
        return _f1(self.precision_any, self.recall)

    def metrics(self) -> dict[str, float | None]:
        return {m: getattr(self, m) for m in METRICS}


def _f1(p: float | None, r: float) -> float:
    """Harmonic mean; an empty answer (p None) or p + r = 0 scores 0."""
    return 0.0 if not p or not r else 2 * p * r / (p + r)


def score(
    judged: Sequence[JudgedItem], gold_ids: Sequence[str], keep: Iterable[str] | None = None
) -> Score:
    """Score the answer items in ``keep`` (all of ``judged`` if None) against ``gold_ids``."""
    ks = None if keep is None else set(keep)
    items = [it for it in judged if ks is None or it.id in ks]
    covered = {g for it in items for g in it.gold} & set(gold_ids)
    return Score(
        n_items=len(items),
        n_gold=len(gold_ids),
        covered=len(covered),
        gold_hits=sum(1 for it in items if it.gold),
        any_hits=sum(1 for it in items if it.gold or it.in_transcript),
    )


def mark_precision(
    judged: Sequence[JudgedItem], confirmed: Mapping[str, bool]
) -> dict[str, dict[str, int]]:
    """LM: counts of items, gold hits and gold-or-transcript hits, for confirmed vs
    unconfirmed items separately (does the mark carry information?)."""
    out = {k: {"items": 0, "gold": 0, "any": 0} for k in ("confirmed", "unconfirmed")}
    for it in judged:
        row = out["confirmed" if confirmed.get(it.id, False) else "unconfirmed"]
        row["items"] += 1
        row["gold"] += int(bool(it.gold))
        row["any"] += int(bool(it.gold) or it.in_transcript)
    return out


def sign_test(a: Mapping[str, float | None], b: Mapping[str, float | None]) -> dict[str, Any]:
    """Paired sign test of ``b`` vs ``a`` over questions where both are defined: wins = b
    higher. Ties (and questions with an undefined value) are left out of the test."""
    pairs: list[tuple[float, float]] = []
    for q in sorted(a.keys() & b.keys()):
        x, y = a[q], b[q]
        if x is not None and y is not None:
            pairs.append((x, y))
    wins = sum(1 for x, y in pairs if y > x)
    losses = sum(1 for x, y in pairs if y < x)
    return {
        "n": len(pairs),
        "wins": wins,
        "losses": losses,
        "ties": len(pairs) - wins - losses,
        "sign_p": binom_two_sided_p(wins, wins + losses),
    }


def _mean(xs: Iterable[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return statistics.fmean(vals) if vals else None


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Per-condition means (macro over questions; precision over questions with items) and
    micro totals, LM's confirmed / unconfirmed precision, and the paired sign tests."""
    per: dict[str, dict[str, Any]] = {}
    for c in CONDITIONS:
        scores = [r["scores"][c] for r in records if c in r["scores"]]
        tot = {
            k: sum(s[k] for s in scores)
            for k in ("n_items", "n_gold", "covered", "gold_hits", "any_hits")
        }
        per[c] = {
            "n_questions": len(scores),
            "n_with_items": sum(1 for s in scores if s["n_items"]),
            **{f"mean_{m}": _mean(s[m] for s in scores) for m in METRICS},
            "micro": tot,
            "micro_recall": tot["covered"] / tot["n_gold"] if tot["n_gold"] else None,
            "micro_precision_gold": tot["gold_hits"] / tot["n_items"] if tot["n_items"] else None,
            "micro_precision_any": tot["any_hits"] / tot["n_items"] if tot["n_items"] else None,
        }
    marks = {k: {"items": 0, "gold": 0, "any": 0} for k in ("confirmed", "unconfirmed")}
    for r in records:
        for k, row in r.get("marks", {}).items():
            for f, v in row.items():
                marks[k][f] += v
    for row in marks.values():
        row_any = row["items"]
        row["precision_gold"] = row["gold"] / row_any if row_any else None  # type: ignore[assignment]
        row["precision_any"] = row["any"] / row_any if row_any else None  # type: ignore[assignment]
    paired = {}
    for a, b in PAIRS:
        paired[f"{b} vs {a}"] = {
            m: sign_test(
                {r["qid"]: r["scores"][a][m] for r in records if a in r["scores"]},
                {r["qid"]: r["scores"][b][m] for r in records if b in r["scores"]},
            )
            for m in ("recall", "precision_gold", "precision_any")
        }
    quotes = [it["found"] for r in records for it in r.get("items", [])]
    verdicts = [v for r in records for v in r.get("confirmed", {}).values()]
    return {
        "conditions": per,
        "lm_marks": marks,
        "paired": paired,
        "items_quote_found": {"found": sum(quotes), "items": len(quotes)},
        "items_confirmed": {"confirmed": sum(verdicts), "items": len(verdicts)},
    }


# -- selection -------------------------------------------------------------------------------


def list_questions(questions: Iterable[Any]) -> list[Any]:
    """The questions whose gold is a list of summary sentences (decision, action_what,
    problem), in ID order. Anything with ``.type`` or ``.category``."""
    return sorted((q for q in questions if _kind(q) in LIST_TYPES), key=lambda q: q.id)


def pilot_questions(qs: Sequence[Any], n: int, seed: int) -> list[Any]:
    """``n`` DEV questions, one per list type first (so the pilot sees each prompt shape),
    seeded; fewer if fewer DEV questions exist. ID order."""
    dev = sorted((q for q in qs if q.split == "dev"), key=lambda q: q.id)
    rng = random.Random(f"{seed}:e1.pilot")
    by_type: dict[str, list[Any]] = {}
    for q in dev:
        by_type.setdefault(_kind(q), []).append(q)
    picked: list[Any] = []
    for t in LIST_TYPES:
        if by_type.get(t) and len(picked) < n:
            picked.append(rng.choice(by_type[t]))
    rest = [q for q in dev if q not in picked]
    rng.shuffle(rest)
    picked += rest[: max(0, n - len(picked))]
    return sorted(picked, key=lambda q: q.id)


def _kind(q: Any) -> str:
    return str(getattr(q, "type", None) or getattr(q, "category", ""))


# -- running one question --------------------------------------------------------------------


@dataclass
class Context:
    """What one question reads: Granola's transcript (L), AMI's reference (judge), the gold."""

    qid: str
    question: str
    category: str
    split: str
    transcript: list[Line]
    reference: list[Line]
    gold: Gold


def list_items(
    reader: LLMClient, ctx: Context, chunk_chars: int = CHUNK_CHARS
) -> tuple[list[Candidate], list[Item]]:
    """L's map and merge: candidates from every chunk, exact repeats dropped, then merged."""
    chunks = chunk_lines(ctx.transcript, chunk_chars)
    cands: list[Candidate] = []
    for i, ch in enumerate(chunks):
        out = reader.structured(enumerate_request(ctx.question, ch, i, len(chunks)), EnumerateOut)
        cands += candidates(out, ch, i, ctx.transcript)
    cands = exact_dedupe(cands)
    if len(cands) <= 1:
        return cands, apply_groups(cands, [])
    merged = reader.structured(merge_request(ctx.question, cands), MergeOut)
    return cands, apply_groups(cands, [(g.members, g.item) for g in merged.groups])


def check_items(
    reader: LLMClient, ctx: Context, items: Sequence[Item]
) -> tuple[dict[str, bool], dict[str, str]]:
    """One verifier call per item: (item id -> supported, item id -> reasoning)."""
    ok, why = {}, {}
    for it in items:
        v = reader.structured(verify_request(ctx.question, it, ctx.transcript), VerifyOut)
        ok[it.id], why[it.id] = v.verdict == "supported", v.reasoning
    return ok, why


def run_question(
    ctx: Context,
    reader: LLMClient,
    judge: LLMClient,
    notes_answer: Callable[[], str] | None,
    conditions: Sequence[str] = CONDITIONS,
    chunk_chars: int = CHUNK_CHARS,
) -> dict[str, Any]:
    """Every condition on one question, judged; a JSON-ready record (holds transcript text:
    local only)."""
    rec: dict[str, Any] = {
        "qid": ctx.qid,
        "category": ctx.category,
        "split": ctx.split,
        "gold": [{"id": g, "text": t} for g, t in ctx.gold.items],
        "scores": {},
    }
    gids = ctx.gold.ids
    if N in conditions and notes_answer is not None:
        text = notes_answer()
        out = judge.structured(judge_request(ctx.question, ctx.gold, ctx.reference, text), JudgeOut)
        judged = parse_mapping(out, gids, None)
        rec["answers"] = {N: text}
        rec["judged"] = {N: [j.model_dump() for j in judged]}
        rec["scores"][N] = _score_row(score(judged, gids))
    if {L, LD, LM} & set(conditions):
        cands, items = list_items(reader, ctx, chunk_chars)
        rec["candidates"] = [asdict(c) for c in cands]
        rec["items"] = [asdict(it) for it in items]
        confirmed: dict[str, bool] = {}
        if {LD, LM} & set(conditions):
            confirmed, why = check_items(reader, ctx, items)
            rec["confirmed"], rec["verify_reasoning"] = confirmed, why
        listed = [(f"A{i}", it.text) for i, it in enumerate(items, 1)]
        out = judge.structured(
            judge_request(ctx.question, ctx.gold, ctx.reference, listed), JudgeOut
        )
        judged_a = parse_mapping(out, gids, [a for a, _ in listed])
        # Back to item IDs, so LD/LM can select by item.
        judged = [j.model_copy(update={"id": it.id}) for j, it in zip(judged_a, items, strict=True)]
        rec.setdefault("answers", {})
        rec.setdefault("judged", {})[L] = [j.model_dump() for j in judged]
        for c in (L, LD, LM):
            if c not in conditions:
                continue
            if c == L:
                rec["answers"][c] = render_list(items)
                rec["scores"][c] = _score_row(score(judged, gids))
            else:
                rec["answers"][c] = render_list(items, confirmed, drop=c == LD)
                rec["scores"][c] = _score_row(score(judged, gids, kept_ids(items, c, confirmed)))
        if LM in conditions:
            rec["marks"] = mark_precision(judged, confirmed)
    return rec


def _score_row(s: Score) -> dict[str, Any]:
    return {**asdict(s), **s.metrics()}


# -- the quote -------------------------------------------------------------------------------


@dataclass
class QuoteLine:
    """One stage of the run: calls, exact input where countable, and a cost range."""

    stage: str
    model: str
    calls: int
    cached: int
    input_tokens: int
    exact: bool  # input counted with count_tokens (else partly ASSUMED)
    usd_expected: float
    usd_high: float
    note: str = ""


Counter = Callable[[list[str]], list[int]]


def _offline(client: LLMClient) -> LLMClient:
    """A cache-only twin of ``client`` (own ledger): replays, never calls the API."""
    return LLMClient(model=client.model, cache_dir=client.cache_dir, offline=True, ledger=Ledger())


@dataclass
class _Stage:
    calls: int = 0
    cached: int = 0
    tin: list[float] = field(default_factory=lambda: [0.0, 0.0])
    tout: list[float] = field(default_factory=lambda: [0.0, 0.0])
    assumed: bool = False

    def add(self, tin: tuple[float, float], tout: tuple[float, float], calls: int = 1) -> None:
        self.calls += calls
        for i in (0, 1):
            self.tin[i] += tin[i]
            self.tout[i] += tout[i]

    def line(self, stage: str, model: str, note: str) -> QuoteLine:
        exp = cost_usd(model, round(self.tin[0]), round(self.tout[0]))
        high = cost_usd(model, round(self.tin[1]), round(self.tout[1]))
        return QuoteLine(
            stage, model, self.calls, self.cached, round(self.tin[0]), not self.assumed, exp,
            high, note,
        )  # fmt: skip


def quote(
    contexts: Sequence[Context],
    reader: LLMClient,
    judge: LLMClient,
    counter: Counter,
    judge_counter: Counter,
    notes_requests: Sequence[Any] = (),
    notes_cached: Callable[[Any], bool] = lambda _r: False,
    conditions: Sequence[str] = CONDITIONS,
    chunk_chars: int = CHUNK_CHARS,
    a: Assumed = ASSUMED,
    notes_texts: Mapping[str, str] | None = None,
) -> list[QuoteLine]:
    """Price a run before any call, question by question. Input is exact (count_tokens)
    wherever the request can be built: the map calls always; the merge, verify and judge
    calls once the calls before them are cached (they are replayed offline to build them);
    N's judge call when N's answer is known (``notes_texts``). Otherwise :class:`Assumed`.
    Output lengths are always ASSUMED. Cached calls cost $0."""
    oh, rm, jm = a.overhead, reader.model, judge.model
    listing = bool({L, LD, LM} & set(conditions))
    checking = bool({LD, LM} & set(conditions))
    off_r = _offline(reader)
    st = {k: _Stage() for k in ("N", "enumerate", "merge", "verify", "judge")}
    sys_m = counter([load_prompt(MERGE_PROMPT)])[0] + oh + 40
    sys_v = counter([load_prompt(VERIFY_PROMPT)])[0] + oh
    sys_j = judge_counter([load_prompt(JUDGE_PROMPT)])[0] + oh

    def exact(client: LLMClient, cnt: Counter, req: LLMRequest, out: tuple[float, float]) -> None:
        stage = st[req.stage.removeprefix("e1.")]
        if client.is_cached(req):
            stage.cached += 1
            return
        t = cnt([f"{req.system}\n\n{req.user}"])[0] + oh
        stage.add((t, t), out)

    if N in conditions and notes_requests:
        todo = [r for r in notes_requests if not notes_cached(r)]
        st["N"].cached = len(notes_requests) - len(todo)
        if todo:
            t = sum(counter([f"{r.system}\n\n{r.messages[0]['content']}" for r in todo]))
            t += oh * len(todo)
            st["N"].add((t, t), _scale(a.answer_out, len(todo)), len(todo))
    n_chunks = 0
    for c in contexts:
        ctx_j = judge_counter([judge_context(c.question, c.gold, c.reference)])[0]
        if N in conditions:
            text = (notes_texts or {}).get(c.qid)
            if text is not None:
                req = judge_request(c.question, c.gold, c.reference, text)
                exact(judge, judge_counter, req, _judge_out(a, (4.0, 8.0)))
            else:
                t = sys_j + ctx_j + a.answer_item_tokens * 4
                st["judge"].add((t, t), _judge_out(a, (4.0, 8.0)))
                st["judge"].assumed = True
        if not listing:
            continue
        chunks = chunk_lines(c.transcript, chunk_chars)
        n_chunks += len(chunks)
        reqs = [enumerate_request(c.question, ch, i, len(chunks)) for i, ch in enumerate(chunks)]
        for r in reqs:
            exact(reader, counter, r, a.map_out)
        items: list[Item] | None = None
        if all(reader.is_cached(r) for r in reqs):
            cands: list[Candidate] = []
            for i, (ch, r) in enumerate(zip(chunks, reqs, strict=True)):
                cands += candidates(off_r.structured(r, EnumerateOut), ch, i, c.transcript)
            cands = exact_dedupe(cands)
            if len(cands) <= 1:
                items = apply_groups(cands, [])
            else:
                mreq = merge_request(c.question, cands)
                if reader.is_cached(mreq):
                    st["merge"].cached += 1
                    m = off_r.structured(mreq, MergeOut)
                    items = apply_groups(cands, [(g.members, g.item) for g in m.groups])
                else:
                    exact(reader, counter, mreq, _scale(a.merge_out_per_cand, len(cands)))
        else:
            nc = [k * len(chunks) for k in a.cands_per_chunk]
            st["merge"].add(
                (sys_m + a.cand_tokens * nc[0], sys_m + a.cand_tokens * nc[1]),
                (a.merge_out_per_cand[0] * nc[0], a.merge_out_per_cand[1] * nc[1]),
            )
            st["merge"].assumed = True
        if items is not None:
            if checking:
                for it in items:
                    exact(
                        reader, counter, verify_request(c.question, it, c.transcript), a.verify_out
                    )
            listed = [(f"A{i}", it.text) for i, it in enumerate(items, 1)]
            jreq = judge_request(c.question, c.gold, c.reference, listed)
            n = float(len(items))
            exact(judge, judge_counter, jreq, _judge_out(a, (n, n)))
        else:
            ni = a.items_per_q
            if checking:
                t = sys_v + a.verify_context
                st["verify"].add(
                    (t * ni[0], t * ni[1]),
                    (a.verify_out[0] * ni[0], a.verify_out[1] * ni[1]),
                    round(ni[0]),
                )
                st["verify"].assumed = True
            t = sys_j + ctx_j
            st["judge"].add(
                (t + a.answer_item_tokens * ni[0], t + a.answer_item_tokens * ni[1]),
                _judge_out(a, ni),
            )
            st["judge"].assumed = True
    nq = len(contexts)
    lines: list[QuoteLine] = []
    if N in conditions and notes_requests:
        lines.append(
            st["N"].line(
                f"N answer ({ANSWER_PROMPT})",
                rm,
                "replays the deletion run's cache when the request is identical",
            )
        )
    if listing:
        lines.append(
            st["enumerate"].line(
                f"L map ({ENUMERATE_PROMPT})", rm, f"{n_chunks} chunks over {nq} questions"
            )
        )
        lines.append(
            st["merge"].line(
                f"L merge ({MERGE_PROMPT})",
                rm,
                "exact once the map calls are cached; else candidates per chunk ASSUMED",
            )
        )
    if checking:
        lines.append(
            st["verify"].line(
                f"LD/LM verify ({VERIFY_PROMPT})",
                rm,
                "exact once the merge is cached; else items per question ASSUMED; "
                "shared by LD and LM",
            )
        )
    if (N in conditions or listing) and contexts:
        lines.append(
            st["judge"].line(
                f"judge ({JUDGE_PROMPT})",
                jm,
                "N + L answers (LD/LM scored from L's items); exact once the answer is known",
            )
        )
    return lines


def _scale(pair: tuple[float, float], k: float) -> tuple[float, float]:
    return pair[0] * k, pair[1] * k


def _judge_out(a: Assumed, items: tuple[float, float]) -> tuple[float, float]:
    """Judge output tokens (expected, high) for an answer of ``items`` items (at least 4 / 8)."""
    lo, hi = max(items[0], 4.0), max(items[1], 8.0)
    return (
        a.judge_out_base[0] + a.judge_out_per_item[0] * lo,
        a.judge_out_base[1] + a.judge_out_per_item[1] * hi,
    )


def quote_markdown(sections: Sequence[tuple[str, Sequence[QuoteLine]]], how: str) -> str:
    """The quote as one table per section."""
    out = [f"Input tokens: {how}, + {ASSUMED.overhead} structured-output overhead per call."]
    for title, lines in sections:
        out += [
            "",
            f"### {title}",
            "",
            "| stage | model | calls (cached) | input tokens | exact? | expected | high |",
            "|---|---|---|---|---|---|---|",
        ]
        for x in lines:
            exact = "yes" if x.exact else "partly ASSUMED"
            out.append(
                f"| {x.stage} | {x.model} | {x.calls} ({x.cached}) | {x.input_tokens:,} | "
                f"{exact} | ${x.usd_expected:.3f} | ${x.usd_high:.3f} |"
            )
        e, h = totals(lines)
        out.append(f"| **total** | | | | | **${e:.3f}** | **${h:.3f}** |")
        out += [f"- {x.stage}: {x.note}" for x in lines if x.note]
    return "\n".join(out) + "\n"


def totals(lines: Iterable[QuoteLine]) -> tuple[float, float]:
    ls = list(lines)
    return sum(x.usd_expected for x in ls), sum(x.usd_high for x in ls)


def reprice(
    ledger_records: Sequence[Mapping[str, Any]],
    pilot_contexts: Sequence[Context],
    full_contexts: Sequence[Context],
    chunk_chars: int = CHUNK_CHARS,
    margin: float = MARGIN,
    free_stages: Iterable[str] = (),
) -> dict[str, Any]:
    """The full run re-priced from the pilot's actual ledger: each stage's recorded pilot cost
    (cached calls included at their original cost) scaled to the full run, times ``margin``.
    Map, merge and verify scale with transcript chunks (longer meetings: more calls and more
    items); the judge with reference-transcript characters (its input is mostly that); anything
    else with questions. ``free_stages`` are fully cached in the full run ($0)."""
    by: dict[str, list[float]] = {}
    for r in ledger_records:
        by.setdefault(str(r["stage"]), []).append(float(r["cost_usd"]))

    def ratio(f: Callable[[Context], float]) -> float:
        p = sum(f(c) for c in pilot_contexts)
        return sum(f(c) for c in full_contexts) / p if p else 0.0

    by_chunks = ratio(lambda c: len(chunk_lines(c.transcript, chunk_chars)))
    scale = {
        "e1.enumerate": by_chunks,
        "e1.merge": by_chunks,
        "e1.verify": by_chunks,
        "e1.judge": ratio(lambda c: sum(len(x.render()) for x in c.reference)),
    }
    free = set(free_stages)
    out: dict[str, Any] = {"stages": {}, "margin": margin}
    total = 0.0
    for stage, costs in sorted(by.items()):
        k = 0.0 if stage in free else scale.get(stage, ratio(lambda _c: 1.0))
        usd = sum(costs) * k
        out["stages"][stage] = {
            "pilot_calls": len(costs),
            "pilot_usd_recorded": sum(costs),
            "usd_per_call": sum(costs) / len(costs),
            "scale": k,
            "full_usd": usd,
        }
        total += usd
    out["full_usd"] = total
    out["full_usd_with_margin"] = total * margin
    out["note"] = (
        "pilot cost recorded per stage, cached calls at their original cost; "
        f"free in the full run (cached): {sorted(free) or 'none'}"
    )
    return out


def write_json(path: Path, obj: Any) -> None:
    """Sorted keys, 1-space indent: byte-identical for identical input."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True, default=str) + "\n")


@dataclass
class RunMeta:
    """What every E1 result records (CLAUDE.md: git SHA, models, prompt versions, seed, date)."""

    date: str
    git: dict[str, Any]
    seed: int
    reader_model: str
    judge_model: str
    pilot: bool
    max_usd: float | None
    questions: list[str]
    prompts: dict[str, list[str]] = field(default_factory=lambda: dict(PROMPTS))
    chunk_chars: int = CHUNK_CHARS
    overlap_lines: int = OVERLAP_LINES
    context_lines: int = CONTEXT_LINES
