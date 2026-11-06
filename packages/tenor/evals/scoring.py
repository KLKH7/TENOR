"""Scoring: exact-match signals, LLM judge, and aggregate metrics with bootstrap CIs.

The **LLM judge** is the primary correctness signal (answers are free text). Two deterministic
signals are reported alongside so judge bias is visible: an exact-match check of the gold
``answer_norm`` values, and citation precision/recall against the gold utterance IDs.
"""

from __future__ import annotations

import random
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from tenor.core.spoken import spoken_numbers
from tenor.llm.client import LLMClient, LLMRequest, load_prompt
from tenor.synth.questions import UNANSWERABLE, Question
from tenor.synth.roster import PEOPLE

JUDGE_PROMPT = "judge.v1"
Verdict = Literal["correct", "partially_correct", "incorrect"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december"]  # fmt: skip


def _norm_present(norm: str, answer: str) -> bool:
    """Is one normalized gold value expressed in a free-text answer?"""
    text = answer.lower()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", norm):
        d = date.fromisoformat(norm)
        month = _MONTHS[d.month - 1]
        return norm in text or bool(
            re.search(rf"\b{month[:3]}\w*\.?\s+{d.day}(st|nd|rd|th)?\b", text)
        )
    if re.fullmatch(r"\d+", norm):
        value = int(norm)
        found = set(spoken_numbers(text.replace("$", " ")))
        return value in found or (value % 1000 == 0 and value // 1000 in found and "k" in text)
    if norm in PEOPLE:
        p = PEOPLE[norm]
        return p.name.lower() in text or p.name.split()[-1].lower() in text
    return norm.replace("_", " ").lower() in text


def exact_match(q: Question, answer: str, abstained: bool) -> float:
    """Fraction of gold normalized values found in the answer (UNANSWERABLE: abstained?)."""
    if q.answer_norm == [UNANSWERABLE]:
        return 1.0 if abstained else 0.0
    if abstained:
        return 0.0
    return sum(_norm_present(n, answer) for n in q.answer_norm) / len(q.answer_norm)


def citation_scores(q: Question, citations: list[str]) -> tuple[float | None, float | None]:
    """(precision, recall) of cited utterance IDs vs gold. None when undefined."""
    lines = {c.strip("[]") for c in citations if ":" in c}
    gold = set(q.gold_utterance_ids)
    if not gold:
        return None, None
    precision = len(lines & gold) / len(lines) if lines else None
    return precision, len(lines & gold) / len(gold)


class JudgeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Verdict
    reason: str


JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["correct", "partially_correct", "incorrect"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
    "additionalProperties": False,
}


def judge_request(q: Question, answer: str, abstained: bool) -> LLMRequest:
    """The judge call for one answer, unsent (``judge`` sends it; batch mode prefetches it)."""
    return judge_request_for(
        q.question, q.answer, q.answer_norm == [UNANSWERABLE], answer, abstained
    )


def judge_request_for(
    question: str, gold_answer: str, unanswerable: bool, answer: str, abstained: bool
) -> LLMRequest:
    """``judge_request`` from plain fields, so a saved report can be re-judged verbatim."""
    gold = "UNANSWERABLE" if unanswerable else gold_answer
    system_answer = f"(abstained) {answer}" if abstained else answer
    return LLMRequest(
        stage="eval.judge",
        prompt_id=JUDGE_PROMPT,
        system=load_prompt(JUDGE_PROMPT),
        user=f"Question: {question}\n\nGold answer: {gold}\n\nSystem answer: {system_answer}",
        schema=JUDGE_SCHEMA,
        max_tokens=2000,
        effort="low",
    )


def judge(client: LLMClient, q: Question, answer: str, abstained: bool) -> JudgeOutput:
    return client.structured(judge_request(q, answer, abstained), JudgeOutput)


# -- aggregation --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Row:
    """One (system, question) outcome."""

    system: str
    qid: str
    category: str
    correct: bool
    verdict: str
    exact_match: float
    abstained: bool
    unanswerable: bool
    cite_precision: float | None
    cite_recall: float | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_s: float
    tool_calls: int
    # False when latency_s is not a real measurement (answer served by a batch result, or a
    # batched system outside the interactive latency sample): excluded from p50/p95.
    latency_measured: bool = True


def bootstrap_ci(values: list[float], seed: int, n: int = 2000) -> tuple[float, float]:
    """95% percentile bootstrap CI of the mean."""
    if not values:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(values, k=len(values))) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def _pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p * (len(xs) - 1)))] if xs else float("nan")


def _mean(xs: list[float]) -> float | None:
    return statistics.fmean(xs) if xs else None


def summarize(rows: list[Row], seed: int) -> dict[str, Any]:
    """Per-system metrics, overall and per category."""
    out: dict[str, Any] = {}
    by_system: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_system[r.system].append(r)
    for system, rs in by_system.items():
        acc = [float(r.correct) for r in rs]
        abstained = [r for r in rs if r.abstained]
        unanswerable = [r for r in rs if r.unanswerable]
        cats: dict[str, list[float]] = defaultdict(list)
        for r in rs:
            cats[r.category].append(float(r.correct))
        precs = [r.cite_precision for r in rs if r.cite_precision is not None]
        recs = [r.cite_recall for r in rs if r.cite_recall is not None]
        lat = [r.latency_s for r in rs if r.latency_measured]
        out[system] = {
            "n": len(rs),
            "accuracy": statistics.fmean(acc),
            "accuracy_ci95": bootstrap_ci(acc, seed),
            "exact_match": statistics.fmean(r.exact_match for r in rs),
            "abstain_precision": (
                sum(r.unanswerable for r in abstained) / len(abstained) if abstained else None
            ),
            "abstain_recall": (
                sum(r.abstained for r in unanswerable) / len(unanswerable) if unanswerable else None
            ),
            "cite_precision": _mean(precs),
            "cite_recall": _mean(recs),
            "input_tokens_per_q": statistics.fmean(r.input_tokens for r in rs),
            "output_tokens_per_q": statistics.fmean(r.output_tokens for r in rs),
            "cost_per_q": statistics.fmean(r.cost_usd for r in rs),
            "cost_total": sum(r.cost_usd for r in rs),
            "latency_p50": _pct(lat, 0.5),
            "latency_p95": _pct(lat, 0.95),
            "latency_n": len(lat),
            "tool_calls_per_q": statistics.fmean(r.tool_calls for r in rs),
            "by_category": {
                c: {"n": len(v), "accuracy": statistics.fmean(v)} for c, v in sorted(cats.items())
            },
        }
    return out
