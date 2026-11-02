"""``concentrate`` command-line interface."""

from __future__ import annotations

import logging
import os
from contextlib import ExitStack
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from concentrate.core.store import NoteStore
from concentrate.llm.client import LLMClient
from concentrate.sources.base import Source
from concentrate.sources.fixtures import FixtureSource
from concentrate.sources.granola_api import GranolaAPISource
from concentrate.sources.granola_mcp import GranolaMCPSource
from concentrate.sources.poller import sync as run_sync

app = typer.Typer(no_args_is_help=True, add_completion=False)

DEFAULT_FIXTURES = Path("data/fixtures/sample")


class SourceKind(StrEnum):
    fixtures = "fixtures"
    granola = "granola"  # public REST API (Business/Enterprise; GRANOLA_API_KEY)
    granola_mcp = "granola-mcp"  # official MCP server (any plan; browser sign-in)


def _db_path(db: Path | None) -> Path:
    return db or Path(os.environ.get("CONCENTRATE_DB") or "data/concentrate.db")


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    """Concentrate: compressed, cited, time-aware memory over Granola notes."""
    load_dotenv()
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request lines at INFO; keep it quiet so nothing sensitive leaks by default.
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.command()
def sync(
    source: Annotated[SourceKind, typer.Option(help="Where to read notes from.")] = (
        SourceKind.fixtures
    ),
    fixtures_dir: Annotated[Path, typer.Option(help="Fixture set root.")] = DEFAULT_FIXTURES,
    db: Annotated[Path | None, typer.Option(help="SQLite path (env CONCENTRATE_DB).")] = None,
    full: Annotated[bool, typer.Option(help="Ignore the watermark and re-list all notes.")] = False,
) -> None:
    """Pull notes from Granola (API, MCP) or fixtures and normalize them into SQLite."""
    with ExitStack() as stack:
        src: Source
        if source is SourceKind.fixtures:
            src = FixtureSource(fixtures_dir)
        elif source is SourceKind.granola:
            api = GranolaAPISource()
            stack.callback(api.close)
            src = api
        else:
            from concentrate.sources.mcp_transport import LiveMcpCaller

            src = GranolaMCPSource(stack.enter_context(LiveMcpCaller()))
        store = NoteStore(_db_path(db))
        stack.callback(store.close)
        report = run_sync(src, store, full=full)
        counts = store.counts()
    typer.echo(
        f"listed={report.listed} fetched={report.fetched} unchanged={report.unchanged} "
        f"deleted={report.deleted} recovered={report.recovered} pending={len(report.pending)}"
    )
    typer.echo(f"watermark={report.watermark.isoformat() if report.watermark else '-'}")
    typer.echo("store: " + " ".join(f"{k}={v}" for k, v in counts.items()))


NORTHWIND = Path("data/fixtures/northwind")
NORTHWIND_XL = Path("data/fixtures/large/northwind-xl")  # gitignored: several MB
BUDGET_CONFIRM_USD = 2.0


@app.command()
def synth(
    seed: Annotated[int, typer.Option(help="RNG seed; same seed → same dataset.")] = 42,
    out: Annotated[Path, typer.Option(help="Output fixture directory.")] = NORTHWIND,
    polish: Annotated[bool, typer.Option(help="LLM-rewrite wording (hybrid rendering).")] = False,
    offline: Annotated[bool, typer.Option(help="Polish from the LLM cache only.")] = False,
    estimate: Annotated[
        bool, typer.Option(help="Print the polish cost estimate and exit.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 cost confirmation.")] = False,
    scale: Annotated[str, typer.Option(help="base (40 meetings) or xl (~185 meetings).")] = "base",
) -> None:
    """Generate the synthetic Northwind Labs dataset and benchmark questions."""
    if scale == "xl" and out == NORTHWIND:
        out = NORTHWIND_XL
    from concentrate.synth.pipeline import (
        GENERATOR_VERSION,
        build,
        estimate_polish_usd,
        generate_dataset,
    )

    manifest = out / "manifest.json"
    if scale == "xl" and manifest.exists() and not estimate:
        import json as _json

        old = _json.loads(manifest.read_text()).get("generator")
        if old != GENERATOR_VERSION:
            raise typer.BadParameter(
                f"{out} holds a {old} corpus that memories and results may be built on; "
                f"this generator is {GENERATOR_VERSION}. Write elsewhere with --out (e.g. "
                f"{out}-v2), or delete {out} first."
            )

    llm = None
    if polish or estimate:
        llm = LLMClient(offline=offline)
        tin, tout, usd = estimate_polish_usd(build(seed, scale), llm.model)
        typer.echo(
            f"polish estimate ({llm.model}): ~{tin:,} input + ~{tout:,} output tokens "
            f"≈ ${usd:.2f} (uncached; heuristic counts incl. 30% thinking allowance)"
        )
        if estimate:
            return
        if usd > BUDGET_CONFIRM_USD and not offline and not yes:
            typer.confirm(
                f"Estimated cost ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
    result = generate_dataset(seed, out, llm=llm, scale=scale)
    v = result.validation
    typer.echo(
        f"wrote {result.meetings} notes, {result.utterances:,} utterances, "
        f"{result.words:,} words, {result.questions} questions → {out}"
    )
    typer.echo("questions: " + ", ".join(f"{k}={n}" for k, n in sorted(v.by_category.items())))
    typer.echo("split: " + ", ".join(f"{k}={n}" for k, n in sorted(v.by_split.items())))
    if result.polish:
        p = result.polish
        typer.echo(
            f"polish: {p.accepted}/{p.turns} turns accepted, rejected={p.rejected}, "
            f"missing={p.missing}, spent=${result.llm_spent_usd:.2f}"
        )
    if v.errors:
        typer.echo(f"VALIDATION FAILED ({len(v.errors)} errors):", err=True)
        for e in v.errors[:20]:
            typer.echo(f"  {e}", err=True)
        raise typer.Exit(1)
    typer.echo("validation: all gold answers are stated in their gold utterances ✓")


@app.command("eval")
def eval_cmd(
    systems: Annotated[
        str, typer.Option(help="Comma-separated: b0,b1,b2,b2o,b3,b4,b5 (default depends on set).")
    ] = "",
    xl: Annotated[bool, typer.Option("--xl", help="Use the Northwind-XL benchmark.")] = False,
    split: Annotated[str, typer.Option(help="dev | test | all. Tune only on dev.")] = "dev",
    limit: Annotated[int, typer.Option(help="Max questions (0 = all in split).")] = 0,
    category: Annotated[
        list[str] | None, typer.Option(help="Restrict to categories (repeatable).")
    ] = None,
    k: Annotated[int, typer.Option(help="Meetings retrieved by b0/b1.")] = 20,
    seed: Annotated[int, typer.Option(help="Seed for bootstrap CIs.")] = 42,
    fixtures: Annotated[Path, typer.Option(help="Benchmark fixture set.")] = NORTHWIND,
    out: Annotated[Path, typer.Option(help="Report directory.")] = Path("evals/reports"),
    embeddings: Annotated[bool, typer.Option(help="Use dense embeddings in b0/b1.")] = True,
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
    offline: Annotated[bool, typer.Option(help="Use cached LLM results only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    batch: Annotated[
        bool,
        typer.Option(
            "--batch/--no-batch",
            help="Run single-turn systems and the judge via the Message Batches API (50% off).",
        ),
    ] = True,
    latency_sample: Annotated[
        int,
        typer.Option(help="With --batch: first N questions run interactively to measure latency."),
    ] = 10,
) -> None:
    """Run the baselines on the benchmark and write a report."""
    from concentrate.agents.retrieval import SentenceTransformerEmbedder
    from concentrate.evals.runner import (
        build_systems,
        estimate,
        load_corpus,
        load_questions,
        run_eval,
    )

    if xl and fixtures == NORTHWIND:
        fixtures = NORTHWIND_XL
    default = "b0,b1,b2o,b3" if fixtures == NORTHWIND_XL else "b0,b1,b2,b3"
    names = tuple(s.strip() for s in (systems or default).split(",") if s.strip())
    client = LLMClient(offline=offline)
    budget = float(os.environ.get("CONCENTRATE_BUDGET_USD") or 25)
    notes = load_corpus(fixtures)
    questions = load_questions(fixtures, split, limit, tuple(category or ()))
    needs_index = {"b0", "b1", "b2o", "c"} & set(names)
    embedder = SentenceTransformerEmbedder() if embeddings and needs_index else None
    built = build_systems(names, client, notes, embedder, k, fixtures=fixtures)
    ests = estimate(
        built,
        questions,
        client.model,
        client=client,
        notes=notes,
        batch=batch,
        latency_sample=latency_sample,
    )
    typer.echo(
        f"{fixtures}: {len(notes)} meetings · {len(questions)} questions ({split}) · "
        f"{len(built)} systems · {client.model}"
    )
    if batch:
        typer.echo(
            f"  batch mode: Message Batches API at 50% for single-turn systems and the judge, "
            f"except the first {min(latency_sample, len(questions))} questions (interactive, "
            "full price, the only latency source)"
        )
    for e in ests:
        tag = "exact" if e.exact else "est. "
        ingest = f", ingest ${e.ingest_usd:.2f}" if e.ingest_usd else ""
        disc = ", batch 50%" if e.batch else ""
        typer.echo(
            f"  {e.system:<13} ~{e.input_tokens / max(1, e.questions):>8,.0f} in-tok/q ({tag}) "
            f"≈ ${e.usd:6.2f}  (incl. judge{ingest}{disc})"
        )
    total = sum(e.usd for e in ests)
    typer.echo(f"  estimated total ≈ ${total:.2f} (before cache reuse) · cap ${budget:.2f}")
    if estimate_only:
        return
    if not offline and not os.environ.get("ANTHROPIC_API_KEY"):
        raise typer.BadParameter("ANTHROPIC_API_KEY is not set (put it in .env)")
    if total > BUDGET_CONFIRM_USD and not offline and not yes:
        typer.confirm(
            f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    report = run_eval(
        built,
        questions,
        client,
        seed=seed,
        split=split,
        budget_usd=budget,
        out_dir=out,
        notes=notes,
        batch=batch,
        latency_sample=latency_sample,
        meta={"k": k, "embedder": getattr(embedder, "name", None), "fixtures": str(fixtures)},
    )
    typer.echo((Path(report["paths"]["md"])).read_text())
    typer.echo(f"spent ${report['meta']['spent_usd']:.2f} · report: {report['paths']['md']}")


@app.command()
def ingest(
    systems: Annotated[str, typer.Option(help="Write-time systems to build: b4,b5.")] = "b4,b5",
    xl: Annotated[bool, typer.Option("--xl", help="Use the Northwind-XL benchmark.")] = False,
    fixtures: Annotated[Path, typer.Option(help="Benchmark fixture set.")] = NORTHWIND,
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
) -> None:
    """Build write-time memories once (Mem0, Graphiti, ...). Resumable; reruns are free."""
    import json as _json
    from dataclasses import asdict

    from concentrate.evals.runner import build_systems, estimate, load_corpus

    if xl and fixtures == NORTHWIND:
        fixtures = NORTHWIND_XL
    client = LLMClient()
    notes = load_corpus(fixtures)
    names = tuple(s.strip() for s in systems.split(",") if s.strip())
    built = build_systems(names, client, notes, None, 0, fixtures=fixtures)
    for e in estimate(built, [], client.model, notes=notes):
        typer.echo(f"{e.system}: ingest ≈ ${e.ingest_usd:.2f} for {len(notes)} meetings")
    if estimate_only:
        return
    for s in built:
        stats = s.ingest(notes)  # type: ignore[attr-defined]
        typer.echo(f"{s.name}: " + _json.dumps(asdict(stats), default=str))


def _report_paths(report: Path) -> tuple[Path, Path]:
    """``evals/reports/<stamp>`` (any extension or none) -> its ``.json`` and ``.jsonl``."""
    stem = report.with_suffix("") if report.suffix in {".json", ".jsonl", ".md"} else report
    js, jl = stem.with_suffix(".json"), stem.with_suffix(".jsonl")
    if not jl.exists():
        raise typer.BadParameter(f"{jl} not found")
    return js, jl


@app.command("judge-check")
def judge_check(
    report: Annotated[Path, typer.Argument(help="A report from `eval` (any of its files).")],
    model: Annotated[str, typer.Option(help="Second judge model.")] = "claude-sonnet-5-5",
    batch: Annotated[bool, typer.Option("--batch/--no-batch", help="Batch API (50% off).")] = True,
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
) -> None:
    """Re-judge a saved report with a second model and report agreement with the first."""
    import json as _json

    from concentrate.evals.judge_check import judge_check_markdown, load_report, rejudge_request
    from concentrate.evals.runner import JUDGE_TOKENS_EST
    from concentrate.evals.scoring import JudgeOutput
    from concentrate.llm.client import cost_usd, estimate_tokens, prefetch_structured

    js, jl = _report_paths(report)
    primary = _json.loads(js.read_text())["meta"]["model"] if js.exists() else "unknown"
    if model == primary:
        raise typer.BadParameter(f"the second judge must differ from the primary ({primary})")
    answers = load_report(jl)
    client = LLMClient(model=model)
    reqs = [rejudge_request(a) for a in answers]
    todo = [r for r in reqs if not client.is_cached(r)]
    usd = sum(
        cost_usd(
            model,
            estimate_tokens(r.system) + estimate_tokens(r.user),
            JUDGE_TOKENS_EST[1],
            batch=batch,
        )
        for r in todo
    )
    typer.echo(f"{jl.name}: {len(answers)} answers · {len(todo)} uncached · {model} ≈ ${usd:.2f}")
    if estimate_only:
        return
    if usd > BUDGET_CONFIRM_USD and not yes:
        typer.confirm(
            f"Estimated ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    batch_usd = prefetch_structured(client, todo).cost_usd if batch and todo else 0.0
    second = [client.structured(r, JudgeOutput) for r in reqs]
    md = judge_check_markdown(
        answers, second, primary_model=primary, second_model=model, report_name=jl.stem
    )
    out = jl.with_name(f"{jl.stem}.judgecheck.{model}.md")
    out.write_text(md)
    typer.echo(md)
    typer.echo(f"spent ${batch_usd + client.ledger.spent_usd():.2f} · written: {out}")


@app.command("hand-check")
def hand_check(
    report: Annotated[Path, typer.Argument(help="A report from `eval` (any of its files).")],
    n: Annotated[int, typer.Option(help="Answers to sample for hand-labelling.")] = 50,
    seed: Annotated[int, typer.Option(help="Sampling seed.")] = 42,
    score: Annotated[
        bool, typer.Option("--score", help="Score a filled-in sheet instead of exporting one.")
    ] = False,
    second_model: Annotated[
        str, typer.Option(help="Also score this judge's verdicts (from `judge-check`'s cache).")
    ] = "claude-sonnet-5-5",
) -> None:
    """Export a blind sample for a person to label, then score the judges against it. Free."""
    from concentrate.evals.judge_check import (
        hand_sample,
        load_report,
        rejudge_request,
        score_hand_check,
        write_hand_check,
    )
    from concentrate.evals.scoring import JudgeOutput

    _, jl = _report_paths(report)
    sheet, key = (
        jl.with_name(f"{jl.stem}.handcheck.csv"),
        jl.with_name(f"{jl.stem}.handcheck.key.json"),
    )
    answers = load_report(jl)
    if not score:
        if sheet.exists():
            raise typer.BadParameter(f"{sheet} exists; label it and pass --score")
        write_hand_check(hand_sample(answers, n, seed), sheet, key)
        typer.echo(
            f"Wrote {sheet}. Fill `your_verdict` with c / p / i (correct / partially / "
            f"incorrect) comparing `answer` to `gold` only, then rerun with --score."
        )
        return
    judges = {"primary": {a.key: a.verdict for a in answers}}
    second = LLMClient(model=second_model, offline=True)
    cached = {a.key: a for a in answers if second.is_cached(rejudge_request(a))}
    if cached:
        judges[second_model] = {
            k: second.structured(rejudge_request(a), JudgeOutput).verdict for k, a in cached.items()
        }
    for name, s in score_hand_check(sheet, key, judges).items():
        typer.echo(
            f"{name:<20} vs human: {s['agreement']:.1%} agree on correct/not "
            f"(kappa {s['kappa']:.2f}, n={s['n']}) · 3-way {s['agreement_3way']:.1%}"
        )


# -- tune-c: Concentrate variants and answer-time ablations on the XL dev split (workstream L) --

TUNE_VARIANTS: dict[str, dict[str, object]] = {
    "full": {},
    "no_entities": {"query_entities": False},  # question parser resolves no entities
    "no_supersession": {"supersession": False},  # append-only fact log, no validity/state
    "no_digests": {"k_digests": 0},
}


@app.command("tune-c")
def tune_c(
    variants: Annotated[
        str, typer.Option(help=f"Comma-separated: {', '.join(TUNE_VARIANTS)}.")
    ] = "full",
    split: Annotated[str, typer.Option(help="Tuning uses dev only.")] = "dev",
    limit: Annotated[int, typer.Option(help="Max questions (0 = all in split).")] = 0,
    seed: Annotated[int, typer.Option(help="Seed for bootstrap CIs.")] = 42,
    out: Annotated[Path, typer.Option(help="Report directory.")] = Path("evals/reports"),
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
    offline: Annotated[bool, typer.Option(help="Use cached LLM results only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    batch: Annotated[bool, typer.Option("--batch/--no-batch")] = True,
    latency_sample: Annotated[int, typer.Option()] = 10,
) -> None:
    """Run Concentrate variants (answer-time ablations) on the XL benchmark, same harness."""
    from concentrate.agents.baselines import System
    from concentrate.agents.concentrate_system import COMPLETE_MARKER, ConcentrateSystem
    from concentrate.agents.retrieval import SentenceTransformerEmbedder
    from concentrate.evals.runner import estimate, load_corpus, load_questions, run_eval

    if split == "test":
        raise typer.BadParameter("tune-c never runs on test; use `concentrate eval` for that")
    names = [v.strip() for v in variants.split(",") if v.strip()]
    unknown = [v for v in names if v not in TUNE_VARIANTS]
    if unknown:
        raise typer.BadParameter(f"unknown variants {unknown}; choose from {list(TUNE_VARIANTS)}")
    client = LLMClient(offline=offline)
    notes = load_corpus(NORTHWIND_XL)
    questions = load_questions(NORTHWIND_XL, split, limit)
    embedder = SentenceTransformerEmbedder()
    built = [
        ConcentrateSystem(
            client,
            embedder=embedder,
            directory_path=NORTHWIND_XL / "directory.json",
            name="concentrate" if v == "full" else f"c_{v}",
            **TUNE_VARIANTS[v],  # type: ignore[arg-type]
        )
        for v in names
    ]
    for s in built:  # answer-time variants share the cached store; never build one here
        if not (s.store_dir(notes) / COMPLETE_MARKER).exists():
            raise typer.BadParameter(f"no finished store at {s.store_dir(notes)}; build it first")
        s.ingest(notes)
    systems: list[System] = list(built)
    ests = estimate(
        systems, questions, client.model, client=client, batch=batch, latency_sample=latency_sample
    )
    total = sum(e.usd for e in ests)
    for e in ests:
        typer.echo(f"  {e.system:<20} ≈ ${e.usd:6.2f} (incl. judge)")
    typer.echo(f"{len(questions)} questions ({split}) · estimated total ≈ ${total:.2f}")
    if estimate_only:
        return
    if total > BUDGET_CONFIRM_USD and not offline and not yes:
        typer.confirm(
            f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    report = run_eval(
        systems,
        questions,
        client,
        seed=seed,
        split=split,
        budget_usd=float(os.environ.get("CONCENTRATE_BUDGET_USD") or 25),
        out_dir=out,
        notes=notes,
        batch=batch,
        latency_sample=latency_sample,
        meta={"variants": names, "fixtures": str(NORTHWIND_XL)},
    )
    typer.echo((Path(report["paths"]["md"])).read_text())
    typer.echo(f"spent ${report['meta']['spent_usd']:.2f} · report: {report['paths']['md']}")
