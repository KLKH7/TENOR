"""Report v2: post-hoc analysis of a saved eval report (``concentrate analyze <report>``).

Reads ``<stamp>.json`` (meta + metrics) and ``<stamp>.jsonl`` (one row per system x question)
and adds what a skeptical reader needs to trust a comparison:

* paired significance for every pair of systems (McNemar exact test + paired bootstrap CI of
  the accuracy gap), Holm-corrected, overall and per category;
* power: how many questions a 5- or 10-point gap needs, and the smallest gap this run can see;
* the cost-accuracy(-latency) Pareto frontier, with ingest amortised at a question volume;
* a failure taxonomy per system with example question IDs;
* the $0 metrics of ``concentrate.evals.metrics`` (source recall, sources pulled, list
  completeness, abstention, accuracy when answered vs coverage, equal-price cost).

Every $/question here is re-priced from tokens at one interactive price list
(``metrics.reprice``), never the recorded ``cost_usd``, which mixes Batch-priced and full-priced
rows.

Pure and deterministic given the report, the fixtures and the seed; costs nothing unless the
optional LLM failure classifier is used (``concentrate.evals.failures.llm_request``).
"""

from __future__ import annotations

import itertools
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from concentrate.evals import metrics, stats
from concentrate.evals.failures import FAILURE_TYPES, Failure, failure_table
from concentrate.evals.pareto import SystemPoint, breakeven_volume, frontier

ALPHA = 0.05
DELTAS = (0.05, 0.10)
PLANNED_N = (120, 206)  # candidate test-set sizes we are deciding between
VOLUMES = (100, 1_000, 10_000, 100_000)


def load_rows(jsonl: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]


# -- paired comparisons --------------------------------------------------------------------------


@dataclass
class PairResult:
    """A vs B on the questions both answered; A is the more accurate of the two."""

    family: str  # "overall" or a category name
    a: str
    b: str
    n: int
    acc_a: float
    acc_b: float
    diff: float
    ci_low: float
    ci_high: float
    a_only: int
    b_only: int
    p: float
    p_holm: float = math.nan

    @property
    def significant(self) -> bool:
        return self.p_holm < ALPHA

    @property
    def discordance(self) -> float:
        return (self.a_only + self.b_only) / self.n if self.n else math.nan


def _outcomes(rows: list[dict[str, Any]]) -> dict[str, dict[str, tuple[str, bool]]]:
    """``{system: {qid: (category, correct)}}``."""
    out: dict[str, dict[str, tuple[str, bool]]] = defaultdict(dict)
    for r in rows:
        out[r["system"]][r["qid"]] = (r["category"], bool(r["correct"]))
    return out


def compare_pair(
    family: str, a: str, b: str, xa: list[bool], xb: list[bool], seed: int
) -> PairResult:
    if sum(xb) > sum(xa):  # report the leader first
        a, b, xa, xb = b, a, xb, xa
    c = stats.paired_counts(xa, xb)
    diff, lo, hi = stats.paired_bootstrap(xa, xb, seed)
    n = len(xa)
    return PairResult(
        family=family,
        a=a,
        b=b,
        n=n,
        acc_a=sum(xa) / n if n else math.nan,
        acc_b=sum(xb) / n if n else math.nan,
        diff=diff,
        ci_low=lo,
        ci_high=hi,
        a_only=c.a_only,
        b_only=c.b_only,
        p=stats.mcnemar_exact(xa, xb),
    )


def pairwise(rows: list[dict[str, Any]], seed: int) -> dict[str, list[PairResult]]:
    """All system pairs, overall and per category; Holm is applied within each family."""
    outcomes = _outcomes(rows)
    systems = sorted(outcomes)
    categories = sorted({r["category"] for r in rows})
    families: dict[str, list[PairResult]] = {}
    for family in ("overall", *categories):
        results = []
        for a, b in itertools.combinations(systems, 2):
            qids = sorted(
                q
                for q in outcomes[a].keys() & outcomes[b].keys()
                if family == "overall" or outcomes[a][q][0] == family
            )
            if not qids:
                continue
            xa = [outcomes[a][q][1] for q in qids]
            xb = [outcomes[b][q][1] for q in qids]
            results.append(compare_pair(family, a, b, xa, xb, seed))
        for r, p in zip(results, stats.holm([r.p for r in results]), strict=True):
            r.p_holm = p
        families[family] = results
    return families


# -- power ---------------------------------------------------------------------------------------


def power_table(
    overall: list[PairResult], n_now: int, alpha: float = ALPHA
) -> list[dict[str, Any]]:
    """Per pair: observed discordance, questions needed for each gap, and detectable gaps."""
    out = []
    for r in overall:
        psi = r.discordance
        out.append(
            {
                "pair": f"{r.a} vs {r.b}",
                "discordance": psi,
                "needed": {d: stats.mcnemar_sample_size(d, psi, alpha) for d in DELTAS},
                "mde": {n: stats.mcnemar_mde(n, psi, alpha) for n in (n_now, *PLANNED_N)},
            }
        )
    return out


def generic_power(accuracies: list[float], overall: list[PairResult]) -> dict[str, Any]:
    """Sample sizes at the typical observed discordance, with and without correlation."""
    mean_acc = statistics.fmean(accuracies) if accuracies else 0.8
    observed = [r.discordance for r in overall if r.n]
    psi_obs = statistics.median(observed) if observed else math.nan
    m = max(1, len(overall))
    rows = []
    for d in DELTAS:
        p1, p2 = min(0.999, mean_acc + d / 2), max(0.001, mean_acc - d / 2)
        psi_ind = stats.independent_discordance(p1, p2)
        rows.append(
            {
                "delta": d,
                "paired_observed": (
                    stats.mcnemar_sample_size(d, psi_obs) if psi_obs == psi_obs else None
                ),
                "paired_observed_bonferroni": (
                    stats.mcnemar_sample_size(d, psi_obs, ALPHA / m) if psi_obs == psi_obs else None
                ),
                "paired_independent": stats.mcnemar_sample_size(d, psi_ind),
                "unpaired": stats.unpaired_sample_size(p1, p2),
            }
        )
    return {"mean_accuracy": mean_acc, "median_discordance": psi_obs, "pairs": m, "rows": rows}


# -- cost / Pareto -------------------------------------------------------------------------------


def system_points(
    report: dict[str, Any],
    rows: list[dict[str, Any]],
    price_model: str = metrics.DEFAULT_PRICE_MODEL,
) -> list[SystemPoint]:
    """One point per system with observable tokens: answers at the interactive price of
    ``price_model`` (equal prices for everyone), ingest as recorded when the store was built."""
    costs = {c.system: c for c in metrics.cost_stats(rows, price_model) if c.observable}
    points = []
    for name, m in report["metrics"].items():
        if name not in costs:
            continue
        lat = m.get("latency_p50")
        points.append(
            SystemPoint(
                name=name,
                accuracy=m["accuracy"],
                answer_usd_per_q=costs[name].usd_interactive,
                ingest_usd=metrics.ingest_usd(report, name),
                latency_p50=lat if isinstance(lat, int | float) and lat == lat else None,
            )
        )
    return points


NOT_DEPLOYABLE = frozenset({"b2o_oracle"})  # reads the gold meetings: a ceiling, not a rival


# -- the analysis --------------------------------------------------------------------------------


def analyze(
    report: dict[str, Any],
    rows: list[dict[str, Any]],
    fails: list[Failure],
    families: dict[str, list[PairResult]],
    *,
    seed: int,
    volume: int,
    gold: dict[str, metrics.Gold] | None = None,
    price_model: str = metrics.DEFAULT_PRICE_MODEL,
) -> dict[str, Any]:
    """Everything the markdown shows, as JSON-serialisable data (``families``: ``pairwise``)."""
    overall = families.get("overall", [])
    n_now = max((r.n for r in overall), default=0)
    points = system_points(report, rows, price_model)
    report_acc = {n: m["accuracy"] for n, m in report["metrics"].items()}
    return {
        "meta": {
            "report": report["meta"].get("date"),
            "seed": seed,
            "volume": volume,
            "alpha": ALPHA,
            "fixtures": report["meta"].get("fixtures"),
            "price_model": price_model,
            "price_basis": "interactive (no Batch API, no prompt-cache credit), from tokens",
        },
        "accuracy": report_acc,
        "pairs": {
            f: [asdict(r) | {"significant": r.significant} for r in rs]
            for f, rs in families.items()
        },
        "power": {
            "per_pair": power_table(overall, n_now),
            "generic": generic_power(list(report_acc.values()), overall),
            "n_now": n_now,
        },
        "pareto": {
            str(v): [asdict(r) | {"on_frontier": r.on_frontier} for r in frontier(points, v)]
            for v in sorted({volume, *VOLUMES})
        },
        "breakeven": [
            {
                "write_time": w.name,
                "query_time": q.name,
                "ingest_usd": w.ingest_usd,
                "volume": breakeven_volume(w, q),
            }
            for w in points
            if w.ingest_usd
            for q in points
            if not q.ingest_usd and q.name not in NOT_DEPLOYABLE
        ],
        "metrics": metrics.all_metrics(report, rows, gold, price_model=price_model),
        "failures": {
            "table": {
                s: {t: {"count": c, "examples": ex} for t, (c, ex) in cells.items()}
                for s, cells in failure_table(fails).items()
            },
            "llm": {
                f"{s}/{t}": c
                for (s, t), c in sorted(
                    Counter((f.system, f.llm_type) for f in fails if f.llm_type).items()
                )
            },
        },
    }


# -- markdown ------------------------------------------------------------------------------------


def _pct(x: float | None, signed: bool = False) -> str:
    if x is None or x != x:
        return "-"
    return f"{100 * x:+.0f}" if signed else f"{100 * x:.0f}%"


def _pts(x: float) -> str:
    return "-" if x != x else f"{100 * x:.0f} pts"


def _p(p: float) -> str:
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _usd(x: float) -> str:
    return f"${x:.4f}" if x < 0.01 else f"${x:.3f}"


def _sentence(r: PairResult) -> str:
    return (
        f"{r.a} beats {r.b} by {100 * r.diff:.0f} points "
        f"[{100 * r.ci_low:+.0f}, {100 * r.ci_high:+.0f}], p={_p(r.p_holm)} (Holm)"
    )


def summary_paragraph(
    report: dict[str, Any],
    a: dict[str, Any],
    families: dict[str, list[PairResult]],
    fails: list[Failure],
) -> str:
    meta = report["meta"]
    acc = a["accuracy"]
    best = max(acc, key=lambda s: (acc[s], s))
    overall = families.get("overall", [])
    sig = [r for r in overall if r.significant]
    n_now = a["power"]["n_now"]
    parts = [
        f"On {n_now} questions from the **{meta.get('split', '?')}** split, "
        f"{len(acc)} systems were compared; **{best}** scored highest ({_pct(acc[best])})."
    ]
    if sig:
        shown = "; ".join(_sentence(r) for r in sig[:4])
        more = f" (and {len(sig) - 4} more)" if len(sig) > 4 else ""
        parts.append(
            f"{len(sig)} of {len(overall)} pairwise gaps are statistically reliable after "
            f"correcting for multiple comparisons: {shown}{more}."
        )
    else:
        parts.append(
            f"None of the {len(overall)} pairwise gaps is statistically reliable after "
            "correcting for multiple comparisons, so the ranking above could be noise."
        )

    def typical_mde(n: int) -> float:
        xs = [p["mde"][n] for p in a["power"]["per_pair"] if p["mde"][n] == p["mde"][n]]
        return 100 * statistics.median(xs) if xs else math.nan

    if a["power"]["per_pair"]:
        parts.append(
            f"With {n_now} questions a typical pair needs a gap of about "
            f"{typical_mde(n_now):.0f} points to be detected reliably (80% power); with "
            f"{PLANNED_N[0]} or {PLANNED_N[1]} questions, about {typical_mde(PLANNED_N[0]):.0f} "
            f"or {typical_mde(PLANNED_N[1]):.0f} points."
        )
    pm = a["meta"]["price_model"]
    if a["breakeven"]:
        # The cheapest query-time rival each write-time system must beat, and when it does.
        bits = []
        for w in sorted({b["write_time"] for b in a["breakeven"]}):
            bs = [b for b in a["breakeven"] if b["write_time"] == w]
            paid = sorted((b["volume"], b["query_time"]) for b in bs if b["volume"] is not None)
            never = [b["query_time"] for b in bs if b["volume"] is None]
            s = f"{w} (ingest ${bs[0]['ingest_usd']:.2f})"
            if paid:
                s += " pays back its ingest after " + ", ".join(
                    f"{v:,} questions vs {q}" for v, q in paid
                )
            if never:
                s += ("; " if paid else " ") + "never vs " + ", ".join(never)
            bits.append(s)
        parts.append(
            f"On cost, with every answer at the interactive `{pm}` price: "
            + "; ".join(bits)
            + ". Below those question counts per corpus the query-time system is cheaper."
        )
    else:
        parts.append(f"Costs are compared at the interactive `{pm}` price.")
    if fails:
        top = Counter(f.primary for f in fails).most_common(2)
        desc = " and ".join(f"{t.replace('_', ' ')} ({c})" for t, c in top)
        parts.append(f"Of {len(fails)} wrong answers, the most common causes are {desc}.")
    if meta.get("split") == "dev":
        parts.append("This is the dev split: treat it as exploratory, not the headline result.")
    return " ".join(parts)


def to_markdown(
    report: dict[str, Any],
    a: dict[str, Any],
    families: dict[str, list[PairResult]],
    fails: list[Failure],
    *,
    gt_note: str,
    aggregate_only: bool = False,
) -> str:
    meta = report["meta"]
    git = meta.get("git") or {}
    rows_by_cat = {
        c: {
            s: m["by_category"][c]["accuracy"]
            for s, m in report["metrics"].items()
            if c in m["by_category"]
        }
        for c in families
        if c != "overall"
    }
    lines = [
        f"# Analysis — {meta.get('date')}",
        "",
        f"Report `{meta.get('date')}` · split **{meta.get('split')}** · model "
        f"`{meta.get('model')}` · git `{git.get('sha')}`{' (dirty)' if git.get('dirty') else ''}"
        f" · analysis seed {a['meta']['seed']} · alpha = {ALPHA}",
        "",
        "## Summary",
        "",
        summary_paragraph(report, a, families, fails),
        "",
        "## 1. Paired significance",
        "",
        "Each row compares two systems on the questions both answered. **Gap** is the accuracy "
        "difference in points with a 95% paired-bootstrap CI; **A only / B only** count the "
        "questions exactly one of them got right (the only ones that carry information); **p** "
        "is McNemar's exact test and **p (Holm)** corrects for testing every pair. ✅ = reliable "
        "at alpha = 0.05 after correction.",
        "",
    ]
    lines += _pair_table(families.get("overall", []))
    cats = [f for f in families if f != "overall"]
    if cats:
        lines += [
            "",
            "### Per category",
            "",
            "Holm is applied within each category (a family of all pairs). Category samples are "
            "small, so few gaps are reliable here; every pair is in `<stamp>.analysis.json`.",
            "",
            "| Category | n | Accuracy by system | Reliable gaps | Largest unreliable gap |",
            "|---|---|---|---|---|",
        ]
        for c in cats:
            lines.append(_category_row(c, families[c], rows_by_cat.get(c, {})))
    lines += _power_md(a)
    lines += _pareto_md(a)
    lines += _failures_md(a, fails, gt_note, aggregate_only=aggregate_only)
    lines += metrics.to_markdown(a["metrics"], section=5)
    lines += [
        "",
        "---",
        "Methods: `docs/results/ANALYSIS.md`. Machine-readable: `<stamp>.analysis.json`, "
        "per-answer "
        "failure tags: `<stamp>.failures.jsonl`.",
        "",
    ]
    return "\n".join(lines)


def _category_row(c: str, rs: list[PairResult], acc: dict[str, float]) -> str:
    n = max((r.n for r in rs), default=0)
    accs = ", ".join(f"{s} {_pct(v)}" for s, v in sorted(acc.items(), key=lambda kv: -kv[1]))
    sig = "; ".join(_sentence(r) for r in rs if r.significant) or "none"
    rest = [r for r in rs if not r.significant and r.diff > 0]
    top = max(rest, key=lambda r: (r.diff, r.a, r.b), default=None)
    unrel = (
        f"{top.a} vs {top.b}: {_pct(top.diff, True)} pts, p={_p(top.p_holm)} (Holm)" if top else "-"
    )
    return f"| {c} | {n} | {accs} | {sig} | {unrel} |"


def _pair_table(rs: list[PairResult]) -> list[str]:
    out = [
        "| A | B | n | A acc | B acc | Gap [95% CI] | A only / B only | p | p (Holm) | |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(rs, key=lambda r: (r.p_holm, -r.diff, r.a, r.b)):
        out.append(
            f"| {r.a} | {r.b} | {r.n} | {_pct(r.acc_a)} | {_pct(r.acc_b)} | "
            f"{_pct(r.diff, True)} [{_pct(r.ci_low, True)}, {_pct(r.ci_high, True)}] | "
            f"{r.a_only} / {r.b_only} | {_p(r.p)} | {_p(r.p_holm)} | "
            f"{'✅' if r.significant else ''} |"
        )
    return out


def _power_md(a: dict[str, Any]) -> list[str]:
    pw = a["power"]
    n_now = pw["n_now"]
    g = pw["generic"]
    lines = [
        "",
        "## 2. Power: how many questions do we need?",
        "",
        "Questions needed for an 80% chance of detecting a gap at alpha = 0.05 with a paired "
        "(McNemar) test. What matters is the **discordance**: the share of questions on which "
        "exactly one of the two systems is right. Systems that fail on the same hard questions "
        "disagree less, and then a smaller sample suffices.",
        "",
        "| Pair | Discordance | n for 5 pts | n for 10 pts | "
        f"Detectable gap @ {n_now} | @ {PLANNED_N[0]} | @ {PLANNED_N[1]} |",
        "|---|---|---|---|---|---|---|",
    ]
    for p in pw["per_pair"]:
        lines.append(
            f"| {p['pair']} | {_pct(p['discordance'])} | {p['needed'][0.05]:,} | "
            f"{p['needed'][0.1]:,} | {_pts(p['mde'][n_now])} | {_pts(p['mde'][PLANNED_N[0]])} | "
            f"{_pts(p['mde'][PLANNED_N[1]])} |"
        )
    lines += [
        "",
        f"Typical case (mean accuracy {_pct(g['mean_accuracy'])}, median observed discordance "
        f"{_pct(g['median_discordance'])}, {g['pairs']} pairs):",
        "",
        "| Gap | Paired, observed discordance | …with Bonferroni over all pairs | "
        "Paired, if errors were independent | Unpaired (per system) |",
        "|---|---|---|---|---|",
    ]
    for r in g["rows"]:
        lines.append(
            f"| {100 * r['delta']:.0f} pts | {r['paired_observed'] or '-'} | "
            f"{r['paired_observed_bonferroni'] or '-'} | {r['paired_independent']:,} | "
            f"{r['unpaired']:,} |"
        )
    lines += [
        "",
        "Discordance estimated from a small pilot is itself noisy; a pair with 0 observed "
        "disagreements is floored at the gap size (the most optimistic case).",
    ]
    return lines


def _pareto_md(a: dict[str, Any]) -> list[str]:
    vol = a["meta"]["volume"]
    rows = a["pareto"][str(vol)]
    lines = [
        "",
        "## 3. Cost vs accuracy (Pareto frontier)",
        "",
        f"$/q = answer cost at the **interactive `{a['meta']['price_model']}` price** (re-priced "
        "from tokens, so batch-priced runs are not mixed in; batch savings are in §7) + one-off "
        f"ingest as recorded at build time ÷ {vol:,} questions (judge excluded). A system is "
        "**dominated** if another is at least as accurate and at least as cheap (and, in the "
        "last column, at least as fast), and strictly better on one. Accuracy gaps here are "
        "point estimates; check §1 before calling a domination real.",
        "",
        "| System | Accuracy | $/q | p50 latency | Cost-accuracy | + latency |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        lat = f"{r['latency_p50']:.1f}s" if r["latency_p50"] is not None else "n/a"
        ca = "**frontier**" if r["on_frontier"] else "dominated by " + ", ".join(r["dominated_by"])
        cal = (
            "dominated by " + ", ".join(r["dominated_by_latency"])
            if r["dominated_by_latency"]
            else ("frontier" if r["latency_p50"] is not None else "n/a")
        )
        lines.append(
            f"| {r['name']} | {_pct(r['accuracy'])} | {_usd(r['usd_per_q'])} | {lat} | {ca} | "
            f"{cal} |"
        )
    vols = sorted(int(v) for v in a["pareto"])
    names = [r["name"] for r in rows]
    if not a["breakeven"]:
        return [
            *lines,
            "",
            "No write-time system in this report has an ingest cost, so the frontier doesn't "
            "depend on question volume.",
        ]
    lines += [
        "",
        "Frontier membership as question volume grows (✅ = on the cost-accuracy frontier):",
        "",
        "| System | " + " | ".join(f"{v:,} q" for v in vols) + " |",
        "|---|" + "---|" * len(vols),
    ]
    for n in names:
        cells = []
        for v in vols:
            r = next(x for x in a["pareto"][str(v)] if x["name"] == n)
            cells.append(f"{'✅' if r['on_frontier'] else ''} {_usd(r['usd_per_q'])}")
        lines.append(f"| {n} | " + " | ".join(cells) + " |")
    if a["breakeven"]:
        lines += [
            "",
            "**Break-even questions per corpus**: write-time memory becomes cheaper per question "
            "than… (a frontier at one volume is a snapshot; this is the number to quote):",
            "",
        ]
        for b in a["breakeven"]:
            v = b["volume"]
            when = f"after {v:,} questions" if v is not None else "never (its answers cost more)"
            lines.append(f"- {b['write_time']} vs {b['query_time']}: {when}")
    return lines


def _failures_md(
    a: dict[str, Any], fails: list[Failure], gt_note: str, *, aggregate_only: bool = False
) -> list[str]:
    table = a["failures"]["table"]
    types = [t for t in FAILURE_TYPES if any(t in cells for cells in table.values())]
    lines = [
        "",
        "## 4. Failure taxonomy",
        "",
        "Every answer not judged *correct* gets one **primary** failure type from cheap "
        "deterministic signals (priority order as in the columns; definitions in "
        f"`docs/results/ANALYSIS.md`). {gt_note}",
        "",
    ]
    if not fails:
        return [*lines, "No wrong answers."]
    lines += [
        "| System | Wrong | " + " | ".join(t.replace("_", " ") for t in types) + " |",
        "|---|---|" + "---|" * len(types),
    ]
    for s in sorted(table):
        cells = table[s]
        total = sum(c["count"] for c in cells.values())
        vals = [
            (
                str(cells[t]["count"])
                if aggregate_only
                else f"{cells[t]['count']} · {' '.join(cells[t]['examples'])}"
            )
            if t in cells
            else ""
            for t in types
        ]
        lines.append(f"| {s} | {total} | " + " | ".join(vals) + " |")
    if aggregate_only:
        return [*lines, "", "Cells: counts only (aggregate-only output)."]
    lines += ["", "Cells: count · up to 3 example question IDs.", ""]
    if fails:
        lines += ["<details><summary>Every wrong answer with its signals</summary>", ""]
        for f in sorted(fails, key=lambda f: (f.system, f.qid)):
            sig = "; ".join(f"{t.type}: {t.detail}" if t.detail else t.type for t in f.tags)
            note = f" · LLM: **{f.llm_type}** ({f.llm_reason})" if f.llm_type else ""
            lines.append(
                f"- `{f.system}/{f.qid}` ({f.category}, {f.verdict}) → **{f.primary}** — "
                f"{sig or 'no signals'}{note}"
            )
        lines += ["", "</details>"]
    refined = [f for f in fails if f.llm_type]
    if refined:
        counts = Counter((f.system, f.llm_type) for f in refined)
        kinds = sorted({t for _, t in counts if t})
        lines += [
            "",
            f"LLM refinement of the {len(refined)} answers in generic buckets (retrieval miss / "
            "reasoning / unclassified):",
            "",
            "| System | " + " | ".join(k.replace("_", " ") for k in kinds) + " |",
            "|---|" + "---|" * len(kinds),
        ]
        for s in sorted({f.system for f in refined}):
            lines.append(
                f"| {s} | " + " | ".join(str(counts.get((s, k), "")) for k in kinds) + " |"
            )
    return lines


def write_analysis(
    report: dict[str, Any],
    rows: list[dict[str, Any]],
    fails: list[Failure],
    out_stem: Path,
    *,
    seed: int,
    volume: int,
    gt_note: str,
    gold: dict[str, metrics.Gold] | None = None,
    price_model: str = metrics.DEFAULT_PRICE_MODEL,
    aggregate_only: bool = False,
) -> dict[str, Path]:
    """Write ``<stem>.analysis.md``, ``<stem>.analysis.json`` and ``<stem>.failures.jsonl``.

    ``aggregate_only`` (for private data such as the real slice): no failures file and no
    per-question IDs or signals, only counts and rates."""
    families = pairwise(rows, seed)
    a = analyze(
        report, rows, fails, families, seed=seed, volume=volume, gold=gold,
        price_model=price_model,
    )  # fmt: skip
    if aggregate_only:
        for cells in a["failures"]["table"].values():
            for c in cells.values():
                c["examples"] = []
        a["meta"]["aggregate_only"] = True
    paths = {
        "md": out_stem.with_name(f"{out_stem.name}.analysis.md"),
        "json": out_stem.with_name(f"{out_stem.name}.analysis.json"),
    }
    paths["md"].write_text(
        to_markdown(report, a, families, fails, gt_note=gt_note, aggregate_only=aggregate_only)
    )
    paths["json"].write_text(json.dumps(a, indent=2, default=str) + "\n")
    if not aggregate_only:
        paths["failures"] = out_stem.with_name(f"{out_stem.name}.failures.jsonl")
        paths["failures"].write_text("".join(json.dumps(f.to_json()) + "\n" for f in fails))
    return paths
