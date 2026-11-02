"""The grounding gate: no fact enters memory unless the lines it cites support it.

Three layers, cheapest first:

1. **Deterministic checks** - cited line IDs exist in this note; ``valid_from`` is a date; every
   number in ``value_norm`` (and the month/day of an ISO date) is said in the cited lines,
   using :func:`concentrate.core.spoken.spoken_numbers` to read "twenty five dollars" as 25.
2. **Quote check** - the extractor's supporting quote is a (normalised) substring of the cited
   lines; ``...`` may join pieces from different cited lines.
3. **Entailment** (optional, LLM) - only for items that pass 1-2 but are low-confidence or whose
   date is relative ("by Thursday") so step 1 couldn't check it. One cheap call per meeting.

:func:`gate_eval` injects known-bad variants of real facts and reports the gate's precision and
recall at catching them.
"""

from __future__ import annotations

import itertools
import random
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from concentrate.core.spoken import spoken_numbers
from concentrate.memory.schema import (
    ENTAILMENT_SCHEMA,
    Entailment,
    ExtractedCommitment,
    ExtractedFact,
    ExtractedUpdate,
)

ENTAIL_PROMPT = "entail.v1"
LOW_CONFIDENCE = 0.75
_MONTHS = [
    "january", "february", "march", "april", "may", "june", "july", "august", "september",
    "october", "november", "december",
]  # fmt: skip
_RELATIVE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|tonight|"
    r"yesterday|next week|this week|end of (?:the )?(?:week|month|quarter)|weekend|eod|eow)\b"
)
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")

Item = ExtractedFact | ExtractedCommitment | ExtractedUpdate


@dataclass
class GateResult:
    ok: bool
    reason: str = ""  # machine-readable rejection reason, "" when ok
    detail: str = ""
    needs_entailment: bool = False


def _norm_text(s: str) -> str:
    s = s.lower().replace("\u2019", "'")
    s = re.sub(r"[^a-z0-9'$%]+", " ", s)
    return " ".join(s.split())


def quote_supported(quote: str, cited_text: str) -> bool:
    """Every ``...``-separated piece of ``quote`` is a normalised substring of the cited text."""
    hay = _norm_text(cited_text)
    pieces = [_norm_text(p) for p in re.split(r"\.\.\.|…", quote)]
    pieces = [p for p in pieces if p]
    return bool(pieces) and all(p in hay for p in pieces)


def _numbers(text: str) -> set[int]:
    nums = spoken_numbers(text)
    out = set(nums)
    for a, b in itertools.pairwise(nums):  # "PAY two twelve" -> 212
        out.add(int(f"{a}{b}"))
    for n in list(out):
        out.add(n * 1000)
        if n % 1000 == 0:
            out.add(n // 1000)
    return out


def check_value(value_norm: str, cited_text: str) -> tuple[bool, str, bool]:
    """Numbers and dates in ``value_norm`` vs. the cited text.

    Returns ``(ok, detail, relative)``; ``relative`` means a date that is only said relatively
    ("by Thursday"), so it passes here but should be entailment-checked.
    """
    v = value_norm.strip()
    low = cited_text.lower()
    said = _numbers(cited_text)
    if _ISO.match(v):
        d = date.fromisoformat(v)
        if _MONTHS[d.month - 1] in low and d.day in said:
            return True, "", False
        if _RELATIVE.search(low):
            return True, "relative date", True
        return False, f"date {v} not said in cited lines", False
    digits = re.findall(r"\d+(?:\.\d+)?", v.replace(",", ""))
    for tok in digits:
        if "." in tok:
            whole = tok.split(".")[0]
            if int(whole) not in said and tok not in low:
                return False, f"number {tok} not said in cited lines", False
            continue
        n = int(tok)
        if n not in said and str(n) not in low:
            return False, f"number {n} not said in cited lines", False
    return True, "", False


def check_item(
    item: Item, lines: dict[str, str], *, low_confidence: float = LOW_CONFIDENCE
) -> GateResult:
    """Deterministic + quote checks for one extracted item. ``lines`` maps full utterance IDs
    of *this* note to their text; ``item.source_lines`` must already be full IDs."""
    if not item.source_lines:
        return GateResult(False, "no_source_lines", "item cites no lines")
    missing = [x for x in item.source_lines if x not in lines]
    if missing:
        return GateResult(False, "unknown_line", f"cited lines not in this note: {missing[:3]}")
    cited = " ".join(lines[x] for x in item.source_lines)
    if not quote_supported(item.quote, cited):
        return GateResult(False, "quote_mismatch", f"quote not in cited lines: {item.quote[:80]!r}")
    relative = False
    if isinstance(item, ExtractedFact):
        if not item.subject.strip() or not item.predicate.strip():
            return GateResult(False, "malformed", "empty subject or predicate")
        if item.valid_from is not None and not _ISO.match(item.valid_from):
            return GateResult(False, "malformed", f"valid_from {item.valid_from!r} is not ISO")
        ok, detail, relative = check_value(item.value_norm, cited)
        if not ok:
            return GateResult(False, "value_not_in_lines", detail)
    for d in _dates(item):
        if not _ISO.match(d):
            return GateResult(False, "malformed", f"date {d!r} is not ISO")
    needs = relative or item.confidence < low_confidence
    return GateResult(True, needs_entailment=needs)


def _dates(item: Item) -> list[str]:
    if isinstance(item, ExtractedCommitment):
        return [item.due] if item.due else []
    if isinstance(item, ExtractedUpdate):
        return [item.new_due] if item.new_due else []
    return []


def claim_text(item: Item) -> str:
    """One-sentence claim for the entailment checker."""
    if isinstance(item, ExtractedFact):
        when = f" (from {item.valid_from})" if item.valid_from else ""
        return f"{item.subject} {item.predicate} = {item.value}{when}"
    if isinstance(item, ExtractedCommitment):
        due = f", due {item.due}" if item.due else ""
        return f"{item.owner} committed to: {item.task}{due}"
    extra = f" (new due {item.new_due})" if item.new_due else ""
    extra += f" (new owner {item.new_owner})" if item.new_owner else ""
    return f"{item.owner}'s commitment '{item.task}' is {item.kind}{extra}"


EntailFn = Callable[[str, dict[str, Any]], Entailment]  # (user prompt, schema) -> verdicts


def entailment_prompt(items: Sequence[Item], lines: dict[str, str], meeting_day: str) -> str:
    blocks = []
    for i, it in enumerate(items):
        cited = "\n".join(f"  [{x}] {lines[x]}" for x in it.source_lines if x in lines)
        blocks.append(f"Claim {i}: {claim_text(it)}\nCited lines:\n{cited}")
    return f"Meeting date: {meeting_day}\n\n" + "\n\n".join(blocks)


def entail(
    items: Sequence[Item], lines: dict[str, str], meeting_day: str, fn: EntailFn
) -> list[GateResult]:
    """Ask the entailment model about ``items`` in one call; unanswered items fail closed."""
    if not items:
        return []
    out = fn(entailment_prompt(items, lines, meeting_day), ENTAILMENT_SCHEMA)
    by_index = {v.index: v for v in out.verdicts}
    results = []
    for i in range(len(items)):
        v = by_index.get(i)
        if v is None:
            results.append(GateResult(False, "entailment_missing", "no verdict returned"))
        elif not v.entailed:
            results.append(GateResult(False, "not_entailed", v.reason))
        else:
            results.append(GateResult(True))
    return results


# -- gate evaluation: inject known-bad facts ----------------------------------------------------


@dataclass
class GateEval:
    good: int = 0
    bad: int = 0
    true_rejects: int = 0  # bad facts rejected
    false_rejects: int = 0  # good facts rejected
    by_corruption: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def precision(self) -> float:
        rejected = self.true_rejects + self.false_rejects
        return self.true_rejects / rejected if rejected else 1.0

    @property
    def recall(self) -> float:
        return self.true_rejects / self.bad if self.bad else 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "good": self.good,
            "bad": self.bad,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "false_rejects": self.false_rejects,
            "by_corruption": self.by_corruption,
        }


def corrupt(
    fact: ExtractedFact, lines: dict[str, str], rng: random.Random
) -> list[tuple[str, ExtractedFact]]:
    """Known-bad variants of a (good) fact, each labelled with how it was corrupted."""
    out: list[tuple[str, ExtractedFact]] = []
    note = fact.source_lines[0].split(":")[0]
    out.append(("fake_line", fact.model_copy(update={"source_lines": [f"{note}:9999"]})))
    others = [x for x in lines if x not in fact.source_lines and len(lines[x].split()) >= 5]
    if others:
        wrong = rng.choice(others)
        out.append(("wrong_line", fact.model_copy(update={"source_lines": [wrong]})))
        out.append(
            (
                "invented_quote",
                fact.model_copy(update={"quote": "we agreed to ship it all by Friday"}),
            )
        )
    nums = re.findall(r"\d+", fact.value_norm)
    if nums:
        n, v = nums[-1], fact.value_norm
        at = v.rfind(n)
        bumped = f"{v[:at]}{int(n) + 7:0{len(n)}d}{v[at + len(n) :]}"
        out.append(("wrong_number", fact.model_copy(update={"value_norm": bumped})))
    out.append(("wrong_subject", fact.model_copy(update={"subject": "zz_unrelated_project"})))
    return out


def gate_eval(
    facts: Sequence[ExtractedFact],
    lines: dict[str, str],
    *,
    seed: int = 0,
    entail_fn: EntailFn | None = None,
    meeting_day: str = "",
) -> GateEval:
    """Run the gate over ``facts`` (assumed good: they passed it) plus corrupted copies.

    With ``entail_fn`` every item that passes the deterministic layers is also entailment-checked
    (the configured gate only checks low-confidence ones); without it, only layers 1-2 run.
    """
    rng = random.Random(seed)
    labelled: list[tuple[str, ExtractedFact]] = [("good", f) for f in facts]
    for f in facts:
        labelled += corrupt(f, lines, rng)
    results = [check_item(f, lines) for _, f in labelled]
    if entail_fn is not None:
        idx = [i for i, r in enumerate(results) if r.ok]
        verdicts = entail([labelled[i][1] for i in idx], lines, meeting_day, entail_fn)
        for i, v in zip(idx, verdicts, strict=True):
            results[i] = v
    ev = GateEval()
    tally: dict[str, Counter[str]] = {}
    for (label, _), r in zip(labelled, results, strict=True):
        c = tally.setdefault(label, Counter())
        c["n"] += 1
        c["rejected"] += int(not r.ok)
        if label == "good":
            ev.good += 1
            ev.false_rejects += int(not r.ok)
        else:
            ev.bad += 1
            ev.true_rejects += int(not r.ok)
    ev.by_corruption = {k: dict(v) for k, v in tally.items()}
    return ev
