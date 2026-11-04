"""Unit economics: what each memory approach would cost per user per month, from measured data.

Inputs are *measured*, not guessed:
  - ingest (write-time systems): the per-chunk/per-meeting ``progress.jsonl`` records every build
    writes under ``data/cache/{mem0,graphiti,concentrate}/<corpus>/<config>/``;
  - answering (every system): the per-question rows of an eval report (``evals/reports/*.jsonl``).

Token counts are re-priced with :data:`concentrate.llm.client.PRICING`, so the same measurements
answer "what if ingest ran on Haiku?" or "what if answers came from Sonnet?". Everything the
model cannot measure is a named parameter of :class:`Scenario` (usage volumes, cache hit rate,
batch discount), and every guess is labelled as such in :data:`ASSUMPTIONS`.

The core relationship is amortisation. A write-time system pays ``I`` per meeting once and a
small ``a_w`` per question; a query-time system pays nothing up front and ``a_r`` per question.
Per user per month with ``M`` meetings and ``Q`` questions::

    write  = M * I + Q * a_w        query = Q * a_r
    break-even Q* = M * I / (a_r - a_w)     i.e. Q*/M = I / (a_r - a_w) questions per meeting

The ratio ``Q*/M`` does not depend on how many meetings a user has: it is the number of questions
each meeting must eventually be asked about before studying it up front pays for itself.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from concentrate.llm.client import BATCH_DISCOUNT, CACHE_READ_PER_MTOK, PRICING

INGEST_SYSTEMS = ("mem0", "graphiti", "concentrate")
REPORT_NAMES = {  # eval report system name -> economics name
    "b0_topk": "b0 top-k",
    "b1_summaries": "b1 summaries",
    "b2_full": "b2 full context",
    "b2o_oracle": "b2o oracle",
    "b3_agentic": "b3 agent",
    "mem0": "mem0",
    "b4_mem0": "mem0",
    "graphiti": "graphiti",
    "b5_graphiti": "graphiti",
    "concentrate": "concentrate",
}
# Per-question answer tokens for write-time systems an eval report doesn't cover yet. ASSUMED:
# these are the planning estimates each system's own ``estimate()`` uses (input incl. ~450
# instruction tokens, output); they are replaced by measurements as soon as a report has them.
PLANNING_ANSWER_TOKENS = {
    "concentrate": (4_950, 400),  # concentrate_system: 4,500-token context + instructions
    "mem0": (1_750, 900),  # mem0_system: k=25 memories x ~40 tokens + 300 + instructions
    "graphiti": (3_500, 900),  # graphiti_system: 30 edges x 60 + 10 nodes x 120 + instructions
}

ASSUMPTIONS = [
    (
        "measured",
        "ingest tokens per meeting: mean over completed meetings in each build's "
        "progress.jsonl (Northwind-XL, synthetic; real meetings may be longer, see FIDELITY.md)",
    ),
    ("measured", "per-question answer tokens, latency and accuracy: the eval report given"),
    (
        "measured",
        "ingest model of each build: inferred by re-pricing its tokens with every model "
        "in PRICING and keeping the closest match to the recorded cost",
    ),
    (
        "assumed",
        "write-time systems with no report rows use their planning estimate "
        "(PLANNING_ANSWER_TOKENS) for the answer call",
    ),
    (
        "assumed",
        "query-time systems need no LLM at ingest; embedding/indexing cost is ignored "
        "for every system (local embedder, ~0 marginal cost)",
    ),
    (
        "assumed",
        "ingest can always use the Batch API (it is asynchronous); interactive answers "
        "cannot, unless --batch-queries",
    ),
    (
        "assumed",
        "prompt-cache override applies one hit rate to all prompt tokens; cache writes "
        "are then ignored (writes cost 1.25x but happen once per prefix)",
    ),
    ("guess", "meetings and questions per user per month (CLI defaults 40 and 30): set them"),
    (
        "ignored",
        "storage, vector DB hosting, re-ingest on edits, retries, failed calls, "
        "the judge (an eval artefact, not a product cost)",
    ),
]


@dataclass(frozen=True)
class Usage:
    """LLM token usage (sums or means) with the cost as recorded at the time."""

    calls: float = 0.0
    input_tokens: float = 0.0
    output_tokens: float = 0.0
    cache_read_tokens: float = 0.0
    cache_write_tokens: float = 0.0
    recorded_usd: float = 0.0
    seconds: float = 0.0

    def __add__(self, o: Usage) -> Usage:
        return Usage(*(getattr(self, f) + getattr(o, f) for f in self.__dataclass_fields__))

    def scaled(self, k: float) -> Usage:
        return Usage(*(getattr(self, f) * k for f in self.__dataclass_fields__))

    @property
    def prompt_tokens(self) -> float:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


def price(
    u: Usage,
    model: str,
    *,
    cache_hit: float | None = None,
    batch: bool = False,
    batch_multiplier: float = BATCH_DISCOUNT,
) -> float:
    """Re-price ``u`` on ``model``. ``cache_hit=None`` keeps the measured cache split; a number
    serves that fraction of all prompt tokens from cache instead."""
    p_in, p_out = PRICING[model]
    p_read = CACHE_READ_PER_MTOK.get(model, p_in * 0.1)
    if cache_hit is None:
        prompt = (
            u.input_tokens * p_in
            + u.cache_read_tokens * p_read
            + u.cache_write_tokens * (p_in * 1.25)
        )
    else:
        prompt = u.prompt_tokens * ((1 - cache_hit) * p_in + cache_hit * p_read)
    usd = (prompt + u.output_tokens * p_out) / 1_000_000
    return usd * batch_multiplier if batch else usd


def infer_model(u: Usage) -> tuple[str, float]:
    """The PRICING model whose price for ``u`` is closest to the recorded cost (rel. error)."""
    best = min(PRICING, key=lambda m: abs(price(u, m) - u.recorded_usd))
    return best, abs(price(u, best) - u.recorded_usd) / max(u.recorded_usd, 1e-9)


# -- ingest (write-time) --


@dataclass
class IngestProfile:
    system: str
    build: str  # store directory, relative to the cache root
    model: str  # inferred ingest model (dominant: Concentrate's Haiku entailment is ~1% of cost)
    model_error: float
    meetings: int  # completed meetings measured
    complete: bool  # the build covered the whole corpus
    total: Usage
    per_meeting: Usage
    updated: float = 0.0  # progress-file mtime, to prefer the newest of equal builds


# Builds whose progress rows count ``input_tokens`` as the whole prompt (cache reads and writes
# included); Graphiti's rows count uncached input only. VERIFIED by re-pricing rows to the cent.
INPUT_INCLUDES_CACHE = {"mem0", "concentrate"}


def _usage(system: str, row: dict[str, Any]) -> Usage:
    cached = float(row.get("cache_read_tokens", 0)) + float(row.get("cache_write_tokens", 0))
    total_in = float(row.get("input_tokens", 0))
    return Usage(
        calls=float(row.get("llm_calls", 0)),
        input_tokens=total_in - cached if system in INPUT_INCLUDES_CACHE else total_in,
        output_tokens=float(row.get("output_tokens", 0)),
        cache_read_tokens=float(row.get("cache_read_tokens", 0)),
        cache_write_tokens=float(row.get("cache_write_tokens", 0)),
        recorded_usd=float(row.get("cost_usd", 0.0)),
        seconds=float(row.get("seconds", 0.0)),
    )


def _meeting_id(system: str, row: dict[str, Any]) -> str:
    if system == "concentrate":
        return str(row["note_id"])
    if system == "graphiti":
        return str(row["ref"]["note_id"])
    return str(row["key"]).split(":", 1)[0]  # mem0: "<note>:summary" / "<note>:t000"


def ingest_profile(system: str, progress: Path, cache_root: Path) -> IngestProfile | None:
    """Per-meeting usage of one build. Builds ingest meetings in order, so in an unfinished build
    the last meeting seen may be partial and is left out."""
    by_meeting: dict[str, Usage] = {}
    for line in progress.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        mid = _meeting_id(system, row)
        by_meeting[mid] = by_meeting.get(mid, Usage()) + _usage(system, row)
    complete = (progress.parent / "complete.json").exists()
    if not complete and by_meeting:
        by_meeting.pop(list(by_meeting)[-1])
    if not by_meeting:
        return None
    total = sum(by_meeting.values(), Usage())
    model, err = infer_model(total)
    return IngestProfile(
        system=system,
        build=str(progress.parent.relative_to(cache_root)),
        model=model,
        model_error=err,
        meetings=len(by_meeting),
        complete=complete,
        total=total,
        per_meeting=total.scaled(1 / len(by_meeting)),
        updated=progress.stat().st_mtime,
    )


def load_ingest(cache_root: Path) -> list[IngestProfile]:
    """Every build with a progress record under ``cache_root``."""
    out = []
    for system in INGEST_SYSTEMS:
        for progress in sorted((cache_root / system).glob("**/progress.jsonl")):
            p = ingest_profile(system, progress, cache_root)
            if p is not None:
                out.append(p)
    return out


def pick_builds(profiles: Iterable[IngestProfile]) -> dict[str, IngestProfile]:
    """One build per system: prefer complete builds, then most meetings, then most recent."""
    best: dict[str, IngestProfile] = {}
    for p in profiles:
        cur = best.get(p.system)
        if cur is None or (p.complete, p.meetings, p.updated) > (
            cur.complete,
            cur.meetings,
            cur.updated,
        ):
            best[p.system] = p
    return best


# -- answering (every system) --


@dataclass
class AnswerProfile:
    system: str
    n: int
    accuracy: float | None
    per_question: Usage
    latency_p50: float | None
    tool_calls: float
    source: str  # report path, or "planning estimate"


def split_cache(u: Usage, model: str) -> Usage:
    """Report rows give total prompt tokens but not the cache split. Recover the cache-read
    share ``h`` from the recorded cost (B2's corpus prefix is cached, others are not):
    ``usd = in * ((1 - h) * p_in + h * p_read) + out * p_out``. Batch-priced rows (cheaper than
    any cache-free price) are left uncached: the discount is not a cache effect."""
    p_in, p_out = PRICING[model]
    p_read = CACHE_READ_PER_MTOK.get(model, p_in * 0.1)
    if not u.input_tokens or p_in == p_read:
        return u
    prompt_usd = u.recorded_usd * 1_000_000 - u.output_tokens * p_out
    h = (u.input_tokens * p_in - prompt_usd) / (u.input_tokens * (p_in - p_read))
    if not 0.05 < h <= 1.0:
        return u
    return replace(u, input_tokens=u.input_tokens * (1 - h), cache_read_tokens=u.input_tokens * h)


def unbatched_usd(row: dict[str, Any]) -> float:
    """A row's recorded cost at the interactive price: Batch-priced rows (``batched``) paid
    :data:`BATCH_DISCOUNT` of it. Averaging raw ``cost_usd`` mixes the two price bases, and
    :func:`split_cache` would then read the batch discount as prompt-cache hits."""
    usd = float(row.get("cost_usd") or 0.0)
    return usd / BATCH_DISCOUNT if row.get("batched") else usd


def load_answers(report_jsonl: Path) -> tuple[str, dict[str, AnswerProfile]]:
    """(answer model, per-system profile) from a report's per-question rows. Every row is
    brought to the interactive price basis first (:func:`unbatched_usd`); the Batch API is
    then a :class:`Scenario` choice applied to all systems alike."""
    meta = json.loads(report_jsonl.with_suffix(".json").read_text())["meta"]
    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in report_jsonl.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[REPORT_NAMES.get(r["system"], r["system"])].append(r)
    out = {}
    for name, rs in rows.items():
        lat = [r["latency_s"] for r in rs if r.get("latency_measured", True)]
        n = len(rs)
        out[name] = AnswerProfile(
            system=name,
            n=n,
            accuracy=sum(bool(r["correct"]) for r in rs) / n,
            per_question=split_cache(
                Usage(
                    calls=1 + sum(r.get("tool_calls", 0) for r in rs) / n,
                    input_tokens=sum(r["input_tokens"] for r in rs) / n,
                    output_tokens=sum(r["output_tokens"] for r in rs) / n,
                    recorded_usd=sum(unbatched_usd(r) for r in rs) / n,
                ),
                str(meta["model"]),
            ),
            latency_p50=statistics.median(lat) if lat else None,
            tool_calls=sum(r.get("tool_calls", 0) for r in rs) / n,
            source=str(report_jsonl),
        )
    return str(meta["model"]), out


def with_planning_estimates(answers: dict[str, AnswerProfile]) -> dict[str, AnswerProfile]:
    out = dict(answers)
    for name, (tin, tout) in PLANNING_ANSWER_TOKENS.items():
        if name not in out:
            out[name] = AnswerProfile(
                name, 0, None, Usage(1, tin, tout), None, 0.0, "planning estimate (ASSUMED)"
            )
    return out


# -- the model --


@dataclass(frozen=True)
class Scenario:
    """The sliders. Volumes are per user per month."""

    meetings: float = 40.0
    questions: float = 30.0
    ingest_model: str | None = None  # None: each build's own (measured) model
    answer_model: str | None = None  # None: the report's model
    batch_ingest: bool = True
    batch_queries: bool = False
    batch_discount: float = 1.0 - BATCH_DISCOUNT  # fraction off with the Batch API
    cache_hit: float | None = None  # None: as measured


@dataclass
class SystemCost:
    system: str
    kind: str  # "write-time" | "query-time"
    ingest_per_meeting: float
    answer_per_question: float
    accuracy: float | None
    latency_p50: float | None
    answer_source: str
    ingest_model: str | None
    answer_model: str
    n: int = 0
    answer_batch_saving: float = 0.0  # $/question saved if answers went through the Batch API
    monthly: float = 0.0
    ingest_share: float = 0.0
    breakeven: dict[str, float | None] = field(default_factory=dict)  # vs query-time systems


def compute(
    ingest: dict[str, IngestProfile],
    answers: dict[str, AnswerProfile],
    report_model: str,
    sc: Scenario,
) -> list[SystemCost]:
    """$/user/month for every system, and each write-time system's break-even question volume
    (per user per month at ``sc.meetings``) against each query-time system."""
    answer_model = sc.answer_model or report_model
    mult = 1.0 - sc.batch_discount  # Batch API price multiplier
    costs: list[SystemCost] = []
    for name, a in with_planning_estimates(answers).items():
        ing = ingest.get(name)
        model = (sc.ingest_model or ing.model) if ing else None
        per_meeting = (
            price(
                ing.per_meeting,
                model,
                cache_hit=sc.cache_hit,
                batch=sc.batch_ingest,
                batch_multiplier=mult,
            )
            if ing and model
            else 0.0
        )
        per_q = price(
            a.per_question,
            answer_model,
            cache_hit=sc.cache_hit,
            batch=sc.batch_queries,
            batch_multiplier=mult,
        )
        interactive_q = price(a.per_question, answer_model, cache_hit=sc.cache_hit)
        monthly = sc.meetings * per_meeting + sc.questions * per_q
        costs.append(
            SystemCost(
                system=name,
                kind="write-time" if name in INGEST_SYSTEMS else "query-time",
                ingest_per_meeting=per_meeting,
                answer_per_question=per_q,
                accuracy=a.accuracy,
                latency_p50=a.latency_p50,
                answer_source=a.source,
                ingest_model=model,
                answer_model=answer_model,
                n=a.n,
                answer_batch_saving=interactive_q * (1.0 - mult),
                monthly=monthly,
                ingest_share=(sc.meetings * per_meeting / monthly) if monthly else 0.0,
            )
        )
    for w in costs:
        if w.kind != "write-time":
            continue
        for r in costs:
            if r.kind == "query-time":
                w.breakeven[r.system] = breakeven_questions(
                    sc.meetings * w.ingest_per_meeting,
                    w.answer_per_question,
                    r.answer_per_question,
                )
    return sorted(costs, key=lambda c: (c.kind, c.monthly))


def breakeven_questions(ingest_monthly: float, a_write: float, a_read: float) -> float | None:
    """Questions per month above which write-time is cheaper; None if it never is."""
    saving = a_read - a_write
    if saving <= 0:
        return None
    return ingest_monthly / saving


def sensitivity(
    ingest: dict[str, IngestProfile],
    answers: dict[str, AnswerProfile],
    report_model: str,
    sc: Scenario,
    models: Iterable[str] = ("claude-sonnet-5-5", "claude-haiku-5-5"),
    answer_models: Iterable[str] = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"),
) -> list[dict[str, Any]]:
    """$/user/month for every (ingest model, answer model) pair."""
    out = []
    for im in models:
        for am in answer_models:
            res = compute(
                ingest, answers, report_model, replace(sc, ingest_model=im, answer_model=am)
            )
            out.append(
                {
                    "ingest_model": im,
                    "answer_model": am,
                    "monthly": {c.system: c.monthly for c in res},
                }
            )
    return out


def curve(
    costs: list[SystemCost], meetings: float, q_max: float, steps: int = 60
) -> dict[str, list[tuple[float, float]]]:
    """$/user/month as question volume grows (the break-even chart)."""
    qs = [q_max * i / steps for i in range(steps + 1)]
    return {
        c.system: [(q, meetings * c.ingest_per_meeting + q * c.answer_per_question) for q in qs]
        for c in costs
    }


# -- rendering --


def _usd(x: float | None, digits: int = 3) -> str:
    return "—" if x is None else f"${x:,.{digits}f}"


def _short(model: str | None) -> str:
    return "—" if model is None else model.removeprefix("claude-")


def to_markdown(
    costs: list[SystemCost],
    ingest: dict[str, IngestProfile],
    sens: list[dict[str, Any]],
    sc: Scenario,
    report: str,
) -> str:
    """The economics table, break-even matrix, sensitivity grid and assumptions."""
    lines = [
        "# Unit economics",
        "",
        f"Answers measured in `{report}`; ingest measured from build progress records.",
        f"Scenario: **{sc.meetings:g} meetings** and **{sc.questions:g} questions** per user per "
        f"month · ingest batch: {'yes' if sc.batch_ingest else 'no'} · answer batch: "
        f"{'yes' if sc.batch_queries else 'no'} · batch discount {sc.batch_discount:.0%} · "
        f"prompt-cache hit: {'as measured' if sc.cache_hit is None else f'{sc.cache_hit:.0%}'}",
        "",
        "Every answer is priced from its tokens on one basis (interactive unless "
        "`--batch-queries`); recorded costs from batch-priced report rows are converted back to "
        "the interactive price first. **Batch saving** is what the Batch API would save per "
        "question, reported separately and not included in $/question.",
        "",
        "| system | kind | ingest model | $/meeting | answer model | $/question | batch saving/q |"
        " $/user/month | ingest share | accuracy (n) | p50 latency | answer cost from |",
        "|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for c in costs:
        acc = "—" if c.accuracy is None else f"{c.accuracy:.0%} ({c.n})"
        lat = "—" if c.latency_p50 is None else f"{c.latency_p50:.1f}s"
        lines.append(
            f"| {c.system} | {c.kind} | {_short(c.ingest_model)} | {_usd(c.ingest_per_meeting, 4)}"
            f" | {_short(c.answer_model)} | {_usd(c.answer_per_question, 4)} | "
            f"{_usd(c.answer_batch_saving, 4) if not sc.batch_queries else 'applied'} | "
            f"**{_usd(c.monthly, 2)}** | {c.ingest_share:.0%} | {acc} | {lat} | "
            f"{'ASSUMED estimate' if c.answer_source.startswith('planning') else 'measured'} |"
        )
    reads = [c for c in costs if c.kind == "query-time"]
    lines += [
        "",
        "## Break-even: questions per user per month above which write-time is cheaper",
        "",
        f"At {sc.meetings:g} meetings/month. Divide by {sc.meetings:g} for *questions per "
        "meeting*, which does not depend on meeting volume. `never` = the write-time system's "
        "answers cost more than the query-time system's, so ingest is never paid back.",
        "",
        "| write-time \\ vs | " + " | ".join(r.system for r in reads) + " |",
        "|---|" + "---:|" * len(reads),
    ]
    for c in costs:
        if c.kind == "write-time":
            cells = []
            for r in reads:
                q = c.breakeven.get(r.system)
                cells.append("never" if q is None else f"{q:,.1f} ({q / sc.meetings:.2f}/mtg)")
            lines.append(f"| {c.system} | " + " | ".join(cells) + " |")
    systems = [c.system for c in costs]
    lines += [
        "",
        "## Sensitivity: $/user/month by ingest model x answer model",
        "",
        "| ingest | answer | " + " | ".join(systems) + " |",
        "|---|---|" + "---:|" * len(systems),
    ]
    for row in sens:
        lines.append(
            f"| {_short(row['ingest_model'])} | {_short(row['answer_model'])} | "
            + " | ".join(_usd(row["monthly"].get(s), 2) for s in systems)
            + " |"
        )
    lines += [
        "",
        "## Builds measured",
        "",
        "| system | build | meetings | complete | inferred model (err) | calls/mtg |"
        " in/mtg | cache-read/mtg | out/mtg | recorded $/mtg |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|",
    ]
    for p in ingest.values():
        u = p.per_meeting
        lines.append(
            f"| {p.system} | `{p.build}` | {p.meetings} | {'yes' if p.complete else 'no'} | "
            f"{_short(p.model)} ({p.model_error:.1%}) | {u.calls:.1f} | {u.input_tokens:,.0f} | "
            f"{u.cache_read_tokens:,.0f} | {u.output_tokens:,.0f} | {_usd(u.recorded_usd, 4)} |"
        )
    lines += ["", "## Assumptions", ""]
    lines += [f"- **{kind}**: {text}" for kind, text in ASSUMPTIONS]
    return "\n".join(lines) + "\n"
