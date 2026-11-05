"""Figures for the real-meeting results: the headline charts of the retention track.

Every builder reads committed, content-free aggregates only:

- ``deletion``: ``evals/reports/real-deletion-sim-dev.aggregate.json`` (P2 deletion simulation),
  or its AMI replication ``evals/reports/ami/deletion-sim.aggregate.json`` (``meta.corpus ==
  "ami"``): the same three deletion charts draw either, labelled with the AMI meeting and
  question counts the aggregate holds;
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

# The AMI replication (docs/tracks/RETENTION.md, "AMI replication"): A is Granola's own
# Chat, and B' (Wispr's notes) sits beside the ladder as a product comparison, not a rung.
AMI_LADDER = ("granola_chat", "notes_only", "notes_facts", "notes_facts_fallback")
AMI_STEPS = (
    ("notes_only vs granola_chat", "granola_chat", "notes_only"),
    ("notes_facts vs notes_only", "notes_only", "notes_facts"),
    ("notes_facts_fallback vs notes_facts", "notes_facts", "notes_facts_fallback"),
)
AMI_ROWS = ("granola_chat", "notes_only", "notes_only_wispr", "notes_facts", "notes_facts_fallback")
AMI_COMPARE = "notes_only_wispr vs notes_only"
AMI_LABELS = LADDER_LABELS | {
    "granola_chat": "With transcripts\n(Granola Chat)",
    "notes_only": "Transcripts deleted:\nGranola notes only",
}
AMI_SHORT = LADDER_SHORT | {
    "granola_chat": "Granola Chat + transcripts",
    "notes_only": "Granola notes only",
    "notes_only_wispr": "Wispr notes only",
}


def is_ami(deletion: dict[str, Any]) -> bool:
    """Is this the AMI replication's aggregate (not the author's real meetings)?"""
    return bool(deletion.get("meta", {}).get("corpus") == "ami")


def _ladder(deletion: dict[str, Any]) -> tuple[tuple[str, ...], tuple[tuple[str, str, str], ...]]:
    return (AMI_LADDER, AMI_STEPS) if is_ami(deletion) else (LADDER, STEPS)


def _rows(deletion: dict[str, Any]) -> tuple[str, ...]:
    """Conditions to show, top to bottom (AMI: B' too, where the run has it)."""
    if not is_ami(deletion):
        return LADDER
    cond = deletion["metrics"]["deletion"]["conditions"]
    return tuple(c for c in AMI_ROWS if c in cond)


def _short(deletion: dict[str, Any]) -> dict[str, str]:
    return AMI_SHORT if is_ami(deletion) else LADDER_SHORT


def _colour(c: str) -> str:
    """Granola's Chat shares its agent's black; Wispr's notes take Wispr's colour."""
    return {"granola_chat": fs.colour("granola_agent"), "notes_only_wispr": fs.colour("wispr")}.get(
        c, fs.colour(c)
    )


def _p(p: float) -> str:
    return f"p = {p:.2g}"


def _pts(x: float) -> str:
    """Signed points with a real minus sign."""
    return f"{x:+.0f} pts".replace("-", "\u2212")


def _dev_note(meta: dict[str, Any]) -> str:
    if meta.get("corpus") == "ami":
        return _ami_note(meta)
    return (
        f"Author's 4 real meetings, {meta['questions']} questions, DEV (seen during development); "
        f"run {meta['date']}."
    )


def _ami_note(meta: dict[str, Any]) -> str:
    """'AMI, n meetings, n questions': both counts come from the aggregate, so they grow as
    more AMI recordings are synced."""
    split = {"all": "DEV + TEST", "dev": "DEV", "test": "TEST"}.get(str(meta.get("split")), "?")
    pilot = ", PILOT" if meta.get("pilot") else ""
    return (
        f"AMI, {meta['meetings']} meetings, {meta['questions']} questions ({split}{pilot}): "
        f"public meetings played into the apps; run {meta['date']}."
    )


# -- (a) retention step chart ------------------------------------------------------------------


def _ladder_bars(
    ax: Any,
    cond: dict[str, Any],
    paired: dict[str, Any],
    rungs: tuple[str, ...],
    steps: tuple[tuple[str, str, str], ...],
    labels: dict[str, str],
) -> None:
    """The step chart itself: one bar per rung, a paired annotation on each step."""
    acc = [100 * cond[c]["accuracy"] for c in rungs]
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
    for x, (c, a) in enumerate(zip(rungs, acc, strict=True)):
        _bar(ax, x, a, cond[c]["accuracy_ci"], _colour(c))
    for key, a, b in steps:
        i, j = rungs.index(a), rungs.index(b)
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
    ax.set_xticks(range(len(rungs)), [labels[c] for c in rungs], fontsize=9)
    ax.set_ylim(0, 132)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("accuracy (%), 95% CI")
    ax.set_xlim(-0.5, 3.5)
    ax.grid(axis="x", visible=False)


def _bar(ax: Any, x: float, a: float, ci: list[float], colour: str) -> None:
    lo, hi = (100 * v for v in ci)
    ax.bar(x, a, width=0.56, color=colour, zorder=2)
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


def _compare_panel(ax: Any, cond: dict[str, Any], s: dict[str, Any]) -> None:
    """AMI side panel: notes only, Granola's notes vs Wispr's, paired on the same questions."""
    pair = ("notes_only", "notes_only_wispr")
    acc = [100 * cond[c]["accuracy"] for c in pair]
    for x, c in enumerate(pair):
        _bar(ax, x, acc[x], cond[c]["accuracy_ci"], _colour(c))
    sig = s["sign_p"] < 0.05
    ax.text(
        0.5,
        max(acc) + 9,
        f"Wispr {_pts(acc[1] - acc[0])}\n{s['wins']} better, {s['losses']} worse\n"
        f"{_p(s['sign_p'])}",
        ha="center",
        va="bottom",
        fontsize=8.5,
        color=fs.INK if sig else fs.MUTED,
        fontweight="bold" if sig else "normal",
    )
    ax.set_xticks([0, 1], ["Granola\nnotes", "Wispr Flow\nnotes"], fontsize=9)
    ax.set_title("Notes only, by app", fontsize=9.5, loc="left", fontweight="normal")
    ax.set_ylim(0, 132)
    ax.set_yticks([0, 25, 50, 75, 100], [])
    ax.set_xlim(-0.6, 1.6)
    ax.grid(axis="x", visible=False)


def fig_retention_steps(deletion: dict[str, Any] | None) -> Any:
    """Step chart: accuracy at each rung of the deletion ladder, paired p on each step. On the
    AMI replication, a side panel compares Granola's and Wispr's notes (notes only)."""
    if deletion is None:
        return None
    d = deletion["metrics"]["deletion"]
    cond, paired = d["conditions"], d["paired"]
    rungs, steps = _ladder(deletion)
    if not is_ami(deletion):
        fig, ax = fs.new_figure(4.9)
        _ladder_bars(ax, cond, paired, rungs, steps, LADDER_LABELS)
        fs.title(
            fig,
            "What does deleting transcripts cost, and what brings it back?",
            _dev_note(deletion["meta"]) + "\nEach step is paired on the same questions (exact "
            "sign test); bold = p < 0.05.",
        )
        fs.footnote(
            fig,
            "Notes only and notes + facts are answered by our model over Granola's own notes, "
            "not by Granola's degraded chat. The last bar reads transcripts, so it models 'keep a "
            "cold copy', not deletion. LLM-drafted gold, approved in bulk; n is small. "
            "docs/tracks/RETENTION.md",
        )
        fig.subplots_adjust(left=0.09, right=0.98, top=0.8, bottom=0.25)
        return fig
    side = "notes_only_wispr" in cond and AMI_COMPARE in paired
    if side:
        fig, (ax, ax2) = fs.new_figure(5.1, ncols=2, gridspec_kw={"width_ratios": [4, 1.5]})
        _compare_panel(ax2, cond, paired[AMI_COMPARE])
    else:
        fig, ax = fs.new_figure(5.1)
    _ladder_bars(ax, cond, paired, rungs, steps, AMI_LABELS)
    fs.title(
        fig,
        "What does deleting transcripts cost on public AMI meetings?",
        _ami_note(deletion["meta"]) + "\nEach step is paired on the same questions (exact "
        "sign test); bold = p < 0.05.",
    )
    fs.footnote(
        fig,
        "Gold answers come from AMI's human summaries (summary-level; one human verifier so "
        "far). Silent replay: the apps' notes have no typed notes. Mostly role-play (scenario) "
        "meetings. Notes-only bars are our model over each app's own notes, not the apps' "
        "degraded chat; the last bar reads Granola's transcript ('keep a cold copy'). "
        "docs/tracks/RETENTION.md",
    )
    fig.subplots_adjust(left=0.08, right=0.99, top=0.8, bottom=0.27, wspace=0.12)
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
    rows, short = _rows(deletion), _short(deletion)
    fig, ax = fs.new_figure(3.9 + 0.3 * (len(rows) - len(LADDER)))
    ys = list(range(len(rows)))[::-1]
    for y, c in zip(ys, rows, strict=True):
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
    ax.set_yticks(ys, [f"{short[c]}  (n={cond[c]['n']})" for c in rows])
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
        "confident = judged incorrect with no hedge. "
        + (
            "AMI gold is summary-level, so 'incomplete' often means a missed list item."
            if is_ami(deletion)
            else "Granola's confident errors were 2 of 6 unanswerable questions."
        ),
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


def _cells(
    cond: dict[str, Any], field: str, rows: tuple[str, ...] = LADDER
) -> tuple[list[str], np.ndarray, np.ndarray]:
    keys = sorted({k for c in rows for k in cond[c].get(field, {})})
    acc = np.full((len(rows), len(keys)), np.nan)
    ns = np.zeros((len(rows), len(keys)), dtype=int)
    for i, c in enumerate(rows):
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
    rows = _rows(deletion)
    panels = [(f, *_cells(cond, f, rows)) for f in ("by_tag", "by_category")]
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
        for i in range(len(rows)):
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
    axes[0].set_yticks(range(len(rows)), [_short(deletion)[c] for c in rows])
    fs.title(
        fig,
        "Which questions does deletion hurt, and which layer brings them back?",
        _dev_note(deletion["meta"]) + f"\nAccuracy (%) and n per cell; cells with n < "
        f"{MIN_CELL_N} are greyed out.",
    )
    fs.footnote(
        fig,
        (
            "Small cells: a 95% CI is roughly ±20-40 points, so read rows and columns as "
            "patterns, single cells as hints. Tags: DEV/TEST split and one- vs two-meeting "
            "questions; types are AMI's question templates."
        )
        if is_ami(deletion)
        else "Cells hold 5-23 questions: a 95% CI is roughly ±20-40 points, so read rows and "
        "columns as patterns, single cells as hints. Unanswerable: 'correct' means it declined.",
    )
    fig.subplots_adjust(left=0.25, right=0.99, top=0.78, bottom=0.27, wspace=0.05)
    return fig


# -- capture coverage (docs/tracks/CAPTURE.md, section A) ------------------------------

COVERAGE_MEETING = "EN2002b"  # the one real (unstaged) meeting with a flagged gap
COVERAGE_PRODUCTS = ("granola", "wispr")


def _cov_series(rows: list[list[float]]) -> tuple[list[float], list[float]]:
    return [t / 60 for t, _ in rows], [100 * s for _, s in rows]


def fig_capture_coverage(coverage: dict[str, Any] | None) -> Any:
    """EN2002b timeline: share of each 15 s window each app caught, threshold and control."""
    if coverage is None:
        return None
    meta, summary = coverage["meta"], coverage["summary"]
    meetings = coverage["meetings"]
    m = next(x for x in meetings if x["meeting_id"] == COVERAGE_MEETING)
    thr = 100 * meta["threshold"]
    fig, ax = fs.new_figure(5.2)
    handles = []
    for p in COVERAGE_PRODUCTS:
        cov = m["products"].get(p)
        if cov is None:
            continue
        x, y = _cov_series(cov["windows"])
        (h,) = ax.plot(x, y, color=fs.colour(p), lw=1.4, label=fs.label(p), zorder=3)
        handles.append(h)
    cx, cy = _cov_series(m["control"]["windows"])
    passed = 100 * m["control"]["coverage"]
    (hc,) = ax.plot(cx, cy, color="#999999", lw=1.2, ls=":", zorder=2,
                    label=f"wrong meeting (control): {passed:.0f}% of windows pass")  # fmt: skip
    ht = ax.axhline(thr, color=fs.MUTED, lw=1.1, ls="--", zorder=1,
                    label=f"counts as captured ({thr:.0f}%)")  # fmt: skip
    handles += [ht, hc]
    ax.set_xlim(0, 30)
    ax.set_ylim(-3, 104)
    ax.set_xticks(range(0, 31, 5))
    ax.set_yticks(range(0, 101, 20), [f"{v}%" for v in range(0, 101, 20)])
    ax.set_xlabel("Minutes into the meeting")
    ax.set_ylabel("Caught (% of what was said)")
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.005, 0.885), ncol=4,
               handlelength=1.8, columnspacing=1.2, handletextpad=0.5)  # fmt: skip
    fs.title(
        fig,
        "How much of what was said did each app catch?",
        f"Real (unstaged) AMI meeting {COVERAGE_MEETING}, every 15 seconds; "
        f"{len(meetings)}-meeting summary in the footnote.",
    )
    ctrl = [100 * x["control"]["coverage"] for x in meetings]
    overall = "; ".join(
        f"{fs.label(p)} {100 * summary[p]['coverage']:.1f}% ({summary[p]['meetings']} meetings)"
        for p in COVERAGE_PRODUCTS
        if p in summary
    )
    fs.footnote(
        fig,
        f"All {len(meetings)} AMI meetings, share of 15 s windows caught (≥{thr:.0f}%): "
        f"{overall}; wrong-meeting control {min(ctrl):.0f} to {max(ctrl):.0f}% of windows pass. "
        "Method: share of each window's distinctive word pairs (AMI's human transcript) found "
        "anywhere in the app's transcript. Both apps dip at the same moments (overlapping "
        "speech): hard audio, not lost recording. Threshold-sensitive: at 50% the gaps change.",
    )
    fig.subplots_adjust(left=0.1, right=0.98, top=0.8, bottom=0.22)
    return fig


# -- capture health (docs/tracks/CAPTURE_HEALTH.md) ------------------------------------------

HEALTH_VARIANTS = (  # (key, label, colour, marker)
    ("energy/start", "energy VAD + block starts", fs.OTHER_COLOURS[0], "o"),
    ("oracle/start", "perfect VAD + block starts", fs.OTHER_COLOURS[2], "o"),
    ("energy/span", "energy VAD + live words (est.)", fs.OTHER_COLOURS[0], "^"),
    ("oracle/span", "perfect VAD + live words (est.)", fs.OTHER_COLOURS[2], "^"),
)


def fig_capture_health(detector: dict[str, Any] | None) -> Any:
    """One injected failure on a TEST meeting: speech, transcript blocks, and each alert."""
    if detector is None or detector.get("example") is None:
        return None
    ex, variants = detector["example"], detector["variants"]
    t0, t1 = ex["window_s"]
    start, length = ex["fault"]
    secs = [(t0 + i) / 60 for i in range(len(ex["oracle"]))]
    fig, ax = fs.new_figure(5.2)
    ax.axvspan(start / 60, (start + length) / 60, color="#CC6677", alpha=0.12, lw=0, zorder=0)
    ax.text((start + length / 2) / 60, 4.05, f"injected failure: {length} s of transcript deleted",
            ha="center", va="bottom", fontsize=8.5, color="#882255")  # fmt: skip
    for row, key, colour in (
        (3, "oracle", fs.OTHER_COLOURS[2]),
        (2, "energy", fs.OTHER_COLOURS[0]),
    ):
        base = row - 0.4
        ax.fill_between(secs, base, [base + 0.8 * v for v in ex[key]], step="post",
                        color=colour, alpha=0.75, lw=0)  # fmt: skip
    for t in ex["blocks_kept"]:
        ax.plot([t / 60, t / 60], [0.62, 1.38], color=fs.INK, lw=1.0)
    for t in ex["blocks_deleted"]:
        ax.plot([t / 60, t / 60], [0.62, 1.38], color="#BBBBBB", lw=1.0, ls=(0, (2, 1.5)))
    for i, (key, label, colour, marker) in enumerate(HEALTH_VARIANTS):
        y = 0.5 - 0.36 * i
        onset = ex["alert_onset"].get(key)
        if onset is None:
            ax.text(t1 / 60, y, f"{label}: missed", fontsize=8, va="center", ha="right")
            continue
        ax.plot([start / 60, onset / 60], [y, y], color=colour, lw=1.0, alpha=0.6)
        ax.plot([onset / 60], [y], marker=marker, color=colour, ms=6, ls="none")
        ax.text(onset / 60 + 0.05, y, f"{label}: alert after {onset - start} s",
                fontsize=8, va="center", color=fs.INK)  # fmt: skip
    rows = ["Speech heard\n(AMI word timings)", "Speech heard\n(energy VAD)",
            "Transcript blocks\n(grey: deleted)", "Alert"]  # fmt: skip
    ax.set_yticks([3, 2, 1, -0.04], rows)
    ax.set_ylim(-0.85, 4.4)
    ax.set_xlim(t0 / 60, t1 / 60)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(f"Minutes into AMI meeting {ex['meeting_id']} (TEST)")
    fs.title(
        fig,
        "Can a product tell when its transcript has stopped?",
        "Alert when speech is heard but no new transcript arrives for T seconds. One injected "
        "failure, TEST meeting.",
    )
    parts = []
    for key, label, _, _ in HEALTH_VARIANTS:
        r, p = variants[key]["test"], variants[key]["params"]
        parts.append(f"{label} (T={p['t_s']} s): {r['detected']}/{r['faults_confirmed']} caught, "
                     f"{r['false_alarms_per_hour']:.1f} false alarms/h, median delay "
                     f"{r['delay_s_median']:.0f} s")  # fmt: skip
    test = variants["energy/start"]["test"]
    fs.footnote(
        fig,
        f"TEST, {test['meetings']} AMI meetings, {test['hours']:.1f} h, injected failures of "
        "30 s to 5 min confirmed by the coverage scorer. " + "; ".join(parts) + ". Tuned on DEV "
        "(1 meeting, 15 min) only. 'Live words' spreads each block over its estimated speaking "
        "time (2.6 words/s, assumed). Injected failures, not observed ones.",
    )
    fig.subplots_adjust(left=0.2, right=0.98, top=0.85, bottom=0.3)
    return fig
