"""One fixed matplotlib style for every committed figure.

- **Non-interactive.** The Agg backend is selected before pyplot is imported, so figures render
  the same in CI, over SSH and on a laptop.
- **Deterministic SVG.** A fixed ``svg.hashsalt`` makes clip-path ids stable, the ``Date``
  metadata is dropped and the ``Creator`` is a constant, so the same inputs give byte-identical
  files on the same platform. Text is kept as ``<text>`` (``svg.fonttype = "none"``): smaller
  files and no glyph outlines that depend on the font rasteriser.
- **One colour per system, everywhere.** Okabe-Ito (colour-blind safe), plus a wine and a grey
  from Paul Tol's palette for the two systems Okabe-Ito runs out on. The real Granola agent is
  black so it never looks like one of our re-implementations.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

HASHSALT = "hindsight-figures"
CREATOR = "concentrate figures"

# README width is ~830 px; 8 in at 100 dpi-equivalent keeps 10 pt text legible there.
WIDTH_IN = 8.0

INK = "#222222"
MUTED = "#666666"
GRID = "#e5e5e5"
BACKGROUND = "#ffffff"

SYSTEM_COLOURS: dict[str, str] = {
    "concentrate": "#0072B2",  # blue
    "c_fallback_on_abstain": "#56B4E9",  # sky blue: same family as Concentrate
    "b0_topk": "#E69F00",  # orange
    "b1_summaries": "#D55E00",  # vermillion
    "b2o_oracle": "#999999",  # grey: a ceiling, not a contender
    "b3_agentic": "#009E73",  # bluish green
    "b4_mem0": "#CC79A7",  # reddish purple
    "b5_graphiti": "#882255",  # wine (Tol)
    "granola_agent": "#000000",  # black: the only real product
}

SYSTEM_LABELS: dict[str, str] = {
    "concentrate": "Concentrate",
    "c_fallback_on_abstain": "Concentrate + fallback",
    "b0_topk": "Top-k (b0)",
    "b1_summaries": "Summaries (b1)",
    "b2o_oracle": "Oracle (b2o, ceiling)",
    "b3_agentic": "Agent re-impl. (b3)",
    "b4_mem0": "Mem0 (b4)",
    "b5_graphiti": "Graphiti (b5)",
    "granola_agent": "Granola agent (real)",
}

# Categorical palette for things that are *not* systems (failure types, cost parts), so a
# colour never means two different systems. Paul Tol "muted".
OTHER_COLOURS = ("#332288", "#88CCEE", "#44AA99", "#117733", "#DDCC77", "#CC6677", "#AA4499")

RC: dict[Any, Any] = {  # Any keys: matplotlib types rcParams keys as Literals
    "svg.hashsalt": HASHSALT,
    "svg.fonttype": "none",
    "text.parse_math": False,  # "$4/M" is a price, not LaTeX
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans", "Helvetica", "Arial"],
    "font.size": 10,
    "axes.titlesize": 11,
    "axes.titleweight": "bold",
    "axes.labelsize": 10,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "axes.facecolor": BACKGROUND,
    "axes.grid": True,
    "axes.axisbelow": True,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "xtick.color": INK,
    "ytick.color": INK,
    "xtick.labelsize": 9,
    "ytick.labelsize": 9,
    "legend.fontsize": 9,
    "legend.frameon": False,
    "figure.facecolor": BACKGROUND,
    "figure.dpi": 100,
    "savefig.facecolor": BACKGROUND,
    "lines.linewidth": 1.8,
    "lines.markersize": 7,
}


def colour(system: str) -> str:
    """The one colour for ``system`` (unknown systems are dark grey)."""
    return SYSTEM_COLOURS.get(system, "#444444")


def label(system: str) -> str:
    """Human-readable system name used in every figure."""
    return SYSTEM_LABELS.get(system, system)


def new_figure(height_in: float, ncols: int = 1, **kw: Any) -> tuple[Figure, Any]:
    """A figure at README width with the house style applied."""
    plt.rcParams.update(RC)
    fig, axes = plt.subplots(1, ncols, figsize=(WIDTH_IN, height_in), **kw)
    return fig, axes


def title(fig: Figure, question: str, subtitle: str) -> None:
    """Every figure is titled with the question it answers, plus a one-line scope note."""
    fig.suptitle(question, x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.text(0.01, 0.935, subtitle, ha="left", va="top", fontsize=9, color=MUTED)


def footnote(fig: Figure, text: str) -> None:
    """Caveats live on the figure itself, so a screenshot can't drop them."""
    fig.text(0.01, 0.01, text, ha="left", va="bottom", fontsize=8, color=MUTED, wrap=True)


def svg_bytes(fig: Figure) -> bytes:
    """Render ``fig`` to deterministic SVG bytes and close it."""
    buf = io.StringIO()
    with plt.rc_context(RC):
        fig.savefig(buf, format="svg", metadata={"Date": None, "Creator": CREATOR})
    plt.close(fig)
    return buf.getvalue().encode()


def save(fig: Figure, path: Path) -> Path:
    """Write ``fig`` as SVG to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(svg_bytes(fig))
    return path
