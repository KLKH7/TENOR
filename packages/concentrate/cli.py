"""``concentrate`` command-line interface."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
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
STALL_MINUTES = 15.0  # > the longest silent wait one API call can have (~6 min; HARDENING.md)


class SourceKind(StrEnum):
    fixtures = "fixtures"
    granola = "granola"  # public REST API (Business/Enterprise; GRANOLA_API_KEY)
    granola_mcp = "granola-mcp"  # official MCP server (any plan; browser sign-in)
    wispr_mcp = "wispr-mcp"  # Wispr Flow's official MCP (browser sign-in; WISPR_MCP_URL)


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
    """Pull notes from Granola (API, MCP), Wispr Flow (MCP) or fixtures into SQLite.

    Real meetings belong under gitignored paths, e.g. ``--db data/real_wispr.db``."""
    with ExitStack() as stack:
        src: Source
        if source is SourceKind.fixtures:
            src = FixtureSource(fixtures_dir)
        elif source is SourceKind.granola:
            api = GranolaAPISource()
            stack.callback(api.close)
            src = api
        elif source is SourceKind.wispr_mcp:
            from concentrate.sources.mcp_transport import wispr_caller
            from concentrate.sources.wispr_mcp import WisprMCPSource

            src = WisprMCPSource(stack.enter_context(wispr_caller()))
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
    extras: Annotated[
        bool, typer.Option(help="v2.1: add hybrid rooms, identity mess, corrections, etc.")
    ] = False,
) -> None:
    """Generate the synthetic Northwind Labs dataset and benchmark questions."""
    if scale == "xl" and out == NORTHWIND:
        out = NORTHWIND_XL
    from concentrate.synth.pipeline import (
        build,
        estimate_polish_usd,
        generate_dataset,
        generator_version,
    )

    version = generator_version(extras)

    manifest = out / "manifest.json"
    if scale == "xl" and manifest.exists() and not estimate:
        import json as _json

        old = _json.loads(manifest.read_text()).get("generator")
        if old != version:
            raise typer.BadParameter(
                f"{out} holds a {old} corpus that memories and results may be built on; "
                f"this generator is {version}. Write elsewhere with --out (e.g. "
                f"{out}-v2), or delete {out} first."
            )

    llm = None
    if polish or estimate:
        llm = LLMClient(offline=offline)
        tin, tout, usd = estimate_polish_usd(build(seed, scale, extras), llm.model)
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
    result = generate_dataset(seed, out, llm=llm, scale=scale, extras=extras)
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
    stall_minutes: Annotated[
        float,
        typer.Option(help="Exit 75 if no progress for this many minutes (0 = never)."),
    ] = STALL_MINUTES,
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
    with _stall_guard(stall_minutes, "eval"):
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
            fixtures=fixtures,
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
    stall_minutes: Annotated[
        float,
        typer.Option(help="Exit 75 if no progress for this many minutes (0 = never)."),
    ] = STALL_MINUTES,
) -> None:
    """Build write-time memories once (Mem0, Graphiti, ...). Resumable; reruns are free.

    A build that makes no progress for ``--stall-minutes`` exits with code 75; rerun it (e.g.
    in a shell loop) and it resumes from ``progress.jsonl``. See docs/engineering/HARDENING.md.
    """
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
    with _stall_guard(stall_minutes, f"ingest {systems}"):
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


# -- stall watchdog (workstream I) -------------------------------------------------------------


@contextmanager
def _stall_guard(minutes: float, what: str) -> Iterator[None]:
    """Run a long command under the stall watchdog (off when ``minutes`` is 0) and graceful
    SIGTERM/SIGINT handling; a requested stop closes stores cleanly and exits 75."""
    from concentrate import watchdog

    watchdog.reset_stop()
    with ExitStack() as stack:
        stack.enter_context(watchdog.graceful_signals())
        if minutes > 0:
            stack.enter_context(watchdog.Watchdog(minutes * 60, what=what))
        try:
            yield
        except watchdog.StopRequested as exc:
            typer.echo(f"{what}: stopped cleanly ({exc}); rerun to resume", err=True)
            raise typer.Exit(watchdog.STALL_EXIT_CODE) from None


@app.command()
def analyze(
    report: Annotated[Path, typer.Argument(help="A report from `eval` (any of its files).")],
    fixtures: Annotated[
        Path | None,
        typer.Option(help="Ground truth for the failure taxonomy (default: the report's own)."),
    ] = None,
    volume: Annotated[
        int, typer.Option(help="Questions per corpus over which ingest cost is amortised.")
    ] = 1_000,
    seed: Annotated[int | None, typer.Option(help="Bootstrap seed (default: report's).")] = None,
    llm: Annotated[
        bool, typer.Option("--llm", help="Refine generic failure buckets with an LLM classifier.")
    ] = False,
    llm_model: Annotated[str, typer.Option(help="Model for --llm.")] = "claude-haiku-5-5",
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="With --llm: estimate its cost and exit.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    price_model: Annotated[
        str, typer.Option(help="Re-price every answer at this model's interactive price.")
    ] = "claude-opus-5-5",
    real_questions: Annotated[
        Path | None,
        typer.Option(help="Real-slice questions.jsonl: gold meetings for a real-slice report."),
    ] = None,
    aggregate_only: Annotated[
        bool,
        typer.Option(help="Counts and rates only: no question IDs, signals or failures file."),
    ] = False,
    out: Annotated[
        Path | None, typer.Option(help="Output stem (default: next to the report).")
    ] = None,
) -> None:
    """Report v2: paired significance, power, Pareto frontier, failure taxonomy and the $0
    metrics (docs/benchmark/METRICS.md). Free unless --llm."""
    import json as _json

    from concentrate.evals.analyze import load_rows, write_analysis
    from concentrate.evals.failures import (
        GENERIC_TYPES,
        GroundTruth,
        LLMFailure,
        failures,
        llm_request,
    )
    from concentrate.evals.metrics import gold_from_questions, gold_from_real
    from concentrate.llm.client import PRICING, cost_usd, estimate_tokens

    if price_model not in PRICING:
        raise typer.BadParameter(f"unknown model {price_model!r}; known: {', '.join(PRICING)}")
    js, jl = _report_paths(report)
    rep = _json.loads(js.read_text())
    rows = load_rows(jl)
    fx = fixtures or Path(rep["meta"].get("fixtures") or NORTHWIND)
    gt = None
    if real_questions is None:
        gt = GroundTruth.load(fx) if (fx / "questions.jsonl").exists() else None
    gold = gold_from_questions(gt.questions) if gt is not None else None
    if real_questions is not None:
        from concentrate.evals.real_slice import load_questions as load_real

        gold = gold_from_real(load_real(real_questions))
        gt_note = "Ground truth: real-slice questions (gold meetings only; no world model)."
    elif gt is None:
        gt_note = f"Ground truth: **not found** at `{fx}`; only report-row signals were used."
    else:
        qids = {r["qid"]: r["question"] for r in rows}
        ok = sum(gt.questions.get(q) is not None and gt.questions[q].question == t
                 for q, t in qids.items())  # fmt: skip
        gt_note = f"Ground truth: `{fx}` ({ok}/{len(qids)} report questions matched)."
        if ok < len(qids):
            typer.echo(f"warning: {len(qids) - ok} questions don't match {fx}", err=True)
    fails = failures(rows, gt)
    if llm:
        by_key = {(r["system"], r["qid"]): r for r in rows}
        todo = [f for f in fails if f.primary in GENERIC_TYPES]
        reqs = [llm_request(by_key[(f.system, f.qid)], f) for f in todo]
        client = LLMClient(model=llm_model)
        usd = sum(
            cost_usd(llm_model, estimate_tokens(r.system) + estimate_tokens(r.user), 120)
            for r in reqs
            if not client.is_cached(r)
        )
        typer.echo(f"LLM classifier: {len(reqs)} answers · {llm_model} ≈ ${usd:.3f}")
        if estimate_only:
            return
        if usd > BUDGET_CONFIRM_USD and not yes:
            typer.confirm(
                f"Estimated ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
        for f, req in zip(todo, reqs, strict=True):
            res = client.structured(req, LLMFailure)
            f.llm_type, f.llm_reason = res.type, res.reason
        typer.echo(f"LLM classifier spent ${client.ledger.spent_usd():.4f}")
    stem = out or jl.with_suffix("")
    stem.parent.mkdir(parents=True, exist_ok=True)
    paths = write_analysis(
        rep, rows, fails, stem, seed=seed if seed is not None else int(rep["meta"].get("seed", 0)),
        volume=volume, gt_note=gt_note, gold=gold, price_model=price_model,
        aggregate_only=aggregate_only,
    )  # fmt: skip
    typer.echo("written: " + ", ".join(str(p) for p in paths.values()))


# -- MCP server (workstream D) --------------------------------------------------------------------


@app.command("mcp-serve")
def mcp_serve(
    xl: Annotated[bool, typer.Option("--xl", help="Serve the Northwind-XL memory.")] = False,
    fixtures: Annotated[
        Path | None, typer.Option(help="Fixture set the memory was built from.")
    ] = None,
    store: Annotated[
        Path | None, typer.Option(help="A build directory (holds memory.sqlite); default: find it.")
    ] = None,
    allow_partial: Annotated[
        bool, typer.Option(help="Serve an unfinished build if no finished one exists.")
    ] = False,
    embeddings: Annotated[
        bool, typer.Option(help="Dense embeddings in retrieval, as in the eval (slower start).")
    ] = True,
) -> None:
    """Serve a built Concentrate memory as a read-only MCP server over stdio.

    stdout carries the MCP protocol, so this command prints nothing to it; errors go to stderr.
    See docs/systems/MCP_SERVER.md for the Claude Desktop / Claude Code config.
    """
    from concentrate.mcp_server.server import build_server
    from concentrate.mcp_server.service import Answerer, open_memory

    root = fixtures or (NORTHWIND_XL if xl else NORTHWIND)
    embedder = None
    if embeddings:
        from concentrate.agents.retrieval import SentenceTransformerEmbedder

        embedder = SentenceTransformerEmbedder()
    try:
        svc = open_memory(
            root,
            store_dir=store,
            allow_partial=allow_partial,
            embedder=embedder,
            answerer=Answerer(LLMClient()),
        )
    except FileNotFoundError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    logging.getLogger(__name__).warning(
        "concentrate MCP: serving %s (%d meetings)", svc.info["store"], len(svc.meetings)
    )
    build_server(svc).run("stdio")


# -- pre-meeting brief (workstream H) --------------------------------------------------------

BRIEF_STORE_ROOT = Path("data/cache/concentrate")


def _brief_store(store: Path | None) -> Path:
    """The given memory store, else the most recently finished one under data/cache."""
    if store is not None:
        return store
    done = sorted(BRIEF_STORE_ROOT.glob("*/*/complete.json"), key=lambda p: p.stat().st_mtime)
    if not done:
        raise typer.BadParameter("no finished memory store found; build one or pass --store")
    return done[-1].parent / "memory.sqlite"


def _brief_world(fixtures: Path) -> tuple[object, object, dict[str, str], dict[str, str]]:
    import json as _json

    from concentrate.evals.brief import GoldBuilder, name_index
    from concentrate.evals.runner import load_corpus
    from concentrate.synth.world import World

    world = World.model_validate_json((fixtures / "world.json").read_text())
    notes = load_corpus(fixtures)
    names, ids = name_index(_json.loads((fixtures / "directory.json").read_text()))
    return GoldBuilder(world, notes), notes, names, ids


@app.command()
def brief(
    note_id: Annotated[str, typer.Argument(help="The meeting to brief for (its note ID).")],
    fixtures: Annotated[Path, typer.Option(help="Fixture set with world.json.")] = NORTHWIND_XL,
    store: Annotated[Path | None, typer.Option(help="Memory store (memory.sqlite).")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the structured brief.")] = False,
) -> None:
    """Print the pre-meeting brief for a meeting, from memory as of its start."""
    from concentrate.evals.brief import GoldBuilder
    from concentrate.memory.brief import build_brief, to_markdown
    from concentrate.memory.store import MemoryStore

    gold, _, names, _ = _brief_world(fixtures)
    assert isinstance(gold, GoldBuilder)
    meeting = next((m for m in gold.w.meetings if m.note_id == note_id), None)
    if meeting is None:
        raise typer.BadParameter(f"{note_id} is not a meeting in {fixtures}")
    path = _brief_store(store)
    b = build_brief(MemoryStore(path), gold.upcoming(meeting))
    typer.echo(b.model_dump_json(indent=1) if as_json else to_markdown(b, names))


@app.command("brief-eval")
def brief_eval(
    fixtures: Annotated[Path, typer.Option(help="Fixture set with world.json.")] = NORTHWIND_XL,
    store: Annotated[Path | None, typer.Option(help="Memory store (memory.sqlite).")] = None,
    split: Annotated[str, typer.Option(help="dev, test or all.")] = "dev",
    limit: Annotated[int, typer.Option(help="Meetings to sample (0 = all).")] = 0,
    baseline: Annotated[bool, typer.Option(help="Also run the top-k LLM baseline.")] = False,
    model: Annotated[str, typer.Option(help="Baseline model.")] = "claude-sonnet-5-5",
    k: Annotated[int, typer.Option(help="Chunks the baseline retrieves.")] = 24,
    seed: Annotated[int, typer.Option(help="Seed for the dev/test split.")] = 42,
    estimate: Annotated[bool, typer.Option(help="Print the baseline estimate and exit.")] = False,
    offline: Annotated[bool, typer.Option(help="Baseline from the LLM cache only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 cost confirmation.")] = False,
    out: Annotated[Path, typer.Option(help="Report directory.")] = Path("evals/reports"),
) -> None:
    """Score pre-meeting briefs against the world's ground truth (memory vs top-k baseline)."""
    import hashlib
    import subprocess
    from datetime import UTC, datetime

    from concentrate.agents.retrieval import SentenceTransformerEmbedder
    from concentrate.evals.brief import (
        BASELINE_PROMPT,
        GoldBuilder,
        TopKBriefBaseline,
        dumps,
        evaluate,
        sample_targets,
    )
    from concentrate.llm.client import load_prompt
    from concentrate.memory.store import MemoryStore

    gold, notes, names, ids = _brief_world(fixtures)
    assert isinstance(gold, GoldBuilder) and isinstance(notes, list)
    gold.seed = seed
    targets = sample_targets(gold, split, limit)
    path = _brief_store(store)
    typer.echo(f"store={path} split={split} meetings={len(targets)}")
    base = None
    client = None
    if baseline:
        client = LLMClient(model=model, offline=offline)
        base = TopKBriefBaseline(client, notes, names, SentenceTransformerEmbedder(), k=k)
        usd = sum(base.estimate_usd(gold.upcoming(m)) for m in targets)
        typer.echo(f"baseline estimate: ${usd:.2f} ({model}, {len(targets)} calls, k={k})")
        if estimate:
            return
        if usd > BUDGET_CONFIRM_USD and not offline and not yes:
            typer.confirm(
                f"Estimated cost ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
    report = evaluate(MemoryStore(path), gold, targets, base, ids)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    prompt_hash = hashlib.sha256(load_prompt(BASELINE_PROMPT).encode()).hexdigest()[:8]
    report["meta"] = {
        "git_sha": sha.stdout.strip(),
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "fixtures": str(fixtures),
        "store": str(path),
        "split": split,
        "limit": limit,
        "seed": seed,
        "baseline": {"model": model, "prompt": BASELINE_PROMPT, "prompt_sha": prompt_hash, "k": k}
        if baseline
        else None,
        "spent_usd": client.ledger.spent_usd() if client else 0.0,
    }
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"brief-{split}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    dest.write_text(dumps(report))
    for system, agg in report["summary"].items():
        typer.echo(f"{system}: last-meeting {agg['last_meeting_accuracy']:.0%}")
        for sec in ("commitments", "commitments_any_owner", "changes", "requests"):
            r = agg[sec]

            def pct(x: float | None) -> str:
                return "-" if x is None else f"{x:.0%}"

            typer.echo(
                f"  {sec:<22} P {pct(r['precision'])} R {pct(r['recall'])} "
                f"stale {pct(r['staleness'])} cite {pct(r['citation_precision'])} "
                f"(shown {r['shown']}, gold {r['gold']})"
            )
    typer.echo(f"spent ${report['meta']['spent_usd']:.2f} · report {dest}")


# -- fidelity (workstream G) -------------------------------------------------------------------


@app.command()
def fidelity(
    target: Annotated[
        str, typer.Argument(help="Fixture directory, or a NoteStore .db (e.g. data/real.db).")
    ],
) -> None:
    """Aggregate style statistics of a corpus (docs/benchmark/FIDELITY_V2.md).

    Prints no transcript text.
    """
    import json

    from concentrate.synth.fidelity import fixture_corpus, report, store_corpus

    path = Path(target)
    corpus = store_corpus(path) if path.suffix == ".db" else fixture_corpus(path)
    typer.echo(json.dumps(report(corpus), indent=1))


# -- tune-c: Concentrate variants and answer-time ablations on the XL dev split (workstream L) --

TUNE_VARIANTS: dict[str, dict[str, object]] = {
    "full": {},
    "no_entities": {"query_entities": False},  # question parser resolves no entities
    "no_supersession": {"supersession": False},  # append-only fact log, no validity/state
    "no_digests": {"k_digests": 0},
    "fallback_on_abstain": {"fallback": "on_abstain"},  # docs/systems/concentrate/fallback.md
    "fallback_always": {"fallback": "always"},
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


# -- workstream C: transcript-retirement curve --


@app.command("retire-curve")
def retire_curve(
    settings: Annotated[
        str, typer.Option(help="Concentrate retirement settings N (days); inf = none.")
    ] = "inf,30,14,7,0",
    b0_settings: Annotated[
        str, typer.Option(help="Settings for b0 top-k with transcripts deleted ('' = skip).")
    ] = "inf,14,0",
    b1: Annotated[bool, typer.Option("--b1/--no-b1", help="Include b1 (summaries only).")] = True,
    split: Annotated[str, typer.Option(help="dev | test | all. Tune only on dev.")] = "dev",
    limit: Annotated[int, typer.Option(help="Max questions (0 = all).")] = 0,
    k: Annotated[int, typer.Option(help="Meetings retrieved by b0/b1.")] = 20,
    seed: Annotated[int, typer.Option(help="Seed for bootstrap CIs.")] = 42,
    fixtures: Annotated[Path, typer.Option(help="Benchmark fixture set.")] = NORTHWIND_XL,
    out: Annotated[Path, typer.Option(help="Report directory.")] = Path("evals/reports/retirement"),
    estimate_only: Annotated[
        bool, typer.Option("--estimate", help="Estimate cost and exit.")
    ] = False,
    evidence_only: Annotated[
        bool, typer.Option("--evidence-only", help="Gold-line coverage of contexts only ($0).")
    ] = False,
    offline: Annotated[bool, typer.Option(help="Use cached LLM results only.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    latency_sample: Annotated[
        int, typer.Option(help="First N questions run interactively (latency sample).")
    ] = 5,
) -> None:
    """Sweep transcript retirement (Concentrate vs b0 with deleted transcripts vs b1)."""
    import json

    from concentrate.agents.baselines import MeetingRetriever, SummariesOnlySystem, System
    from concentrate.agents.concentrate_system import COMPLETE_MARKER, ConcentrateSystem
    from concentrate.agents.retrieval import SentenceTransformerEmbedder
    from concentrate.evals import retirement as rt
    from concentrate.evals.runner import estimate, load_corpus, load_questions, run_eval

    client = LLMClient(offline=offline)
    budget = float(os.environ.get("CONCENTRATE_BUDGET_USD") or 25)
    notes = load_corpus(fixtures)
    questions = load_questions(fixtures, split, limit)
    embedder = SentenceTransformerEmbedder()
    c_ns, b0_ns = rt.parse_settings(settings), rt.parse_settings(b0_settings)

    concentrates: list[ConcentrateSystem] = []
    for n in c_ns:
        c = ConcentrateSystem(
            client,
            embedder=embedder,
            directory_path=fixtures / "directory.json",
            retire_after_days=n,
        )
        c.name = f"concentrate@{rt.label(n)}"
        concentrates.append(c)
    systems: list[System] = list(concentrates)
    store_dir = concentrates[0].store_dir(notes) if concentrates else None
    complete = store_dir is None or (store_dir / COMPLETE_MARKER).exists()
    if not complete and not estimate_only:
        raise typer.BadParameter(
            f"Concentrate store {store_dir} is not complete; this command never builds memory "
            "(run `concentrate eval --systems c` or wait for the build)"
        )
    if complete:  # a finished store is a free cache hit: load it so estimates are exact
        for c in concentrates:
            c.ingest(notes)
    retriever = MeetingRetriever(notes, embedder) if b0_ns or b1 else None
    for n in b0_ns:
        assert retriever is not None
        systems.append(rt.RetiredTopKSystem(client, retriever, k, retire_after_days=n))
    if b1:
        assert retriever is not None
        systems.append(SummariesOnlySystem(client, retriever, k))

    ests = estimate(
        systems,
        questions,
        client.model,
        client=client,
        notes=notes,
        batch=True,
        latency_sample=latency_sample,
    )
    typer.echo(
        f"{fixtures}: {len(notes)} meetings · {len(questions)} questions ({split}) · "
        f"{len(systems)} systems · {client.model} · batch mode"
    )
    usd = {e.system: e.usd - (e.ingest_usd if complete else 0.0) for e in ests}
    for e in ests:
        typer.echo(
            f"  {e.system:<18} ~{e.input_tokens / max(1, e.questions):>8,.0f} in-tok/q "
            f"≈ ${usd[e.system]:6.2f}  (incl. judge)"
        )
    total = sum(usd.values())
    typer.echo(f"  estimated total ≈ ${total:.2f} (before cache reuse) · cap ${budget:.2f}")
    if estimate_only:
        return
    if evidence_only:  # free: build every context, count gold lines, no LLM calls
        for text in (True, False):
            typer.echo(
                f"\nGold transcript lines {'shown as text' if text else 'referenced by ID'}:"
            )
            ev = rt.context_evidence(systems, questions, text=text)
            typer.echo(rt.evidence_markdown(ev, questions))
        return
    if evidence_only:  # free: build every context, count gold lines, no LLM calls
        for text in (True, False):
            typer.echo(
                f"\nGold transcript lines {'shown as text' if text else 'referenced by ID'}:"
            )
            ev = rt.context_evidence(systems, questions, text=text)
            typer.echo(rt.evidence_markdown(ev, questions))
        return
    if not offline and not os.environ.get("ANTHROPIC_API_KEY"):
        raise typer.BadParameter("ANTHROPIC_API_KEY is not set (put it in .env)")
    if total > BUDGET_CONFIRM_USD and not offline and not yes:
        typer.confirm(
            f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    out.mkdir(parents=True, exist_ok=True)
    report = run_eval(
        systems,
        questions,
        client,
        seed=seed,
        split=split,
        budget_usd=budget,
        out_dir=out,
        notes=notes,
        batch=True,
        latency_sample=latency_sample,
        meta={"k": k, "embedder": embedder.name, "fixtures": str(fixtures), "retirement": True},
    )

    # Post-process: gold evidence in each context, tokens kept, retired share, curve.
    evidence = rt.context_evidence(systems, questions)
    pointers = rt.context_evidence(systems, questions, text=False)
    rows = rt.load_rows(Path(report["paths"]["jsonl"]))
    all_ns = list(dict.fromkeys([*c_ns, *b0_ns]))
    store_meetings = concentrates[0].store.meetings() if concentrates else []
    as_ofs = [q.as_of for q in questions]
    result = {
        "meta": {
            **{k_: report["meta"][k_] for k_ in ("date", "git", "model", "seed", "split")},
            "questions": len(questions),
            "prompts": report["meta"]["prompts"],
            "spent_usd": report["meta"]["spent_usd"],
            "store": str(store_dir),
            "eval_report": report["paths"]["md"],
        },
        "settings": {
            s.name: getattr(s, "retire_after_days", None)
            for s in systems
            if not isinstance(s, SummariesOnlySystem)
        },
        "order": [rt.label(n) for n in rt.ordered(all_ns)],
        "kept": {rt.label(n): rt.tokens_kept(store_meetings, as_ofs, n) for n in all_ns},
        "retired_share": {rt.label(n): rt.retired_share(notes, as_ofs, n) for n in all_ns},
        "curve": rt.curve(rows, evidence, seed, pointers),
    }
    stem = out / f"{report['meta']['date']}.curve"
    Path(f"{stem}.json").write_text(json.dumps(result, indent=2, default=str))
    Path(f"{stem}.md").write_text(rt.to_markdown(result))
    cats = sorted({r["category"] for r in rows})
    Path(f"{stem}.svg").write_text(rt.to_svg(result, cats))
    typer.echo(rt.to_markdown(result))
    typer.echo(f"spent ${report['meta']['spent_usd']:.2f} · curve: {stem}.md / .svg")


# -- workstream F: unit economics --------------------------------------------------------------


def _latest_report() -> Path:
    reports = sorted(Path("evals/reports").glob("*.jsonl"))
    if not reports:
        raise typer.BadParameter("no eval report in evals/reports; pass --report")
    return reports[-1]


@app.command("economics")
def economics_cmd(
    report: Annotated[
        Path | None,
        typer.Option(help="Eval report (.jsonl) for per-question costs; default latest."),
    ] = None,
    cache_root: Annotated[Path, typer.Option(help="Where memory builds live.")] = Path(
        "data/cache"
    ),
    meetings: Annotated[float, typer.Option(help="Meetings per user per month (GUESS).")] = 40,
    questions: Annotated[float, typer.Option(help="Questions per user per month (GUESS).")] = 30,
    ingest_model: Annotated[
        str | None, typer.Option(help="Re-price ingest on this model (default: as built).")
    ] = None,
    answer_model: Annotated[
        str | None, typer.Option(help="Re-price answers on this model (default: the report's).")
    ] = None,
    batch_ingest: Annotated[bool, typer.Option("--batch-ingest/--no-batch-ingest")] = True,
    batch_queries: Annotated[bool, typer.Option("--batch-queries/--no-batch-queries")] = False,
    batch_discount: Annotated[
        float, typer.Option(help="Batch API discount (0.5 = 50% off).")
    ] = 0.5,
    cache_hit: Annotated[
        float | None, typer.Option(help="Prompt-cache hit rate 0-1 (default: as measured).")
    ] = None,
    q_max: Annotated[float, typer.Option(help="X range of the break-even chart.")] = 150,
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("evals/economics"),
) -> None:
    """$/user/month per system and write-vs-query break-even, from measured runs. Free."""
    import json
    from dataclasses import asdict

    from concentrate.evals import economics as ec
    from concentrate.evals.svgchart import line_chart
    from concentrate.llm.client import PRICING

    for m in (ingest_model, answer_model):
        if m is not None and m not in PRICING:
            raise typer.BadParameter(f"unknown model {m!r}; known: {', '.join(PRICING)}")
    report = report or _latest_report()
    sc = ec.Scenario(
        meetings,
        questions,
        ingest_model,
        answer_model,
        batch_ingest,
        batch_queries,
        batch_discount,
        cache_hit,
    )
    builds = ec.pick_builds(ec.load_ingest(cache_root))
    model, answers = ec.load_answers(report)
    costs = ec.compute(builds, answers, model, sc)
    sens = ec.sensitivity(builds, answers, model, sc)
    out.mkdir(parents=True, exist_ok=True)
    stem = report.stem
    md = ec.to_markdown(costs, builds, sens, sc, str(report))
    (out / f"{stem}.md").write_text(md)
    (out / f"{stem}.json").write_text(
        json.dumps(
            {
                "scenario": asdict(sc),
                "report": str(report),
                "costs": [asdict(c) for c in costs],
                "sensitivity": sens,
                "builds": {k: asdict(v) for k, v in builds.items()},
            },
            indent=2,
            default=str,
        )
    )
    chart = line_chart(
        {
            c.system: pts
            for c, pts in zip(costs, ec.curve(costs, meetings, q_max).values(), strict=True)
        },
        title=f"$/user/month vs questions ({meetings:g} meetings/month)",
        xlabel="questions per user per month",
        ylabel="$ per user per month",
        dashed=[c.system for c in costs if c.kind == "query-time"],
    )
    (out / f"{stem}.breakeven.svg").write_text(chart)
    typer.echo(md)
    typer.echo(f"written: {out / stem}.{{md,json,breakeven.svg}}")


# -- workstream F: scale curve -----------------------------------------------------------------


def _concentrate_source_build(full_dir: Path) -> Path:
    """The current-config Concentrate build of the full corpus if complete, else the most
    recently finished build of the same corpus (older prompts; reported in the meta)."""
    if (full_dir / "complete.json").exists():
        return full_dir
    done = sorted(full_dir.parent.glob("*/complete.json"), key=lambda p: p.stat().st_mtime)
    if not done:
        raise typer.BadParameter(f"no complete Concentrate build under {full_dir.parent}")
    return done[-1].parent


@app.command("scale-curve")
def scale_curve_cmd(
    systems: Annotated[str, typer.Option(help="Comma-separated systems.")] = "b0,b3,c",
    fractions: Annotated[str, typer.Option(help="Nested prefix sizes.")] = "0.25,0.5,1.0",
    anchor: Annotated[float, typer.Option(help="Questions = dev Qs eligible at this size.")] = 0.5,
    n: Annotated[int, typer.Option(help="Stratified sample size of the question set.")] = 40,
    k: Annotated[int, typer.Option(help="Meetings retrieved by b0.")] = 20,
    seed: Annotated[int, typer.Option(help="Sampling + bootstrap seed.")] = 42,
    model: Annotated[
        str | None, typer.Option(help="Answer + judge model (default CONCENTRATE_MODEL / Opus).")
    ] = None,
    concentrate_build: Annotated[
        Path | None, typer.Option(help="Concentrate store dir to rewind (default: auto).")
    ] = None,
    out: Annotated[Path, typer.Option(help="Output root.")] = Path("evals/scale"),
    estimate_only: Annotated[bool, typer.Option("--estimate", help="Estimate and exit.")] = False,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the >$2 confirmation.")] = False,
    budget: Annotated[float, typer.Option(help="Hard cap for the whole curve, USD.")] = 12.0,
) -> None:
    """Accuracy and $/q vs corpus size on nested prefixes of Northwind-XL (batch mode)."""
    import json
    from datetime import UTC, datetime

    from concentrate.agents.concentrate_system import ConcentrateSystem
    from concentrate.agents.retrieval import SentenceTransformerEmbedder
    from concentrate.evals import scale_curve as scv
    from concentrate.evals.runner import (
        _git,
        build_systems,
        estimate,
        load_corpus,
        load_questions,
        run_eval,
    )
    from concentrate.evals.svgchart import line_chart

    names = tuple(s.strip() for s in systems.split(",") if s.strip())
    fixtures = NORTHWIND_XL
    notes = load_corpus(fixtures)
    plan = scv.plan(
        notes,
        load_questions(fixtures, "dev"),
        [float(f) for f in fractions.split(",")],
        anchor=anchor,
        n=n,
        seed=seed,
    )
    client = LLMClient(model=model)
    embedder = SentenceTransformerEmbedder() if {"b0", "c"} & set(names) else None
    source = None
    if "c" in names:
        probe = ConcentrateSystem(client, directory_path=fixtures / "directory.json")
        source = concentrate_build or _concentrate_source_build(probe.store_dir(notes))
    scratch = Path("data/cache/scale-curve/concentrate")
    built_by_size = {}
    total = 0.0
    typer.echo(f"{client.model} · k={k} · Concentrate rewound from {source}")
    for f, (sub, qs) in plan.items():
        base = [s for s in names if s != "c"]
        built = build_systems(tuple(base), client, sub, embedder, k, fixtures=fixtures)
        if "c" in names and source is not None:
            c = ConcentrateSystem(
                client,
                embedder=embedder,
                cache_root=scratch,
                directory_path=fixtures / "directory.json",
            )
            dst = c.store_dir(sub)
            if not (dst / scv.COMPLETE_MARKER).exists():
                scv.place_rewound(source, dst, {m.note_id for m in sub})
            c.ingest(sub)
            built.append(c)
        built_by_size[f] = built
        ests = estimate(built, qs, client.model, client=client, batch=True, latency_sample=0)
        usd = sum(e.usd for e in ests)
        total += usd
        typer.echo(
            f"  {f:>5.0%}: {len(sub):>3} meetings · {len(qs):>2} questions · "
            + " · ".join(f"{e.system} ${e.usd:.2f}" for e in ests)
            + f" = ${usd:.2f}"
        )
    typer.echo(f"  estimated total ≈ ${total:.2f} (before LLM-cache reuse) · cap ${budget:.2f}")
    if estimate_only:
        return
    if total > budget:
        raise typer.BadParameter(f"estimate ${total:.2f} is over --budget ${budget:.2f}")
    if total > BUDGET_CONFIRM_USD and not yes:
        typer.confirm(
            f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    root = out / stamp
    results = {}
    spent = 0.0
    for f, (sub, qs) in plan.items():
        rep = run_eval(
            built_by_size[f],
            qs,
            client,
            seed=seed,
            split="dev",
            budget_usd=budget,
            out_dir=root / f"p{round(f * 100):03d}",
            notes=sub,
            batch=True,
            latency_sample=0,
            meta={"k": k, "fraction": f, "meetings": len(sub), "concentrate_source": str(source)},
        )
        spent = rep["meta"]["spent_usd"]  # one client across sizes: its ledger is cumulative
        rows = [json.loads(line) for line in Path(rep["paths"]["jsonl"]).read_text().splitlines()]
        results[f] = {"meetings": len(sub), "rows": rows, "report": rep["paths"]["json"]}
        typer.echo(f"  {f:.0%} done · spent so far ${spent:.2f}")
    summary = scv.summarize_curve(results)
    meta = {
        "model": client.model,
        "k": k,
        "seed": seed,
        "git": _git()["sha"],
        "date": stamp,
        "anchor": anchor,
        "spent_usd": spent,
        "concentrate_source": str(source),
        "reports": {str(f): r["report"] for f, r in results.items()},
    }
    (root / "scale.json").write_text(json.dumps({"meta": meta, **summary}, indent=2))
    md = scv.to_markdown(summary, meta)
    (root / "scale.md").write_text(md)
    for metric, label, key in (
        ("accuracy", "accuracy on questions common to all sizes", "accuracy_common"),
        ("cost", "$ per question", "cost_per_q"),
    ):
        series: dict[str, list[tuple[float, float]]] = {
            s: [
                (p["meetings"], p[key] * (100 if metric == "accuracy" else 1))
                for p in d["points"]
                if p[key] is not None
            ]
            for s, d in summary["systems"].items()
        }
        (root / f"scale_{metric}.svg").write_text(
            line_chart(
                series,
                title=f"{label} vs corpus size",
                xlabel="meetings in corpus",
                ylabel="% correct" if metric == "accuracy" else "$/question",
                markers=True,
                y_max=100 if metric == "accuracy" else None,
            )
        )
    typer.echo(md)
    typer.echo(f"spent ${spent:.2f} · written: {root}")


# -- real-data slice (workstream B) -------------------------------------------------------------
# Everything these commands read or write about the user's meetings stays under data/real/
# (gitignored). Granola is reached only through the read-only allowlist in evals.real_slice.

real_kit = typer.Typer(no_args_is_help=True, help="Write and verify questions about real meetings.")
app.add_typer(real_kit, name="real-kit")
REAL_DB = Path("data/real.db")


def _real_notes(db: Path) -> list:  # type: ignore[type-arg]
    if not db.exists():
        raise typer.BadParameter(f"{db} not found: run `concentrate sync --source granola-mcp`")
    store = NoteStore(db)
    try:
        return list(store.iter_notes())
    finally:
        store.close()


def _real_client(offline: bool = False, model: str | None = None) -> LLMClient:
    from concentrate.evals.real_slice import REAL_DIR

    return LLMClient(model=model, cache_dir=REAL_DIR / "cache" / "llm", offline=offline)


class SplitName(StrEnum):
    dev = "dev"  # the questions already seen while building and tuning
    test = "test"  # hand-written fresh questions, held out (import-test)


SplitOpt = Annotated[SplitName, typer.Option("--split", help="dev (seen) or test (held out).")]


@real_kit.command("status")
def real_status(
    split: SplitOpt = SplitName.dev,
    product: Annotated[
        list[str] | None,
        typer.Option("--product", help="Also count recorded answers from this product."),
    ] = None,
) -> None:
    """Questions per category: drafted, verified, rejected vs the ~30-question target."""
    from concentrate.blackbox.kit import ProductSystem
    from concentrate.blackbox.products import product_name
    from concentrate.evals.real_slice import coverage, load_split

    qs = load_split(split.value)
    typer.echo(f"split: {split.value}")
    typer.echo(f"{'category':<18} draft verified rejected target")
    for c, v in coverage(qs).items():
        typer.echo(
            f"{c:<18} {v['draft']:>5} {v['verified']:>8} {v['rejected']:>8} {v['target']:>6}"
        )
    typer.echo(f"verified: {sum(q.status == 'verified' for q in qs)} of {len(qs)}")
    verified = [q for q in qs if q.status == "verified"]
    for p in product or []:
        name = product_name(p)
        typer.echo(f"{name}: {ProductSystem(name, None).recorded(verified)} answers recorded")


@real_kit.command("directory")
def real_directory_cmd(db: Annotated[Path, typer.Option()] = REAL_DB) -> None:
    """Write data/real/directory.json (people from attendee metadata) for Concentrate."""
    import json as _json

    from concentrate.evals.real_slice import REAL_DIR, real_directory

    out = REAL_DIR / "directory.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(real_directory(_real_notes(db)), indent=1))
    typer.echo(f"wrote {out} (private; edit names freely)")


@real_kit.command("draft")
def real_draft(
    db: Annotated[Path, typer.Option()] = REAL_DB,
    extra: Annotated[float, typer.Option(help="Draft this many x the gap (for rejections).")] = 1.5,
    estimate_only: Annotated[bool, typer.Option("--estimate")] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """LLM-draft candidate questions for categories still short of their target (dev split
    only: the test split is hand-written, see `real-kit import-test`)."""
    import math
    from datetime import date as _date

    from concentrate.evals.real_slice import (
        DraftOutput,
        coverage,
        draft_request,
        drafts_to_questions,
        load_questions,
        save_questions,
    )
    from concentrate.llm.client import cost_usd, estimate_tokens

    notes = _real_notes(db)
    qs = load_questions()
    wanted = {
        c: max(0, math.ceil((v["target"] - v["verified"] - v["draft"]) * extra))
        for c, v in coverage(qs).items()
    }
    if not any(wanted.values()):
        typer.echo("every category has enough drafts or verified questions")
        return
    today = _date.today()
    client = _real_client()
    req = draft_request(notes, wanted, today)
    usd = cost_usd(
        client.model, estimate_tokens(req.system) + estimate_tokens(req.user), req.max_tokens // 2
    )
    typer.echo(f"{len(notes)} meetings · drafting {sum(wanted.values())} questions ≈ ${usd:.2f}")
    if estimate_only:
        return
    if usd > BUDGET_CONFIRM_USD and not yes:
        typer.confirm(
            f"Estimated ${usd:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
        )
    new = drafts_to_questions(client.structured(req, DraftOutput), qs, today)
    save_questions([*qs, *new])
    typer.echo(f"added {len(new)} drafts · spent ${client.ledger.spent_usd():.2f}")
    typer.echo("next: `uv run concentrate real-kit review` (you verify every gold answer)")


@real_kit.command("review")
def real_review(
    show_evidence: Annotated[bool, typer.Option("--evidence/--no-evidence")] = True,
    split: SplitOpt = SplitName.dev,
) -> None:
    """Walk through drafts: [a]ccept, [e]dit, [r]eject, [s]kip, [q]uit. Saves after each."""
    from concentrate.evals.real_slice import load_split, questions_file, save_questions

    qs = load_split(split.value)
    path = questions_file(split.value)
    todo = [i for i, q in enumerate(qs) if q.status == "draft"]
    typer.echo(f"{len(todo)} drafts to review. Check each gold answer against your memory/notes.")
    for n, i in enumerate(todo, 1):
        q = qs[i]
        typer.echo(f"\n[{n}/{len(todo)}] {q.id} · {q.category}")
        typer.echo(f"  Q: {q.question}")
        typer.echo(f"  Gold: {q.gold_answer}")
        typer.echo(f"  Key terms: {', '.join(q.key_terms) or '-'}")
        if show_evidence and q.evidence:
            typer.echo(f"  Evidence: {q.evidence}")
        choice = typer.prompt("  [a]ccept/[e]dit/[r]eject/[s]kip/[q]uit", default="s").lower()
        if choice.startswith("q"):
            break
        if choice.startswith("a"):
            qs[i] = q.model_copy(update={"status": "verified"})
        elif choice.startswith("r"):
            note = typer.prompt("  why (optional)", default="")
            qs[i] = q.model_copy(update={"status": "rejected", "reviewer_note": note})
        elif choice.startswith("e"):
            question = typer.prompt("  question", default=q.question)
            gold = typer.prompt("  gold answer (UNANSWERABLE if none)", default=q.gold_answer)
            terms = typer.prompt(
                "  key terms, comma-separated", default=", ".join(q.key_terms) or "-"
            )
            key_terms = [] if terms.strip() == "-" else [t.strip() for t in terms.split(",")]
            qs[i] = q.model_copy(
                update={
                    "question": question,
                    "gold_answer": gold,
                    "key_terms": [t for t in key_terms if t],
                    "status": "verified",
                    "reviewer_note": "edited",
                }
            )
        save_questions(qs, path)
    typer.echo(f"\nverified: {sum(q.status == 'verified' for q in qs)} of {len(qs)}")


@real_kit.command("add")
def real_add(split: SplitOpt = SplitName.dev) -> None:
    """Write your own question (counts as verified: you are the source of the gold answer)."""
    from datetime import date as _date

    from concentrate.evals.real_slice import (
        CATEGORIES,
        RealQuestion,
        load_split,
        questions_file,
        save_questions,
    )

    qs = load_split(split.value)
    category = typer.prompt(f"category ({' | '.join(CATEGORIES)})")
    if category not in CATEGORIES:
        raise typer.BadParameter(f"unknown category {category!r}")
    question = typer.prompt("question")
    gold = "UNANSWERABLE" if category == "unanswerable" else typer.prompt("gold answer")
    terms = (
        "" if category == "unanswerable" else typer.prompt("key terms, comma-separated", default="")
    )
    prefix = "test" if split is SplitName.test else "real"
    q = RealQuestion(
        id=f"{prefix}-{len(qs) + 1:03d}",
        category=category,
        question=question,
        gold_answer=gold,
        key_terms=[t.strip() for t in terms.split(",") if t.strip()],
        as_of=_date.today(),
        status="verified",
        origin="user",
        split=split.value,
    )
    save_questions([*qs, q], questions_file(split.value))
    typer.echo(f"added {q.id}")


@app.command("real-slice")
def real_slice_cmd(
    systems: Annotated[
        str,
        typer.Option(
            help="granola (Granola's real agent), c, c_on_abstain, c_always (Concentrate with "
            "the transcript fallback), b0, b3; comma-separated."
        ),
    ] = "granola,c,b0,b3",
    db: Annotated[Path, typer.Option()] = REAL_DB,
    k: Annotated[int, typer.Option(help="Meetings b0 retrieves.")] = 2,
    seed: Annotated[int, typer.Option()] = 42,
    budget: Annotated[float, typer.Option(help="Hard cap on API spend (USD).")] = 5.0,
    estimate_only: Annotated[bool, typer.Option("--estimate")] = False,
    offline: Annotated[
        bool, typer.Option(help="Cached LLM results / recorded Granola only.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    qids_from: Annotated[
        Path | None,
        typer.Option(help="Only the questions answered in this earlier report (.jsonl)."),
    ] = None,
    split: SplitOpt = SplitName.dev,
) -> None:
    """Answer the verified real questions with every system and judge them (judge.v1)."""
    import json as _json
    from dataclasses import asdict

    from concentrate.agents.baselines import AgenticSystem, MeetingRetriever, System, TopKSystem
    from concentrate.agents.concentrate_system import ConcentrateSystem
    from concentrate.agents.retrieval import SentenceTransformerEmbedder
    from concentrate.evals.real_slice import (
        REAL_DIR,
        GranolaAgentSystem,
        aggregate_only,
        load_split,
        run_slice,
    )
    from concentrate.evals.runner import estimate

    names = [s.strip() for s in systems.split(",") if s.strip()]
    notes = _real_notes(db)
    questions = [q for q in load_split(split.value) if q.status == "verified"]
    if qids_from is not None:  # e.g. re-run a variant on exactly the questions of a past run
        keep = {_json.loads(line)["qid"] for line in qids_from.read_text().splitlines() if line}
        questions = [q for q in questions if q.id in keep]
    if not questions:
        raise typer.BadParameter("no verified questions: run `concentrate real-kit review` first")
    directory = REAL_DIR / "directory.json"
    if any(n.startswith("c") for n in names) and not directory.exists():
        raise typer.BadParameter(f"{directory} missing: run `concentrate real-kit directory`")
    client = _real_client(offline)
    embedder = (
        SentenceTransformerEmbedder()
        if {"c", "c_on_abstain", "c_always", "b0"} & set(names)
        else None
    )
    built: list[System] = []
    concentrate: ConcentrateSystem | None = None
    with ExitStack() as stack:
        for n in names:
            if n == "granola":
                caller = None
                if not offline and not estimate_only:
                    from concentrate.sources.mcp_transport import LiveMcpCaller

                    caller = stack.enter_context(LiveMcpCaller())
                built.append(GranolaAgentSystem(caller))
            elif n in ("c", "c_on_abstain", "c_always"):  # c_*: transcript fallback modes
                mode = n.removeprefix("c_") if n != "c" else "off"
                variant = ConcentrateSystem(
                    client,
                    embedder=embedder,
                    directory_path=directory,
                    cache_root=REAL_DIR / "cache" / "concentrate",
                    fallback=mode,
                    name=None if n == "c" else f"concentrate_{mode}",
                )
                concentrate = concentrate or variant
                built.append(variant)
            elif n == "b0":
                built.append(TopKSystem(client, MeetingRetriever(notes, embedder), k))
            elif n == "b3":
                built.append(AgenticSystem(client, notes))
            else:
                raise typer.BadParameter(f"unknown system {n!r}")
        ests = estimate(built, questions, client.model, notes=notes)  # type: ignore[arg-type]
        typer.echo(f"{len(notes)} meetings · {len(questions)} verified questions · {client.model}")
        for e in ests:
            ingest = f", ingest ${e.ingest_usd:.2f}" if e.ingest_usd else ""
            typer.echo(f"  {e.system:<14} ≈ ${e.usd:5.2f} (incl. judge{ingest})")
        total = sum(e.usd for e in ests)
        typer.echo(f"  estimated total ≈ ${total:.2f} (before cache reuse) · cap ${budget:.2f}")
        if estimate_only:
            return
        if total > BUDGET_CONFIRM_USD and not offline and not yes:
            typer.confirm(
                f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
        ingested: dict[str, object] = {}
        if concentrate is not None:
            stats = concentrate.ingest(notes)
            ingested["concentrate"] = {k2: v for k2, v in asdict(stats).items() if k2 != "detail"}
            for s in built:  # fallback variants share the (now cached) store
                if isinstance(s, ConcentrateSystem) and s is not concentrate:
                    s.ingest(notes)

        def spent() -> float:
            extra = concentrate.ingest_ledger.spent_usd() if concentrate is not None else 0.0
            return client.ledger.spent_usd() + extra

        cats: dict[str, int] = {}
        for q in questions:
            cats[q.category] = cats.get(q.category, 0) + 1
        report = run_slice(
            built,
            questions,
            client,
            seed=seed,
            budget_usd=budget,
            out_dir=REAL_DIR / "reports",
            spent=spent,
            split=split.value,
            meta={
                "meetings": len(notes),
                "k": k,
                "category_counts": cats,
                "ingest": ingested,
                "embedder": getattr(embedder, "name", None),
            },
        )
    agg = REAL_DIR / "reports" / f"{report['meta']['date']}.aggregate.json"
    agg.write_text(_json.dumps(aggregate_only(report), indent=2, default=str))
    typer.echo(Path(report["paths"]["md"]).read_text())
    typer.echo(f"spent ${spent():.2f} · report: {report['paths']['md']} · aggregate: {agg}")


@real_kit.command("probe")
def real_probe(
    probes: Annotated[Path, typer.Argument(help="JSON list of probes (keep it in data/real/).")],
) -> None:
    """Run a read-only probe of Granola's MCP; raw output -> data/real/probe/<stamp>.jsonl."""
    import json as _json
    import statistics
    from datetime import UTC, datetime

    from concentrate.evals.real_slice import REAL_DIR, run_probes
    from concentrate.sources.mcp_transport import LiveMcpCaller

    spec = _json.loads(probes.read_text())
    out = REAL_DIR / "probe" / f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}.jsonl"
    with LiveMcpCaller() as live:
        res = run_probes(live, spec, out)
    by_tool: dict[str, list[float]] = {}
    for r in res:
        if not r["error"]:
            by_tool.setdefault(r["tool"], []).append(r["latency_s"])
    for tool, xs in sorted(by_tool.items()):
        typer.echo(
            f"{tool:<24} n={len(xs):>2} median {statistics.median(xs):5.1f}s  max {max(xs):5.1f}s"
        )
    typer.echo(f"errors: {sum(bool(r['error']) for r in res)} · raw: {out}")


@real_kit.command("serve")
def real_serve(
    db: Annotated[Path, typer.Option()] = REAL_DB,
    store: Annotated[Path | None, typer.Option(help="Build dir; default: newest complete.")] = None,
    embeddings: Annotated[bool, typer.Option(help="Dense embeddings, as in the eval.")] = True,
) -> None:
    """Serve Concentrate's memory of the REAL meetings over MCP stdio (WS-D's read-only server).

    Same tools as `mcp-serve`; ask_memory(answer=true) caches under data/real/ (private).
    """
    from concentrate.evals.real_slice import open_real_memory
    from concentrate.mcp_server.server import build_server
    from concentrate.mcp_server.service import Answerer

    embedder = None
    if embeddings:
        from concentrate.agents.retrieval import SentenceTransformerEmbedder

        embedder = SentenceTransformerEmbedder()
    try:
        svc = open_real_memory(
            _real_notes(db), store_dir=store, embedder=embedder, answerer=Answerer(_real_client())
        )
    except FileNotFoundError as exc:
        typer.echo(
            f"{exc}\nBuild it first: `concentrate real-slice --systems c` (or ingest).", err=True
        )
        raise typer.Exit(1) from exc
    logging.getLogger(__name__).warning(
        "concentrate MCP: serving real memory %s (%d meetings)",
        svc.info["store"],
        len(svc.meetings),
    )
    build_server(svc).run("stdio")


@real_kit.command("spotcheck")
def real_spotcheck(
    reports: Annotated[list[Path], typer.Argument(help="Report .jsonl files to combine.")],
    split: SplitOpt = SplitName.dev,
    product: Annotated[
        list[str] | None,
        typer.Option("--product", help="Only these products/systems (repeatable)."),
    ] = None,
) -> None:
    """Write data/real/reports/spotcheck[.test].md: where any system disagrees with the gold."""
    import json as _json

    from concentrate.evals.real_slice import REAL_DIR, load_split, spotcheck_markdown

    rows = [_json.loads(x) for p in reports for x in p.read_text().splitlines() if x]
    if product:
        rows = [
            r
            for r in rows
            if any(r["system"] == p or r["system"].startswith(f"{p}_") for p in product)
        ]
    out = REAL_DIR / "reports" / ("spotcheck.md" if split is SplitName.dev else "spotcheck.test.md")
    out.write_text(spotcheck_markdown(rows, load_split(split.value)))
    typer.echo(f"wrote {out} (private)")


# -- black-box track (P1-B): any meeting-memory MCP, public interfaces only ----------------------
# Products: `granola` (query_granola_meetings), `concentrate` (ask_memory over stdio), or a JSON
# spec (docs/benchmark/product.example.json). Answers, questions and per-question reports stay
# under data/real/; only the aggregate passes the committable-report check.


@real_kit.command("import-test")
def real_import_test(
    md: Annotated[
        Path, typer.Argument(help="The filled-in test template (keep it in data/real/).")
    ],
) -> None:
    """Parse hand-written Q/A/KEY/KIND blocks into the held-out test split (re-runs skip dupes)."""
    from datetime import date as _date

    from concentrate.blackbox.kit import TemplateError, parse_test_markdown
    from concentrate.evals.real_slice import (
        CATEGORIES,
        TEST_QUESTIONS_FILE,
        RealQuestion,
        load_split,
        save_questions,
    )

    try:
        parsed = parse_test_markdown(md.read_text(), CATEGORIES)
    except TemplateError as exc:
        raise typer.BadParameter(f"{md}: {exc}") from exc
    qs = load_split("test")
    seen = {q.question.strip().lower() for q in qs}
    new: list[RealQuestion] = []
    for p in parsed:
        if p["question"].strip().lower() in seen:
            continue
        seen.add(p["question"].strip().lower())
        new.append(
            RealQuestion(
                id=f"test-{len(qs) + len(new) + 1:03d}",
                as_of=_date.today(),
                status="verified",
                origin="user",
                split="test",
                **p,
            )
        )
    save_questions([*qs, *new], TEST_QUESTIONS_FILE)
    cats: dict[str, int] = {}
    for q in [*qs, *new]:
        cats[q.category] = cats.get(q.category, 0) + 1
    typer.echo(
        f"{len(parsed)} blocks parsed · {len(new)} new · test split now {len(qs) + len(new)}"
    )
    typer.echo("  " + ", ".join(f"{c} {n}" for c, n in sorted(cats.items())))


@real_kit.command("run")
def real_run(
    product: Annotated[
        list[str],
        typer.Option("--product", help="granola, concentrate, or a spec name/path (repeatable)."),
    ],
    split: SplitOpt = SplitName.dev,
    seed: Annotated[int, typer.Option()] = 42,
    budget: Annotated[float, typer.Option(help="Hard cap on judge spend (USD).")] = 2.0,
    estimate_only: Annotated[bool, typer.Option("--estimate")] = False,
    offline: Annotated[
        bool, typer.Option(help="Recorded answers and cached judge verdicts only.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    commit_to: Annotated[
        Path | None,
        typer.Option(help="Also write the aggregate here (checked: no per-question content)."),
    ] = None,
) -> None:
    """Ask every verified question of the split to each product over MCP, judge (judge.v1)."""
    from concentrate.blackbox.kit import ProductSystem, write_committable
    from concentrate.blackbox.products import open_product, product_name
    from concentrate.evals.real_slice import REAL_DIR, load_split, run_slice
    from concentrate.evals.runner import estimate

    questions = [q for q in load_split(split.value) if q.status == "verified"]
    if not questions:
        raise typer.BadParameter(f"no verified {split.value} questions (status / import-test)")
    client = _real_client(offline)
    with ExitStack() as stack:
        systems = []
        for p in product:
            name = product_name(p)
            recorded = ProductSystem(name, None)
            missing = len(questions) - recorded.recorded(questions)
            live = open_product(p, stack) if missing and not (offline or estimate_only) else None
            systems.append(ProductSystem(name, live))
            typer.echo(f"  {name:<14} {missing} of {len(questions)} to ask live")
        skipped = 0
        if offline:  # score only what every product has a recorded answer for, and say so
            kept = [q for q in questions if all(s.recorded([q]) for s in systems)]
            skipped, questions = len(questions) - len(kept), kept
            typer.echo(f"  offline: {skipped} questions skipped (no recorded answer)")
            if not questions:
                raise typer.BadParameter("nothing recorded yet: run once without --offline")
        ests = estimate(systems, questions, client.model)  # type: ignore[arg-type]
        total = sum(e.usd for e in ests)
        typer.echo(
            f"{len(questions)} {split.value} questions · judge {client.model} "
            f"≈ ${total:.2f} before cache reuse · cap ${budget:.2f}"
        )
        if estimate_only:
            return
        if total > BUDGET_CONFIRM_USD and not offline and not yes:
            typer.confirm(
                f"Estimated ${total:.2f} exceeds ${BUDGET_CONFIRM_USD:.0f}. Proceed?", abort=True
            )
        cats: dict[str, int] = {}
        for q in questions:
            cats[q.category] = cats.get(q.category, 0) + 1
        report = run_slice(
            systems,  # type: ignore[arg-type]
            questions,
            client,
            seed=seed,
            budget_usd=budget,
            out_dir=REAL_DIR / "reports",
            split=split.value,
            meta={
                "products": [s.name for s in systems],
                "category_counts": cats,
                "skipped_unrecorded": skipped,
            },
        )
    private = [t for q in questions for t in (q.question, q.gold_answer)]
    private += [s.answer(q).text for s in systems for q in questions if s.recorded([q])]
    agg = write_committable(
        report, REAL_DIR / "reports" / f"{report['meta']['date']}.aggregate.json", private
    )
    if commit_to is not None:
        write_committable(report, commit_to, private)
    typer.echo(Path(report["paths"]["md"]).read_text())
    typer.echo(f"spent ${client.ledger.spent_usd():.2f} · report: {report['paths']['md']}")
    typer.echo(f"aggregate (safe to commit): {commit_to or agg}")


# -- figures (P1-F) ----------------------------------------------------------------------------

_REAL_REPORTS = Path("data/real/reports")


@app.command("figures")
def figures_cmd(
    stamp: Annotated[str, typer.Argument(help="Synthetic report stamp, e.g. 20261103T002456Z.")],
    out: Annotated[Path, typer.Option(help="Output directory.")] = Path("docs/figures"),
    check: Annotated[
        bool, typer.Option("--check", help="Don't write; fail if committed figures differ.")
    ] = False,
    refresh_real: Annotated[
        bool,
        typer.Option(
            "--refresh-real",
            help="Rebuild docs/figures/data/real_slice.json from the private data/real reports.",
        ),
    ] = False,
    real_main: Annotated[Path, typer.Option(help="Real run: Granola agent + Concentrate.")] = (
        _REAL_REPORTS / "20261103T004019Z.jsonl"
    ),
    real_topk: Annotated[Path, typer.Option(help="Real run: b0 top-k on all questions.")] = (
        _REAL_REPORTS / "20261103T013240Z.jsonl"
    ),
    real_fallback: Annotated[Path, typer.Option(help="Real fallback replay (DEV).")] = (
        _REAL_REPORTS / "fallback_dev_replay.jsonl"
    ),
) -> None:
    """Render every results figure as deterministic SVG into docs/figures/ (PLAN.md section 5)."""
    import json as _json

    from concentrate.evals import figures as F

    if refresh_real:
        snap = F.snapshot_real(real_main, real_topk, real_fallback)
        F.REAL_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        F.REAL_SNAPSHOT.write_text(_json.dumps(snap, indent=2, sort_keys=True) + "\n")
        typer.echo(f"wrote {F.REAL_SNAPSHOT} (aggregates only)")
    rendered, skipped = F.render(F.load_inputs(stamp))
    for note in skipped:
        typer.echo(f"skipped {note}", err=True)
    if check:
        bad = F.check(rendered, out)
        if bad:
            typer.echo(
                "figures out of date: " + ", ".join(bad) + f"\nrun `concentrate figures {stamp}`",
                err=True,
            )
            raise typer.Exit(1)
        typer.echo(f"figures: {len(rendered)} files match {out}")
        return
    for p in F.write(rendered, out):
        typer.echo(f"wrote {p}")


# -- notes-sufficient vs transcript-needed (Phase 2, $0) ---------------------------------------


@app.command("notes-vs-transcript")
def notes_vs_transcript_cmd(
    seed: Annotated[int, typer.Option(help="Bootstrap seed.")] = 0,
    real: Annotated[
        bool, typer.Option(help="Recompute the real-slice aggregates (needs private data/real).")
    ] = True,
) -> None:
    """Tag questions notes-sufficient / transcript-needed and re-analyse saved runs by tag ($0)."""
    from concentrate.evals import notes_vs_transcript as N

    for p in N.write(seed, real=real):
        typer.echo(f"wrote {p}")


# -- deletion simulation (P2-D) ----------------------------------------------------------------


@app.command("deletion-sim")
def deletion_sim_cmd(
    systems: Annotated[
        str,
        typer.Option(
            help="granola (A), notes_only (B), notes_facts (C), notes_facts_fallback (D); "
            "comma-separated. See docs/benchmark/DELETION_SIM.md."
        ),
    ] = "granola,notes_only,notes_facts,notes_facts_fallback",
    split: SplitOpt = SplitName.dev,
    tags: Annotated[
        Path | None, typer.Option(help="P2-T's question tag file (JSON or JSONL); optional.")
    ] = None,
    judge2: Annotated[
        str, typer.Option(help="Second judge model (same judge.v1 prompt), or 'none'.")
    ] = "claude-sonnet-5-5",
    db: Annotated[Path, typer.Option()] = REAL_DB,
    seed: Annotated[int, typer.Option()] = 42,
    budget: Annotated[float, typer.Option(help="Hard cap on API spend (USD).")] = 5.0,
    quote_only: Annotated[
        bool,
        typer.Option(
            "--quote",
            help="Exact cost quote (free token counting, cached LLM results only), no spend.",
        ),
    ] = False,
    test_n: Annotated[
        int, typer.Option(help="--quote: test questions to price when none are imported yet.")
    ] = 30,
    offline: Annotated[
        bool, typer.Option(help="Cached LLM results / recorded Granola only.")
    ] = False,
    yes: Annotated[bool, typer.Option("--yes")] = False,
    commit_to: Annotated[
        Path | None,
        typer.Option(help="Also write the aggregate here (checked: no per-question content)."),
    ] = None,
) -> None:
    """Simulate transcript deletion on the real questions: Granola vs notes-only vs notes+facts
    vs notes+facts+fallback, same answer model and judge. Nothing paid runs without --yes or a
    confirmation; start with --quote."""
    import functools
    import json as _json
    import statistics

    from concentrate.agents.baselines import Answer, MeetingRetriever, System, TopKSystem
    from concentrate.agents.concentrate_system import COMPLETE_MARKER, ConcentrateSystem
    from concentrate.agents.retrieval import SentenceTransformerEmbedder
    from concentrate.blackbox.kit import write_committable
    from concentrate.evals import deletion_sim as D
    from concentrate.evals.real_slice import (
        REAL_DIR,
        GranolaAgentSystem,
        load_split,
    )
    from concentrate.llm.client import count_tokens
    from concentrate.memory.fallback import says_not_covered

    names = [s.strip() for s in systems.split(",") if s.strip()]
    unknown = [n for n in names if n not in D.CONDITIONS]
    if unknown:
        raise typer.BadParameter(f"unknown conditions {unknown}; choose from {D.CONDITIONS}")
    notes = _real_notes(db)
    questions = [q for q in load_split(split.value) if q.status == "verified"]
    if not questions and not (quote_only and split is SplitName.test):
        raise typer.BadParameter(
            f"no verified {split.value} questions"
            + (": `concentrate real-kit import-test data/real/test_questions.md`" if split else "")
        )
    directory = REAL_DIR / "directory.json"
    client = _real_client(offline or quote_only)
    j2 = (
        None
        if judge2 == "none"
        else LLMClient(
            model=judge2, cache_dir=REAL_DIR / "cache" / "llm", offline=offline or quote_only
        )
    )
    if j2 is not None and j2.model == client.model:
        raise typer.BadParameter(f"the second judge must differ from the primary ({client.model})")
    needs_memory = {"notes_facts", "notes_facts_fallback"} & set(names)
    embedder = SentenceTransformerEmbedder() if needs_memory or quote_only else None
    tag_map = D.load_tags(tags)
    with ExitStack() as stack:
        built: list[System] = []
        for n in names:
            if n == "granola":
                caller = None
                if not offline and not quote_only:
                    from concentrate.sources.mcp_transport import LiveMcpCaller

                    caller = stack.enter_context(LiveMcpCaller())
                built.append(GranolaAgentSystem(caller))
            elif n == "notes_only":
                built.append(D.NotesOnlySystem(client, notes))
            else:
                cls = D.NotesFactsSystem if n == "notes_facts" else D.NotesFactsFallbackSystem
                s = cls(
                    client,
                    embedder=embedder,
                    directory_path=directory,
                    cache_root=REAL_DIR / "cache" / "concentrate",
                )
                if not (s.store_dir(notes) / COMPLETE_MARKER).exists():
                    raise typer.BadParameter(
                        "no complete Concentrate build of data/real.db (building it costs money): "
                        "run `concentrate real-slice --systems c --estimate` and ask first"
                    )
                s.ingest(notes)  # cached build: free
                built.append(s)

        if quote_only:
            counter = functools.partial(
                count_tokens, model=client.model, cache_dir=client.cache_dir
            )
            dev = [q for q in load_split("dev") if q.status == "verified"]
            # Calibrate on the real-slice calls already cached: Concentrate (c) and top-k (b0).
            ref_c = ConcentrateSystem(
                client,
                embedder=embedder,
                directory_path=directory,
                cache_root=REAL_DIR / "cache" / "concentrate",
            )
            ref_c.ingest(notes)
            ref_b0 = TopKSystem(client, MeetingRetriever(notes, embedder), 2)
            pairs, texts, fired = [], [], []
            for ref in (ref_c, ref_b0):
                for q in dev:
                    req = ref.turn_request(q)
                    done = D.cached_turn(client, req)
                    if done is None:
                        continue
                    user = req.messages[0]["content"]
                    pairs.append(
                        (f"{req.system}\n\n{user}", done.total_input_tokens, done.output_tokens)
                    )
                    ans = Answer.model_validate_json(done.text)
                    texts.append(ans.answer)
                    if ref is ref_c:
                        fired.append(says_not_covered(ans.answer, ans.abstain))
            cal = D.calibrate(
                pairs,
                D.judge_cache_pairs(client.cache_dir, client.model),
                texts,
                counter,
                fire_rate=statistics.fmean(fired) if fired else 0.5,
            )
            granola = next((s for s in built if isinstance(s, GranolaAgentSystem)), None)

            def recorded(q: object) -> tuple[str, bool] | None:
                if granola is None or not granola.recorded([q]):
                    return None
                a = granola.answer(q)
                return a.text, bool(a.abstained)

            lines = []
            shared: set[Path] = set()  # identical requests across conditions are paid once
            by_cond = dict(zip(names, built, strict=True))
            for n in names:
                if questions:
                    lines.append(
                        D.quote_condition(
                            by_cond[n],
                            n,
                            questions,
                            client,
                            j2,
                            counter,
                            cal,
                            split=split.value,
                            granola_answer=recorded,
                            shared=shared,
                        )
                    )
                else:  # test split not imported yet: price per dev question, scaled
                    base = D.quote_condition(
                        by_cond[n],
                        n,
                        dev,
                        client,
                        j2,
                        counter,
                        cal,
                        split="dev",
                        granola_answer=None,
                        shared=shared,
                    )
                    lines.append(D.scale_line(base, test_n, "test"))
                if n == "granola":
                    missing = len(questions) - granola.recorded(questions) if granola else 0
                    lines[-1].note = (
                        f"Granola answers $0 to us (hosted); {missing} to ask live over MCP"
                        if questions
                        else f"Granola answers $0 to us; all {test_n} asked live over MCP"
                    )
            md = D.quote_markdown(lines, cal, client.model, j2.model if j2 else "none")
            out = REAL_DIR / "reports" / f"deletion_quote.{split.value}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md)
            typer.echo(md)
            typer.echo(f"quote written: {out} (no money spent: token counting is free)")
            return

        if not yes and not offline:
            typer.confirm(
                f"Run {len(built)} conditions on {len(questions)} {split.value} questions "
                f"(cap ${budget:.2f}; see `--quote` first)?",
                abort=True,
            )
        memory = next((s for s in built if isinstance(s, ConcentrateSystem)), None)

        def spent() -> float:
            extra = memory.ingest_ledger.spent_usd() if memory is not None else 0.0
            more = j2.ledger.spent_usd() if j2 is not None else 0.0
            return client.ledger.spent_usd() + extra + more

        cats: dict[str, int] = {}
        for q in questions:
            cats[q.category] = cats.get(q.category, 0) + 1
        report = D.run_deletion_sim(
            built,
            questions,
            client,
            judge2=j2,
            tags=tag_map,
            seed=seed,
            budget_usd=budget,
            out_dir=REAL_DIR / "reports",
            split=split.value,
            spent=spent,
            meta={"meetings": len(notes), "category_counts": cats, "track": "deletion-sim"},
        )
    agg = {**report, "metrics": {"judge1": report["metrics"], "deletion": report["deletion"]}}
    # "UNANSWERABLE" is a placeholder gold, not content (it would match the category name).
    private = [q.question for q in questions] + [
        q.gold_answer for q in questions if not q.unanswerable
    ]
    for line in Path(report["paths"]["jsonl"]).read_text().splitlines():
        private.append(_json.loads(line)["answer"]["answer"])
    path = REAL_DIR / "reports" / f"{report['meta']['date']}.deletion.aggregate.json"
    write_committable(agg, path, private)
    if commit_to is not None:
        write_committable(agg, commit_to, private)
    typer.echo(_json.dumps(report["deletion"]["paired"], indent=1))
    typer.echo(f"spent ${spent():.2f} · report: {report['paths']['md']} · aggregate: {path}")


# -- agent-interface quality track --------------------------------------------------------------


@app.command("agent-interface")
def agent_interface_cmd(
    products: Annotated[
        str,
        typer.Option(
            help="Comma-separated: granola, concentrate, or any product recorded by "
            "`real-kit run --product <name>` (scored against --report)."
        ),
    ] = "granola,concentrate",
    report: Annotated[
        Path | None,
        typer.Option(help="Report .jsonl with verdicts, for products other than the built-ins."),
    ] = None,
    tags: Annotated[Path, typer.Option(help="Question tag file.")] = Path(
        "data/real/tags.p2t.json"
    ),
    db: Annotated[Path, typer.Option()] = REAL_DB,
    sample: Annotated[int, typer.Option(help="Questions to re-ask for stability.")] = 12,
    repeats: Annotated[int, typer.Option(help="Extra asks per sampled question.")] = 2,
    seed: Annotated[int, typer.Option()] = 42,
    live: Annotated[
        str | None,
        typer.Option(
            help="Record missing repeats for this one product through its read-only adapter. "
            "Free for Granola; Concentrate's ask_memory(answer=true) costs an LLM call each."
        ),
    ] = None,
    max_calls: Annotated[int, typer.Option(help="Hard cap on live calls.")] = 24,
    out: Annotated[Path, typer.Option(help="Committable aggregate.")] = Path(
        "evals/reports/agent-interface.aggregate.json"
    ),
) -> None:
    """Score meeting products' MCP answers on the agent-interface track: four answer types,
    evidence granularity, payload signals, stability, latency. No LLM calls unless --live names
    a product that makes them; see docs/benchmark/AGENT_INTERFACE.md."""
    import json as _json
    import subprocess
    from contextlib import ExitStack
    from datetime import UTC, datetime

    from concentrate.blackbox.kit import check_committable
    from concentrate.evals import agent_interface as AI
    from concentrate.evals.deletion_sim import load_tags
    from concentrate.evals.real_slice import load_questions

    names = [n.strip() for n in products.split(",") if n.strip()]
    if live == "concentrate":
        raise typer.BadParameter(
            "Concentrate repeats cost LLM calls; price them first (budget rule)"
        )
    entries = []
    for n in names:
        if n == "granola":
            entries.append(AI.granola_entry())
        elif n == "concentrate":
            entries.append(AI.concentrate_entry())
        elif report is None:
            raise typer.BadParameter(f"{n}: pass --report with its judged black-box report")
        else:
            entries.append(AI.recorded_entry(n, report))
    questions = load_questions()
    tag_map = load_tags(tags)
    calls = 0
    if live is not None:
        from concentrate.blackbox.products import open_product

        e = next((x for x in entries if x.product == AI.product_key(live)), None)
        if e is None:
            raise typer.BadParameter(f"--live {live}: not among --products")
        rows = AI.build_rows(e, questions, tag_map, db)
        with ExitStack() as stack:
            prod = open_product(live, stack)
            calls = AI.record_repeats(
                prod, questions, AI.stratified_sample(rows, sample, seed), repeats, max_calls
            )
    results = {}
    for e in entries:
        rows, stab, _ = AI.score_entry(e, questions, tag_map, db, sample, repeats, seed)
        results[e.product] = (e, rows, stab)
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    meta = {
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": sha.stdout.strip(),
        "split": "real dev",
        "seed": seed,
        "sample": sample,
        "repeats": repeats,
        "llm_calls": 0,
        "live_calls_this_run": calls,
    }
    pairs = []
    if {"granola", "concentrate"} <= set(results):
        # Same judge run for both: the real-slice report holds Granola's answers too.
        g = AI.granola_entry(AI.CONCENTRATE_REPORT)
        pairs.append(("granola", "concentrate", g, results["concentrate"][0]))
    agg = AI.aggregate(results, meta, pairs, questions, tag_map, db)
    private = [q.question for q in questions] + [
        q.gold_answer for q in questions if not q.unanswerable
    ]
    private += [a.text for e, _, _ in results.values() for a in e.answers.values()]
    check_committable(agg, private)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(agg, indent=2) + "\n")
    for n, b in agg["products"].items():
        ft = b["four_types"]["overall"]
        typer.echo(f"{n}: n={b['questions']} " + " ".join(f"{k}={v['k']}" for k, v in ft.items()))
    typer.echo(f"live calls: {calls} · aggregate: {out}")
# -- notes faithfulness track: are a system's notes grounded in the transcript? -------------


class FaithSystem(StrEnum):
    granola = "granola"
    northwind_synth = "northwind-synth"


@app.command("notes-faithfulness")
def notes_faithfulness_cmd(
    system: Annotated[FaithSystem, typer.Option(help="Which system's notes to score.")] = (
        FaithSystem.granola
    ),
    meetings: Annotated[int, typer.Option(help="Synthetic only: meetings to sample.")] = 4,
    seed: Annotated[int, typer.Option(help="Seed for the 30% re-check (and synthetic pick).")] = 0,
    quote: Annotated[bool, typer.Option(help="Count tokens and price the run; spend $0.")] = False,
    budget: Annotated[float, typer.Option(help="Refuse to run above this quote (USD).")] = 0.80,
    yes: Annotated[bool, typer.Option(help="Skip the confirmation prompt.")] = False,
    db: Annotated[Path, typer.Option(help="Private store of the real sync (granola).")] = Path(
        "data/real.db"
    ),
    fixtures: Annotated[Path, typer.Option(help="Synthetic corpus.")] = Path(
        "data/fixtures/northwind"
    ),
) -> None:
    """Score one system's meeting notes against their transcripts (notes faithfulness track).

    Private systems (granola) write per-claim output and the owner's spot-check list to
    data/real/ only, and the aggregate is privacy-checked before it is committed. Synthetic
    systems write per-claim output next to the aggregate.
    """
    import json as _json
    import re as _re
    import subprocess
    from datetime import UTC, datetime

    from concentrate.blackbox.kit import check_committable
    from concentrate.evals import faithfulness as F
    from concentrate.evals.runner import load_corpus
    from concentrate.llm.client import PRICING, count_tokens

    if system is FaithSystem.granola:
        entry = F.granola_system(db)
    else:
        entry = F.northwind_system(load_corpus(fixtures), meetings, seed)
    ms = F.prepare(entry)
    reqs = F.requests(ms, seed)
    # Output tokens per claim: ~65 observed for both models (reason + label + lines); x1.5 pad.
    out_per_claim = {F.CHECK_MODEL: 100, F.RECHECK_MODEL: 100}
    cache = Path(os.environ.get("CONCENTRATE_CACHE", "data/cache/llm"))
    lines, total = [], 0.0
    tokens: dict[str, dict[str, int]] = {}
    for m, req, _, kind in reqs:
        model = F.CHECK_MODEL if kind == "check" else F.RECHECK_MODEL
        sys_t, user_t = count_tokens([req.system, req.user], model, cache)
        n_claims = len(req.user.splitlines()) - 1
        p_in, p_out = PRICING[model]
        est_out = out_per_claim[model] * n_claims
        usd = (sys_t * p_in * 1.25 + user_t * p_in + est_out * p_out) / 1e6
        hit = LLMClient(model=model).is_cached(req)
        total += 0.0 if hit else usd
        t = tokens.setdefault(model, {"system_cached": 0, "user": 0, "est_output": 0})
        t["system_cached"] += sys_t
        t["user"] += user_t
        t["est_output"] += est_out
        lines.append(
            f"{m.label} {kind:<7} {model:<18} claims={n_claims:>3} system={sys_t:>6} "
            f"user={user_t:>5} est_out={est_out:>5} ${usd:.4f}{' (cached: $0)' if hit else ''}"
        )
    typer.echo("\n".join(lines))
    typer.echo(f"quote: ${total:.4f} new spend (input exact via count_tokens; output estimated)")
    if quote:
        return
    if total > budget:
        typer.echo(f"quote ${total:.2f} exceeds --budget ${budget:.2f}", err=True)
        raise typer.Exit(1)
    if not yes and total > 0:
        typer.confirm(f"Spend about ${total:.2f}?", abort=True)
    clients = {mod: LLMClient(model=mod) for mod in (F.CHECK_MODEL, F.RECHECK_MODEL)}
    missing: dict[str, int] = {}
    for m, req, sample, kind in reqs:
        client = clients[F.CHECK_MODEL if kind == "check" else F.RECHECK_MODEL]
        reply = client.structured(req, F.AuditReply)
        left = F.apply_verdicts(sample, reply, len(m.note.utterances), kind)
        if left:
            missing[f"{m.label}.{kind}"] = len(left)
    records = [r for m in ms for r in m.records]
    agg = F.aggregate(records, {m.label: m.split for m in ms})
    git = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    spent = sum(c.ledger.spent_usd() for c in clients.values())
    # What every call cost when it was made (cache hits replay their original cost).
    cost = sum(r.cost_usd for c in clients.values() for r in c.ledger.records)
    report = {
        "meta": {
            "track": "notes-faithfulness",
            "system": entry.name,
            "system_description": entry.description,
            "date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_sha": git.stdout.strip(),
            "check_model": F.CHECK_MODEL,
            "check_effort": "low",
            "recheck_model": F.RECHECK_MODEL,
            "recheck_fraction": F.RECHECK_FRACTION,
            "strict_pass": f"{F.RECHECK_MODEL} on every claim",
            "prompt": F.PROMPT_ID,
            "seed": seed,
            "meetings": len(ms),
            "pool_meetings": len(entry.pool),
            "quote_usd": round(total, 4),
            "cost_usd": round(cost, 4),
            "spent_this_invocation_usd": round(spent, 4),
            "tokens_quoted": tokens,
            "missing_verdicts": missing,
            "by_stage": {k: v for c in clients.values() for k, v in c.ledger.by_stage().items()},
        },
        **agg,
    }
    out_dir = Path("evals/reports/notes-faithfulness")
    out_dir.mkdir(parents=True, exist_ok=True)
    claims_dir = Path("data/real/notes_faithfulness") if entry.private else out_dir
    claims_dir.mkdir(parents=True, exist_ok=True)
    if entry.private:
        private = F.private_texts(ms)
        check_committable(report, private)
        blob = _json.dumps(report).lower()
        names = {
            w.lower()
            for m in ms
            for p in (m.note.owner, *m.note.attendees)
            for w in _re.findall(r"[A-Za-z]{3,}", p.name or "")
        }
        leaked = sorted(n for n in names if _re.search(rf"\b{_re.escape(n)}\b", blob))
        if leaked:
            typer.echo(f"refusing to write: aggregate has {len(leaked)} attendee name(s)", err=True)
            raise typer.Exit(1)
        spot = F.spotcheck_markdown(records, {m.label: m.note for m in ms}, seed)
        (claims_dir / f"{entry.name}.spotcheck.md").write_text(spot)
    with (claims_dir / f"{entry.name}.claims.jsonl").open("w") as fh:
        for r in records:
            fh.write(_json.dumps(r.to_json(), ensure_ascii=False) + "\n")
    agg_path = out_dir / f"{entry.name}.aggregate.json"
    agg_path.write_text(_json.dumps(report, indent=2) + "\n")
    typer.echo(_json.dumps(agg["overall"]["ungrounded"]))
    typer.echo(f"spent ${spent:.4f} · aggregate {agg_path} · per-claim {claims_dir}")
# -- cross-product pairing (docs/benchmark/PRODUCTS.md) ----------------------------------------

PAIR_OVERRIDES = Path("data/real/pairs.override.json")
PAIRS_OUT = Path("data/real/pairs.json")


@app.command("pair")
def pair_cmd(
    granola_db: Annotated[Path, typer.Option(help="Granola sync (product A).")] = Path(
        "data/real.db"
    ),
    wispr_db: Annotated[Path, typer.Option(help="Wispr Flow sync (product B).")] = Path(
        "data/real_wispr.db"
    ),
    overrides: Annotated[Path, typer.Option(help="Manual pairs / never-pairs.")] = PAIR_OVERRIDES,
    out: Annotated[Path, typer.Option(help="Where to write the pairing (private).")] = PAIRS_OUT,
    window_min: Annotated[float, typer.Option(help="Start-time window, ± minutes.")] = 10.0,
) -> None:
    """Pair meetings recorded by both Granola and Wispr Flow. Prints IDs and scores only."""
    from datetime import timedelta

    from concentrate.blackbox.pairing import Overrides, pair_meetings

    for db in (granola_db, wispr_db):
        if not db.exists():
            raise typer.BadParameter(f"{db} not found: sync it first (see PRODUCTS.md)")
    stores = [NoteStore(granola_db), NoteStore(wispr_db)]
    try:
        a, b = (list(s.iter_notes()) for s in stores)
    finally:
        for s in stores:
            s.close()
    result = pair_meetings(
        a, b, window=timedelta(minutes=window_min), overrides=Overrides.load(overrides)
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(result.model_dump_json(indent=1) + "\n")
    for p in result.pairs:
        typer.echo(f"{p.a}  {p.b}  score={p.score:.2f}  {p.method}")
    typer.echo(
        f"paired={len(result.pairs)} unpaired_granola={len(result.unpaired_a)} "
        f"unpaired_wispr={len(result.unpaired_b)} · {out}"
    )
