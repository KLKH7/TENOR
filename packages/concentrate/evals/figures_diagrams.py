"""Explanatory figures: how Hindsight works, and one card per track.

Drawn with the same matplotlib style as the results figures, so they share the palette, the
deterministic SVG and the values-sidecar check. The track cards' headline numbers are read
from the committed real-meeting aggregates, so a changed result can't leave a stale card.
"""

from __future__ import annotations

from textwrap import fill
from typing import Any

from matplotlib.patches import Arc, FancyBboxPatch, Polygon, Rectangle

from concentrate.evals import figstyle as fs

# Overview canvas, in data units with equal aspect (width x height of the figure, x20).
W, H = 160.0, 100.0


def _canvas(height_in: float) -> tuple[Any, Any]:
    fig, ax = fs.new_figure(height_in)
    ax.set_position((0, 0, 1, 1))
    ax.set_xlim(0, W)
    ax.set_ylim(0, W * height_in / fs.WIDTH_IN)
    ax.set_aspect("equal")
    ax.axis("off")
    return fig, ax


def _box(
    ax: Any,
    x: float,
    y: float,
    w: float,
    h: float,
    fc: str,
    ec: str = "none",
    r: float = 1.6,
    lw: float = 1.0,
) -> None:
    ax.add_patch(
        FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", fc=fc, ec=ec, lw=lw)
    )


def _arrow(ax: Any, x0: float, x1: float, y: float) -> None:
    ax.annotate(
        "",
        xy=(x1, y),
        xytext=(x0, y),
        arrowprops={"arrowstyle": "-|>", "color": fs.MUTED, "lw": 1.6},
    )


# -- glyphs: one per track, drawn from primitives so no font has to carry them -----------------


def _glyph(ax: Any, kind: str, cx: float, cy: float, s: float = 2.6, col: str = fs.INK) -> None:
    """A small icon centred on (cx, cy), about 2s wide."""
    lw = 1.4
    if kind == "qa":  # speech bubble with a question mark
        _box(ax, cx - s, cy - 0.6 * s, 2 * s, 1.5 * s, "none", col, r=0.5 * s, lw=lw)
        ax.add_patch(
            Polygon(
                [
                    (cx - 0.5 * s, cy - 0.6 * s),
                    (cx - 0.9 * s, cy - 1.1 * s),
                    (cx - 0.1 * s, cy - 0.6 * s),
                ],
                fc=col,
                ec=col,
                lw=0.5,
            )
        )
        ax.text(
            cx,
            cy + 0.15 * s,
            "?",
            ha="center",
            va="center",
            fontsize=9,
            fontweight="bold",
            color=col,
        )
    elif kind == "retention":  # a page, struck through
        ax.add_patch(Rectangle((cx - 0.75 * s, cy - s), 1.5 * s, 2 * s, fc="none", ec=col, lw=lw))
        for k in range(3):
            yy = cy + 0.5 * s - 0.5 * k * s
            ax.plot([cx - 0.45 * s, cx + 0.45 * s], [yy, yy], color=col, lw=1)
        ax.plot([cx - 1.05 * s, cx + 1.05 * s], [cy - 1.1 * s, cy + 1.1 * s], color="#CC6677", lw=2)
    elif kind == "faithfulness":  # a page with a tick
        ax.add_patch(Rectangle((cx - 0.75 * s, cy - s), 1.5 * s, 2 * s, fc="none", ec=col, lw=lw))
        ax.plot(
            [cx - 0.45 * s, cx - 0.1 * s, cx + 0.5 * s],
            [cy, cy - 0.45 * s, cy + 0.5 * s],
            color="#117733",
            lw=2.2,
        )
    elif kind == "agent":  # two arrows: an agent asks, a product answers
        for dy, x0, x1 in ((0.4 * s, cx - s, cx + s), (-0.4 * s, cx + s, cx - s)):
            ax.annotate(
                "",
                xy=(x1, cy + dy),
                xytext=(x0, cy + dy),
                arrowprops={"arrowstyle": "-|>", "color": col, "lw": lw},
            )
    elif kind == "integrity":  # a padlock
        ax.add_patch(Rectangle((cx - 0.8 * s, cy - s), 1.6 * s, 1.15 * s, fc=col, ec=col))
        ax.add_patch(
            Arc((cx, cy + 0.15 * s), 1.1 * s, 1.5 * s, theta1=0, theta2=180, ec=col, lw=lw + 0.4)
        )
        ax.plot([cx - 0.55 * s, cx - 0.55 * s], [cy + 0.15 * s, cy], color=col, lw=lw + 0.4)
        ax.plot([cx + 0.55 * s, cx + 0.55 * s], [cy + 0.15 * s, cy], color=col, lw=lw + 0.4)


# -- the five tracks: one source of truth for both figures -------------------------------------

TRACKS: tuple[tuple[str, str, str, str, str, str], ...] = (
    # (glyph, name, one line for the overview, task, metrics, status)
    (
        "qa",
        "Question answering",
        "recall across many meetings",
        "Answer questions across many meetings",
        "accuracy · confident error · citations · $ · latency",
        "built",
    ),
    (
        "retention",
        "Retention",
        "answer after transcripts are deleted",
        "Answer after transcripts are deleted",
        "deletion cost by question type · recovery per layer",
        "built",
    ),
    (
        "faithfulness",
        "Notes faithfulness",
        "is each note backed by the meeting?",
        "Is every line of the notes backed by what was said?",
        "supported · inferred · unsupported · contradicted",
        "in progress",
    ),
    (
        "agent",
        "Agent interface quality",
        "what an outside agent gets back",
        "An outside agent asks a product a question through its MCP",
        "four answer types · evidence · stability · latency",
        "in progress",
    ),
    (
        "integrity",
        "Contextual integrity",
        "don't leak meetings you can't see",
        "Answer someone who can see only some meetings, without revealing the rest",
        "leak rate · over-restriction · inference leaks",
        "designed",
    ),
)


def fig_overview() -> Any:
    """'How Hindsight works': meetings -> products (via adapters) -> five tracks -> scores."""
    fig, ax = _canvas(5.0)
    top = 80.0
    heads = (
        (2, "1  Meetings"),
        (35, "2  Products answer"),
        (78, "3  Five tracks test them"),
        (131, "4  Scores out"),
    )
    for x, t in heads:
        ax.text(x, top, t, fontsize=10.5, fontweight="bold", color=fs.INK, va="bottom")

    # 1: a meeting card: transcript lines (Me / Them) and notes.
    _box(ax, 2, 40, 27, 37, fs.REAL_TINT)
    ax.text(4, 73.5, "transcript", fontsize=8.5, color=fs.MUTED, va="center")
    for k, who in enumerate(("Me", "Them", "Me", "Them")):
        yy = 69 - 4.2 * k
        ax.text(4, yy, who, fontsize=7.5, color=fs.MUTED, va="center")
        ax.plot([11, 26 - 3 * (k % 2)], [yy, yy], color="#9a9a9a", lw=2.2, solid_capstyle="round")
    ax.text(4, 51, "notes", fontsize=8.5, color=fs.MUTED, va="center")
    for k in range(2):
        ax.plot(
            [4, 22 - 5 * k],
            [47 - 3.6 * k, 47 - 3.6 * k],
            color="#9a9a9a",
            lw=2.2,
            solid_capstyle="round",
        )

    # 2: products, each behind one adapter.
    products = (
        ("Granola", "reference product", fs.colour("granola_agent"), "white"),
        ("Wispr Flow", "second product, adapter coming", "#444444", "white"),
        (
            "Open baselines",
            "top-k, agent, Mem0, Graphiti\nand Concentrate (ours)",
            "#E8E8E8",
            fs.INK,
        ),
    )
    for k, (name, sub, fc, tc) in enumerate(products):
        extra = 3.0 * sub.count("\n")
        y = 66 - 13 * k - extra
        _box(ax, 35, y, 37, 10.5 + extra, fc)
        ax.text(37, y + 7 + extra, name, fontsize=9.5, fontweight="bold", color=tc, va="center")
        ax.text(37, y + 3 + extra / 2, sub, fontsize=7.5, color=tc, va="center")
    ax.text(
        53.5,
        34.5,
        "each through one adapter\n(public API / MCP only)",
        ha="center",
        va="top",
        fontsize=8,
        color=fs.MUTED,
        style="italic",
    )
    _arrow(ax, 29.5, 34.5, 58.5)

    # 3: tracks, glyph + name + one line.
    for k, (glyph, name, line, *_rest) in enumerate(TRACKS):
        y = 72 - 9 * k
        _glyph(ax, glyph, 82, y, s=2.4)
        ax.text(87, y + 1.6, name, fontsize=9, fontweight="bold", color=fs.INK, va="center")
        ax.text(87, y - 2.3, line, fontsize=7.5, color=fs.MUTED, va="center")
    _arrow(ax, 72.5, 77.5, 58.5)

    # 4: what comes out.
    scores = (
        "accuracy",
        "confident errors",
        "deletion cost",
        "faithfulness",
        "leak rate",
        "$ · latency",
    )
    _box(ax, 131, 40, 27, 37, "white", ec=fs.GRID, lw=1.2)
    for k, sc in enumerate(scores):
        yy = 72 - 5.6 * k
        ax.add_patch(Rectangle((133.5, yy - 1), 2, 2, fc=fs.colour("concentrate"), ec="none"))
        ax.text(137.5, yy, sc, fontsize=7.5, color=fs.INK, va="center")
    _arrow(ax, 124.5, 130, 58.5)
    ax.text(
        144.5,
        37.5,
        "no single score:\neach track has its own",
        ha="center",
        va="top",
        fontsize=8,
        color=fs.MUTED,
        style="italic",
    )

    # Data roles band.
    _box(ax, 2, 4, 76, 16, fs.colour("granola_agent"))
    ax.text(
        5,
        15.5,
        "Real meetings  =  evidence",
        fontsize=10,
        fontweight="bold",
        color="white",
        va="center",
    )
    ax.text(
        5,
        9,
        "your own account, through the product's public MCP;\neverything stays local, "
        "only aggregates are shared",
        fontsize=7.5,
        color="white",
        va="center",
    )
    _box(ax, 82, 4, 76, 16, "white", ec=fs.MUTED, lw=1.2)
    ax.text(
        85,
        15.5,
        "Synthetic meetings  =  diagnostic",
        fontsize=10,
        fontweight="bold",
        color=fs.INK,
        va="center",
    )
    ax.text(
        85,
        9,
        "Northwind: 184 meetings in Granola's format, known answers;\nfor "
        "debugging systems, never for headline findings",
        fontsize=7.5,
        color=fs.INK,
        va="center",
    )
    ax.text(
        2, 96, "How does Hindsight work?", fontsize=13, fontweight="bold", color=fs.INK, va="center"
    )
    ax.text(
        2,
        90.5,
        "An open benchmark for meeting memory: the same tasks for every product, "
        "measured from the outside.",
        fontsize=9,
        color=fs.MUTED,
        va="center",
    )
    return fig


def _headlines(real: dict[str, Any] | None, deletion: dict[str, Any] | None) -> dict[str, str]:
    """One measured line per built track, from the committed aggregates."""
    out: dict[str, str] = {}
    if real is not None:
        s = real["systems"]
        out["qa"] = (
            f"Real (DEV, n={s['granola_agent']['n']}): Granola's agent "
            f"{100 * s['granola_agent']['accuracy']:.0f}%, our memory system "
            f"{100 * s['concentrate']['accuracy']:.0f}%"
        )
    if deletion is not None:
        c = deletion["metrics"]["deletion"]["conditions"]
        out["retention"] = (
            f"Real (DEV, n={c['granola_agent']['n']}): "
            f"{100 * c['granola_agent']['accuracy']:.0f}% with transcripts, "
            f"{100 * c['notes_only']['accuracy']:.0f}% with notes only, "
            f"{100 * c['notes_facts_fallback']['accuracy']:.0f}% reading them when needed"
        )
    return out


def fig_tracks(real: dict[str, Any] | None, deletion: dict[str, Any] | None) -> Any:
    """Five track cards: task -> metrics -> status, with the measured headline where built."""
    fig, ax = _canvas(5.4)
    heads = _headlines(real, deletion)
    ax.text(
        2,
        104,
        "What does each track test, and how far along is it?",
        fontsize=13,
        fontweight="bold",
        color=fs.INK,
        va="center",
    )
    ax.text(
        2,
        98.5,
        "Each track is a task plus metrics any product can be scored on; "
        "Granola is the reference entry.",
        fontsize=9,
        color=fs.MUTED,
        va="center",
    )
    for x, t in ((14, "Track"), (58, "Task"), (102, "Metrics"), (142, "Status")):
        ax.text(x, 92, t, fontsize=8, color=fs.MUTED, va="center", fontweight="bold")
    for k, (glyph, name, _line, task, metrics, status) in enumerate(TRACKS):
        y = 71 - 17 * k + 4
        _box(
            ax,
            2,
            y - 3,
            156,
            15.5,
            fs.REAL_TINT if status == "built" else "white",
            ec=fs.GRID,
            lw=1.0,
        )
        _glyph(ax, glyph, 8, y + 5, s=2.6)
        ax.text(
            14, y + 5, name, fontsize=9.5, fontweight="bold", color=fs.INK, va="center", wrap=False
        )
        ax.text(58, y + 5, fill(task, 34), fontsize=8, color=fs.INK, va="center")
        ax.text(102, y + 5, fill(metrics, 34), fontsize=7.5, color=fs.INK, va="center")
        _box(ax, 142, y + 2.5, 14.5, 5, fs.STATUS_COLOURS[status], r=2.4)
        ax.text(
            149.25,
            y + 5,
            status,
            fontsize=7.5,
            ha="center",
            va="center",
            color="white" if status == "built" else fs.INK,
            fontweight="bold",
        )
        if glyph in heads:
            ax.text(14, y - 0.3, "▸ " + heads[glyph], fontsize=7.5, color=fs.MUTED, va="center")
    return fig


# -- the outside-in evaluation harness (docs/method/CALIBRATED_JUDGING.md) -------------------------


def _varrow(ax: Any, x0: float, y0: float, x1: float, y1: float, dashed: bool = False) -> None:
    ax.annotate(
        "",
        xy=(x1, y1),
        xytext=(x0, y0),
        arrowprops={
            "arrowstyle": "-|>",
            "color": fs.MUTED,
            "lw": 1.4,
            "linestyle": (0, (4, 3)) if dashed else "-",
            "shrinkA": 0,
            "shrinkB": 0,
        },
    )


def _node(
    ax: Any, cx: float, y: float, w: float, h: float, head: str, sub: str, fc: str, tc: str
) -> None:
    """A box centred on ``cx`` with a bold head line and a smaller sub line."""
    ec = fs.GRID if fc == "white" else "none"
    _box(ax, cx - w / 2, y, w, h, fc, ec=ec, lw=1.2)
    ax.text(cx, y + h * 0.64, head, ha="center", va="center", fontsize=9.5,
            fontweight="bold", color=tc)  # fmt: skip
    ax.text(cx, y + h * 0.3, sub, ha="center", va="center", fontsize=8, color=tc)


def _edge_label(ax: Any, x: float, y: float, text: str, ha: str = "left") -> None:
    ax.text(x, y, text, ha=ha, va="center", fontsize=7.5, color=fs.MUTED, style="italic")


def _hm(seconds: int) -> str:
    h, m = divmod(round(seconds / 60), 60)
    return f"{h} h {m:02d} m"


def fig_harness_flow(coverage: dict[str, Any] | None) -> Any:
    """AMI audio -> two apps -> read-only MCP -> private store -> scorers, checked against truth.

    Meeting count and audio length come from the committed coverage report.
    """
    if coverage is None:
        return None
    meetings = coverage["meetings"]
    total = sum(int(m["duration_s"]) for m in meetings)
    fig, ax = _canvas(7.4)  # canvas is 160 x 148
    ax.text(2, 143, "How does the outside-in evaluation harness work?", fontsize=13,
            fontweight="bold", color=fs.INK, va="center")  # fmt: skip
    ax.text(2, 137.5, "Public meetings go in as audio; we read only what each app exposes, "
            "and score it against a human answer key.", fontsize=9, color=fs.MUTED,
            va="center")  # fmt: skip
    L, R, C = 44.0, 116.0, 80.0  # left / right column centres, middle
    bw, bh = 60.0, 11.0

    # 1: audio in.
    _node(ax, C, 118, 76, bh, "AMI audio (public)",
          f"{len(meetings)} meetings · {_hm(total)}", fs.REAL_TINT, fs.INK)  # fmt: skip
    for x in (L, R):
        _varrow(ax, C + (x - C) * 0.3, 118, x, 105)
    _edge_label(ax, C, 111.5, "played as system audio", ha="center")

    # 2: the two apps.
    _node(ax, L, 94, bw, bh, "Granola", "transcript · notes · Chat",
          fs.colour("granola"), "white")  # fmt: skip
    _node(ax, R, 94, bw, bh, "Wispr Flow", "transcript · notes",
          fs.colour("wispr"), "white")  # fmt: skip
    for x in (L, R):
        _varrow(ax, x, 94, C + (x - C) * 0.3, 81)
    _edge_label(ax, C, 87.5, "what each app exposes", ha="center")

    # 3: read-only MCP.
    _node(ax, C, 70, 76, bh, "read-only MCP tools", "the products' official servers; no writes",
          "white", fs.INK)  # fmt: skip
    _varrow(ax, C, 70, C, 61)
    _edge_label(ax, C + 2, 65.5, "transcripts, notes, answers")

    # 4: private store + drift check.
    _node(ax, C, 50, 76, bh, "Synced, private store", "local only; results publish numbers",
          fs.REAL_TINT, fs.INK)  # fmt: skip
    ax.text(C + 41, 55.5, "Daily drift check:\ncaught 1 silent\nMCP change", ha="left",
            va="center", fontsize=8, color=fs.MUTED, style="italic")  # fmt: skip

    # 5: scorers.
    for x in (L, R):
        _varrow(ax, C + (x - C) * 0.3, 50, x, 37)
    _edge_label(ax, L + 1, 43.5, "transcripts, notes", ha="right")
    _edge_label(ax, R - 1, 43.5, "notes, Chat answers", ha="left")
    _node(ax, L, 26, bw, bh, "Free scorers", "WER · coverage · facts", "white", fs.INK)
    _node(ax, R, 26, bw, bh, "Calibrated judge", "strong model + speaker roster",
          "white", fs.INK)  # fmt: skip

    # 6: ground truth, dashed arrows up.
    _node(ax, C, 3, 92, bh, "Ground truth", "AMI answer key + human labels",
          "#E8E8E8", fs.INK)  # fmt: skip
    for x in (L, R):
        _varrow(ax, C + (x - C) * 0.45, 14, x, 26, dashed=True)
    _edge_label(ax, C, 20, "reference to score against", ha="center")
    return fig
