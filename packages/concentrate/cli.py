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
    in a shell loop) and it resumes from ``progress.jsonl``. See docs/HARDENING.md.
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
) -> None:
    """Report v2: paired significance, power, Pareto frontier and failure taxonomy. Free
    unless --llm."""
    import json as _json

    from concentrate.evals.analyze import load_rows, write_analysis
    from concentrate.evals.failures import (
        GENERIC_TYPES,
        GroundTruth,
        LLMFailure,
        failures,
        llm_request,
    )
    from concentrate.llm.client import cost_usd, estimate_tokens

    js, jl = _report_paths(report)
    rep = _json.loads(js.read_text())
    rows = load_rows(jl)
    fx = fixtures or Path(rep["meta"].get("fixtures") or NORTHWIND)
    gt = GroundTruth.load(fx) if (fx / "questions.jsonl").exists() else None
    if gt is None:
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
            out = client.structured(req, LLMFailure)
            f.llm_type, f.llm_reason = out.type, out.reason
        typer.echo(f"LLM classifier spent ${client.ledger.spent_usd():.4f}")
    stem = jl.with_suffix("")
    paths = write_analysis(
        rep, rows, fails, stem, seed=seed if seed is not None else int(rep["meta"].get("seed", 0)),
        volume=volume, gt_note=gt_note,
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
    See docs/MCP_SERVER.md for the Claude Desktop / Claude Code config.
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
    """Aggregate style statistics of a corpus (docs/FIDELITY_V2.md). Prints no transcript text."""
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
