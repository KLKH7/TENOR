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
    from concentrate.synth.pipeline import build, estimate_polish_usd, generate_dataset

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
    needs_index = {"b0", "b1", "b2o"} & set(names)
    embedder = SentenceTransformerEmbedder() if embeddings and needs_index else None
    built = build_systems(names, client, notes, embedder, k)
    ests = estimate(built, questions, client.model, client=client, notes=notes)
    typer.echo(
        f"{fixtures}: {len(notes)} meetings · {len(questions)} questions ({split}) · "
        f"{len(built)} systems · {client.model}"
    )
    for e in ests:
        tag = "exact" if e.exact else "est. "
        ingest = f", ingest ${e.ingest_usd:.2f}" if e.ingest_usd else ""
        typer.echo(
            f"  {e.system:<13} ~{e.input_tokens / max(1, e.questions):>8,.0f} in-tok/q ({tag}) "
            f"≈ ${e.usd:6.2f}  (incl. judge{ingest})"
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
        meta={"k": k, "embedder": getattr(embedder, "name", None), "fixtures": str(fixtures)},
    )
    typer.echo((Path(report["paths"]["md"])).read_text())
    typer.echo(f"spent ${report['meta']['spent_usd']:.2f} · report: {report['paths']['md']}")
