"""Figures for the real-meeting results: the headline charts of the retention track.

Every builder reads committed, content-free aggregates only:

- ``deletion``: ``evals/reports/real-deletion-sim-dev.aggregate.json`` (P2 deletion simulation);
- ``real``: ``docs/figures/data/real_slice.json`` (question-answering slice);
- ``synthetic`` / ``retirement``: the synthetic report and retirement curve, used only to show
  how far synthetic numbers sat from real ones.

Each returns a matplotlib figure, or ``None`` when an input is missing (the registry in
:mod:`concentrate.evals.figures` turns that into a skipped-figure note).
"""

from __future__ import annotations

import math
from typing import Any

import matplotlib as mpl
import numpy as np
from matplotlib.patches import Patch

from concentrate.evals import figstyle as fs

# The real-meeting ladder, top to bottom of the story.
LADDER = ("granola_agent", "notes_only", "notes_facts", "notes_facts_fallback")
LADDER_LABELS = {
    "granola_agent": "With transcripts\n(Granola's agent)",
    "notes_only": "Transcripts deleted:\nnotes only",
    "notes_facts": "+ our fact layer\n(no transcripts)",
    "notes_facts_fallback": "+ read the transcript\nwhen notes fall short",
}
LADDER_SHORT = {
    "granola_agent": "Granola + transcripts",
    "notes_only": "Notes only",
    "notes_facts": "Notes + facts",
    "notes_facts_fallback": "+ transcript when needed",
}
# Paired comparison for each step, keyed "<later> vs <earlier>" in the aggregate.
STEPS = (
    ("notes_only vs granola", "granola_agent", "notes_only"),
    ("notes_facts vs notes_only", "notes_only", "notes_facts"),
    ("notes_facts_fallback vs notes_facts", "notes_facts", "notes_facts_fallback"),
)
MIN_CELL_N = 5  # heatmap cells with fewer questions are greyed out, not coloured


def _p(p: float) -> str:
    return f"p = {p:.2g}"


def _pts(x: float) -> str:
    """Signed points with a real minus sign."""
    return f"{x:+.0f} pts".replace("-", "\u2212")


def _dev_note(meta: dict[str, Any]) -> str:
    return (
        f"Author's 4 real meetings, {meta['questions']} questions, DEV (seen during development); "
        f"run {meta['date']}."
    )


# -- (a) retention step chart ------------------------------------------------------------------


def fig_retention_steps(deletion: dict[str, Any] | None) -> Any:
    """Step chart: accuracy at each rung of the real deletion ladder, paired p on each step."""
    if deletion is None:
        return None
    d = deletion["metrics"]["deletion"]
    cond, paired = d["conditions"], d["paired"]
    acc = [100 * cond[c]["accuracy"] for c in LADDER]
    fig, ax = fs.new_figure(4.9)
    ax.axvspan(0.5, 2.5, color=fs.REAL_TINT, zorder=0)
    ax.text(
        1.5,
        127,
        "transcripts deleted",
        ha="center",
        va="center",
        fontsize=9,
        color=fs.MUTED,
        style="italic",
    )
    ax.text(
        3,
        127,
        "transcripts kept",
        ha="center",
        va="center",
        fontsize=9,
        color=fs.MUTED,
        style="italic",
    )
    for x, (c, a) in enumerate(zip(LADDER, acc, strict=True)):
        lo, hi = (100 * v for v in cond[c]["accuracy_ci"])
        ax.bar(x, a, width=0.56, color=fs.colour(c), zorder=2)
        ax.errorbar(
            x,
            a,
            yerr=[[a - lo], [hi - a]],
            fmt="none",
            ecolor=fs.INK,
            elinewidth=1,
            capsize=3,
            zorder=3,
        )
        ax.text(
            x,
            4,
            f"{a:.0f}%",
            ha="center",
            va="bottom",
            fontsize=13,
            fontweight="bold",
            color="white",
            zorder=4,
        )
    for key, a, b in STEPS:
        i, j = LADDER.index(a), LADDER.index(b)
        ya, yb = acc[i], acc[j]
        ax.plot([i + 0.28, j - 0.28], [ya, ya], color=fs.MUTED, lw=1, ls=(0, (2, 2)), zorder=2)
        ax.annotate(
            "",
            xy=(j - 0.36, yb),
            xytext=(j - 0.36, ya),
            arrowprops={"arrowstyle": "-|>", "color": fs.INK, "lw": 1.4},
            zorder=4,
        )
        s = paired[key]
        sig = s["sign_p"] < 0.05
        ax.text(
            (i + j) / 2,
            max(ya, yb) + 9,
            f"{_pts(yb - ya)}\n{s['wins']} gained, {s['losses']} lost\n{_p(s['sign_p'])}",
            ha="center",
            va="bottom",
            fontsize=8.5,
            color=fs.INK if sig else fs.MUTED,
            fontweight="bold" if sig else "normal",
            zorder=4,
        )
    ax.set_xticks(range(len(LADDER)), [LADDER_LABELS[c] for c in LADDER], fontsize=9)
    ax.set_ylim(0, 132)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("accuracy (%), 95% CI")
    ax.set_xlim(-0.5, 3.5)
    ax.grid(axis="x", visible=False)
    fs.title(
        fig,
        "What does deleting transcripts cost, and what brings it back?",
        _dev_note(deletion["meta"]) + "\nEach step is paired on the same questions (exact "
        "sign test); bold = p < 0.05.",
    )
    fs.footnote(
        fig,
        "Notes only and notes + facts are answered by our model over Granola's own notes, not by "
        "Granola's degraded chat. The last bar reads transcripts, so it models 'keep a cold copy', "
        "not deletion. LLM-drafted gold, approved in bulk; n is small. "
        "docs/benchmark/DELETION_SIM.md",
    )
    fig.subplots_adjust(left=0.09, right=0.98, top=0.8, bottom=0.25)
    return fig


# -- (b) answer types --------------------------------------------------------------------------


def answer_types(c: dict[str, Any]) -> dict[str, float]:
    """Split one condition into Granola's four answer types (shares summing to 1).

    The aggregate's fields partition answers as correct + confident_error + hedged_miss = 1,
    where hedged_miss (= not correct and not a confident error) includes every partial answer
    (a partial is never a confident error). So: complete = correct; incomplete = partial;
    says so = hedged_miss - partial (judged incorrect, but abstained or said the material
    doesn't cover it); confident wrong = confident_error (judged incorrect, no hedge).
    """
    says_so = max(0.0, c["hedged_miss"] - c["partial"])
    return {
        "complete": c["accuracy"],
        "incomplete": c["partial"],
        "says_so": round(says_so, 12),
        "confident_wrong": c["confident_error"],
    }


def fig_answer_types(deletion: dict[str, Any] | None) -> Any:
    """100% stacked bars: Granola's four answer types per condition of the real ladder."""
    if deletion is None:
        return None
    cond = deletion["metrics"]["deletion"]["conditions"]
    fig, ax = fs.new_figure(3.9)
    ys = list(range(len(LADDER)))[::-1]
    for y, c in zip(ys, LADDER, strict=True):
        shares = answer_types(cond[c])
        left = 0.0
        for key, _name, col in fs.ANSWER_TYPES:
            w = 100 * shares[key]
            ax.barh(y, w, left=left, color=col, height=0.62, edgecolor="white", linewidth=1)
            if w >= 6:
                ax.text(
                    left + w / 2,
                    y,
                    f"{w:.0f}%",
                    ha="center",
                    va="center",
                    fontsize=9,
                    color="white" if key in ("complete", "confident_wrong") else fs.INK,
                )
            left += w
    ax.set_yticks(ys, [f"{LADDER_SHORT[c]}  (n={cond[c]['n']})" for c in LADDER])
    ax.set_xlim(0, 100)
    ax.set_xlabel("share of answers (%)")
    ax.grid(False)
    handles = [Patch(color=col, label=name) for _k, name, col in fs.ANSWER_TYPES]
    ax.legend(
        handles=handles,
        loc="lower center",
        ncol=4,
        bbox_to_anchor=(0.4, -0.42),
        handlelength=1.2,
        columnspacing=1.2,
    )
    fs.title(
        fig,
        "When the answer isn't right, does it say so?",
        _dev_note(deletion["meta"]) + "\nGranola's four answer types, from the judge's verdict "
        "and whether the answer hedged.",
    )
    fs.footnote(
        fig,
        "Mapping: complete = judged correct; incomplete = judged partially correct; says so = "
        "judged incorrect but abstained or said the material doesn't cover it; wrong but "
        "confident = judged incorrect with no hedge. Granola's confident errors were 2 of 6 "
        "unanswerable questions.",
    )
    fig.subplots_adjust(left=0.3, right=0.97, top=0.78, bottom=0.33)
    return fig


# -- (c) synthetic vs real ---------------------------------------------------------------------


def _syn_real_rows(
    synthetic: dict[str, Any],
    real: dict[str, Any],
    retirement: dict[str, Any] | None,
    deletion: dict[str, Any] | None,
) -> list[tuple[str, str, str, float, int, float, int]]:
    """(group, label, colour key, synthetic acc, n, real acc, n) for every available pair."""
    m, r = synthetic["metrics"], real["systems"]
    n_syn = int(synthetic["meta"]["questions"])
    rows = [
        (
            "Question answering",
            "Memory only (Concentrate)",
            "concentrate",
            m["concentrate"]["accuracy"],
            n_syn,
            r["concentrate"]["accuracy"],
            r["concentrate"]["n"],
        ),
        (
            "Question answering",
            "Top-k retrieval",
            "b0_topk",
            m["b0_topk"]["accuracy"],
            n_syn,
            r["b0_topk"]["accuracy"],
            r["b0_topk"]["n"],
        ),
        (
            "Question answering",
            "Agent over transcripts*",
            "granola_agent",
            m["b3_agentic"]["accuracy"],
            n_syn,
            r["granola_agent"]["accuracy"],
            r["granola_agent"]["n"],
        ),
    ]
    if retirement is not None and deletion is not None:
        cur, cond = retirement["curve"], deletion["metrics"]["deletion"]["conditions"]
        n_ret = int(retirement["meta"]["questions"])
        rows += [
            (
                "Retention",
                "Notes only",
                "notes_only",
                cur["b1_summaries"]["accuracy"],
                n_ret,
                cond["notes_only"]["accuracy"],
                cond["notes_only"]["n"],
            ),
            (
                "Retention",
                "Notes + fact layer",
                "notes_facts",
                cur["concentrate@0"]["accuracy"],
                n_ret,
                cond["notes_facts"]["accuracy"],
                cond["notes_facts"]["n"],
            ),
        ]
    return rows


def fig_synthetic_vs_real(
    synthetic: dict[str, Any],
    real: dict[str, Any] | None,
    retirement: dict[str, Any] | None,
    deletion: dict[str, Any] | None,
) -> Any:
    """Dumbbell: each approach's synthetic score (hollow) against its real score (filled)."""
    if real is None:
        return None
    rows = _syn_real_rows(synthetic, real, retirement, deletion)
    fig, ax = fs.new_figure(0.48 * len(rows) + 2.3)
    groups: list[str] = []
    ys: list[float] = []
    y = 0.0
    for g, *_ in rows:
        if groups and g != groups[-1]:
            y += 0.7
        groups.append(g)
        ys.append(-y)
        y += 1
    for yy, (_g, _name, key, s, _ns, r, _nr) in zip(ys, rows, strict=True):
        col = fs.colour(key)
        ax.annotate(
            "",
            xy=(100 * r, yy),
            xytext=(100 * s, yy),
            arrowprops={"arrowstyle": "-|>", "color": col, "lw": 2, "shrinkA": 6, "shrinkB": 6},
        )
        ax.scatter([100 * s], [yy], s=70, facecolor="white", edgecolor=col, linewidth=2, zorder=3)
        ax.scatter([100 * r], [yy], s=70, color=col, zorder=3)
        ax.text(
            100 * s + (2.2 if s >= r else -2.2),
            yy,
            f"{100 * s:.0f}",
            va="center",
            ha="left" if s >= r else "right",
            fontsize=9,
            color=fs.MUTED,
        )
        ax.text(
            100 * r + (-2.2 if s >= r else 2.2),
            yy,
            f"{100 * r:.0f}",
            va="center",
            ha="right" if s >= r else "left",
            fontsize=9,
            fontweight="bold",
            color=fs.INK,
        )
        ax.text(
            103,
            yy,
            _pts(100 * (r - s)),
            va="center",
            fontsize=9,
            fontweight="bold" if abs(r - s) >= 0.15 else "normal",
            color=fs.INK if abs(r - s) >= 0.15 else fs.MUTED,
        )
    labels = [f"{name}\n{g.lower()}, n {ns} → {nr}" for g, name, _k, _s, ns, _r, nr in rows]
    ax.set_yticks(ys, labels, fontsize=8.5)
    ax.set_xlim(40, 112)
    ax.set_xticks([40, 50, 60, 70, 80, 90, 100])
    ax.set_xlabel("accuracy (%): hollow = synthetic, filled = real meetings")
    ax.set_ylim(min(ys) - 0.6, 0.6)
    ax.grid(axis="y", visible=False)
    fs.title(
        fig,
        "Why did synthetic data mislead us?",
        "The same approach on synthetic questions (hollow) and on the author's real meetings "
        "(filled). Memory built\nfrom extracted facts lost most; reading transcripts held up.",
    )
    fs.footnote(
        fig,
        "*Synthetic: our agent re-implementation (b3); real: Granola's own agent, which can only "
        "run on real accounts. Synthetic QA is the TEST split; synthetic retention is the DEV "
        "retirement run; all real numbers are DEV and n is small. Synthetic is diagnostic, not "
        "evidence: its questions target the facts an extractor keeps.",
    )
    fig.subplots_adjust(left=0.3, right=0.97, top=0.8, bottom=0.28)
    return fig


# -- (d) question type x condition heatmap -----------------------------------------------------


def _cells(cond: dict[str, Any], field: str) -> tuple[list[str], np.ndarray, np.ndarray]:
    keys = sorted({k for c in LADDER for k in cond[c].get(field, {})})
    acc = np.full((len(LADDER), len(keys)), np.nan)
    ns = np.zeros((len(LADDER), len(keys)), dtype=int)
    for i, c in enumerate(LADDER):
        for j, k in enumerate(keys):
            cell = cond[c].get(field, {}).get(k)
            if cell:
                ns[i, j] = cell["n"]
                acc[i, j] = 100 * cell["accuracy"]
    return keys, acc, ns


def fig_question_heatmap(deletion: dict[str, Any] | None) -> Any:
    """Heatmap: accuracy per real-ladder condition by tag and by category, n in every cell."""
    if deletion is None:
        return None
    cond = deletion["metrics"]["deletion"]["conditions"]
    panels = [(f, *_cells(cond, f)) for f in ("by_tag", "by_category")]
    panels = [p for p in panels if p[1]]
    widths = [max(len(p[1]), 1) for p in panels]
    fig, axes = fs.new_figure(
        4.4, ncols=len(panels), sharey=True, gridspec_kw={"width_ratios": widths}
    )
    axes = np.atleast_1d(axes)
    cmap = mpl.colormaps["cividis"].with_extremes(bad="#E0E0E0")
    for ax, (field, keys, acc, ns) in zip(axes, panels, strict=True):
        shown = np.ma.masked_where((ns < MIN_CELL_N) | np.isnan(acc), acc)
        ax.imshow(shown, cmap=cmap, vmin=0, vmax=100, aspect="auto")
        for i in range(len(LADDER)):
            for j in range(len(keys)):
                v, n = acc[i, j], ns[i, j]
                if n == 0:
                    continue
                low = n < MIN_CELL_N or math.isnan(v)
                txt = f"n={n}" if low else f"{v:.0f}\nn={n}"
                ax.text(
                    j,
                    i,
                    txt,
                    ha="center",
                    va="center",
                    fontsize=8 if low else 9,
                    color=fs.MUTED if low else ("black" if v > 60 else "white"),
                    linespacing=1.1,
                )
        ax.set_xticks(
            range(len(keys)),
            [k.replace("_", " ") for k in keys],
            rotation=30,
            ha="right",
            fontsize=8.5,
        )
        ax.set_title(
            "By tag" if field == "by_tag" else "By question type", fontsize=9.5, loc="left"
        )
        ax.grid(False)
        ax.tick_params(length=0)
    axes[0].set_yticks(range(len(LADDER)), [LADDER_SHORT[c] for c in LADDER])
    fs.title(
        fig,
        "Which questions does deletion hurt, and which layer brings them back?",
        _dev_note(deletion["meta"]) + f"\nAccuracy (%) and n per cell; cells with n < "
        f"{MIN_CELL_N} are greyed out.",
    )
    fs.footnote(
        fig,
        "Cells hold 5-23 questions: a 95% CI is roughly ±20-40 points, so read rows and columns "
        "as patterns, single cells as hints. Unanswerable: 'correct' means it declined.",
    )
    fig.subplots_adjust(left=0.25, right=0.99, top=0.78, bottom=0.27, wspace=0.05)
    return fig
