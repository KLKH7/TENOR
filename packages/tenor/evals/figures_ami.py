"""README headline figures for the AMI results: one question each, real committed numbers.

Drawn in the paper style (``figstyle.paper_*``): the question as the title, plain axis labels,
short direct labels only. n, interval methods and paired tests go in the README captions.

Inputs (all committed, numbers only):

- ``coverage``: ``evals/reports/ami/coverage.json`` (capture coverage per 15 s window);
- ``ami_retention``: ``evals/reports/ami/deletion_ami.json`` (the retention replication: five
  ways of answering the same AMI questions, with Wilson intervals);
- ``notes_check``: ``evals/reports/ami/notes_check.json``, a content-free snapshot of the
  headline notes-grounding run (the corrected stage-2 run) and the human verification sheet,
  built by :func:`snapshot_notes_check` from the gitignored ``data/ami/results/facts`` files
  (counts only: no claim, question or answer text, no meeting IDs);
- ``wer``: ``evals/reports/ami/wer.json`` (pooled word error rate and its per-meeting range).

Each builder returns a matplotlib figure, or ``None`` when an input is missing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from tenor.evals import figstyle as fs

NOTES_CHECK = Path("evals/reports/ami/notes_check.json")
_PRODUCTS = ("granola", "wispr")


def _pct(x: float, digits: int = 0) -> str:
    return f"{100 * x:.{digits}f}%"


def _product_legend(fig: Any, extra: list[Any] | None = None, y: float = 0.94) -> None:
    """Small legend, top right under the title: the two products, plus any marker keys."""
    handles = [
        Line2D([], [], marker="o", ls="none", ms=7, color=fs.colour(p), label=fs.label(p))
        for p in _PRODUCTS
    ]
    fig.legend(handles=handles + (extra or []), loc="upper right", bbox_to_anchor=(0.985, y),
               ncol=len(handles) + len(extra or []), handletextpad=0.3,
               columnspacing=1.2)  # fmt: skip


def _row_axis(ax: Any, ys: list[float], labels: list[str]) -> None:
    """Rows as y tick labels, no y spine; a 0-100 x axis with a light vertical grid."""
    ax.set_yticks(ys, labels)
    ax.tick_params(axis="y", length=0, pad=8)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, 100)
    ax.set_xticks(range(0, 101, 20))
    ax.grid(axis="x", color=fs.GRID, lw=0.8)
    ax.set_axisbelow(True)


# -- 1. results at a glance --------------------------------------------------------------------


def _coverage_row(coverage: dict[str, Any]) -> dict[str, Any]:
    """Pooled share of 15 s windows captured, whisker = range of the per-meeting shares."""
    thr = coverage["meta"]["threshold"]
    out: dict[str, Any] = {}
    for p in _PRODUCTS:
        k = n = 0
        per = []
        for m in coverage["meetings"]:
            w = m["products"].get(p)
            if w is None:
                continue
            kk = sum(1 for _, s in w["windows"] if s >= thr)
            k, n = k + kk, n + len(w["windows"])
            per.append(kk / len(w["windows"]))
        out[p] = {"k": k, "n": n, "rate": k / n, "lo": min(per), "hi": max(per)}
    return {"label": "Speech captured\n(15-second windows)", "values": out, "whisker": "range"}


def _rate_row(c: dict[str, Any]) -> dict[str, Any]:
    lo, hi = c["ci95_wilson"] if "ci95_wilson" in c else c["ci95"]
    return {"k": c["k"], "n": c["n"], "rate": c["rate"], "lo": lo, "hi": hi}


def _wer_row(wer: dict[str, Any]) -> dict[str, Any]:
    """Pooled word error rate, whisker = range of the per-meeting rates."""
    out = {}
    for p in _PRODUCTS:
        w = wer["products"][p]
        lo, hi = w["wer_range"]
        out[p] = {"n": w["n_meetings"], "rate": w["pooled_wer"], "lo": lo, "hi": hi}
    return {"label": "Word error rate", "values": out, "whisker": "range"}


def overview_rows(
    coverage: dict[str, Any], retention: dict[str, Any], notes: dict[str, Any]
) -> list[dict[str, Any]]:
    """One row per share both products have (higher is better), top to bottom."""
    g = notes["grounding"]["products"]
    cond = retention["conditions"]
    n_q = cond["B"]["n"]
    rows = [_coverage_row(coverage)]
    rows.append(
        {
            "label": "Note claims supported\nby both transcripts",
            "values": {p: _rate_row(g[p]) for p in _PRODUCTS},
            "whisker": "ci",
        }
    )
    for metric, name in (("not_wrong", "correct or partly correct"), ("correct", "correct")):
        rows.append(
            {
                "label": f"Answers from notes:\n{name}",
                "values": {
                    "granola": _rate_row({**cond["B"][metric], "n": n_q}),
                    "wispr": _rate_row({**cond["B_wispr"][metric], "n": n_q}),
                },
                "whisker": "ci",
            }
        )
    return rows


def _dot_rows(ax: Any, rows: list[dict[str, Any]], digits: int) -> list[float]:
    """Granola above, Wispr Flow below, in each row: dot = value, whisker = interval or range."""
    offset = {"granola": 0.16, "wispr": -0.16}
    ys = [float(y) for y in range(len(rows) - 1, -1, -1)]
    for y, row in zip(ys, rows, strict=True):
        for p in _PRODUCTS:
            v, yy, col = row["values"][p], y + offset[p], fs.colour(p)
            ls = (0, (1.5, 1.5)) if row["whisker"] == "range" else "-"
            ax.plot([100 * v["lo"], 100 * v["hi"]], [yy, yy], color=col, lw=1.6, ls=ls,
                    solid_capstyle="butt", zorder=2)  # fmt: skip
            ax.plot([100 * v["rate"]], [yy], "o", color=col, ms=7, zorder=3, clip_on=False)
            d = 1 if v["rate"] > 0.95 else digits
            ax.text(102, yy, _pct(v["rate"], d), va="center", ha="left", color=col,
                    clip_on=False)  # fmt: skip
    return ys


def fig_results_overview(
    coverage: dict[str, Any] | None,
    retention: dict[str, Any] | None,
    notes: dict[str, Any] | None,
    wer: dict[str, Any] | None,
) -> Any:
    """Dot-and-whisker rows: Granola vs Wispr Flow on every measure both have.

    Shares (higher is better) in the top panel; word error rate (lower is better) below it.
    """
    if coverage is None or retention is None or notes is None or wer is None:
        return None
    rows = overview_rows(coverage, retention, notes)
    fig, (top, bottom) = fs.paper_figure(
        5.0, nrows=2, sharex=True, gridspec_kw={"height_ratios": [len(rows), 1.2]}
    )
    ys = _dot_rows(top, rows, 0)
    _row_axis(top, ys, [r["label"] for r in rows])
    top.set_ylim(-0.55, len(rows) - 0.45)
    top.set_title("Higher is better", loc="left", fontsize=fs.PAPER_TEXT, fontweight="normal",
                  color=fs.MUTED)  # fmt: skip
    top.tick_params(axis="x", length=0)
    wer_row = _wer_row(wer)
    ys = _dot_rows(bottom, [wer_row], 1)
    _row_axis(bottom, ys, [wer_row["label"]])
    bottom.set_ylim(-0.55, 0.55)
    bottom.set_title("Lower is better", loc="left", fontsize=fs.PAPER_TEXT, fontweight="normal",
                     color=fs.MUTED)  # fmt: skip
    bottom.set_xlabel("Percent")
    _product_legend(fig)
    fs.paper_title(fig, "Where do Granola and Wispr Flow differ, and by how much?")
    fig.subplots_adjust(left=0.27, right=0.9, top=0.84, bottom=0.11, hspace=0.42)
    return fig


# -- 2. retention ladder -----------------------------------------------------------------------

RETENTION_ROWS = (  # (condition, label, product colour)
    ("A", "Granola Chat (transcript kept)", "granola"),
    ("B", "Answers from Granola's notes", "granola"),
    ("B_wispr", "Answers from Wispr Flow's notes", "wispr"),
    ("C", "Notes + extracted facts", "granola"),
    ("D", "Notes + transcript when needed", "granola"),
)


def fig_ami_retention(retention: dict[str, Any] | None) -> Any:
    """Correct and correct-or-partly-correct per way of answering, with Wilson 95% CIs."""
    if retention is None:
        return None
    cond = retention["conditions"]
    fig, ax = fs.paper_figure(4.4)
    ys = [float(len(RETENTION_ROWS) - 1 - i) for i in range(len(RETENTION_ROWS))]
    for y, (c, _, key) in zip(ys, RETENTION_ROWS, strict=True):
        col = fs.colour(key)
        for m, dy, face in (
            (cond[c]["correct"], 0.14, col),
            (cond[c]["not_wrong"], -0.14, "white"),
        ):
            lo, hi = m["ci95_wilson"]
            ax.plot([100 * lo, 100 * hi], [y + dy, y + dy], color=col, lw=1.4,
                    solid_capstyle="butt", zorder=2)  # fmt: skip
            ax.plot([100 * m["rate"]], [y + dy], "o", ms=7, mfc=face, mec=col, mew=1.6,
                    zorder=3)  # fmt: skip
            ax.text(100 * hi + 1.5, y + dy, _pct(m["rate"]), va="center", ha="left", color=col)
    _row_axis(ax, ys, [lab for _, lab, _ in RETENTION_ROWS])
    ax.set_ylim(-0.55, len(RETENTION_ROWS) - 0.45)
    ax.set_xlabel("Share of questions (%)")
    markers = [
        Line2D([], [], marker="o", ls="none", ms=7, mfc=fs.MUTED, mec=fs.MUTED, label="Correct"),
        Line2D(
            [],
            [],
            marker="o",
            ls="none",
            ms=7,
            mfc="white",
            mec=fs.MUTED,
            mew=1.6,
            label="Correct or partly correct",
        ),
    ]
    _product_legend(fig, markers, y=0.925)
    fs.paper_title(fig, "What does deleting transcripts cost, and what wins it back?")
    fig.subplots_adjust(left=0.33, right=0.97, top=0.81, bottom=0.14)
    return fig


# -- 3. judge vs human -------------------------------------------------------------------------

HUMAN_LABELS = (  # (key, legend label, colour): green to red, grey for "could not judge"
    ("supported", "Supported", "#117733"),
    ("partly_right", "Partly right", "#DDCC77"),
    ("overreach", "Went beyond what was said", "#EE8866"),
    ("wrong", "Wrong", "#CC6677"),
    ("cannot_judge", "Could not judge", "#BBBBBB"),
)
JUDGE_ROWS = (  # (set, automated verdict, label)
    ("claims", "contradicted", "Note claims the checker\ncalled “contradicted”"),
    ("claims", "inferred", "Note claims the checker\ncalled “inferred”"),
    ("claims", "supported", "Note claims the checker\ncalled “supported”"),
    (
        "chat",
        "confident_contradiction",
        "Chat answers the judge\ncalled “confidently wrong”",
    ),
)


def fig_judge_vs_human(notes: dict[str, Any] | None) -> Any:
    """Counts: for each automated verdict, what the human verifier said about the same items."""
    if notes is None:
        return None
    h = notes["human"]
    rows = [(lab, h[s]["by_automated"].get(v, {})) for s, v, lab in JUDGE_ROWS]
    fig, ax = fs.paper_figure(4.2)
    ys: list[float] = [float(i) for i in range(len(rows) - 1, -1, -1)]
    ys[-1] -= 0.4  # a gap before the Chat row: a different judge and item type
    width = max(sum(c.values()) for _, c in rows)
    for y, (_, counts) in zip(ys, rows, strict=True):
        left = 0
        for key, _, colour in HUMAN_LABELS:
            k = counts.get(key, 0)
            if k == 0:
                continue
            ax.barh(y, k, left=left, height=0.62, color=colour, edgecolor="white", lw=1.5,
                    zorder=2)  # fmt: skip
            ax.text(left + k / 2, y, str(k), ha="center", va="center", fontweight="bold",
                    color="white" if key == "supported" else fs.INK, zorder=3)  # fmt: skip
            left += k
    ax.set_yticks(ys, [lab for lab, _ in rows])
    ax.tick_params(axis="y", length=0, pad=8)
    ax.spines["left"].set_visible(False)
    ax.set_xlim(0, width + 0.2)
    ax.set_xticks(range(0, width + 1))
    ax.set_xlabel("Items labelled by a human (count)")
    ax.set_ylim(min(ys) - 0.55, max(ys) + 0.55)
    present = {k for _, c in rows for k, v in c.items() if v}
    handles = [
        Patch(color=colour, label=lab + ("" if key in present else " (none)"))
        for key, lab, colour in HUMAN_LABELS
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.01, 0.92), ncol=3,
               handlelength=1.2, columnspacing=1.2, handletextpad=0.4, title="Human verdict",
               title_fontsize=fs.PAPER_TEXT, alignment="left")  # fmt: skip
    fs.paper_title(fig, "When an automated judge says “wrong”, does a human agree?")
    fig.subplots_adjust(left=0.3, right=0.97, top=0.7, bottom=0.14)
    return fig


# -- content-free snapshot of the private notes-check files ------------------------------------

# Codes used on the v3 verification sheet (docs/method/HUMAN_VERIFICATION.md). Only codes the
# sheet actually uses are mapped; a new code raises KeyError instead of being guessed.
_CLAIM_CODES = {"S": "supported", "O": "overreach", "?": "cannot_judge"}
_CHAT_CODES = {"B": "partly_right"}  # "both partly right"
_AGREE = {"supported": "supported", "inferred": "overreach"}


# Classes copied into the snapshot (each as k / n / rate / Wilson ci95), per product.
NOTES_CLASSES = (
    "ok",
    "transcript_propagated",
    "note_error",
    "borderline",
    "inferred",
    "human_only",
)


def _human_verdicts(claims: Path) -> dict[tuple[str, str, str], str]:
    """(product, meeting, claim text) -> the AMI-side verdict, from a stage-2 claims JSONL."""
    out: dict[tuple[str, str, str], str] = {}
    for line in claims.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[(str(r["product"]), str(r["meeting_id"]), str(r["text"]))] = str(r["human"])
    return out


def snapshot_notes_check(grounding: Path, verification: Path, claims: Path) -> dict[str, Any]:
    """Counts only, from the gitignored grounding run and human verification sheet.

    ``grounding``: the headline stage-2 aggregate (``stage2.json``: the corrected run, with the
    borderline class, named AMI speakers and the labels note). ``verification``: the v3 human
    sheet (JSONL). Its automated verdicts (``checker_human_verdict_run1``) are stored on the
    sheet and come from the *first* grounding pass, on which the sample was drawn; the figure
    compares the human with those. ``claims``: the same run's ``stage2.claims.jsonl``; each
    sheet row is matched to it by (product, meeting, claim text), the sheet has no claim IDs,
    to also count agreement with the headline run's verdicts. Only the sheet's product, verdict
    and human-code fields, and the claims' verdicts, reach the output.
    """
    rec = json.loads(grounding.read_text())
    products: dict[str, Any] = {}
    for p, v in sorted(rec["products"].items()):
        cls = v["classes"]
        products[p] = {
            **{f: cls["ok"][f] for f in ("k", "n", "rate", "ci95")},
            "classes": {
                c: {f: cls[c][f] for f in ("k", "n", "rate", "ci95")} for c in NOTES_CLASSES
            },
        }
    headline = _human_verdicts(claims)
    claims_by: dict[str, dict[str, int]] = {}
    chat: dict[str, dict[str, int]] = {}
    verifiers: set[str] = set()
    n_claims = agree = judgeable = n_chat = matched = agree_headline = 0
    for line in verification.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        verifiers.add(str(r.get("verifier")))
        if r.get("set") == "stage3_contradictions":
            for item in r["items"]:
                human = _CHAT_CODES[item["kian"]]
                cell = chat.setdefault("confident_contradiction", {})
                cell[human] = cell.get(human, 0) + 1
                n_chat += 1
            continue
        auto = str(r["checker_human_verdict_run1"])
        human = _CLAIM_CODES[r["kian"]]
        cell = claims_by.setdefault(auto, {})
        cell[human] = cell.get(human, 0) + 1
        n_claims += 1
        now = headline.get((str(r["product"]), str(r["meeting"]), str(r["claim"])))
        matched += now is not None
        if human != "cannot_judge":
            judgeable += 1
            agree += int(_AGREE.get(auto, auto) == human)
            if now is not None:
                agree_headline += int(_AGREE.get(now, now) == human)
    return {
        "note": "Content-free counts from the gitignored data/ami/results/facts files: no claim, "
        "question or answer text and no meeting IDs. Rebuild with `tenor figures <stamp> "
        "--refresh-ami`.",
        "sources": sorted(p.name for p in (grounding, verification, claims)),
        "grounding": {
            "what": "note claims supported by both the app's and AMI's transcript (class ok)",
            "run": rec["meta"].get("git_sha"),
            "date": rec["meta"].get("date"),
            "render": rec["meta"].get("render"),
            "products": products,
        },
        "human": {
            "verifiers": len(verifiers),
            "claims": {
                "n": n_claims,
                "judgeable": judgeable,
                "agree_with_checker": agree,
                "checker_run": "first grounding pass (verdicts stored on the sheet)",
                "matched_in_headline_run": matched,
                "agree_with_headline_run": agree_headline,
                "by_automated": {k: dict(sorted(v.items())) for k, v in sorted(claims_by.items())},
            },
            "chat": {
                "n": n_chat,
                "by_automated": {k: dict(sorted(v.items())) for k, v in sorted(chat.items())},
            },
        },
    }
