"""README headline figures for the AMI results: one question each, real committed numbers.

Inputs (all committed, numbers only):

- ``coverage``: ``evals/reports/ami/coverage.json`` (capture coverage per 15 s window);
- ``ami_retention``: ``evals/reports/ami/deletion_ami.json`` (the retention replication, 5
  conditions A, B, B', C, D on the same AMI questions, with paired sign tests);
- ``notes_check``: ``evals/reports/ami/notes_check.json``, a content-free snapshot of the
  notes-grounding pass and the human verification sheet, built by :func:`snapshot_notes_check`
  from the gitignored ``data/ami/results/facts`` files (counts only: no claim, question or
  answer text, no meeting IDs);
- ``deletion``: ``evals/reports/real-deletion-sim-dev.aggregate.json`` (the earlier private
  study), used only for a footnote on the non-replication.

Each builder returns a matplotlib figure, or ``None`` when an input is missing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from matplotlib.patches import Patch

from concentrate.evals import figstyle as fs

NOTES_CHECK = Path("evals/reports/ami/notes_check.json")
_PRODUCTS = ("granola", "wispr")


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def _p(p: float) -> str:
    """'p = 0.001', or 'n.s.' with the value when p >= 0.05."""
    if p < 0.001:
        return "p < 0.001"
    if p < 0.01:
        return f"p = {p:.3f}"
    return f"p = {p:.2g}" if p < 0.05 else f"p = {p:.2g}, n.s."


def _paired(rows: list[dict[str, Any]], before: str, after: str, metric: str) -> dict[str, Any]:
    """Paired counts for the move ``before`` -> ``after``: wins = only ``after`` meets it."""
    for r in rows:
        if r["metric"] != metric:
            continue
        if (r["a"], r["b"]) == (after, before):
            return {"wins": r["a_only"], "losses": r["b_only"], "p": r["p_two_sided"]}
        if (r["a"], r["b"]) == (before, after):
            return {"wins": r["b_only"], "losses": r["a_only"], "p": r["p_two_sided"]}
    raise KeyError(f"no paired test {before} vs {after} ({metric})")


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
    return {
        "label": "Speech captured\n(15 s windows)",
        "n": f"n = {out['granola']['n']} windows each",
        "whisker": "range across meetings",
        "values": out,
        "note": "",
    }


def _rate_row(c: dict[str, Any]) -> dict[str, Any]:
    lo, hi = c["ci95_wilson"] if "ci95_wilson" in c else c["ci95"]
    return {"k": c["k"], "n": c["n"], "rate": c["rate"], "lo": lo, "hi": hi}


def overview_rows(
    coverage: dict[str, Any], retention: dict[str, Any], notes: dict[str, Any]
) -> list[dict[str, Any]]:
    """One row per measure both products have, top to bottom."""
    g = notes["grounding"]["products"]
    cond = retention["conditions"]
    n_q = cond["B"]["n"]
    rows = [_coverage_row(coverage)]
    rows.append(
        {
            "label": "Note claims supported\nby both transcripts",
            "n": f"n = {g['granola']['n']} / {g['wispr']['n']} claims",
            "whisker": "95% CI",
            "values": {p: _rate_row(g[p]) for p in _PRODUCTS},
            "note": "different claims, no paired\ntest; checker noise inflates\n"
            "the gap (see Notes)",
        }
    )
    for metric, name in (("not_wrong", "not wrong"), ("correct", "correct")):
        s = _paired(retention["paired"], "B", "B_wispr", metric)
        rows.append(
            {
                "label": f"Notes-only answers\n{name}",
                "n": f"n = {n_q} questions each",
                "whisker": "95% CI",
                "values": {
                    "granola": _rate_row({**cond["B"][metric], "n": n_q}),
                    "wispr": _rate_row({**cond["B_wispr"][metric], "n": n_q}),
                },
                "note": f"paired: Wispr {s['wins']} better,\n{s['losses']} worse; {_p(s['p'])}",
            }
        )
    return rows


def fig_results_overview(
    coverage: dict[str, Any] | None,
    retention: dict[str, Any] | None,
    notes: dict[str, Any] | None,
) -> Any:
    """Dot-and-whisker rows: Granola vs Wispr Flow on every measure both have."""
    if coverage is None or retention is None or notes is None:
        return None
    rows = overview_rows(coverage, retention, notes)
    fig, ax = fs.new_figure(4.9)
    offset = {"granola": 0.15, "wispr": -0.15}
    ys = list(range(len(rows) - 1, -1, -1))
    for y, row in zip(ys, rows, strict=True):
        if y % 2 == 1:
            ax.axhspan(y - 0.5, y + 0.5, color=fs.REAL_TINT, lw=0, zorder=0)
        for p in _PRODUCTS:
            v = row["values"][p]
            yy = y + offset[p]
            ls = (0, (1.5, 1.5)) if row["whisker"] != "95% CI" else "-"
            ax.plot([100 * v["lo"], 100 * v["hi"]], [yy, yy], color=fs.colour(p), lw=1.6,
                    ls=ls, solid_capstyle="butt", zorder=2)  # fmt: skip
            ax.plot([100 * v["rate"]], [yy], "o", color=fs.colour(p), ms=7, zorder=3,
                    clip_on=False)  # fmt: skip
            ax.text(103, yy, f"{100 * v['rate']:.1f}%" if v["rate"] > 0.95 else _pct(v["rate"]),
                    va="center", ha="left", fontsize=9, color=fs.colour(p),
                    fontweight="bold")  # fmt: skip
        ax.text(117, y, row["note"], va="center", ha="left", fontsize=8, color=fs.MUTED,
                clip_on=False)  # fmt: skip
    ax.set_yticks(
        ys, [f"{r['label']}\n{r['n']}" for r in rows], fontsize=8.5, multialignment="right"
    )
    ax.set_ylim(-0.6, len(rows) - 0.4)
    ax.set_xlim(0, 100)
    ax.set_xticks(range(0, 101, 20), [f"{v}%" for v in range(0, 101, 20)])
    ax.set_xlabel("Share (higher is better)")
    ax.grid(axis="y", visible=False)
    for side in ("left",):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="y", length=0)
    handles = [ax.plot([], [], "o-", color=fs.colour(p), label=fs.label(p))[0] for p in _PRODUCTS]
    handles.append(ax.plot([], [], color=fs.MUTED, lw=1.6, label="95% Wilson CI")[0])
    handles.append(
        ax.plot(
            [],
            [],
            color=fs.MUTED,
            lw=1.6,
            ls=(0, (1.5, 1.5)),
            label=f"range across the {len(coverage['meetings'])} meetings",
        )[0]
    )
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.005, 0.875), ncol=4,
               handlelength=1.8, columnspacing=1.2, handletextpad=0.5)  # fmt: skip
    meta = retention["meta"]
    fs.title(
        fig,
        "Where do Granola and Wispr Flow differ, and by how much?",
        f"Every measure both products have, on public AMI meetings ({len(coverage['meetings'])} "
        f"replayed; answers on {meta['meetings']}). Preliminary; small n.",
    )
    fs.footnote(
        fig,
        "Capture: share of 15 s windows of AMI speech found in the app's transcript (windows "
        "cluster by meeting, so the whisker is the per-meeting range, not a CI). Notes: first "
        "automated grounding pass; claims differ between apps, so no paired test. Answers: our "
        "reader over each app's own notes, judged against AMI's human summaries; paired sign "
        "test on the same questions. Granola-only measures (Chat answers) are omitted here.",
    )
    fig.subplots_adjust(left=0.22, right=0.68, top=0.79, bottom=0.25)
    return fig


# -- 2. retention ladder -----------------------------------------------------------------------

RETENTION_ROWS = (  # (condition, label, colour key)
    ("A", "A  Granola Chat,\ntranscript kept", "granola_agent"),
    ("B", "B  Granola notes only", "notes_only"),
    ("B_wispr", "B\u2032 Wispr Flow notes only", "wispr"),
    ("C", "C  B + fact layer", "notes_facts"),
    ("D", "D  C + transcript when\nC abstains or hedges", "notes_facts_fallback"),
)
# Sky blue is too pale for small text on white; its labels use a darker blue of the same hue.
_TEXT_COLOUR = {fs.colour("notes_facts_fallback"): "#2B7FB0"}


def _split(meta: dict[str, Any]) -> str:
    """'13 DEV + 55 TEST' from the aggregate's split counts."""
    dev = sum(v for k, v in meta["split_counts"].items() if k.startswith("dev"))
    test = sum(v for k, v in meta["split_counts"].items() if k.startswith("test"))
    return f"{dev} DEV + {test} TEST"


RETENTION_STEPS = (("A", "B"), ("B", "B_wispr"), ("B", "C"), ("C", "D"))
_STEP_NAMES = {"A": "A", "B": "B", "B_wispr": "B\u2032", "C": "C", "D": "D"}


def fig_ami_retention(retention: dict[str, Any] | None, earlier: dict[str, Any] | None) -> Any:
    """Correct and not-wrong per condition, Wilson CIs, paired wins-losses between rungs."""
    if retention is None:
        return None
    cond, paired, meta = retention["conditions"], retention["paired"], retention["meta"]
    n_q = meta["questions"]
    fig, ax = fs.new_figure(5.6)
    ys = {c: len(RETENTION_ROWS) - 1 - i for i, (c, _, _) in enumerate(RETENTION_ROWS)}
    for c, _, key in RETENTION_ROWS:
        y, col = ys[c], fs.colour(key)
        if c in ("B", "B_wispr", "C"):
            ax.axhspan(y - 0.5, y + 0.5, color=fs.REAL_TINT, lw=0, zorder=0)
        cr, nw = cond[c]["correct"], cond[c]["not_wrong"]
        for m, dy, face in ((cr, 0.12, col), (nw, -0.12, "white")):
            lo, hi = m["ci95_wilson"]
            ax.plot([100 * lo, 100 * hi], [y + dy, y + dy], color=col, lw=1.4, zorder=2)
            ax.plot([100 * m["rate"]], [y + dy], "o", ms=7, mfc=face, mec=col, mew=1.6,
                    zorder=3)  # fmt: skip
            ax.text(100 * hi + 1.2, y + dy, _pct(m["rate"]), va="center", ha="left",
                    fontsize=8.5, color=_TEXT_COLOUR.get(col, col))  # fmt: skip
    ax.text(2, ys["B"] + 0.38, "transcripts deleted", fontsize=8, color=fs.MUTED,
            style="italic", va="center")  # fmt: skip
    # Paired steps, in the right margin, each between the two rows it compares.
    for before, after in RETENTION_STEPS:
        yb, ya = ys[before], ys[after]
        s1 = _paired(paired, before, after, "correct")
        s2 = _paired(paired, before, after, "not_wrong")
        sig = s1["p"] < 0.05
        x = 112 if (before, after) == ("B", "C") else 116
        ax.plot([x, x], [yb - 0.08, ya + 0.08], color=fs.INK if sig else fs.MUTED, lw=0.9,
                clip_on=False)  # fmt: skip
        ax.plot([x], [ya + 0.08], marker="v", ms=4, color=fs.INK if sig else fs.MUTED,
                clip_on=False)  # fmt: skip
        mid = (yb + ya) / 2 if (before, after) != ("B", "C") else ys["B_wispr"] - 0.5
        ax.text(
            x + 1.5,
            mid,
            f"{_STEP_NAMES[before]}\u2192{_STEP_NAMES[after]}: correct {s1['wins']}\u2013"
            f"{s1['losses']}, {_p(s1['p'])}\n"
            f"not wrong {s2['wins']}\u2013{s2['losses']}, {_p(s2['p'])}",
            va="center",
            ha="left",
            fontsize=7.8,
            color=fs.INK if sig else fs.MUTED,
            fontweight="bold" if sig else "normal",
            clip_on=False,
        )
    ax.set_yticks([ys[c] for c, _, _ in RETENTION_ROWS], [lab for _, lab, _ in RETENTION_ROWS],
                  fontsize=8.5, multialignment="right")  # fmt: skip
    for tick, (c, _, _) in zip(ax.get_yticklabels(), RETENTION_ROWS, strict=True):
        if c == "B_wispr":
            tick.set_color(fs.colour("wispr"))
            tick.set_fontweight("bold")
    ax.set_ylim(-0.6, len(RETENTION_ROWS) - 0.4)
    ax.set_xlim(0, 100)
    ax.set_xticks(range(0, 101, 20), [f"{v}%" for v in range(0, 101, 20)])
    ax.set_xlabel(f"Share of the {n_q} questions")
    ax.grid(axis="y", visible=False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    handles = [
        ax.plot([], [], "o", ms=7, mfc=fs.INK, mec=fs.INK, label="correct")[0],
        ax.plot(
            [],
            [],
            "o",
            ms=7,
            mfc="white",
            mec=fs.INK,
            mew=1.6,
            label="not wrong (correct or partly correct)",
        )[0],
        ax.plot([], [], color=fs.INK, lw=1.4, label="95% Wilson CI")[0],
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.005, 0.86), ncol=3,
               handlelength=1.6, columnspacing=1.2, handletextpad=0.4)  # fmt: skip
    fs.title(
        fig,
        f"What does deleting transcripts cost, and what wins it back? ({n_q} AMI questions)",
        f"AMI, {meta['meetings']} meetings, {n_q} questions ({_split(meta)}), each answered over "
        "its own meeting(s).\nRight: paired wins\u2013losses on the same questions (exact sign "
        "test); bold = p < 0.05 on correct.",
    )
    note = ""
    if earlier is not None:
        e = earlier["metrics"]["deletion"]["conditions"]
        ad = _paired(paired, "D", "A", "not_wrong")
        note = (
            f" Not replicated: in the earlier private study (author's 4 meetings, "
            f"{earlier['meta']['questions']} DEV questions) D scored "
            f"{_pct(e['notes_facts_fallback']['accuracy'])} vs Granola's agent "
            f"{_pct(e['granola_agent']['accuracy'])}; here A beats D on not wrong, "
            f"{ad['wins']}\u2013{ad['losses']} (p = {ad['p']:.2g}, uncorrected)."
        )
    fs.footnote(
        fig,
        f"A is Granola's own Chat; B to D are our reader ({meta['reader_model'].split(' ')[0]}) "
        f"over each app's notes, so A\u2192B mixes deletion with a change of model. Judge "
        f"{meta['judge_model']} against AMI's human summaries; it over-counts errors (see "
        "Answers)." + note,
    )
    fig.subplots_adjust(left=0.22, right=0.6, top=0.76, bottom=0.22)
    return fig


# -- 3. judge vs human -------------------------------------------------------------------------

HUMAN_LABELS = (  # (key, legend label, colour): green to red, grey for "could not judge"
    ("supported", "fine (supported)", "#117733"),
    ("partly_right", "partly right", "#DDCC77"),
    ("overreach", "overreach", "#EE8866"),
    ("wrong", "wrong", "#CC6677"),
    ("cannot_judge", "could not judge", "#BBBBBB"),
)
JUDGE_ROWS = (  # (set, automated verdict, label)
    ("claims", "contradicted", "Note claim: checker said\n\u201ccontradicted\u201d"),
    ("claims", "inferred", "Note claim: checker said\n\u201coverreach\u201d"),
    ("claims", "supported", "Note claim: checker said\n\u201csupported\u201d"),
    ("chat", "confident_contradiction", "Chat answer: judge said\n\u201cconfidently wrong\u201d"),
)


def fig_judge_vs_human(notes: dict[str, Any] | None) -> Any:
    """Counts: for each automated verdict, what the human verifier said about the same items."""
    if notes is None:
        return None
    h = notes["human"]
    rows = [(s, v, lab, h[s]["by_automated"].get(v, {})) for s, v, lab in JUDGE_ROWS]
    fig, ax = fs.new_figure(4.6)
    ys: list[float] = [float(i) for i in range(len(rows) - 1, -1, -1)]
    ys[-1] -= 0.4  # a gap before the Chat row: a different judge and item type
    width = max(sum(c.values()) for *_, c in rows)
    for y, (_, _, _, counts) in zip(ys, rows, strict=True):
        left = 0
        for key, _, colour in HUMAN_LABELS:
            k = counts.get(key, 0)
            if k == 0:
                continue
            for u in range(k):  # one cell per label, so small n stays countable
                ax.barh(y, 1, left=left + u, height=0.62, color=colour, edgecolor="white",
                        lw=1.5, zorder=2)  # fmt: skip
            on_dark = key == "supported"
            ax.text(left + k / 2, y, str(k), ha="center", va="center", fontsize=10,
                    fontweight="bold", color="white" if on_dark else fs.INK, zorder=3,
                    bbox={"boxstyle": "round,pad=0.15", "fc": colour, "ec": "none"})  # fmt: skip
            left += k
        wrong = counts.get("wrong", 0)
        ax.text(left + 0.15, y, f"n = {left}; human found {wrong} wrong", va="center",
                ha="left", fontsize=8.5, color=fs.INK)  # fmt: skip
    ax.set_yticks(ys, [f"{lab}" for _, _, lab, _ in rows], fontsize=8.5, multialignment="right")
    ax.set_xlim(0, width + 4.2)
    ax.set_xticks(range(0, width + 1))
    ax.set_xlabel("Number of items (each cell = one human label)")
    ax.set_ylim(min(ys) - 0.55, max(ys) + 0.55)
    ax.grid(axis="y", visible=False)
    ax.spines["left"].set_visible(False)
    ax.tick_params(axis="y", length=0)
    present = {k for *_, c in rows for k, v in c.items() if v}
    handles = [
        Patch(color=colour, label=lab + ("" if key in present else " (0)"))
        for key, lab, colour in HUMAN_LABELS
    ]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.005, 0.85), ncol=5,
               handlelength=1.2, columnspacing=1.0, handletextpad=0.4,
               title="Human verdict:", title_fontsize=8.5, alignment="left")  # fmt: skip
    cl = h["claims"]
    agree, judgeable = cl["agree_with_checker"], cl["judgeable"]
    total = cl["n"] + h["chat"]["n"]
    fs.title(
        fig,
        f"When an automated judge says \u201cwrong\u201d, does a human agree? ({total} labels)",
        f"Human: {h['verifiers']} verifier, reading the AMI source. Of {judgeable} note claims the "
        f"human could judge, they agreed with the checker\non {agree}. Counts, not rates: "
        "the sample is too small for an interval to mean much.",
    )
    fs.footnote(
        fig,
        f"Note claims: {cl['n']} sampled from both apps' notes, leaning towards items the "
        "checker flagged (so this overstates the over-count a uniform sample would show); "
        "\u201csupported\u201d rows were flagged by the app-transcript check, or are controls. "
        f"Chat: the {h['chat']['n']} answers the automated judge called the strongest confident "
        "contradictions. One verifier, who also designed the study; target about 100 labels "
        "and a second verifier. docs/method/HUMAN_VERIFICATION.md",
    )
    fig.subplots_adjust(left=0.25, right=0.98, top=0.68, bottom=0.25)
    return fig


# -- content-free snapshot of the private notes-check files ------------------------------------

# Codes used on the v3 verification sheet (docs/method/HUMAN_VERIFICATION.md). Only codes the
# sheet actually uses are mapped; a new code raises KeyError instead of being guessed.
_CLAIM_CODES = {"S": "supported", "O": "overreach", "?": "cannot_judge"}
_CHAT_CODES = {"B": "partly_right"}  # "both partly right"
_AGREE = {"supported": "supported", "inferred": "overreach"}


def snapshot_notes_check(reclassified: Path, verification: Path) -> dict[str, Any]:
    """Counts only, from the gitignored grounding pass and human verification sheet.

    ``reclassified``: ``stage2.reclassified.json`` (first grounding pass, re-classified at $0;
    the numbers in the README's Notes table). ``verification``: the v3 human sheet (JSONL);
    only its product, automated-verdict and human-code fields are read.
    """
    rec = json.loads(reclassified.read_text())
    grounding = {
        p: {
            "k": v["classes"]["ok"]["k"],
            "n": v["classes"]["ok"]["n"],
            "rate": v["classes"]["ok"]["rate"],
            "ci95": v["classes"]["ok"]["ci95"],
        }
        for p, v in sorted(rec["products"].items())
    }
    claims: dict[str, dict[str, int]] = {}
    chat: dict[str, dict[str, int]] = {}
    verifiers: set[str] = set()
    n_claims = agree = judgeable = n_chat = 0
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
        cell = claims.setdefault(auto, {})
        cell[human] = cell.get(human, 0) + 1
        n_claims += 1
        if human != "cannot_judge":
            judgeable += 1
            agree += int(_AGREE.get(auto, auto) == human)
    return {
        "note": "Content-free counts from the gitignored data/ami/results/facts files: no claim, "
        "question or answer text and no meeting IDs. Rebuild with `concentrate figures <stamp> "
        "--refresh-ami`.",
        "sources": sorted(p.name for p in (reclassified, verification)),
        "grounding": {
            "what": "note claims supported by both the app's and AMI's transcript (class ok)",
            "run": rec["meta"].get("git_sha"),
            "products": grounding,
        },
        "human": {
            "verifiers": len(verifiers),
            "claims": {
                "n": n_claims,
                "judgeable": judgeable,
                "agree_with_checker": agree,
                "by_automated": {k: dict(sorted(v.items())) for k, v in sorted(claims.items())},
            },
            "chat": {
                "n": n_chat,
                "by_automated": {k: dict(sorted(v.items())) for k, v in sorted(chat.items())},
            },
        },
    }
