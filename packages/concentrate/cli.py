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
