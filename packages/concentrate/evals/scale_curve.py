"""Scale curve: does accuracy fall as the corpus grows?

Nested sub-corpora of Northwind-XL: the first 25%, 50% and 100% of meetings by date. A question
is *eligible* at a size when every meeting holding its gold evidence is inside that prefix
(unanswerable questions have none and are eligible everywhere).

Design choice (see docs/archive/northwind/ECONOMICS.md): the question set is fixed up front, the dev questions
eligible at the *middle* size, and every size runs the subset of it that is eligible there. So
the questions answerable at 25% are asked at 25%, 50% and 100%, and the rest at 50% and 100%.
Across sizes the question is the same and only the haystack grows, which isolates the effect we
want; comparing different question samples per size would confound haystack size with question
difficulty.

Concentrate is not rebuilt per size. Its store is bitemporal and was built causally (meeting by
meeting, in date order, each extraction seeing only earlier meetings), so the store "as of" a
prefix is recoverable by rolling back transaction time: drop rows recorded by later meetings and
un-supersede facts whose supersession came from them. :func:`rewind_store` does that on a copy.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import shutil
import sqlite3
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from concentrate.core.models import MeetingNote
from concentrate.synth.questions import Question

FRACTIONS = (0.25, 0.5, 1.0)
COMPLETE_MARKER = "complete.json"
DB_FILE = "memory.sqlite"


def by_date(notes: Sequence[MeetingNote]) -> list[MeetingNote]:
    return sorted(notes, key=lambda m: (m.created_at, m.note_id))


def prefix(notes: Sequence[MeetingNote], fraction: float) -> list[MeetingNote]:
    """The first ``ceil(fraction * N)`` meetings by date."""
    ordered = by_date(notes)
    return ordered[: math.ceil(fraction * len(ordered))]


def gold_meetings(q: Question) -> set[str]:
    return {u.split(":", 1)[0] for u in q.gold_utterance_ids}


def eligible(questions: Sequence[Question], note_ids: set[str]) -> list[Question]:
    return [q for q in questions if gold_meetings(q) <= note_ids]


def stratified_sample(questions: Sequence[Question], n: int, seed: int) -> list[Question]:
    """``n`` questions, round-robin over categories after a seeded shuffle (all if fewer)."""
    if n <= 0 or n >= len(questions):
        return sorted(questions, key=lambda q: q.id)
    rng = random.Random(seed)
    by_cat: dict[str, list[Question]] = defaultdict(list)
    for q in sorted(questions, key=lambda q: q.id):
        by_cat[q.category].append(q)
    for bucket in by_cat.values():
        rng.shuffle(bucket)
    picked: list[Question] = []
    while len(picked) < n:
        for cat in sorted(by_cat):
            if by_cat[cat] and len(picked) < n:
                picked.append(by_cat[cat].pop())
    return sorted(picked, key=lambda q: q.id)


def plan(
    notes: Sequence[MeetingNote],
    questions: Sequence[Question],
    fractions: Sequence[float] = FRACTIONS,
    *,
    anchor: float = 0.5,
    n: int = 40,
    seed: int = 42,
) -> dict[float, tuple[list[MeetingNote], list[Question]]]:
    """``{fraction: (sub-corpus, questions to run)}`` for the nested design."""
    anchor_ids = {m.note_id for m in prefix(notes, anchor)}
    fixed = stratified_sample(eligible(questions, anchor_ids), n, seed)
    out = {}
    for f in sorted(fractions):
        sub = prefix(notes, f)
        out[f] = (sub, eligible(fixed, {m.note_id for m in sub}))
    return out


# -- Concentrate: transaction-time rollback --

_BY_NOTE = ("meetings", "facts", "evidence", "contradictions", "commitments",
            "commitment_events", "rejections", "lines")  # fmt: skip


def rewind_store(src: Path, dst: Path, keep: set[str]) -> dict[str, int]:
    """Copy the memory store ``src`` to ``dst`` as it was after ingesting only ``keep``.

    Rows written by other meetings are deleted; facts superseded or closed by a deleted fact get
    their ``valid_to`` / ``superseded_by`` cleared (the supersession had not happened yet).
    Returns rows removed per table.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    con = sqlite3.connect(dst)
    try:
        con.execute("CREATE TEMP TABLE keep (note_id TEXT PRIMARY KEY)")
        con.executemany("INSERT INTO keep VALUES (?)", [(k,) for k in sorted(keep)])
        removed = {}
        con.execute(
            "UPDATE facts SET superseded_by = NULL, valid_to = NULL, valid_to_recorded_at = NULL "
            "WHERE note_id IN (SELECT note_id FROM keep) AND superseded_by IS NOT NULL AND "
            "superseded_by NOT IN "
            "(SELECT id FROM facts WHERE note_id IN (SELECT note_id FROM keep))"
        )
        # Facts closed without a superseding fact (e.g. by a later meeting's update): reopen if
        # the closing transaction happened after the last kept meeting.
        last = con.execute(
            "SELECT MAX(recorded_at) FROM meetings WHERE note_id IN (SELECT note_id FROM keep)"
        ).fetchone()[0]
        con.execute(
            "UPDATE facts SET valid_to = NULL, valid_to_recorded_at = NULL "
            "WHERE superseded_by IS NULL AND valid_to_recorded_at > ?",
            (last,),
        )
        for table in _BY_NOTE:
            cur = con.execute(
                f"DELETE FROM {table} WHERE note_id NOT IN (SELECT note_id FROM keep)"
            )
            removed[table] = cur.rowcount
        con.execute(
            "DELETE FROM contradictions WHERE fact_a NOT IN (SELECT id FROM facts) "
            "OR fact_b NOT IN (SELECT id FROM facts)"
        )
        cur = con.execute("DELETE FROM entities WHERE first_seen > ?", (last[:10],))
        removed["entities"] = cur.rowcount
        con.commit()
    finally:
        con.close()
    return removed


def place_rewound(src_dir: Path, dst_dir: Path, keep: set[str]) -> dict[str, Any]:
    """Rewind ``src_dir``'s store into ``dst_dir`` with a completion marker, so
    ``ConcentrateSystem.ingest`` treats it as a finished build (a free cache hit)."""
    removed = rewind_store(src_dir / DB_FILE, dst_dir / DB_FILE, keep)
    marker = {"rewound_from": str(src_dir), "meetings": len(keep), "removed": removed}
    (dst_dir / COMPLETE_MARKER).write_text(json.dumps(marker, indent=2))
    return marker


# -- summary --


def summarize_curve(results: dict[float, dict[str, Any]]) -> dict[str, Any]:
    """Accuracy and $/q per (system, size), on all run questions and on the questions common
    to every size, plus paired flips between consecutive sizes on shared questions."""
    rows: dict[float, dict[str, dict[str, dict[str, Any]]]] = {}
    for f, res in results.items():
        rows[f] = defaultdict(dict)
        for r in res["rows"]:
            rows[f][r["system"]][r["qid"]] = r
    sizes = sorted(results)
    systems = sorted({s for f in sizes for s in rows[f]})
    common = set.intersection(*(set(next(iter(rows[f].values()), {})) for f in sizes))
    out: dict[str, Any] = {"sizes": sizes, "common_questions": sorted(common), "systems": {}}
    for s in systems:
        per: list[dict[str, Any]] = []
        for f in sizes:
            rs = rows[f].get(s, {})
            cm = [rs[q] for q in common if q in rs]
            per.append(
                {
                    "fraction": f,
                    "meetings": results[f]["meetings"],
                    "n": len(rs),
                    "accuracy": _mean(r["correct"] for r in rs.values()),
                    "n_common": len(cm),
                    "accuracy_common": _mean(r["correct"] for r in cm),
                    "cost_per_q": _mean(r["cost_usd"] for r in rs.values()),
                    "input_tokens_per_q": _mean(r["input_tokens"] for r in rs.values()),
                    "cite_recall": _mean(
                        r["cite_recall"] for r in rs.values() if r.get("cite_recall") is not None
                    ),
                }
            )
        flips = []
        for a, b in itertools.pairwise(sizes):
            shared = set(rows[a].get(s, {})) & set(rows[b].get(s, {}))
            lost = sum(rows[a][s][q]["correct"] and not rows[b][s][q]["correct"] for q in shared)
            won = sum(rows[b][s][q]["correct"] and not rows[a][s][q]["correct"] for q in shared)
            flips.append({"from": a, "to": b, "shared": len(shared), "lost": lost, "gained": won})
        out["systems"][s] = {"points": per, "flips": flips}
    return out


def _mean(xs: Any) -> float | None:
    vals = [float(x) for x in xs]
    return sum(vals) / len(vals) if vals else None


def to_markdown(summary: dict[str, Any], meta: dict[str, Any]) -> str:
    lines = [
        "# Scale curve",
        "",
        f"Answer + judge model `{meta['model']}` · k={meta['k']} · seed {meta['seed']} · "
        f"git {meta['git']} · {meta['date']}",
        f"Questions: dev, eligible at the {meta['anchor']:.0%} prefix "
        f"({len(summary['common_questions'])} also eligible at every size).",
        "",
        "| system | corpus | meetings | n | accuracy | accuracy on common Qs | $/q | in-tok/q |"
        " cite recall |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s, d in summary["systems"].items():
        for p in d["points"]:
            lines.append(
                f"| {s} | {p['fraction']:.0%} | {p['meetings']} | {p['n']} | {_pct(p['accuracy'])}"
                f" | {_pct(p['accuracy_common'])} ({p['n_common']}) | "
                f"${(p['cost_per_q'] or 0):.4f} | {(p['input_tokens_per_q'] or 0):,.0f} | "
                f"{_pct(p['cite_recall'])} |"
            )
    lines += [
        "",
        "## Paired flips on shared questions (same question, bigger haystack)",
        "",
        "| system | from → to | shared | correct → wrong | wrong → correct |",
        "|---|---|---:|---:|---:|",
    ]
    for s, d in summary["systems"].items():
        for fl in d["flips"]:
            lines.append(
                f"| {s} | {fl['from']:.0%} → {fl['to']:.0%} | {fl['shared']} | "
                f"{fl['lost']} | {fl['gained']} |"
            )
    return "\n".join(lines) + "\n"


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.0%}"
