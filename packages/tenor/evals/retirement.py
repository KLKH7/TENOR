"""The transcript-retirement curve (workstream C).

Granola has said it is weighing deleting old transcripts while keeping a working memory. This
module measures what that costs in answer quality. A meeting is **retired** for a question when
its day is more than ``N`` days before the question's ``as_of`` date (the same rule as
:func:`tenor.memory.query.build_context`); ``N = None`` means no retirement.

Three systems are swept:

* ``concentrate@N``: Concentrate with ``retire_after_days=N``. Retired meetings keep L2 (verified
  facts with line IDs) and L3 (digests); their L0/L1 transcript lines leave the context.
* ``b0_topk@N`` (:class:`RetiredTopKSystem`): top-k retrieval where a retired meeting's transcript
  is deleted. Its chunks can no longer be retrieved, and if the meeting is still retrieved via its
  summary/notes document it is shown summary-only. This is "Granola deletes transcripts, the agent
  keeps searching what's left".
* ``b1_summaries``: summaries and notes only for every meeting, i.e. ``N = 0`` for b0 and roughly
  what Granola keeps anyway.

Besides accuracy, the curve reports how many tokens each setting keeps (from the Concentrate
build's per-meeting tier sizes) and how much gold transcript evidence each answer context still
contains (:func:`gold_evidence`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from tenor.agents import corpus as cv
from tenor.agents.baselines import (
    DEFAULT_K,
    MeetingRetriever,
    QuestionLike,
    TopKSystem,
    _user_prompt,
)
from tenor.core.models import MeetingNote
from tenor.evals.scoring import bootstrap_ci
from tenor.llm.client import LLMClient, ToolBackend

INF = "inf"  # label of the no-retirement setting


def label(n: int | None) -> str:
    """``None`` -> ``"inf"``, ``14`` -> ``"14"``."""
    return INF if n is None else str(n)


def parse_settings(text: str) -> list[int | None]:
    """``"inf,30,14,7,0"`` -> ``[None, 30, 14, 7, 0]``."""
    out: list[int | None] = []
    for part in (p.strip() for p in text.split(",")):
        if part:
            out.append(None if part in (INF, "∞", "none") else int(part))
    return out


def ordered(ns: Sequence[int | None]) -> list[int | None]:
    """Plot order, from most to least transcript kept: ∞ first, then decreasing N."""
    return sorted(ns, key=lambda n: (n is not None, -(n or 0)))


def meeting_day(m: MeetingNote) -> date:
    return (m.scheduled_start or m.created_at).date()


def cutoff(as_of: date, n: int | None) -> date | None:
    """Meetings strictly before this day are retired (``None``: nothing is)."""
    return None if n is None else as_of - timedelta(days=n)


def is_retired(day: date, as_of: date, n: int | None) -> bool:
    c = cutoff(as_of, n)
    return c is not None and day < c


class RetiredTopKSystem(TopKSystem):
    """B0 after transcript deletion: retired meetings lose their transcript entirely.

    Ranking skips the transcript-chunk documents of retired meetings (only their summary/notes
    document stays searchable), and retired meetings that are still retrieved are rendered with
    :func:`~tenor.agents.corpus.summary_only_view`. ``retire_after_days=None`` is plain B0.
    """

    def __init__(
        self,
        client: LLMClient,
        retriever: MeetingRetriever,
        k: int = DEFAULT_K,
        backend: ToolBackend | None = None,
        *,
        retire_after_days: int | None = None,
    ) -> None:
        super().__init__(client, retriever, k, backend)
        self.retire_after_days = retire_after_days
        self.name = f"b0_topk@{label(retire_after_days)}"
        # MeetingRetriever adds one summary/notes doc per meeting, then that meeting's chunks.
        seen: set[str] = set()
        self._is_chunk: list[bool] = []
        for owner in retriever.owners:
            self._is_chunk.append(owner in seen)
            seen.add(owner)
        self._day = {nid: meeting_day(m) for nid, m in retriever.notes.items()}

    def _top(self, q: QuestionLike) -> list[MeetingNote]:
        r, n = self.retriever, self.retire_after_days
        ranked: list[str] = []
        for doc in r.index.search(q.question, k=len(r.index.docs)):
            nid = r.owners[doc]
            if self._is_chunk[doc] and is_retired(self._day[nid], q.as_of, n):
                continue  # the transcript chunk was deleted
            if nid not in ranked:
                ranked.append(nid)
            if len(ranked) == self.k:
                break
        return [r.notes[x] for x in ranked]

    def _context(self, q: QuestionLike) -> tuple[str, list[str]]:
        meetings = cv.by_date(self._top(q))
        body = "\n\n".join(
            cv.summary_only_view(m)
            if is_retired(meeting_day(m), q.as_of, self.retire_after_days)
            else cv.full_view(m)
            for m in meetings
        )
        return f"<meetings>\n{body}\n</meetings>\n\n{_user_prompt(q)}", [
            m.note_id for m in meetings
        ]


def gold_evidence(context: str, gold_ids: Sequence[str], *, text: bool = True) -> float | None:
    """Share of the question's gold transcript lines present in ``context``.

    With ``text`` (the default) a line counts only if its text is shown: Concentrate's source
    lines and B0's transcripts both render a line as ``[<note>:<idx>] <speaker>: ...`` at the
    start of a row. With ``text=False`` any mention of the ID counts, including the provenance
    pointers that Concentrate facts keep after retirement (``lines [<note>:<idx>, ...]``).
    ``None`` when the question has no gold lines (unanswerable).
    """
    if not gold_ids:
        return None
    if text:
        hay = "\n" + context
        return sum(f"\n[{g}] " in hay for g in gold_ids) / len(gold_ids)
    return sum(g in context for g in gold_ids) / len(gold_ids)


def tokens_kept(
    meetings: list[dict[str, Any]], as_ofs: Sequence[date], n: int | None
) -> dict[str, float]:
    """Mean tokens the store keeps for a question under setting ``n``, from Concentrate tiers.

    Per question, only meetings on or before ``as_of`` exist. Live meetings keep all four tiers;
    retired ones keep L2 + L3. ``transcript`` counts the raw transcript (L0) still stored, and
    ``share`` is total kept over the no-retirement total.
    """
    totals = {"transcript": 0.0, "memory": 0.0, "total": 0.0, "share": 0.0}
    for as_of in as_ofs:
        kept = full = transcript = memory = 0
        for m in meetings:
            day = date.fromisoformat(m["day"])
            if day > as_of:
                continue
            t = m["tiers"]
            mem = t["L2"] + t["L3"]
            full += t["L0"] + t["L1"] + mem
            memory += mem
            if is_retired(day, as_of, n):
                kept += mem
            else:
                kept += t["L0"] + t["L1"] + mem
                transcript += t["L0"]
        totals["transcript"] += transcript
        totals["memory"] += memory
        totals["total"] += kept
        totals["share"] += kept / full if full else 1.0
    return {k: v / max(1, len(as_ofs)) for k, v in totals.items()}


def retired_share(notes: list[MeetingNote], as_ofs: Sequence[date], n: int | None) -> float:
    """Mean share of meetings (held as of each question) whose transcript is retired."""
    shares = []
    for as_of in as_ofs:
        held = [meeting_day(m) for m in notes if meeting_day(m) <= as_of]
        shares.append(sum(is_retired(d, as_of, n) for d in held) / max(1, len(held)))
    return sum(shares) / max(1, len(shares))


def curve(
    rows: list[dict[str, Any]],
    evidence: dict[tuple[str, str], float | None],
    seed: int,
    pointers: dict[tuple[str, str], float | None] | None = None,
) -> dict[str, Any]:
    """Accuracy (overall and by category, with bootstrap CIs) and mean gold evidence per system.

    ``rows`` are the per-question JSONL rows of an eval report; ``evidence`` maps
    ``(system, qid)`` to :func:`gold_evidence` (line text shown); ``pointers`` likewise for
    ``text=False`` (line ID referenced).
    """
    out: dict[str, Any] = {}
    for s in dict.fromkeys(r["system"] for r in rows):
        mine = [r for r in rows if r["system"] == s]
        cats: dict[str, list[float]] = {}
        for r in mine:
            cats.setdefault(r["category"], []).append(float(r["correct"]))
        acc = [float(r["correct"]) for r in mine]
        ev = [e for r in mine if (e := evidence.get((s, r["qid"]))) is not None]
        pt = [e for r in mine if (e := (pointers or {}).get((s, r["qid"]))) is not None]
        out[s] = {
            "n": len(mine),
            "accuracy": sum(acc) / max(1, len(acc)),
            "accuracy_ci95": bootstrap_ci(acc, seed),
            "gold_evidence": sum(ev) / len(ev) if ev else None,
            "gold_pointers": sum(pt) / len(pt) if pt else None,
            "input_tokens_per_q": sum(r["input_tokens"] for r in mine) / max(1, len(mine)),
            "by_category": {
                c: {
                    "n": len(v),
                    "accuracy": sum(v) / len(v),
                    "accuracy_ci95": bootstrap_ci(v, seed),
                }
                for c, v in sorted(cats.items())
            },
        }
    return out


def load_rows(jsonl: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in jsonl.read_text().splitlines() if line.strip()]


def _pct(x: float | None) -> str:
    return "-" if x is None or x != x else f"{100 * x:.0f}%"


def _ci(s: dict[str, Any]) -> str:
    lo, hi = s["accuracy_ci95"]
    return f"{_pct(s['accuracy'])} [{_pct(lo)}, {_pct(hi)}]"


def to_markdown(result: dict[str, Any]) -> str:
    """The curve as markdown: overall table, by-category table, provenance line."""
    m, cur = result["meta"], result["curve"]
    lines = [
        f"# Transcript-retirement curve: {m['date']}",
        "",
        f"Split **{m['split']}**, {m['questions']} questions · answer model `{m['model']}` · "
        f"seed {m['seed']} · git `{m['git']['sha']}`{' (dirty)' if m['git']['dirty'] else ''} · "
        f"Concentrate store `{m['store']}` · eval report `{m['eval_report']}` · "
        f"spent ${m['spent_usd']:.2f}",
        "",
        "A meeting is retired for a question when it is more than N days older than the "
        "question's as-of date. Tokens kept come from the Concentrate build's tier sizes "
        "(mean over questions, meetings held as of each question).",
        "",
        "| System | N (days) | Accuracy [95% CI] | Gold line text in context | "
        "Gold line IDs referenced | Meetings retired | Transcript tok kept | Store tok kept | "
        "Input tok/q |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s, v in cur.items():
        swept = s in result["settings"]
        lab = label(result["settings"][s]) if swept else None
        kept = result["kept"][lab] if lab is not None else {}
        retired = result["retired_share"][lab] if lab is not None else None
        n_txt = ("∞" if lab == INF else lab) if lab is not None else "-"
        lines.append(
            f"| {s} | {n_txt} | {_ci(v)} | {_pct(v['gold_evidence'])} | "
            f"{_pct(v.get('gold_pointers'))} | "
            f"{_pct(retired)} | {kept.get('transcript', 0):,.0f} | "
            f"{_pct(kept.get('share'))} | {v['input_tokens_per_q']:,.0f} |"
        )
    cats = sorted({c for v in cur.values() for c in v["by_category"]})
    names = list(cur)
    lines += [
        "",
        "## Accuracy by category [95% CI]",
        "",
        "| Category | n | " + " | ".join(names) + " |",
        "|---|---|" + "---|" * len(names),
    ]
    for c in cats:
        n_c = next(v["by_category"][c]["n"] for v in cur.values() if c in v["by_category"])
        vals = [_ci(cur[s]["by_category"][c]) if c in cur[s]["by_category"] else "-" for s in names]
        lines.append(f"| {c} | {n_c} | " + " | ".join(vals) + " |")
    lines += ["", f"Prompts: `{json.dumps(m['prompts'])}`", ""]
    return "\n".join(lines)


SVG_COLORS = {"concentrate": "#2a7d4f", "b0_topk": "#c0562f", "b1_summaries": "#6b6b6b"}


def to_svg(result: dict[str, Any], categories: Sequence[str] = ()) -> str:
    """A dependency-free SVG line chart: accuracy vs N, one panel per category (plus overall).

    The x axis is ordinal (∞, then decreasing N); b1 is drawn as a dashed horizontal reference.
    """
    cur, settings = result["curve"], result["settings"]
    order = result["order"]  # setting labels, left to right
    cats = ["overall", *categories]
    pw, ph, pad, cols = 240, 170, 36, 3
    rows_n = (len(cats) + cols - 1) // cols
    w, h = cols * (pw + pad) + pad, rows_n * (ph + pad + 20) + pad + 30
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}" font-family="sans-serif" font-size="11">',
        f'<rect width="{w}" height="{h}" fill="white"/>',
    ]

    def acc(s: str, cat: str) -> float | None:
        v = cur[s] if cat == "overall" else cur[s]["by_category"].get(cat)
        return None if v is None else float(v["accuracy"])

    for i, cat in enumerate(cats):
        x0 = pad + (i % cols) * (pw + pad)
        y0 = pad + 20 + (i // cols) * (ph + pad + 20)
        out.append(f'<text x="{x0}" y="{y0 - 8}" font-weight="bold">{cat}</text>')
        out.append(
            f'<rect x="{x0}" y="{y0}" width="{pw}" height="{ph}" fill="none" stroke="#ccc"/>'
        )
        for t in (0.0, 0.5, 1.0):
            y = y0 + ph * (1 - t)
            out.append(f'<text x="{x0 - 4}" y="{y + 4}" text-anchor="end">{int(t * 100)}%</text>')
        xs = {lab: x0 + 12 + j * (pw - 24) / max(1, len(order) - 1) for j, lab in enumerate(order)}
        for lab, x in xs.items():
            out.append(
                f'<text x="{x}" y="{y0 + ph + 13}" text-anchor="middle">'
                f"{'∞' if lab == INF else lab}</text>"
            )
        for family, color in SVG_COLORS.items():
            pts = [
                (xs[label(n)], a)
                for s, n in settings.items()
                if s.split("@")[0] == family and label(n) in xs and (a := acc(s, cat)) is not None
            ]
            if family == "b1_summaries" and "b1_summaries" in cur:
                a = acc("b1_summaries", cat)
                if a is not None:
                    y = y0 + ph * (1 - a)
                    out.append(
                        f'<line x1="{x0}" x2="{x0 + pw}" y1="{y}" y2="{y}" stroke="{color}" '
                        'stroke-dasharray="4 3"/>'
                    )
                continue
            pts.sort(key=lambda p: p[0])
            if pts:
                path = " ".join(f"{x:.1f},{y0 + ph * (1 - a):.1f}" for x, a in pts)
                out.append(
                    f'<polyline points="{path}" fill="none" stroke="{color}" stroke-width="2"/>'
                )
                out += [
                    f'<circle cx="{x:.1f}" cy="{y0 + ph * (1 - a):.1f}" r="3" fill="{color}"/>'
                    for x, a in pts
                ]
    lx = pad
    for family, color in SVG_COLORS.items():
        if any(s.split("@")[0] == family for s in cur):
            out.append(f'<rect x="{lx}" y="{h - 22}" width="12" height="4" fill="{color}"/>')
            out.append(f'<text x="{lx + 16}" y="{h - 17}">{family}</text>')
            lx += 130
    out.append(
        f'<text x="{w - pad}" y="{h - 17}" text-anchor="end">x: N days of transcript kept '
        "(∞ = no retirement)</text>"
    )
    out.append("</svg>")
    return "\n".join(out)


def answer_context(s: object, q: QuestionLike) -> str | None:
    """The user message a system would send for ``q`` (no LLM call), if it is single-context."""
    from tenor.agents.concentrate_system import ConcentrateSystem

    if isinstance(s, ConcentrateSystem):
        return s._user(q)[0]
    if isinstance(s, TopKSystem):  # b0, retired b0 and b1 (a TopKSystem subclass)
        return s._context(q)[0]
    return None


def context_evidence(
    systems: Sequence[object], questions: Sequence[Any], *, text: bool = True
) -> dict[tuple[str, str], float | None]:
    """:func:`gold_evidence` of every (system, question) context, computed offline."""
    out: dict[tuple[str, str], float | None] = {}
    for s in systems:
        for q in questions:
            ctx = answer_context(s, q)
            if ctx is not None:
                out[(getattr(s, "name", "?"), q.id)] = gold_evidence(
                    ctx, q.gold_utterance_ids, text=text
                )
    return out


def evidence_markdown(
    evidence: dict[tuple[str, str], float | None], questions: Sequence[Any]
) -> str:
    """Mean share of gold transcript lines present in the context, per system and category."""
    cat = {q.id: q.category for q in questions}
    names = list(dict.fromkeys(s for s, _ in evidence))
    cats = sorted(
        {c for q, c in cat.items() if any(evidence.get((s, q)) is not None for s in names)}
    )

    def mean(s: str, c: str | None) -> str:
        v = [
            e
            for (sys_, qid), e in evidence.items()
            if sys_ == s and e is not None and (c is None or cat[qid] == c)
        ]
        return _pct(sum(v) / len(v)) if v else "-"

    lines = [
        "| Category | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
        "| **all answerable** | " + " | ".join(mean(s, None) for s in names) + " |",
    ]
    lines += [f"| {c} | " + " | ".join(mean(s, c) for s in names) + " |" for c in cats]
    return "\n".join(lines)
