"""Re-analyse saved runs by notes-sufficient / transcript-needed tag ($0, no new answers).

For each system and tag: accuracy and confident-error rate (answered *and* judged incorrect,
``metrics.confident_error``) with 95% bootstrap CIs, and how often it declined a question
whose answer existed. For chosen pairs (notes-only vs a transcript reader), the paired accuracy
gap on the same questions with a paired-bootstrap CI and McNemar p.

Pure over report rows and a ``{qid: tag}`` map, so the same code runs on the synthetic test
report, the retirement curve runs and (aggregate-only) the private real slice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from concentrate.evals import stats
from concentrate.evals.metrics import _by_system, confident_error
from concentrate.evals.notes_tags import SHORT, TAGS
from concentrate.evals.scoring import bootstrap_ci

ALL = "all"


@dataclass
class TagCell:
    system: str
    tag: str  # a notes_tags.Tag or "all"
    n: int
    accuracy: float | None
    acc_ci: tuple[float, float]
    confident_error: float | None
    ce_ci: tuple[float, float]
    abstained: int
    abstained_answer_existed: int  # declined though the question had an answer
    answerable: int


def _cell(system: str, tag: str, rs: list[dict[str, Any]], seed: int) -> TagCell:
    correct = [float(bool(r["correct"])) for r in rs]
    wrong = [float(not r.get("abstained") and r.get("verdict") == "incorrect") for r in rs]
    nan = (float("nan"), float("nan"))
    return TagCell(
        system=system,
        tag=tag,
        n=len(rs),
        accuracy=sum(correct) / len(rs) if rs else None,
        acc_ci=bootstrap_ci(correct, seed) if rs else nan,
        confident_error=confident_error(rs),
        ce_ci=bootstrap_ci(wrong, seed) if rs else nan,
        abstained=sum(bool(r.get("abstained")) for r in rs),
        abstained_answer_existed=sum(
            bool(r.get("abstained")) and not r.get("unanswerable") for r in rs
        ),
        answerable=sum(not r.get("unanswerable") for r in rs),
    )


def by_tag(rows: Iterable[dict[str, Any]], tags: Mapping[str, str], seed: int = 0) -> list[TagCell]:
    """One cell per (system, tag), plus each system's ``all`` row. Untagged rows are skipped."""
    out = []
    for system, rs in _by_system(rows).items():
        tagged = [r for r in rs if r["qid"] in tags]
        out.append(_cell(system, ALL, tagged, seed))
        for t in TAGS:
            out.append(_cell(system, t, [r for r in tagged if tags[r["qid"]] == t], seed))
    return out


@dataclass
class TagGap:
    """``a`` minus ``b`` in accuracy on the questions of one tag that both answered."""

    tag: str
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


def tag_gaps(
    rows: Iterable[dict[str, Any]],
    tags: Mapping[str, str],
    pairs: Sequence[tuple[str, str]],
    seed: int = 0,
) -> list[TagGap]:
    """Paired accuracy gap per tag for each (a, b) pair present in the rows."""
    outcome: dict[str, dict[str, bool]] = {}
    for r in rows:
        if r["qid"] in tags:
            outcome.setdefault(r["system"], {})[r["qid"]] = bool(r["correct"])
    out = []
    for a, b in pairs:
        if a not in outcome or b not in outcome:
            continue
        for t in (ALL, *TAGS):
            qids = sorted(
                q for q in outcome[a].keys() & outcome[b].keys() if t == ALL or tags[q] == t
            )
            if not qids:
                continue
            xa = [outcome[a][q] for q in qids]
            xb = [outcome[b][q] for q in qids]
            diff, lo, hi = stats.paired_bootstrap(xa, xb, seed=seed)
            pc = stats.paired_counts(xa, xb)
            out.append(
                TagGap(
                    t, a, b, len(qids), sum(xa) / len(qids), sum(xb) / len(qids), diff, lo, hi,
                    pc.a_only, pc.b_only, stats.mcnemar_exact(xa, xb),
                )
            )  # fmt: skip
    return out


def to_json(cells: list[TagCell], gaps: list[TagGap]) -> dict[str, Any]:
    return {"cells": [asdict(c) for c in cells], "gaps": [asdict(g) for g in gaps]}


def _pct(x: float | None) -> str:
    return "n/a" if x is None or x != x else f"{100 * x:.0f}%"


def _ci(ci: Sequence[float]) -> str:
    lo, hi = ci
    return "" if lo != lo else f" [{100 * lo:.0f}, {100 * hi:.0f}]"


def cells_markdown(cells: list[TagCell], *, ce_upper_bound: Iterable[str] = ()) -> list[str]:
    """Accuracy / confident error by tag, one row per system."""
    ub = set(ce_upper_bound)
    head = " | ".join(f"{SHORT[t]}: acc · conf. err" for t in TAGS[:2])
    lines = [
        f"| System | all: n · acc · conf. err | {head} | declined an answerable q |",
        "|---|---|---|---|---|",
    ]
    by: dict[str, dict[str, TagCell]] = {}
    for c in cells:
        by.setdefault(c.system, {})[c.tag] = c
    for system, cs in by.items():
        mark = "≤ " if system in ub else ""
        parts = []
        for t in TAGS[:2]:
            c = cs[t]
            parts.append(
                f"n={c.n} · {_pct(c.accuracy)}{_ci(c.acc_ci)} · "
                f"{mark}{_pct(c.confident_error)}{_ci(c.ce_ci)}"
            )
        a = cs[ALL]
        lines.append(
            f"| {system} | n={a.n} · {_pct(a.accuracy)}{_ci(a.acc_ci)} · "
            f"{mark}{_pct(a.confident_error)}{_ci(a.ce_ci)} | {' | '.join(parts)} | "
            f"{a.abstained_answer_existed} of {a.answerable} |"
        )
    return lines


def gaps_markdown(gaps: list[TagGap]) -> list[str]:
    lines = [
        "| A - B | Tag | n | A | B | Gap (pts) [95% CI] | A only / B only | McNemar p |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for g in gaps:
        tag = g.tag if g.tag == ALL else SHORT[g.tag]
        lines.append(
            f"| {g.a} - {g.b} | {tag} | {g.n} | {_pct(g.acc_a)} | {_pct(g.acc_b)} | "
            f"{100 * g.diff:+.0f} [{100 * g.ci_low:+.0f}, {100 * g.ci_high:+.0f}] | "
            f"{g.a_only} / {g.b_only} | {g.p:.3f} |"
        )
    return lines
