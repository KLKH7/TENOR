"""``concentrate figures``: every committed results figure, generated from saved data.

Each figure answers one question (PLAN.md section 5) and carries its scope and caveats on the
image itself: split (DEV or TEST), n, CI method, and the price list used for any dollar figure.

Inputs, all committed except where noted:

- ``evals/reports/<stamp>.json`` / ``.jsonl`` / ``.analysis.json`` / ``.judgecheck.*.md``: the
  synthetic eval report the figures describe;
- ``evals/reports/retirement/*.curve.json`` (latest): the transcript-deletion curve;
- ``evals/scale/*/scale.json`` (latest): the scale curve;
- ``docs/figures/data/real_slice.json``: a content-free snapshot of the real-meeting slice
  (scores and paired counts only, all already public in ``docs/results/REAL_SLICE.md`` and
  ``docs/systems/concentrate/fallback.md``). It is rebuilt from the gitignored ``data/real``
  reports by :func:`snapshot_real`, which reads only ``qid``, ``system``, ``category``,
  ``correct`` and ``verdict`` from each row, never questions or answers. CI never rebuilds it;
  it checks the figures against it.

Costs are re-priced from token counts with one price list (:data:`PRICE_IN`, :data:`PRICE_OUT`)
so systems are compared at equal prices; cache and batch discounts are deliberately ignored.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from concentrate.evals import figstyle as fs
from concentrate.evals.scoring import bootstrap_ci
from concentrate.evals.stats import binom_two_sided_p

PRICE_IN = 4.0  # $ per million input tokens, every system, every token
PRICE_OUT = 20.0  # $ per million output tokens
AMORTISE_N = (10, 100, 1000)  # questions asked per corpus, for one-off ingest cost
SEED = 42

REAL_SNAPSHOT = Path("docs/figures/data/real_slice.json")
OUT_DIR = Path("docs/figures")

# Leaderboard / heatmap order: the ceiling first, then by family.
CEILINGS = frozenset({"b2o_oracle"})  # given the right meetings: a reference, not a contender
ORDER = (
    "b2o_oracle",
    "concentrate",
    "b3_agentic",
    "b0_topk",
    "b4_mem0",
    "b5_graphiti",
    "b1_summaries",
)


# -- loading -----------------------------------------------------------------------------------


@dataclass
class Inputs:
    """Everything the figures read, loaded once. ``files`` records provenance."""

    stamp: str
    report: dict[str, Any]
    rows: list[dict[str, Any]]
    analysis: dict[str, Any]
    judgecheck: str | None
    retirement: dict[str, Any] | None
    scale: dict[str, Any] | None
    real: dict[str, Any] | None
    root: Path = Path()
    files: dict[str, list[Path]] = field(default_factory=dict)


def _json(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text())
    return data


def _latest(paths: Sequence[Path]) -> Path | None:
    return sorted(paths)[-1] if paths else None


def load_inputs(stamp: str, root: Path = Path()) -> Inputs:
    """Load the report ``stamp`` and the side experiments' outputs under ``root``."""
    reports = root / "evals" / "reports"
    report_path = reports / f"{stamp}.json"
    rows_path = reports / f"{stamp}.jsonl"
    analysis_path = reports / f"{stamp}.analysis.json"
    judge_path = _latest(list(reports.glob(f"{stamp}.judgecheck.*.md")))
    retire_path = _latest(list((reports / "retirement").glob("*.curve.json")))
    scale_path = _latest(list((root / "evals" / "scale").glob("*/scale.json")))
    real_path = root / REAL_SNAPSHOT
    rows = [json.loads(x) for x in rows_path.read_text().splitlines() if x.strip()]
    core = [report_path, rows_path, analysis_path]
    return Inputs(
        stamp=stamp,
        report=_json(report_path),
        rows=rows,
        analysis=_json(analysis_path),
        judgecheck=judge_path.read_text() if judge_path else None,
        retirement=_json(retire_path) if retire_path else None,
        scale=_json(scale_path) if scale_path else None,
        real=_json(real_path) if real_path.exists() else None,
        root=root,
        files={
            "report": core,
            "judge": [judge_path] if judge_path else [],
            "retirement": [retire_path] if retire_path else [],
            "scale": [scale_path] if scale_path else [],
            "real": [real_path] if real_path.exists() else [],
        },
    )


def _systems(inp: Inputs) -> list[str]:
    present = set(inp.report["metrics"])
    return [s for s in ORDER if s in present] + sorted(present - set(ORDER))


def _split_label(inp: Inputs) -> str:
    meta = inp.report["meta"]
    return f"{str(meta.get('split', '?')).upper()} split, n={meta.get('questions', '?')}"


def _pct(x: float) -> float:
    return 100.0 * x


# -- equal-price costing -----------------------------------------------------------------------


def equal_price(input_tokens: float, output_tokens: float) -> float:
    """Dollars for a token count at the one shared price list."""
    return (input_tokens * PRICE_IN + output_tokens * PRICE_OUT) / 1e6


def answer_cost(rows: Sequence[dict[str, Any]], system: str) -> tuple[float, float]:
    """(input $, output $) per question for ``system`` at equal prices."""
    mine = [r for r in rows if r["system"] == system]
    if not mine:
        return (0.0, 0.0)
    n = len(mine)
    tin = sum(float(r.get("input_tokens") or 0) for r in mine) / n
    tout = sum(float(r.get("output_tokens") or 0) for r in mine) / n
    return (equal_price(tin, 0), equal_price(0, tout))


def ingest_cost(report: dict[str, Any], system: str) -> float:
    """One-off ingest $ for the whole corpus at equal prices (0 for query-time systems).

    Uses the original build's token counts (``detail.built``), not the cache-hit replay.
    """
    ing = report["meta"].get("ingest", {}).get(system)
    if not ing:
        return 0.0
    built = ing.get("detail", {}).get("built") or ing
    return equal_price(float(built.get("input_tokens", 0)), float(built.get("output_tokens", 0)))


# -- (a) hero ----------------------------------------------------------------------------------


def _dot_ci(ax: Any, y: float, acc: float, ci: Sequence[float], system: str) -> None:
    lo, hi = _pct(ci[0]), _pct(ci[1])
    a = _pct(acc)
    ax.errorbar(
        a,
        y,
        xerr=[[a - lo], [hi - a]],
        fmt="o",
        color=fs.colour(system),
        ecolor=fs.colour(system),
        elinewidth=2.2,
        capsize=4,
        markersize=8,
    )
    ax.text(hi + 1.2, y, f"{a:.0f}%", va="center", fontsize=9, color=fs.INK)


def fig_hero(inp: Inputs) -> Any:
    """(a) Synthetic test vs real meetings: the same three approaches, accuracy with 95% CIs."""
    if inp.real is None:
        return None
    m = inp.report["metrics"]
    real = inp.real["systems"]
    fig, (ax1, ax2) = fs.new_figure(4.2, ncols=2, sharex=True)
    left = [
        ("b3_agentic", "Agent (b3, re-impl.)"),
        ("concentrate", "Concentrate"),
        ("b0_topk", "Top-k (k=20)"),
    ]
    right = [
        ("granola_agent", "Granola agent (real)"),
        ("concentrate", "Concentrate"),
        ("b0_topk", "Top-k (k=1)"),
    ]
    for ax, items, src, head in (
        (ax1, left, m, f"Synthetic: Northwind, 184 meetings\n{_split_label(inp)}"),
        (ax2, right, real, f"Real: author's 4 meetings\nDEV (seen), n={real['concentrate']['n']}"),
    ):
        for i, (s, _name) in enumerate(items):
            y = len(items) - 1 - i
            _dot_ci(ax, y, src[s]["accuracy"], src[s]["accuracy_ci95"], s)
        ax.set_yticks(range(len(items)), [n for _, n in reversed(items)])
        ax.set_ylim(-0.6, len(items) - 0.4)
        ax.set_title(head, fontsize=9.5, loc="left")
        ax.set_xlim(30, 105)
        ax.set_xlabel("accuracy (%), 95% bootstrap CI")
        ax.grid(axis="y", visible=False)
    pairs = {(p["a"], p["b"]): p for p in inp.analysis["pairs"]["overall"]}
    syn = [
        pairs.get(k) or pairs.get((k[1], k[0]))
        for k in [
            ("concentrate", "b0_topk"),
            ("b3_agentic", "b0_topk"),
            ("concentrate", "b3_agentic"),
        ]
    ]
    syn_note = "no pair significant (Holm)" if all(p and not p["significant"] for p in syn) else ""
    ax1.text(0.0, -0.33, f"A tie: {syn_note}.", transform=ax1.transAxes, fontsize=8.5, color=fs.INK)
    g = inp.real["paired"]["granola_agent|concentrate"]
    ax2.text(
        0.0,
        -0.33,
        f"A loss: Granola alone right {g['a_only']} times,\n"
        f"Concentrate alone {g['b_only']} (sign test p={g['p']:.3f}).",
        transform=ax2.transAxes,
        fontsize=8.5,
        color=fs.INK,
        va="top",
    )
    fs.title(
        fig,
        "Why did a tie on synthetic data become a loss on real meetings?",
        "Same three approaches; left on the synthetic benchmark, right on the author's own "
        "Granola meetings.",
    )
    fs.footnote(
        fig,
        "b3 imitates the shape of Granola's agent; the real agent can only be run on real data. "
        "Real gold answers were LLM-drafted and bulk-approved; the real questions were seen "
        "during development and n is small, so treat the right panel as DEV evidence.",
    )
    fig.subplots_adjust(left=0.19, right=0.97, top=0.76, bottom=0.36, wspace=0.75)
    return fig


# -- (b) leaderboard ---------------------------------------------------------------------------


def fig_leaderboard(inp: Inputs) -> Any:
    """(b) Accuracy per system with 95% CI whiskers, sorted."""
    m = inp.report["metrics"]
    systems = sorted(_systems(inp), key=lambda s: m[s]["accuracy"])
    fig, ax = fs.new_figure(0.42 * len(systems) + 1.6)
    for y, s in enumerate(systems):
        _dot_ci(ax, y, m[s]["accuracy"], m[s]["accuracy_ci95"], s)
    ax.set_yticks(range(len(systems)), [fs.label(s) for s in systems])
    ax.set_xlim(50, 102)
    ax.set_xlabel("accuracy (%), 95% bootstrap CI over questions")
    ax.grid(axis="y", visible=False)
    meta = inp.report["meta"]
    fs.title(
        fig,
        "Who's ahead, and is it real?",
        f"Synthetic Northwind, {_split_label(inp)}; answers and judge {meta['model']}; "
        f"report {inp.stamp}.",
    )
    fs.footnote(
        fig,
        "Overlapping whiskers are not a test: see the pairwise matrix for paired, "
        "Holm-corrected comparisons. The oracle is given the right meetings: a ceiling.",
    )
    fig.subplots_adjust(left=0.25, right=0.97, top=0.82, bottom=0.2)
    return fig


# -- (c) cost vs accuracy frontier -------------------------------------------------------------


def frontier(points: Sequence[tuple[str, float, float]]) -> list[str]:
    """Names on the cost/accuracy Pareto frontier (cheaper-or-equal and more accurate)."""
    on = []
    for name, cost, acc in points:
        dominated = any(
            c2 <= cost and a2 >= acc and (c2 < cost or a2 > acc)
            for n2, c2, a2 in points
            if n2 != name
        )
        if not dominated:
            on.append(name)
    return on


def per_question_cost(inp: Inputs, system: str, n_questions: int) -> float:
    """Equal-price $/question: answering plus ingest amortised over ``n_questions``."""
    cin, cout = answer_cost(inp.rows, system)
    return cin + cout + ingest_cost(inp.report, system) / n_questions


def fig_cost_frontier(inp: Inputs) -> Any:
    """(c) $/question vs accuracy at equal prices, ingest amortised at 10/100/1,000 questions."""
    m = inp.report["metrics"]
    systems = _systems(inp)
    fig, axes = fs.new_figure(3.6, ncols=len(AMORTISE_N), sharey=True)
    for ax, n in zip(axes, AMORTISE_N, strict=True):
        pts = [(s, per_question_cost(inp, s, n), _pct(m[s]["accuracy"])) for s in systems]
        on = set(frontier([p for p in pts if p[0] not in CEILINGS]))
        front = sorted((c, a) for s, c, a in pts if s in on)
        ax.plot(
            [c for c, _ in front], [a for _, a in front], color=fs.MUTED, lw=1, ls="--", zorder=1
        )
        for s, c, a in pts:
            lo, hi = (_pct(v) for v in m[s]["accuracy_ci95"])
            ax.errorbar(
                c,
                a,
                yerr=[[a - lo], [hi - a]],
                fmt="o",
                color=fs.colour(s),
                elinewidth=1.2,
                capsize=2,
                markersize=6 if s in on else 5,
                markerfacecolor=fs.colour(s) if s in on else "white",
                zorder=2,
            )
        ax.set_xscale("log")
        ax.set_xlim(0.005, 30)
        ax.set_xticks([0.01, 0.1, 1, 10], ["$0.01", "$0.1", "$1", "$10"])
        ax.minorticks_off()
        ax.set_title(f"{n:,} questions per corpus", fontsize=9.5)
        ax.set_xlabel("$ per question (log)")
    axes[0].set_ylabel("accuracy (%)")
    handles = [axes[0].plot([], [], "o", color=fs.colour(s), label=fs.label(s))[0] for s in systems]
    fig.legend(handles=handles, loc="lower center", ncol=4, bbox_to_anchor=(0.5, 0.07))
    fs.title(
        fig,
        "What's the best system for a given budget?",
        f"Equal prices for every token (${PRICE_IN:g}/M input, ${PRICE_OUT:g}/M output, no "
        "discounts).\nOne-off ingest of the 184-meeting corpus is amortised over N "
        "questions per corpus.",
    )
    fs.footnote(
        fig,
        f"{_split_label(inp)}. Filled = on the frontier (dashed); hollow = "
        "dominated; the oracle is a ceiling and never on it. Whiskers: 95% CI. Summaries' "
        "cost excludes making the summaries (Granola does). Equal prices are a "
        "hypothetical list, not a bill.",
    )
    fig.subplots_adjust(left=0.08, right=0.98, top=0.76, bottom=0.42, wspace=0.12)
    return fig


# -- (d) pairwise matrix -----------------------------------------------------------------------


def fig_pairwise(inp: Inputs) -> Any:
    """(d) Accuracy difference row - column, with Holm-significant cells starred."""
    systems = _systems(inp)
    k = len(systems)
    idx = {s: i for i, s in enumerate(systems)}
    diff = np.full((k, k), np.nan)
    sig = np.zeros((k, k), dtype=bool)
    pairs = inp.analysis["pairs"]["overall"]
    for p in pairs:
        if p["a"] in idx and p["b"] in idx:
            i, j = idx[p["a"]], idx[p["b"]]
            diff[i, j], diff[j, i] = _pct(p["diff"]), -_pct(p["diff"])
            sig[i, j] = sig[j, i] = bool(p["significant"])
    fig, ax = fs.new_figure(5.6)
    lim = float(np.nanmax(np.abs(diff))) if np.isfinite(diff).any() else 1.0
    ax.imshow(diff, cmap="PuOr_r", vmin=-lim, vmax=lim)
    for i in range(k):
        for j in range(k):
            if i == j or math.isnan(diff[i, j]):
                continue
            txt = f"{diff[i, j]:+.0f}" + ("*" if sig[i, j] else "")
            ax.text(
                j,
                i,
                txt,
                ha="center",
                va="center",
                fontsize=9,
                fontweight="bold" if sig[i, j] else "normal",
                color="white" if abs(diff[i, j]) > 0.6 * lim else fs.INK,
            )
    names = [fs.label(s) for s in systems]
    ax.set_xticks(range(k), names, rotation=35, ha="right")
    ax.set_yticks(range(k), names)
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    fs.title(
        fig,
        "Which gaps are real?",
        f"Row minus column, accuracy points. {_split_label(inp)}, paired by question.",
    )
    fs.footnote(
        fig,
        f"* bold = significant after Holm correction over all {len(pairs)} pairs "
        "(exact McNemar, alpha=0.05). Unstarred gaps may be noise; orange = row better.",
    )
    fig.subplots_adjust(left=0.24, right=0.8, top=0.86, bottom=0.25)
    return fig


# -- (e) by question type ----------------------------------------------------------------------


def fig_by_category(inp: Inputs) -> Any:
    """(e) Heatmap: accuracy per system per question type."""
    m = inp.report["metrics"]
    systems = _systems(inp)
    cats = sorted({c for s in systems for c in m[s].get("by_category", {})})
    grid = np.array(
        [
            [_pct(m[s]["by_category"].get(c, {}).get("accuracy", np.nan)) for c in cats]
            for s in systems
        ]
    )
    ns = [m[systems[0]]["by_category"].get(c, {}).get("n", 0) for c in cats]
    fig, ax = fs.new_figure(4.6)
    ax.imshow(grid, cmap="cividis", vmin=0, vmax=100, aspect="auto")
    for i in range(len(systems)):
        for j in range(len(cats)):
            v = grid[i, j]
            if not math.isnan(v):
                ax.text(
                    j,
                    i,
                    f"{v:.0f}",
                    ha="center",
                    va="center",
                    fontsize=8.5,
                    color="black" if v > 60 else "white",
                )
    ax.set_xticks(
        range(len(cats)),
        [f"{c.replace('_', ' ')}\n(n={n})" for c, n in zip(cats, ns, strict=True)],
        rotation=35,
        ha="right",
        fontsize=8,
    )
    ax.set_yticks(range(len(systems)), [fs.label(s) for s in systems])
    ax.grid(False)
    fs.title(
        fig, "Where does each system break?", f"Accuracy (%) by question type. {_split_label(inp)}."
    )
    fs.footnote(
        fig,
        "Each cell has only ~12-14 questions: a 95% CI is roughly ±20-25 points, "
        "so read single cells as hints, rows and columns as patterns.",
    )
    fig.subplots_adjust(left=0.22, right=0.98, top=0.86, bottom=0.3)
    return fig


# -- (f) failure taxonomy ----------------------------------------------------------------------


def fig_failures(inp: Inputs) -> Any:
    """(f) Stacked bars: why each system's non-correct answers were wrong."""
    table: dict[str, dict[str, dict[str, Any]]] = inp.analysis["failures"]["table"]
    systems = [s for s in _systems(inp) if s in table]
    types = sorted({t for s in systems for t in table[s]})
    totals = {s: sum(v["count"] for v in table[s].values()) for s in systems}
    systems.sort(key=lambda s: totals[s])
    fig, ax = fs.new_figure(0.42 * len(systems) + 2.0)
    left = np.zeros(len(systems))
    for t, c in zip(types, fs.OTHER_COLOURS * 2, strict=False):
        vals = np.array([table[s].get(t, {}).get("count", 0) for s in systems], dtype=float)
        ax.barh(
            range(len(systems)), vals, left=left, color=c, label=t.replace("_", " "), height=0.65
        )
        left += vals
    for y, s in enumerate(systems):
        ax.text(totals[s] + 0.4, y, str(totals[s]), va="center", fontsize=9)
    ax.set_yticks(range(len(systems)), [fs.label(s) for s in systems])
    ax.set_xlabel("non-correct answers (count)")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower center", ncol=3, bbox_to_anchor=(0.4, -0.55))
    fs.title(
        fig,
        "Why is it wrong?",
        f"Failure type of every non-correct answer. {_split_label(inp)} per system.",
    )
    fs.footnote(
        fig,
        "Types come from a rule-based classifier (evals/failures.py) using planted "
        "ground truth; 'reasoning or reading' is its catch-all, not a diagnosis.",
    )
    fig.subplots_adjust(left=0.25, right=0.95, top=0.85, bottom=0.36)
    return fig


# -- (g) transcript-deletion curve -------------------------------------------------------------


def fig_retirement(inp: Inputs) -> Any:
    """(g) Concentrate accuracy as transcripts are deleted, vs the summaries baseline."""
    r = inp.retirement
    if r is None:
        return None
    order = list(r["order"])
    order.sort(key=lambda k: r["retired_share"][k])
    xs = [_pct(r["retired_share"][k]) for k in order]
    pts = [r["curve"][f"concentrate@{k}"] for k in order]
    ys = [_pct(p["accuracy"]) for p in pts]
    lo = [y - _pct(p["accuracy_ci95"][0]) for y, p in zip(ys, pts, strict=True)]
    hi = [_pct(p["accuracy_ci95"][1]) - y for y, p in zip(ys, pts, strict=True)]
    fig, ax = fs.new_figure(3.8)
    ax.errorbar(
        xs,
        ys,
        yerr=[lo, hi],
        fmt="-o",
        color=fs.colour("concentrate"),
        capsize=3,
        label="Concentrate (memory + remaining transcripts)",
    )
    for x, y, k in zip(xs, ys, order, strict=True):
        name = "keep all" if k == "inf" else f"keep {k} days"
        ax.annotate(
            name,
            (x, y),
            textcoords="offset points",
            xytext=(0, -30),
            ha="center",
            fontsize=8,
            color=fs.MUTED,
        )
    b = r["curve"].get("b1_summaries")
    if b:
        ax.axhspan(
            _pct(b["accuracy_ci95"][0]),
            _pct(b["accuracy_ci95"][1]),
            color=fs.colour("b1_summaries"),
            alpha=0.12,
        )
        ax.axhline(
            _pct(b["accuracy"]),
            color=fs.colour("b1_summaries"),
            ls="--",
            label="Summaries only (b1), 95% CI band",
        )
    ax.set_xlim(-5, 105)
    ax.set_ylim(30, 100)
    ax.set_xlabel("transcript tokens deleted (%)")
    ax.set_ylabel("accuracy (%)")
    ax.legend(loc="lower left")
    meta = r["meta"]
    fs.title(
        fig,
        "What does deleting transcripts cost?",
        f"{str(meta['split']).upper()} split, n={meta['questions']}; synthetic Northwind; "
        f"95% bootstrap CIs.",
    )
    fs.footnote(
        fig,
        "DEV only. Northwind questions follow planted facts, which favour "
        "extracted memory; exact-quote and who-said questions (where deletion should "
        "hurt) are under-represented. See docs/results/RETIREMENT.md.",
    )
    fig.subplots_adjust(left=0.1, right=0.97, top=0.8, bottom=0.27)
    return fig


# -- (h) scale curve ---------------------------------------------------------------------------


def wilson(k: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes out of n (used where only aggregates exist)."""
    if n == 0:
        return (math.nan, math.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def _scale_split(sc: dict[str, Any], root: Path) -> str:
    """The question split of the scale run, read from its first per-size report."""
    reports = sorted(sc["meta"].get("reports", {}).items())
    if reports and (root / reports[0][1]).exists():
        return str(_json(root / reports[0][1])["meta"].get("split", "?")).upper()
    return "?"


def fig_scale(inp: Inputs) -> Any:
    """(h) Accuracy on a fixed question set as the corpus grows."""
    sc = inp.scale
    if sc is None:
        return None
    fig, ax = fs.new_figure(3.8)
    n_common = len(sc["common_questions"])
    for i, (s, v) in enumerate(sc["systems"].items()):
        jitter = 1 + 0.03 * (i - (len(sc["systems"]) - 1) / 2)  # keep overlapping CIs readable
        xs = [p["meetings"] * jitter for p in v["points"]]
        ys = [_pct(p["accuracy_common"]) for p in v["points"]]
        cis = [wilson(p["accuracy_common"] * p["n_common"], p["n_common"]) for p in v["points"]]
        ax.errorbar(
            xs,
            ys,
            yerr=[
                [y - _pct(c[0]) for y, c in zip(ys, cis, strict=True)],
                [_pct(c[1]) - y for y, c in zip(ys, cis, strict=True)],
            ],
            fmt="-o",
            color=fs.colour(s),
            capsize=3,
            label=fs.label(s),
            alpha=0.9,
        )
    ax.set_xscale("log", base=2)
    meetings = sorted({p["meetings"] for v in sc["systems"].values() for p in v["points"]})
    ax.set_xticks(meetings, [str(x) for x in meetings])
    ax.minorticks_off()
    ax.set_ylim(30, 105)
    ax.set_xlabel("meetings in the corpus")
    ax.set_ylabel(f"accuracy on the same {n_common} questions (%)")
    ax.legend(loc="lower right")
    split = _scale_split(sc, inp.root)
    model = sc["meta"]["model"].replace("claude-", "").split("-")[0].capitalize()
    fs.title(
        fig,
        "Does retrieval degrade as meetings pile up?",
        f"{model}, directional, not significant: the same {n_common} {split} questions at "
        "each size; Wilson 95% CIs.",
    )
    fs.footnote(
        fig,
        "A cheap model and 21 shared questions: every CI overlaps. This shows the "
        "experiment works, not a scale effect. See docs/results/ECONOMICS.md.",
    )
    fig.subplots_adjust(left=0.1, right=0.97, top=0.82, bottom=0.22)
    return fig


# -- (i) fallback before/after ------------------------------------------------------------------


def fig_fallback(inp: Inputs) -> Any:
    """(i) Concentrate memory-only vs + on_abstain fallback, paired, per category (DEV)."""
    if inp.real is None:
        return None
    fb = inp.real["fallback_dev"]
    cats = sorted(fb["by_category"], key=lambda c: fb["by_category"][c]["n"])
    fig, ax = fs.new_figure(3.9)
    for y, c in enumerate(cats):
        v = fb["by_category"][c]
        off, on = _pct(v["off"] / v["n"]), _pct(v["on"] / v["n"])
        ax.plot([off, on], [y, y], color=fs.MUTED, lw=1.2, zorder=1)
        ax.scatter(
            [off],
            [y],
            color="white",
            edgecolor=fs.colour("concentrate"),
            s=55,
            zorder=2,
            linewidth=1.8,
        )
        ax.scatter([on], [y], color=fs.colour("c_fallback_on_abstain"), s=55, zorder=3)
        ax.text(108, y, f"{v['off']}→{v['on']} of {v['n']}", va="center", fontsize=8.5)
    ax.scatter(
        [],
        [],
        color="white",
        edgecolor=fs.colour("concentrate"),
        linewidth=1.8,
        label=f"memory only: {fb['off_correct']}/{fb['n']}",
    )
    ax.scatter(
        [],
        [],
        color=fs.colour("c_fallback_on_abstain"),
        label=f"+ transcript fallback: {fb['on_correct']}/{fb['n']}",
    )
    ax.set_yticks(range(len(cats)), [c.replace("_", " ") for c in cats])
    ax.set_xlim(-5, 128)
    ax.set_xticks([0, 25, 50, 75, 100])
    ax.set_xlabel("correct (%)")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower center", ncol=2, bbox_to_anchor=(0.45, -0.5))
    fs.title(
        fig,
        "Did the transcript fallback help, and where?  [DEV]",
        f"Real meetings, the same {fb['n']} questions both ways. Paired: fallback won "
        f"{fb['won']}, lost {fb['lost']} (sign test p={fb['p']:.3f}).",
    )
    fs.footnote(
        fig,
        "DEV, not a claim: these questions were seen while designing the fallback, "
        "and the run stopped at its budget cap (22 of 39). Needs fresh questions (X1).",
    )
    fig.subplots_adjust(left=0.2, right=0.97, top=0.8, bottom=0.33)
    return fig


# -- (j) judge agreement -----------------------------------------------------------------------

_KAPPA = re.compile(r"correct / not correct:\*\*\s*([\d.]+)%\s*\(Cohen's kappa ([\d.]+)\)")
_ROW = re.compile(r"^\| (\w+) \| (\d+) \| ([\d.]+)% \| ([\d.]+)% \|", re.M)
_CONF = re.compile(r"^\| (correct|partially_correct|incorrect) \| (\d+) \| (\d+) \| (\d+) \|", re.M)


def parse_judgecheck(md: str) -> dict[str, Any]:
    """Pull agreement, kappa, per-system accuracies and the confusion matrix from the report."""
    k = _KAPPA.search(md)
    return {
        "agreement": float(k.group(1)) if k else math.nan,
        "kappa": float(k.group(2)) if k else math.nan,
        "systems": {s: (float(a), float(b)) for s, _, a, b in _ROW.findall(md)},
        "confusion": [[int(x) for x in row[1:]] for row in _CONF.findall(md)],
    }


def fig_judge(inp: Inputs) -> Any:
    """(j) Primary vs second judge: confusion matrix and per-system accuracy."""
    if inp.judgecheck is None:
        return None
    jc = parse_judgecheck(inp.judgecheck)
    second = re.search(r"second judge `([^`]+)`", inp.judgecheck)
    fig, (ax1, ax2) = fs.new_figure(3.9, ncols=2, gridspec_kw={"width_ratios": [1, 1.4]})
    conf = np.array(jc["confusion"], dtype=float)
    ax1.imshow(np.log1p(conf), cmap="Blues")
    labels = ["correct", "partial", "incorrect"]
    for i in range(conf.shape[0]):
        for j in range(conf.shape[1]):
            ax1.text(
                j,
                i,
                f"{int(conf[i, j])}",
                ha="center",
                va="center",
                fontsize=9,
                color="white" if conf[i, j] > 100 else fs.INK,
            )
    ax1.set_xticks(range(3), labels, rotation=30, ha="right")
    ax1.set_yticks(range(3), labels)
    ax1.set_xlabel("second judge")
    ax1.set_ylabel("primary judge")
    ax1.grid(False)
    ax1.set_title(f"κ = {jc['kappa']:.2f} (correct vs not)", fontsize=9.5)
    systems = [s for s in _systems(inp) if s in jc["systems"]]
    for y, s in enumerate(systems):
        a, b = jc["systems"][s]
        ax2.plot([a, b], [y, y], color=fs.MUTED, lw=1)
        ax2.scatter([a], [y], color=fs.colour(s), s=30, zorder=4)
        ax2.scatter(
            [b], [y], facecolor="none", edgecolor=fs.colour(s), s=110, zorder=3, linewidth=1.6
        )
    ax2.set_yticks(range(len(systems)), [fs.label(s) for s in systems])
    ax2.set_xlabel("accuracy (%): dot = primary, ring = second")
    ax2.grid(axis="y", visible=False)
    ax2.set_title("Per-system accuracy, both judges", fontsize=9.5)
    fs.title(
        fig,
        "Can we trust the grader?",
        f"{inp.report['meta']['model']} (primary) vs {second.group(1) if second else '?'} "
        f"re-grading all {int(conf.sum())} answers; {_split_label(inp)}. "
        f"Agreement {jc['agreement']:.1f}%.",
    )
    fs.footnote(
        fig,
        "Two LLM judges from one family can share blind spots; the blind human "
        "hand-check is not done yet. Close rankings (e.g. b1 vs b5) can swap by judge.",
    )
    fig.subplots_adjust(left=0.13, right=0.98, top=0.8, bottom=0.27, wspace=0.75)
    return fig


# -- (k) token/cost breakdown ------------------------------------------------------------------


def fig_cost_breakdown(inp: Inputs) -> Any:
    """(k) Equal-price $/question split into input, output and amortised ingest."""
    n_amort = 100
    systems = _systems(inp)
    parts = []
    for s in systems:
        cin, cout = answer_cost(inp.rows, s)
        parts.append((s, cin, cout, ingest_cost(inp.report, s) / n_amort))
    parts.sort(key=lambda p: p[1] + p[2] + p[3])
    fig, ax = fs.new_figure(0.42 * len(parts) + 1.9)
    ys = np.arange(len(parts))
    left = np.zeros(len(parts))
    names = (
        "answer: input tokens",
        "answer: output tokens",
        f"ingest, amortised over {n_amort} questions",
    )
    for k, (name, c) in enumerate(zip(names, fs.OTHER_COLOURS, strict=False)):
        vals = np.array([p[k + 1] for p in parts])
        ax.barh(ys, vals, left=left, color=c, height=0.65, label=name)
        left += vals
    for y, total in zip(ys, left, strict=True):
        ax.text(total * 1.01 + 0.002, y, f"${total:.3f}", va="center", fontsize=9)
    ax.set_yticks(ys, [fs.label(p[0]) for p in parts])
    ax.set_xlabel("$ / question at equal prices")
    ax.set_xlim(0, float(left.max()) * 1.18)
    ax.grid(axis="y", visible=False)
    ax.legend(loc="lower center", ncol=2, bbox_to_anchor=(0.4, -0.5))
    fs.title(
        fig,
        "Where does the money go?",
        f"${PRICE_IN:g}/M input, ${PRICE_OUT:g}/M output for every token, no cache or "
        f"batch discounts. {_split_label(inp)}.",
    )
    fs.footnote(
        fig,
        "Input tokens dominate query-time systems: they re-read transcripts on "
        "every question. Write-time systems pay once at ingest. Billed costs differ.",
    )
    fig.subplots_adjust(left=0.25, right=0.95, top=0.82, bottom=0.33)
    return fig


# -- registry and build ------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    """One figure: output name, the question it answers, its builder and input groups."""

    name: str
    question: str
    build: Callable[[Inputs], Any]
    inputs: tuple[str, ...]
    page: str


FIGURES: tuple[Spec, ...] = (
    Spec(
        "hero_synthetic_vs_real",
        "Why did a tie on synthetic data become a loss on real meetings?",
        fig_hero,
        ("report", "real"),
        "results/REAL_SLICE.md",
    ),
    Spec(
        "leaderboard",
        "Who's ahead, and is it real?",
        fig_leaderboard,
        ("report",),
        "results/ANALYSIS.md",
    ),
    Spec(
        "cost_frontier",
        "What's the best system for a given budget?",
        fig_cost_frontier,
        ("report",),
        "results/ECONOMICS.md",
    ),
    Spec("pairwise", "Which gaps are real?", fig_pairwise, ("report",), "results/ANALYSIS.md"),
    Spec(
        "by_category",
        "Where does each system break?",
        fig_by_category,
        ("report",),
        "results/ANALYSIS.md",
    ),
    Spec("failures", "Why is it wrong?", fig_failures, ("report",), "results/ANALYSIS.md"),
    Spec(
        "retirement",
        "What does deleting transcripts cost?",
        fig_retirement,
        ("retirement",),
        "results/RETIREMENT.md",
    ),
    Spec(
        "scale",
        "Does retrieval degrade as meetings pile up?",
        fig_scale,
        ("scale",),
        "results/ECONOMICS.md",
    ),
    Spec(
        "fallback_dev",
        "Did the transcript fallback help, and where? (DEV)",
        fig_fallback,
        ("real",),
        "systems/concentrate/fallback.md",
    ),
    Spec(
        "judge_agreement",
        "Can we trust the grader?",
        fig_judge,
        ("report", "judge"),
        "results/ANALYSIS.md",
    ),
    Spec(
        "cost_breakdown",
        "Where does the money go?",
        fig_cost_breakdown,
        ("report",),
        "results/ECONOMICS.md",
    ),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def render(inp: Inputs) -> tuple[dict[str, bytes], list[str]]:
    """Render every figure to SVG bytes. Returns (name.svg -> bytes, skipped-figure notes)."""
    out: dict[str, bytes] = {}
    skipped = []
    for spec in FIGURES:
        fig = spec.build(inp)
        if fig is None:
            skipped.append(f"{spec.name}: input missing ({', '.join(spec.inputs)})")
            continue
        out[f"{spec.name}.svg"] = fs.svg_bytes(fig)
    out["README.md"] = readme(inp, out).encode()
    return out, skipped


def readme(inp: Inputs, rendered: dict[str, bytes]) -> str:
    """docs/figures/README.md: each figure, its question and the exact inputs it was built from."""
    lines = [
        "# Figures",
        "",
        f"Generated by `uv run concentrate figures {inp.stamp}`; do not edit by hand. CI "
        "regenerates them from the committed inputs below and fails if they differ.",
        "",
        f"Prices for every dollar figure: ${PRICE_IN:g}/M input and ${PRICE_OUT:g}/M output "
        "tokens, applied to every system and every token (equal prices, no discounts).",
        "",
        "| Figure | Question | Inputs (sha256 prefix) |",
        "|---|---|---|",
    ]
    for spec in FIGURES:
        fname = f"{spec.name}.svg"
        if fname not in rendered:
            continue
        files = [p for g in spec.inputs for p in inp.files.get(g, [])]
        refs = ", ".join(f"`{_rel(p)}` ({_sha(p)})" for p in files)
        lines.append(f"| [{fname}]({fname}) | {spec.question} | {refs} |")
    lines += [
        "",
        "`data/real_slice.json` is a content-free snapshot of the real-meeting slice (scores and "
        "paired counts only). Rebuild it from the private `data/real/` reports with "
        f"`uv run concentrate figures {inp.stamp} --refresh-real`.",
        "",
    ]
    return "\n".join(lines)


def _rel(p: Path) -> str:
    parts = p.parts
    for anchor in ("evals", "docs"):
        if anchor in parts:
            return "/".join(parts[parts.index(anchor) :])
    return p.as_posix()


def write(rendered: dict[str, bytes], out_dir: Path) -> list[Path]:
    """Write rendered files into ``out_dir``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, data in sorted(rendered.items()):
        p = out_dir / name
        p.write_bytes(data)
        paths.append(p)
    return paths


_NUM = re.compile(rb"-?\d+\.\d+|-?\d+")


def svg_equivalent(a: bytes, b: bytes, tol: float = 0.05) -> bool:
    """True if ``a`` and ``b`` differ at most by float noise in numbers (< ``tol``).

    Same platform gives byte-identical output; across platforms (CI's Linux vs a Mac) the last
    digit of a coordinate can differ, so the CI check tolerates that and nothing else.
    """
    if a == b:
        return True
    ta, tb = _NUM.split(a), _NUM.split(b)
    na, nb = _NUM.findall(a), _NUM.findall(b)
    if ta != tb or len(na) != len(nb):
        return False
    return all(abs(float(x) - float(y)) <= tol for x, y in zip(na, nb, strict=True))


def check(rendered: dict[str, bytes], out_dir: Path) -> list[str]:
    """Names of committed figures that are missing or differ from a fresh render."""
    bad = []
    for name, data in sorted(rendered.items()):
        p = out_dir / name
        if not p.exists() or not svg_equivalent(p.read_bytes(), data):
            bad.append(name)
    return bad


# -- real-slice snapshot (needs the gitignored data/real) --------------------------------------


def _rows(path: Path) -> list[dict[str, Any]]:
    """Read only the content-free fields of a private report: never questions or answers."""
    keep = ("qid", "system", "category", "correct", "verdict")
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out.append({k: r.get(k) for k in keep})
    return out


def _acc(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    vals = [1.0 if r["correct"] else 0.0 for r in rows]
    lo, hi = bootstrap_ci(vals, seed=SEED)
    return {"n": len(vals), "accuracy": sum(vals) / len(vals), "accuracy_ci95": [lo, hi]}


def _paired(a: dict[str, bool], b: dict[str, bool]) -> dict[str, Any]:
    shared = sorted(set(a) & set(b))
    a_only = sum(1 for q in shared if a[q] and not b[q])
    b_only = sum(1 for q in shared if b[q] and not a[q])
    return {
        "n": len(shared),
        "a_only": a_only,
        "b_only": b_only,
        "p": binom_two_sided_p(a_only, a_only + b_only),
    }


def snapshot_real(main_report: Path, topk_report: Path, fallback_replay: Path) -> dict[str, Any]:
    """Aggregate the private real-slice reports into the committed, content-free snapshot.

    ``main_report``: Granola agent + Concentrate run; ``topk_report``: b0 on all questions
    (restricted here to the main run's questions so the comparison is paired);
    ``fallback_replay``: memory-only vs ``on_abstain`` on the same questions (DEV).
    """
    main = _rows(main_report)
    topk = _rows(topk_report)
    by_sys: dict[str, dict[str, bool]] = {}
    for r in main + topk:
        by_sys.setdefault(str(r["system"]), {})[str(r["qid"])] = bool(r["correct"])
    qids = set(by_sys["concentrate"])
    systems = {
        s: _acc([r for r in main if r["system"] == s]) for s in ("granola_agent", "concentrate")
    }
    systems["b0_topk"] = _acc([r for r in topk if r["qid"] in qids])
    b0 = {q: v for q, v in by_sys["b0_topk"].items() if q in qids}
    fb = _rows(fallback_replay)
    off = {str(r["qid"]): r for r in fb if r["system"] == "concentrate"}
    on = {str(r["qid"]): r for r in fb if r["system"] == "concentrate_on_abstain"}
    shared = sorted(set(off) & set(on))
    ok_off = {q: off[q]["verdict"] == "correct" for q in shared}
    ok_on = {q: on[q]["verdict"] == "correct" for q in shared}
    cats: dict[str, dict[str, int]] = {}
    for q in shared:
        c = cats.setdefault(str(off[q]["category"]), {"n": 0, "off": 0, "on": 0})
        c["n"] += 1
        c["off"] += int(ok_off[q])
        c["on"] += int(ok_on[q])
    pf = _paired(ok_on, ok_off)
    return {
        "note": "Content-free aggregate of the private real slice (scores and counts only). "
        "Real questions were seen during development: DEV evidence.",
        "sources": sorted(p.name for p in (main_report, topk_report, fallback_replay)),
        "systems": systems,
        "paired": {
            "granola_agent|concentrate": _paired(by_sys["granola_agent"], by_sys["concentrate"]),
            "granola_agent|b0_topk": _paired(by_sys["granola_agent"], b0),
            "b0_topk|concentrate": _paired(b0, by_sys["concentrate"]),
        },
        "fallback_dev": {
            "n": len(shared),
            "off_correct": sum(ok_off.values()),
            "on_correct": sum(ok_on.values()),
            "won": pf["a_only"],
            "lost": pf["b_only"],
            "p": pf["p"],
            "by_category": dict(sorted(cats.items())),
        },
    }
