"""The $0 metrics: computed from the saved per-answer rows of a report, no LLM calls.

Definitions, rationale and weaknesses: ``docs/method/METRICS.md``. Every function here is
pure over the report rows (``<stamp>.jsonl``) and a small gold table, so the same code scores
the synthetic benchmark (gold from ``questions.jsonl``) and the real slice (gold from the
owner-approved real questions), and can be run on aggregate-only outputs.

* **Grounding (sources):** source recall (was a gold meeting among the meetings the system
  pulled?) and sources pulled per answer. Read from each row's ``retrieved`` field; what that
  field means differs by system (:data:`RETRIEVED_MEANS`), and it is stated next to the number.
* **Lists:** completeness on multi-item questions (fraction of gold items present).
* **Knowing what it doesn't know:** abstention precision/recall, and "accuracy when it answered"
  against coverage (the share of questions it answered at all).
* **Efficiency:** every answer re-priced from its tokens at **one** price list, so batch-priced
  and full-priced rows are never mixed; Batch API savings are reported separately.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

from concentrate.llm.client import BATCH_DISCOUNT, cost_usd
from concentrate.synth.questions import UNANSWERABLE, Question

DEFAULT_PRICE_MODEL = "claude-opus-5-5"

# What a row's ``retrieved`` list holds, per system. Source recall means different things for
# different systems, so the meaning is printed next to every number.
RETRIEVED_MEANS = {
    "b0_topk": "meetings whose transcripts were put in context (top-k)",
    "b1_summaries": "meetings whose summaries were put in context (top-k)",
    "b2_full": "every meeting (the whole corpus is in context)",
    "b2o_oracle": "the gold meetings, by construction (a ceiling, not a measurement)",
    "b3_agentic": "meetings opened with a get-meeting/transcript tool call (search hits excluded)",
    "b4_mem0": "source meetings of the retrieved memories",
    "b5_graphiti": "source meetings of the retrieved facts/episodes",
    "concentrate": "source meetings of the facts and passages put in context",
    "granola_agent": "meetings it cited; what it read is not observable from outside",
}
# Systems whose answers carry no line-level citations (they cite meetings, or nothing).
NO_LINE_CITATIONS = frozenset({"b1_summaries", "b4_mem0", "b5_graphiti", "granola_agent"})
# Systems with no abstain flag: abstention is judged from prose, so its metrics are n/a.
NO_ABSTAIN_FLAG = frozenset({"granola_agent"})
# Systems answered by a product we cannot see the tokens of.
EXTERNAL = frozenset({"granola_agent"})


@dataclass(frozen=True)
class Gold:
    """What the $0 metrics need to know about one question's answer."""

    meetings: frozenset[str]  # meetings holding the evidence (empty: unknown or unanswerable)
    items: int  # number of gold items; > 1 makes it a list question
    unanswerable: bool


def gold_from_questions(questions: Mapping[str, Question]) -> dict[str, Gold]:
    """Synthetic gold: evidence meetings are the note IDs of the gold utterance IDs."""
    return {
        qid: Gold(
            meetings=frozenset(u.split(":", 1)[0] for u in q.gold_utterance_ids),
            items=len(q.answer_norm),
            unanswerable=q.answer_norm == [UNANSWERABLE],
        )
        for qid, q in questions.items()
    }


def gold_from_real(questions: Iterable[Any]) -> dict[str, Gold]:
    """Real-slice gold (``RealQuestion``): owner-approved evidence meetings; items unknown."""
    return {q.id: Gold(frozenset(q.evidence_note_ids), 1, bool(q.unanswerable)) for q in questions}


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _by_system(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out[r["system"]].append(r)
    return dict(sorted(out.items()))


# -- correctness: partial credit --------------------------------------------------------------


@dataclass
class CreditStats:
    system: str
    n: int
    accuracy: float  # verdict "correct" only (the headline metric)
    partial: float  # share judged "partially_correct" (counted wrong in accuracy)
    partial_credit: float  # correct = 1, partially correct = 0.5, incorrect = 0
    exact_match: float  # mean share of gold values found by the string matcher


def credit_stats(rows: Iterable[dict[str, Any]]) -> list[CreditStats]:
    """Accuracy with half credit for partially correct answers, beside the strict number."""
    out = []
    for system, rs in _by_system(rows).items():
        n = len(rs)
        part = sum(r.get("verdict") == "partially_correct" for r in rs)
        corr = sum(bool(r["correct"]) for r in rs)
        out.append(
            CreditStats(
                system=system,
                n=n,
                accuracy=corr / n,
                partial=part / n,
                partial_credit=(corr + 0.5 * part) / n,
                exact_match=statistics.fmean(float(r.get("exact_match") or 0.0) for r in rs),
            )
        )
    return out


# -- grounding: sources ----------------------------------------------------------------------


@dataclass
class SourceStats:
    system: str
    means: str  # what "retrieved" holds for this system
    exposed: bool  # did the rows log any retrieved meetings at all?
    n: int  # answerable questions with known gold meetings
    recall_any: float | None  # share where >= 1 gold meeting was pulled
    recall_all: float | None  # share where every gold meeting was pulled
    pulled_mean: float | None  # distinct meetings pulled per answer (all questions)
    pulled_median: float | None
    pulled_max: int | None
    line_citations: bool  # does it cite transcript lines (vs meetings / nothing)?


def source_stats(rows: Iterable[dict[str, Any]], gold: Mapping[str, Gold]) -> list[SourceStats]:
    """Source recall and sources pulled, per system."""
    out = []
    for system, rs in _by_system(rows).items():
        pulled = [set(r["retrieved"]) for r in rs if isinstance(r.get("retrieved"), list)]
        exposed = any(pulled)
        any_hit = all_hit = n = 0
        for r in rs:
            g = gold.get(r["qid"])
            got = r.get("retrieved")
            if g is None or g.unanswerable or not g.meetings or not isinstance(got, list):
                continue
            n += 1
            hit = g.meetings & set(got)
            any_hit += bool(hit)
            all_hit += hit == g.meetings
        sizes = [len(p) for p in pulled]
        out.append(
            SourceStats(
                system=system,
                means=RETRIEVED_MEANS.get(system, "not documented for this system"),
                exposed=exposed,
                n=n if exposed else 0,
                recall_any=_rate(any_hit, n) if exposed else None,
                recall_all=_rate(all_hit, n) if exposed else None,
                pulled_mean=statistics.fmean(sizes) if exposed else None,
                pulled_median=float(statistics.median(sizes)) if exposed else None,
                pulled_max=max(sizes) if exposed else None,
                line_citations=system not in NO_LINE_CITATIONS,
            )
        )
    return out


# -- lists -----------------------------------------------------------------------------------


@dataclass
class ListStats:
    system: str
    n: int  # answerable questions with more than one gold item
    completeness: float | None  # mean fraction of gold items present (string matcher)
    complete: float | None  # share with every gold item present
    judged_correct: float | None  # share the judge marked correct


def list_stats(rows: Iterable[dict[str, Any]], gold: Mapping[str, Gold]) -> list[ListStats]:
    """Completeness on list/aggregation questions: ``exact_match`` is the fraction of gold
    items found in the answer (``scoring.exact_match``), so it is the per-question score."""
    out = []
    for system, rs in _by_system(rows).items():
        xs = [
            r
            for r in rs
            if (g := gold.get(r["qid"])) is not None and g.items > 1 and not g.unanswerable
        ]
        fr = [float(r.get("exact_match") or 0.0) for r in xs]
        out.append(
            ListStats(
                system=system,
                n=len(xs),
                completeness=statistics.fmean(fr) if fr else None,
                complete=_rate(sum(f >= 1.0 for f in fr), len(fr)),
                judged_correct=_rate(sum(bool(r["correct"]) for r in xs), len(xs)),
            )
        )
    return out


# -- knowing what it doesn't know --------------------------------------------------------------


@dataclass
class AbstainStats:
    system: str
    flag: bool  # does the system report abstention explicitly?
    n: int
    unanswerable: int
    abstained: int
    precision: float | None  # of its abstentions, the share on truly unanswerable questions
    recall: float | None  # of unanswerable questions, the share it abstained on
    false_abstain: float | None  # of answerable questions, the share it declined
    coverage: float | None  # share of all questions it answered (did not abstain on)
    accuracy: float  # judged correct over all questions (an abstention is correct only on
    # an unanswerable question)
    accuracy_answered: float | None  # judged correct among the questions it answered
    # Answered (did not abstain) and judged *incorrect*, over all questions: the "wrong, but
    # confident" output. Partially correct answers are not counted. Answering an unanswerable
    # question counts. Without an abstain flag every judged-incorrect answer counts, so it is
    # an upper bound there (``flag`` False).
    confident_error: float
    abstained_answer_existed: int  # declined an answerable question (count; rate: false_abstain)


def confident_error(rows: Iterable[dict[str, Any]]) -> float | None:
    """Share of rows answered (not abstained) and judged incorrect; None for no rows."""
    rs = list(rows)
    wrong = sum(not r.get("abstained") and r.get("verdict") == "incorrect" for r in rs)
    return _rate(wrong, len(rs))


def abstain_stats(rows: Iterable[dict[str, Any]]) -> list[AbstainStats]:
    """Abstention precision/recall, and accuracy-when-answered against coverage."""
    out = []
    for system, rs in _by_system(rows).items():
        flag = system not in NO_ABSTAIN_FLAG
        n = len(rs)
        un = [r for r in rs if r.get("unanswerable")]
        ab = [r for r in rs if r.get("abstained")]
        tp = sum(bool(r.get("unanswerable")) for r in ab)
        answered = [r for r in rs if not r.get("abstained")]
        out.append(
            AbstainStats(
                system=system,
                flag=flag,
                n=n,
                unanswerable=len(un),
                abstained=len(ab),
                precision=_rate(tp, len(ab)) if flag else None,
                recall=_rate(tp, len(un)) if flag else None,
                false_abstain=_rate(len(ab) - tp, n - len(un)) if flag else None,
                coverage=_rate(len(answered), n) if flag else None,
                accuracy=sum(bool(r["correct"]) for r in rs) / n,
                accuracy_answered=(
                    _rate(sum(bool(r["correct"]) for r in answered), len(answered))
                    if flag
                    else None
                ),
                confident_error=confident_error(rs) or 0.0,
                abstained_answer_existed=len(ab) - tp,
            )
        )
    return out


# -- efficiency: equal-price costing -----------------------------------------------------------


@dataclass
class CostStats:
    system: str
    n: int
    price_model: str
    observable: bool  # False when tokens are not visible (an external product)
    input_tokens: float  # mean per answer
    output_tokens: float
    usd_interactive: float  # mean $/answer, every answer at the full interactive price
    usd_batch: float  # the same if every answer had gone through the Batch API
    batch_saving: float  # usd_interactive - usd_batch
    usd_recorded: float  # mean recorded cost_usd (mixed price bases: do not compare)
    batched_share: float  # share of rows that were actually batch-priced


def reprice(row: dict[str, Any], model: str = DEFAULT_PRICE_MODEL) -> float:
    """One answer at the full interactive price of ``model``, from its token counts.

    Rows record total prompt tokens without a cache split, so caching is not credited: this is
    the price of an answer a user waits for, with no prompt cache. For the synthetic report this
    equals the recorded cost of every non-batched row (checked in tests)."""
    return cost_usd(model, int(row.get("input_tokens") or 0), int(row.get("output_tokens") or 0))


def cost_stats(rows: Iterable[dict[str, Any]], model: str = DEFAULT_PRICE_MODEL) -> list[CostStats]:
    """$/answer for every system at one price list, with batch savings kept apart."""
    out = []
    for system, rs in _by_system(rows).items():
        n = len(rs)
        full = statistics.fmean(reprice(r, model) for r in rs)
        out.append(
            CostStats(
                system=system,
                n=n,
                price_model=model,
                observable=system not in EXTERNAL and any(r.get("input_tokens") for r in rs),
                input_tokens=statistics.fmean(float(r.get("input_tokens") or 0) for r in rs),
                output_tokens=statistics.fmean(float(r.get("output_tokens") or 0) for r in rs),
                usd_interactive=full,
                usd_batch=full * BATCH_DISCOUNT,
                batch_saving=full * (1 - BATCH_DISCOUNT),
                usd_recorded=statistics.fmean(float(r.get("cost_usd") or 0.0) for r in rs),
                batched_share=sum(bool(r.get("batched")) for r in rs) / n,
            )
        )
    return out


def ingest_usd(report: dict[str, Any], system: str) -> float:
    """One-off ingest cost of a write-time system, as recorded when it was built.

    A report that reused a cached build records ``cost_usd = 0`` for this run and keeps the
    original build's cost under ``detail.built``; using the top-level number would make a
    cached build look free (the bug this replaces)."""
    ing = (report["meta"].get("ingest") or {}).get(system) or {}
    built = (ing.get("detail") or {}).get("built") or {}
    if ing.get("cached") and built:
        return float(built.get("cost_usd") or 0.0)
    return float(ing.get("cost_usd") or 0.0)


# -- everything --------------------------------------------------------------------------------


def all_metrics(
    report: dict[str, Any],
    rows: list[dict[str, Any]],
    gold: Mapping[str, Gold] | None,
    *,
    price_model: str = DEFAULT_PRICE_MODEL,
) -> dict[str, Any]:
    """Every $0 metric, JSON-serialisable. ``gold=None``: the gold-dependent ones are skipped."""
    costs = cost_stats(rows, price_model)
    systems = [c.system for c in costs]
    ingest = {s: ingest_usd(report, s) for s in systems}
    out: dict[str, Any] = {
        "price_model": price_model,
        "batch_discount": BATCH_DISCOUNT,
        "credit": [asdict(c) for c in credit_stats(rows)],
        "cost": [asdict(c) for c in costs],
        "ingest_usd": ingest,
        "abstention": [asdict(a) for a in abstain_stats(rows)],
    }
    if gold is not None:
        out["sources"] = [asdict(s) for s in source_stats(rows, gold)]
        out["lists"] = [asdict(x) for x in list_stats(rows, gold)]
    return out


# -- markdown ----------------------------------------------------------------------------------


def _pct(x: float | None) -> str:
    return "n/a" if x is None or x != x else f"{100 * x:.0f}%"


def _num(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None or x != x else f"{x:.{digits}f}"


def _usd(x: float) -> str:
    return f"${x:.4f}" if x < 0.01 else f"${x:.3f}"


def to_markdown(m: dict[str, Any], *, section: int = 5) -> list[str]:
    """The $0 metrics as markdown sections (appended to the analysis report)."""
    lines: list[str] = [
        "",
        f"## {section}. Correctness beyond right/wrong",
        "",
        "**Accuracy** counts only answers judged *correct*. **Partial credit** gives half a "
        "point to *partially correct* answers (usually an incomplete list). **Exact match** is "
        "the deterministic cross-check: mean share of gold values the string matcher finds in "
        "the answer (no LLM).",
        "",
        "| System | n | Accuracy | Partially correct | Partial credit | Exact match |",
        "|---|---|---|---|---|---|",
    ]
    for c in m["credit"]:
        lines.append(
            f"| {c['system']} | {c['n']} | {_pct(c['accuracy'])} | {_pct(c['partial'])} | "
            f"{_pct(c['partial_credit'])} | {_pct(c['exact_match'])} |"
        )
    section += 1
    if "sources" in m:
        lines += [
            "",
            f"## {section}. Grounding: did it pull the right meeting?",
            "",
            "**Source recall** = share of answerable questions where at least one gold meeting "
            "was among the meetings the system pulled (*all* = every gold meeting). **Pulled** "
            '= distinct meetings per answer. What "pulled" means differs by system (last '
            "column), so compare with care. Definitions: `docs/method/METRICS.md`.",
            "",
            "| System | n | Source recall (any) | (all) | Pulled: mean | median | max | "
            "Line citations | What `retrieved` holds |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for s in m["sources"]:
            if not s["exposed"]:
                lines.append(
                    f"| {s['system']} | - | not exposed | | | | | "
                    f"{'yes' if s['line_citations'] else 'no'} | {s['means']} |"
                )
                continue
            lines.append(
                f"| {s['system']} | {s['n']} | {_pct(s['recall_any'])} | {_pct(s['recall_all'])}"
                f" | {_num(s['pulled_mean'])} | {_num(s['pulled_median'], 0)} | "
                f"{s['pulled_max']} | {'yes' if s['line_citations'] else 'no (meetings only)'}"
                f" | {s['means']} |"
            )
        lists = [x for x in m["lists"] if x["n"]]
        if lists:
            lines += [
                "",
                "### List completeness",
                "",
                "On answerable questions with more than one gold item: **completeness** = mean "
                "share of gold items found in the answer (deterministic string matcher, "
                "`scoring.exact_match`); **complete** = share with every item; **judge** = "
                "share the LLM judge marked correct.",
                "",
                "| System | n | Completeness | Complete | Judge correct |",
                "|---|---|---|---|---|",
            ]
            for x in lists:
                lines.append(
                    f"| {x['system']} | {x['n']} | {_pct(x['completeness'])} | "
                    f"{_pct(x['complete'])} | {_pct(x['judged_correct'])} |"
                )
    lines += [
        "",
        f"## {section + 1}. Knowing what it doesn't know",
        "",
        "**Abstention precision** = of its abstentions, the share on truly unanswerable "
        "questions; **recall** = of the unanswerable questions, the share it declined; "
        "**false abstain** = share of answerable questions it declined. **Coverage** = share of "
        "all questions it answered; **accuracy when answered** is judged accuracy on those. A "
        "system that declines more can look better on the last column while helping less: read "
        "it with coverage. **Confident error** = share of all questions it answered *and* got "
        'judged incorrect (partials excluded; answering an unanswerable counts): the "wrong, '
        'but confident" output. n/a = the system has no abstain flag (≤: an upper bound).',
        "",
        "| System | n | Unanswerable | Abstained | Precision | Recall | False abstain | "
        "Coverage | Accuracy (all) | Accuracy when answered | Confident error |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in m["abstention"]:
        lines.append(
            f"| {a['system']} | {a['n']} | {a['unanswerable']} | "
            f"{a['abstained'] if a['flag'] else 'n/a'} | {_pct(a['precision'])} | "
            f"{_pct(a['recall'])} | {_pct(a['false_abstain'])} | {_pct(a['coverage'])} | "
            f"{_pct(a['accuracy'])} | {_pct(a['accuracy_answered'])} | "
            f"{'' if a['flag'] else '≤ '}{_pct(a['confident_error'])} |"
        )
    pm = m["price_model"]
    lines += [
        "",
        f"## {section + 2}. Cost at equal prices",
        "",
        f"Every answer re-priced from its tokens at the **interactive `{pm}` price** (no Batch "
        "API, no prompt-cache credit: an answer a user waits for). **Batch** = the same "
        f"tokens through the Batch API ({100 * (1 - m['batch_discount']):.0f}% off), shown "
        "only as a saving, never mixed into comparisons. **Recorded** is what the run actually "
        "paid, on mixed price bases (share batched in the last column): don't compare systems "
        "on it. Judge cost excluded.",
        "",
        "| System | n | In tok/q | Out tok/q | $/q interactive | $/q batch | Batch saving | "
        "Recorded $/q | Batched rows |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in m["cost"]:
        if not c["observable"]:
            lines.append(
                f"| {c['system']} | {c['n']} | not observable (external product) | | | | | | |"
            )
            continue
        lines.append(
            f"| {c['system']} | {c['n']} | {c['input_tokens']:,.0f} | "
            f"{c['output_tokens']:,.0f} | **{_usd(c['usd_interactive'])}** | "
            f"{_usd(c['usd_batch'])} | {_usd(c['batch_saving'])} | {_usd(c['usd_recorded'])} | "
            f"{_pct(c['batched_share'])} |"
        )
    return lines
