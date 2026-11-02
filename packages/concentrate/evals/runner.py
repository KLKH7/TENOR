"""Run baselines over benchmark questions, judge the answers, and write a report.

Reproducibility: every LLM call goes through the disk-cached client, so re-running the same
(git SHA, model, prompts, seed, split) costs nothing and returns identical results. Reports
record all of those. Spending is capped: the run stops before the ledger would exceed
``budget_usd`` and writes a partial report that says so.
"""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from concentrate.agents import corpus as cv
from concentrate.agents.baselines import (
    AgenticSystem,
    FullContextSystem,
    IngestStats,
    MeetingRetriever,
    OracleSystem,
    SummariesOnlySystem,
    System,
    SystemResult,
    TopKSystem,
)
from concentrate.agents.retrieval import Embedder
from concentrate.core.models import MeetingNote
from concentrate.core.store import NoteStore
from concentrate.evals.scoring import (
    JUDGE_PROMPT,
    JudgeOutput,
    Row,
    citation_scores,
    exact_match,
    judge,
    judge_request,
    summarize,
)
from concentrate.llm.client import (
    BatchBackend,
    LLMClient,
    LLMRequest,
    ToolBackend,
    TurnRequest,
    cost_usd,
    count_tokens,
    estimate_tokens,
    prefetch_structured,
    prefetch_turns,
    turn_cache_path,
)
from concentrate.sources.fixtures import FixtureSource
from concentrate.sources.poller import sync
from concentrate.synth.questions import UNANSWERABLE, Question

SYSTEMS = ("b0", "b1", "b2", "b2o", "b3", "b4", "b5")
JUDGE_TOKENS_EST = (800, 80)  # per question (input, output); measured ~780 / ~60 in the pilot
CONTEXT_LIMIT_TOKENS = 950_000  # 1M-token window minus room for instructions and the answer
HEURISTIC_TO_EXACT = 1.93  # measured: chars/3.5 undercounts transcripts with line IDs ~1.9x


def ingest_model() -> str:
    return os.environ.get("CONCENTRATE_INGEST_MODEL") or "claude-sonnet-5-5"


def corpus_tokens(notes: list[MeetingNote], model: str, cache_dir: Path) -> int:
    """Exact token size of the corpus (full views), or a calibrated estimate if offline."""
    views = [cv.full_view(m) for m in notes]
    if os.environ.get("ANTHROPIC_API_KEY"):
        return sum(count_tokens(views, model, cache_dir))
    return round(sum(estimate_tokens(v) for v in views) * HEURISTIC_TO_EXACT)


def load_corpus(fixtures: Path) -> list[MeetingNote]:
    """Sync a fixture set into an in-memory store and return its normalized notes."""
    store = NoteStore(":memory:")
    sync(FixtureSource(fixtures), store)
    return list(store.iter_notes())


def load_questions(
    fixtures: Path, split: str, limit: int = 0, categories: tuple[str, ...] = ()
) -> list[Question]:
    qs = [
        Question.model_validate_json(line)
        for line in (fixtures / "questions.jsonl").read_text().splitlines()
    ]
    qs = [q for q in qs if split == "all" or q.split == split]
    if categories:
        qs = [q for q in qs if q.category in categories]
    if not limit or limit >= len(qs):
        return qs
    # Round-robin across categories so small (pilot) runs cover every question type.
    by_cat: dict[str, list[Question]] = {}
    for q in qs:
        by_cat.setdefault(q.category, []).append(q)
    picked: list[Question] = []
    while len(picked) < limit:
        for bucket in by_cat.values():
            if bucket and len(picked) < limit:
                picked.append(bucket.pop(0))
    return picked


def build_systems(
    names: tuple[str, ...],
    client: LLMClient,
    notes: list[MeetingNote],
    embedder: Embedder | None,
    k: int,
    backend: ToolBackend | None = None,
) -> list[System]:
    retriever = MeetingRetriever(notes, embedder) if {"b0", "b1", "b2o"} & set(names) else None
    built: list[System] = []
    for n in names:
        if n == "b0" and retriever:
            built.append(TopKSystem(client, retriever, k, backend))
        elif n == "b1" and retriever:
            built.append(SummariesOnlySystem(client, retriever, k, backend))
        elif n == "b2":
            size = corpus_tokens(notes, client.model, client.cache_dir)
            if size > CONTEXT_LIMIT_TOKENS:
                raise ValueError(
                    f"b2 (full context) needs {size:,} tokens, over the {CONTEXT_LIMIT_TOKENS:,} "
                    "window: use b2o (oracle ceiling) for this corpus"
                )
            built.append(FullContextSystem(client, notes, backend))
        elif n == "b2o" and retriever:
            built.append(OracleSystem(client, notes, retriever, backend=backend))
        elif n == "b3":
            built.append(AgenticSystem(client, notes, backend))
        elif n in ("b4", "b5"):
            # Optional write-time competitors live in their own modules (heavy dependencies).
            module, cls = {
                "b4": ("mem0_system", "Mem0System"),
                "b5": ("graphiti_system", "GraphitiSystem"),
            }[n]
            try:
                mod = importlib.import_module(f"concentrate.agents.{module}")
            except ImportError as exc:
                raise ValueError(f"{n} is not available: {exc}") from exc
            built.append(getattr(mod, cls)(client, notes))
        else:
            raise ValueError(f"unknown system {n!r}; choose from {SYSTEMS}")
    return built


@dataclass
class Estimate:
    system: str
    questions: int
    input_tokens: int
    output_tokens: int
    usd: float
    exact: bool = False  # input tokens counted with the real tokenizer
    ingest_usd: float = 0.0  # write-time systems: one-off memory build
    batch: bool = False  # Message Batches discount applied (beyond the latency sample)


class Batchable(Protocol):
    def turn_request(self, q: Question) -> TurnRequest: ...


def batchable(s: System) -> bool:
    """Single-turn systems expose ``turn_request(q)`` and can be prefetched in a batch."""
    return callable(getattr(s, "turn_request", None))


def _exact_input(s: System, q: Question, client: LLMClient) -> int | None:
    texts = getattr(s, "prompt_texts", None)
    if texts is None or not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    system, user = texts(q)
    return count_tokens([f"{system}\n\n{user}"], client.model, client.cache_dir)[0]


def _answer_usd(s: System, a: int, b: int, model: str, *, first: bool, batch: bool) -> float:
    """Price one answer of ``a`` input / ``b`` output tokens. B2's corpus sits in a cached
    system prompt: the first call writes the cache, the rest read it."""
    if s.name == "b2_full":
        return cost_usd(model, 60, b, 0 if first else a, a if first else 0, batch=batch)
    return cost_usd(model, a, b, batch=batch)


def estimate(
    systems: list[System],
    questions: list[Question],
    model: str,
    *,
    client: LLMClient | None = None,
    notes: list[MeetingNote] | None = None,
    batch: bool = False,
    latency_sample: int = 0,
) -> list[Estimate]:
    """Cost estimate per system (+ judge, + ingestion for write-time systems).

    With a ``client`` and an API key, prompt-assembling systems are counted exactly with the
    free token-counting endpoint; others use their calibrated planning estimates. B2 assumes
    prompt caching after the first call. With ``batch``, batchable systems and the judge are
    priced at the Message Batches rate (50%) except for the first ``latency_sample`` questions,
    which run interactively at full price.
    """
    out = []
    n_full = min(latency_sample, len(questions)) if batch else len(questions)
    for s in systems:
        tin = tout = 0
        usd = 0.0
        exact = client is not None and hasattr(s, "prompt_texts")
        discounted = batch and batchable(s)
        for i, q in enumerate(questions):
            a, b = s.estimate(q)
            counted = _exact_input(s, q, client) if client is not None else None
            if counted is not None:
                a = counted
            tin, tout = tin + a, tout + b
            usd += _answer_usd(s, a, b, model, first=i == 0, batch=discounted and i >= n_full)
        jin, jout = JUDGE_TOKENS_EST
        n_disc = len(questions) - n_full
        usd += cost_usd(model, jin * n_full, jout * n_full)
        usd += cost_usd(model, jin * n_disc, jout * n_disc, batch=True)
        ingest_usd = 0.0
        estimate_ingest = getattr(s, "estimate_ingest", None)
        if estimate_ingest is not None and notes is not None:
            iin, iout = estimate_ingest(notes)
            ingest_usd = cost_usd(ingest_model(), iin, iout)
        out.append(
            Estimate(
                s.name,
                len(questions),
                tin,
                tout,
                usd + ingest_usd,
                exact,
                ingest_usd,
                batch=discounted,
            )
        )
    return out


def _turn_batch_usd(
    pairs: list[tuple[System, Question, TurnRequest]], client: LLMClient
) -> tuple[list[TurnRequest], float]:
    """Uncached requests among ``pairs`` and their estimated batch cost (conservative: exact
    token counts when available, else the heuristic scaled up to exact-count calibration)."""
    todo: list[TurnRequest] = []
    usd = 0.0
    first: set[str] = set()
    for s, q, req in pairs:
        if turn_cache_path(client, req).exists():
            continue
        todo.append(req)
        a, b = s.estimate(q)
        counted = _exact_input(s, q, client)
        a = counted if counted is not None else round(a * HEURISTIC_TO_EXACT)
        usd += _answer_usd(s, a, b, client.model, first=s.name not in first, batch=True)
        first.add(s.name)
    return todo, usd


def _judge_batch_usd(reqs: list[LLMRequest], client: LLMClient) -> float:
    usd = 0.0
    for r in reqs:
        if not client.is_cached(r):
            a = estimate_tokens(r.system) + estimate_tokens(r.user)
            usd += cost_usd(client.model, a, JUDGE_TOKENS_EST[1], batch=True)
    return usd


class BudgetExceeded(RuntimeError):
    pass


def _git() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], capture_output=True, text=True, check=True
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    return {"sha": run("rev-parse", "--short", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def run_eval(
    systems: list[System],
    questions: list[Question],
    client: LLMClient,
    *,
    seed: int,
    split: str,
    budget_usd: float,
    out_dir: Path,
    meta: dict[str, Any] | None = None,
    notes: list[MeetingNote] | None = None,
    batch: bool = False,
    latency_sample: int = 10,
    batch_backend: BatchBackend | None = None,
) -> dict[str, Any]:
    """Run, judge, aggregate and write ``<out_dir>/<stamp>.{json,md}`` plus per-question JSONL.

    Write-time systems (those with ``ingest``) build their memory first; that one-off cost is
    reported separately and counts against ``budget_usd``.

    With ``batch``, the first ``latency_sample`` questions run fully interactively (systems and
    judge) and are the only source of latency for batchable systems. For the rest: (A) every
    batchable system request is prefetched in one Message Batch into the LLM cache, (B) systems
    run as usual (cache hits; B3 and failed batch items run interactively), (C) all judge calls
    are prefetched in one batch, then judged from cache. Each batch is estimated first and
    refused if it would exceed the remaining budget.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[Row] = []
    details: list[dict[str, Any]] = []
    ingest: dict[str, dict[str, Any]] = {}
    stopped = None
    sample = {q.id for q in questions[:latency_sample]} if batch else set()
    batch_info: dict[str, Any] = {
        "enabled": batch,
        "latency_sample": len(sample),
        "batched_systems": [s.name for s in systems if batch and batchable(s)],
    }
    # (question, system, result, verdict); verdict is None until judged.
    entries: list[tuple[Question, System, SystemResult, JudgeOutput | None]] = []

    def spent() -> float:
        return client.ledger.spent_usd() + sum(
            float(i["cost_usd"]) for i in ingest.values() if not i["cached"]
        )

    def refuse_over_budget(what: str, n: int, usd: float) -> None:
        if n and spent() + usd > budget_usd:
            raise BudgetExceeded(
                f"{what} batch of {n} requests is estimated at ${usd:.2f}, over the remaining "
                f"budget ${budget_usd - spent():.2f}; nothing was submitted"
            )

    try:
        for s in systems:
            build = getattr(s, "ingest", None)
            if build is not None:
                if notes is None:
                    raise ValueError(f"{s.name} needs the corpus (notes) to ingest")
                start = time.perf_counter()
                stats: IngestStats = build(notes)
                stats.seconds = stats.seconds or time.perf_counter() - start
                ingest[s.name] = asdict(stats)
                if spent() > budget_usd:
                    raise BudgetExceeded(f"ingestion pushed spend to ${spent():.2f}")
        if batch:  # phase A: one batch for every batchable request outside the sample
            pairs = [
                (s, q, cast(Batchable, s).turn_request(q))
                for s in systems
                if batchable(s)
                for q in questions
                if q.id not in sample
            ]
            todo, usd = _turn_batch_usd(pairs, client)
            refuse_over_budget("system", len(todo), usd)
            batch_info["systems"] = asdict(prefetch_turns(client, todo, batch_backend))
            batch_info["systems"]["estimated_usd"] = usd
        for q in questions:  # phase B: cache hits for batched requests, live calls otherwise
            for s in systems:
                if spent() > budget_usd:
                    raise BudgetExceeded(f"spent ${spent():.2f} > budget ${budget_usd:.2f}")
                res = s.run(q)
                verdict = None
                if not batch or q.id in sample:
                    verdict = judge(client, q, res.answer.answer, res.answer.abstain)
                entries.append((q, s, res, verdict))
    except BudgetExceeded as exc:
        stopped = str(exc)

    unjudged = [e for e in entries if e[3] is None]
    if unjudged:  # phase C: one batch for the remaining judge calls, then judge from cache
        reqs = [judge_request(q, r.answer.answer, r.answer.abstain) for q, _, r, _ in unjudged]
        usd = _judge_batch_usd(reqs, client)
        try:
            refuse_over_budget("judge", sum(not client.is_cached(r) for r in reqs), usd)
            batch_info["judge"] = asdict(prefetch_structured(client, reqs, batch_backend))
            batch_info["judge"]["estimated_usd"] = usd
            entries = [
                (q, s, r, v or judge(client, q, r.answer.answer, r.answer.abstain))
                for q, s, r, v in entries
            ]
        except BudgetExceeded as exc:
            stopped = f"{stopped}; {exc}" if stopped else str(exc)
            entries = [e for e in entries if e[3] is not None]  # unjudged answers are dropped

    for q, s, res, verdict in entries:
        assert verdict is not None
        precision, recall = citation_scores(q, res.answer.citations)
        in_sample = not batch or not batchable(s) or q.id in sample
        rows.append(
            Row(
                system=s.name,
                qid=q.id,
                category=q.category,
                correct=verdict.verdict == "correct",
                verdict=verdict.verdict,
                exact_match=exact_match(q, res.answer.answer, res.answer.abstain),
                abstained=res.answer.abstain,
                unanswerable=q.answer_norm == [UNANSWERABLE],
                cite_precision=precision,
                cite_recall=recall,
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
                cost_usd=res.cost_usd,
                latency_s=res.latency_s,
                tool_calls=res.tool_calls,
                latency_measured=in_sample and not res.batched,
            )
        )
        details.append(
            {
                "system": s.name,
                "qid": q.id,
                "question": q.question,
                "gold": q.answer,
                "answer": res.answer.model_dump(),
                "verdict": verdict.model_dump(),
                "retrieved": res.retrieved,
                "batched": res.batched,
            }
        )
    for key in ("systems", "judge"):
        if key in batch_info:
            batch_info.setdefault("fallback_interactive", 0)
            batch_info["fallback_interactive"] += sum(batch_info[key]["fallback"].values())

    report = {
        "meta": {
            "date": stamp,
            "git": _git(),
            "model": client.model,
            "seed": seed,
            "split": split,
            "questions": len(questions),
            "prompts": {s.name: list(s.prompt_ids) for s in systems} | {"judge": [JUDGE_PROMPT]},
            "budget_usd": budget_usd,
            "spent_usd": spent(),
            "ingest": ingest,
            "stopped_early": stopped,
            "batch": batch_info,
            **(meta or {}),
        },
        "metrics": summarize(rows, seed),
        "ledger_by_stage": client.ledger.by_stage(),
    }
    (out_dir / f"{stamp}.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / f"{stamp}.jsonl").write_text(
        "".join(
            json.dumps({**d, **asdict(r)}, default=str) + "\n"
            for d, r in zip(details, rows, strict=True)
        )
    )
    (out_dir / f"{stamp}.md").write_text(to_markdown(report))
    report["paths"] = {k: str(out_dir / f"{stamp}.{k}") for k in ("json", "jsonl", "md")}
    return report


def _f(x: Any, pct: bool = False, digits: int = 2) -> str:
    if x is None or x != x:  # None or NaN
        return "-"
    return f"{100 * x:.0f}%" if pct else f"{x:,.{digits}f}"


def to_markdown(report: dict[str, Any]) -> str:
    m = report["meta"]
    lines = [
        f"# Baseline evaluation — {m['date']}",
        "",
        f"Split **{m['split']}**, {m['questions']} questions · model `{m['model']}` · seed "
        f"{m['seed']} · git `{m['git']['sha']}`{' (dirty)' if m['git']['dirty'] else ''} · "
        f"spent ${m['spent_usd']:.2f} of ${m['budget_usd']:.2f}",
    ]
    b = m.get("batch") or {}
    if b.get("enabled"):
        batched = ", ".join(b.get("batched_systems") or []) or "none"
        lines += [
            "",
            f"**Batch mode:** Message Batches API (50% price) for {batched} and the judge, "
            f"except the first {b['latency_sample']} questions, which ran fully interactively "
            "(full price) and are the only latency source for those systems. "
            f"{b.get('fallback_interactive', 0)} batch items failed and ran interactively.",
        ]
    if m.get("stopped_early"):
        lines += ["", f"> **Stopped early:** {m['stopped_early']}. Results are partial."]
    lines += [
        "",
        "| System | Accuracy [95% CI] | Exact match | Abstain P / R | Cite P / R | "
        "Input tok/q | $/q | p50 / p95 s [rows] | Tools/q |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, s in report["metrics"].items():
        lo, hi = s["accuracy_ci95"]
        lines.append(
            f"| {name} | {_f(s['accuracy'], True)} [{_f(lo, True)}, {_f(hi, True)}] | "
            f"{_f(s['exact_match'], True)} | {_f(s['abstain_precision'], True)} / "
            f"{_f(s['abstain_recall'], True)} | {_f(s['cite_precision'], True)} / "
            f"{_f(s['cite_recall'], True)} | {_f(s['input_tokens_per_q'], digits=0)} | "
            f"{_f(s['cost_per_q'], digits=3)} | {_f(s['latency_p50'], digits=1)} / "
            f"{_f(s['latency_p95'], digits=1)} [{s['latency_n']}] | "
            f"{_f(s['tool_calls_per_q'], digits=1)} |"
        )
    lines += [
        "",
        "Latency [rows]: number of rows the p50/p95 come from"
        + (
            " — for batched systems, only the interactive latency sample; batch-served rows "
            "carry no latency."
            if b.get("enabled")
            else "."
        ),
    ]
    cats = sorted({c for s in report["metrics"].values() for c in s["by_category"]})
    if cats:
        names = list(report["metrics"])
        lines += [
            "",
            "## Accuracy by category",
            "",
            "| Category | n | " + " | ".join(names) + " |",
            "|---|---|" + "---|" * len(names),
        ]
        for c in cats:
            n = next(
                s["by_category"][c]["n"]
                for s in report["metrics"].values()
                if c in s["by_category"]
            )
            vals = [
                _f(report["metrics"][s]["by_category"].get(c, {}).get("accuracy"), True)
                for s in names
            ]
            lines.append(f"| {c} | {n} | " + " | ".join(vals) + " |")
    if m.get("ingest"):
        lines += [
            "",
            "## Write-time ingestion (one-off)",
            "",
            "| System | LLM calls | Input tok | Output tok | $ | Minutes | Reused |",
            "|---|---|---|---|---|---|---|",
        ]
        for name, i in m["ingest"].items():
            lines.append(
                f"| {name} | {i['llm_calls']:,} | {i['input_tokens']:,} | "
                f"{i['output_tokens']:,} | {i['cost_usd']:.2f} | "
                f"{i['seconds'] / 60:.1f} | {'yes' if i['cached'] else 'no'} |"
            )
    lines += ["", f"Prompts: `{json.dumps(m['prompts'])}`", ""]
    return "\n".join(lines)
