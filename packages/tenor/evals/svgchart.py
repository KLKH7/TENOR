"""A dependency-free SVG line chart, enough for the economics and scale-curve figures."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from html import escape

PALETTE = ("#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2", "#4b5563", "#ca8a04")
W, H, ML, MR, MT, MB = 720, 420, 70, 170, 40, 50


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    if hi <= lo:
        hi = lo + 1
    step = (hi - lo) / n
    mag = 10 ** len(str(int(step))) / 10 if step >= 1 else 1.0
    while mag > step:
        mag /= 10
    nice = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= step)
    start = int(lo / nice) * nice
    out, t = [], start
    while t <= hi + 1e-9:
        if t >= lo - 1e-9:
            out.append(round(t, 10))
        t += nice
    return out


def _fmt(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 or v == int(v) else f"{v:,.2f}".rstrip("0").rstrip(".")


def line_chart(
    series: Mapping[str, Sequence[tuple[float, float]]],
    *,
    title: str,
    xlabel: str,
    ylabel: str,
    y_min: float | None = 0.0,
    y_max: float | None = None,
    markers: bool = False,
    dashed: Sequence[str] = (),
) -> str:
    """Render ``{label: [(x, y), ...]}`` as an SVG string (legend on the right)."""
    pts = [p for s in series.values() for p in s]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0 = min(p[1] for p in pts) if y_min is None else y_min
    y1 = max(p[1] for p in pts) if y_max is None else y_max
    y1 = y1 * 1.05 if y_max is None else y1
    pw, ph = W - ML - MR, H - MT - MB

    def sx(x: float) -> float:
        return ML + (x - x0) / ((x1 - x0) or 1) * pw

    def sy(y: float) -> float:
        return MT + ph - (y - y0) / ((y1 - y0) or 1) * ph

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
        'font-family="system-ui, sans-serif" font-size="12">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{W / 2}" y="22" text-anchor="middle" font-size="15" font-weight="600">'
        f"{escape(title)}</text>",
    ]
    for t in _ticks(y0, y1):
        out.append(
            f'<line x1="{ML}" x2="{ML + pw}" y1="{sy(t):.1f}" y2="{sy(t):.1f}" stroke="#e5e7eb"/>'
        )
        out.append(
            f'<text x="{ML - 6}" y="{sy(t) + 4:.1f}" text-anchor="end" fill="#374151">'
            f"{_fmt(t)}</text>"
        )
    for t in _ticks(x0, x1):
        out.append(
            f'<text x="{sx(t):.1f}" y="{MT + ph + 18}" text-anchor="middle" '
            f'fill="#374151">{_fmt(t)}</text>'
        )
    out.append(f'<rect x="{ML}" y="{MT}" width="{pw}" height="{ph}" fill="none" stroke="#9ca3af"/>')
    out.append(f'<text x="{ML + pw / 2}" y="{H - 10}" text-anchor="middle">{escape(xlabel)}</text>')
    out.append(
        f'<text transform="translate(16,{MT + ph / 2}) rotate(-90)" text-anchor="middle">'
        f"{escape(ylabel)}</text>"
    )
    for i, (label, s) in enumerate(series.items()):
        c = PALETTE[i % len(PALETTE)]
        dash = ' stroke-dasharray="6 4"' if label in dashed else ""
        path = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in s)
        out.append(f'<polyline points="{path}" fill="none" stroke="{c}" stroke-width="2"{dash}/>')
        if markers:
            out.extend(
                f'<circle cx="{sx(x):.1f}" cy="{sy(y):.1f}" r="3.5" fill="{c}"/>' for x, y in s
            )
        ly = MT + 10 + i * 20
        out.append(
            f'<line x1="{ML + pw + 12}" x2="{ML + pw + 34}" y1="{ly}" y2="{ly}" '
            f'stroke="{c}" stroke-width="2"{dash}/>'
        )
        out.append(f'<text x="{ML + pw + 40}" y="{ly + 4}">{escape(label)}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"
