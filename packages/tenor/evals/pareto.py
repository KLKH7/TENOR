"""Cost-accuracy(-latency) Pareto frontier across systems.

A system is *dominated* when another system is at least as accurate, at least as cheap (and, in
the latency view, at least as fast) and strictly better on one of those. The non-dominated
systems form the Pareto frontier: the only rational choices, whichever trade-off you prefer.

Cost per question includes a write-time system's one-off ingest cost amortised over a chosen
question volume: ``answer $/q + ingest $ / volume``. Ingest is paid once per corpus, so the more
questions a corpus serves, the cheaper write-time memory gets; ``breakeven_volume`` finds where
it overtakes a query-time system.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SystemPoint:
    name: str
    accuracy: float
    answer_usd_per_q: float  # query-time cost, from the report rows
    ingest_usd: float = 0.0  # one-off memory build (0 for query-time systems)
    latency_p50: float | None = None  # seconds; None when not measured

    def usd_per_q(self, volume: int) -> float:
        return self.answer_usd_per_q + (self.ingest_usd / volume if self.ingest_usd else 0.0)


@dataclass
class FrontierRow:
    name: str
    accuracy: float
    usd_per_q: float
    latency_p50: float | None
    dominated_by: list[str] = field(default_factory=list)  # cost-accuracy view
    dominated_by_latency: list[str] = field(default_factory=list)  # cost-accuracy-latency view

    @property
    def on_frontier(self) -> bool:
        return not self.dominated_by


def _dominates(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    """``a`` dominates ``b`` (all objectives as "higher is better")."""
    return all(x >= y for x, y in zip(a, b, strict=True)) and any(
        x > y for x, y in zip(a, b, strict=True)
    )


def frontier(points: list[SystemPoint], volume: int) -> list[FrontierRow]:
    """Dominance at ``volume`` questions per corpus, sorted by cost.

    In the latency view, a system with unknown latency can neither dominate nor be dominated
    on latency, so it is compared only against systems where both latencies are known.
    """
    rows = [FrontierRow(p.name, p.accuracy, p.usd_per_q(volume), p.latency_p50) for p in points]
    for r in rows:
        for o in rows:
            if o is r:
                continue
            if _dominates((o.accuracy, -o.usd_per_q), (r.accuracy, -r.usd_per_q)):
                r.dominated_by.append(o.name)
            if (
                r.latency_p50 is not None
                and o.latency_p50 is not None
                and _dominates(
                    (o.accuracy, -o.usd_per_q, -o.latency_p50),
                    (r.accuracy, -r.usd_per_q, -r.latency_p50),
                )
            ):
                r.dominated_by_latency.append(o.name)
    return sorted(rows, key=lambda r: (r.usd_per_q, -r.accuracy, r.name))


def breakeven_volume(write_time: SystemPoint, query_time: SystemPoint) -> int | None:
    """Questions per corpus above which ``write_time`` is cheaper per question than
    ``query_time``; None if it never is (its answers cost at least as much)."""
    saving = query_time.answer_usd_per_q - write_time.answer_usd_per_q
    if query_time.ingest_usd or saving <= 0:
        return None
    return math.ceil(round(write_time.ingest_usd / saving, 9)) if write_time.ingest_usd else 0
