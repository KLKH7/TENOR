"""Run baselines over benchmark questions, judge the answers, and write a report.

Reproducibility: every LLM call goes through the disk-cached client, so re-running the same
(git SHA, model, prompts, seed, split) costs nothing and returns identical results. Reports
record all of those. Spending is capped: the run stops before the ledger would exceed
``budget_usd`` and writes a partial report that says so.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from concentrate.agents.baselines import (
    AgenticSystem,
    FullContextSystem,
    MeetingRetriever,
    SummariesOnlySystem,
    System,
    TopKSystem,
)
from concentrate.agents.retrieval import Embedder
from concentrate.core.models import MeetingNote
from concentrate.core.store import NoteStore
from concentrate.evals.scoring import (
    JUDGE_PROMPT,
    Row,
    citation_scores,
    exact_match,
    judge,
    summarize,
)
from concentrate.llm.client import LLMClient, ToolBackend, cost_usd
from concentrate.sources.fixtures import FixtureSource
from concentrate.sources.poller import sync
from concentrate.synth.questions import UNANSWERABLE, Question

SYSTEMS = ("b0", "b1", "b2", "b3")
JUDGE_TOKENS_EST = (700, 300)  # per question (input, output incl. thinking), planning only


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
    retriever = MeetingRetriever(notes, embedder) if {"b0", "b1"} & set(names) else None
    built: list[System] = []
    for n in names:
        if n == "b0" and retriever:
            built.append(TopKSystem(client, retriever, k, backend))
        elif n == "b1" and retriever:
            built.append(SummariesOnlySystem(client, retriever, k, backend))
        elif n == "b2":
            built.append(FullContextSystem(client, notes, backend))
        elif n == "b3":
            built.append(AgenticSystem(client, notes, backend))
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


def estimate(systems: list[System], questions: list[Question], model: str) -> list[Estimate]:
    """Offline cost estimate per system (+ judge). B2 assumes prompt caching after call one."""
    out = []
    for s in systems:
        tin = tout = 0
        usd = 0.0
        for i, q in enumerate(questions):
            a, b = s.estimate(q)
            tin, tout = tin + a, tout + b
            if s.name == "b2_full":  # first call writes the cache, the rest read it
                usd += cost_usd(model, 60, b, 0 if i == 0 else a, a if i == 0 else 0)
            else:
                usd += cost_usd(model, a, b)
        jin, jout = JUDGE_TOKENS_EST
        usd += cost_usd(model, jin * len(questions), jout * len(questions))
        out.append(Estimate(s.name, len(questions), tin, tout, usd))
    return out


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
) -> dict[str, Any]:
    """Run, judge, aggregate and write ``<out_dir>/<stamp>.{json,md}`` plus per-question JSONL."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[Row] = []
    details: list[dict[str, Any]] = []
    stopped = None
    try:
        for q in questions:
            for s in systems:
                if client.ledger.spent_usd() > budget_usd:
                    raise BudgetExceeded(
                        f"spent ${client.ledger.spent_usd():.2f} > budget ${budget_usd:.2f}"
                    )
                res = s.run(q)
                verdict = judge(client, q, res.answer.answer, res.answer.abstain)
                precision, recall = citation_scores(q, res.answer.citations)
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
                    }
                )
    except BudgetExceeded as exc:
        stopped = str(exc)

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
            "spent_usd": client.ledger.spent_usd(),
            "stopped_early": stopped,
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
    if x is None:
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
    if m.get("stopped_early"):
        lines += ["", f"> **Stopped early:** {m['stopped_early']}. Results are partial."]
    lines += [
        "",
        "| System | Accuracy [95% CI] | Exact match | Abstain P / R | Cite P / R | "
        "Input tok/q | $/q | p50 / p95 s | Tools/q |",
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
            f"{_f(s['latency_p95'], digits=1)} | {_f(s['tool_calls_per_q'], digits=1)} |"
        )
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
    lines += ["", f"Prompts: `{json.dumps(m['prompts'])}`", ""]
    return "\n".join(lines)
