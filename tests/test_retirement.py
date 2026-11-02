"""Transcript-retirement curve: retired b0, evidence, tokens kept, curve and renderers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from concentrate.agents.baselines import MeetingRetriever, TopKSystem
from concentrate.core.models import MeetingNote
from concentrate.evals import retirement as rt
from concentrate.llm.client import LLMClient
from tests.test_baselines import HashEmbedder, _q, client, llm, notes  # noqa: F401


def test_parse_settings_and_cutoff() -> None:
    assert rt.parse_settings("inf, 30,14,7,0") == [None, 30, 14, 7, 0]
    assert rt.label(None) == "inf" and rt.label(7) == "7"
    as_of = date(2026, 10, 24)
    assert rt.is_retired(date(2026, 10, 16), as_of, 7)
    assert not rt.is_retired(date(2026, 10, 17), as_of, 7)  # exactly N days old: kept
    assert not rt.is_retired(date(2026, 10, 24), as_of, 0)  # same day: kept
    assert not rt.is_retired(date(2020, 1, 1), as_of, None)


def test_retired_topk_deletes_old_transcripts(
    notes: list[MeetingNote],  # noqa: F811
    client: LLMClient,  # noqa: F811
) -> None:
    r = MeetingRetriever(notes, HashEmbedder())
    q = _q("Falcon cutover PAY-212 payments migration")
    plain = TopKSystem(client, r, k=5)
    none = rt.RetiredTopKSystem(client, r, k=5, retire_after_days=None)
    assert none.name == "b0_topk@inf"
    assert none._context(q) == plain._context(q)  # no retirement = plain b0

    allgone = rt.RetiredTopKSystem(client, r, k=5, retire_after_days=0)
    user, retrieved = allgone._context(q)
    old = [m for m in notes if rt.meeting_day(m) < q.as_of]
    assert len(retrieved) == 5 and all(n in {m.note_id for m in old} for n in retrieved)
    assert "## Transcript" not in user  # every retrieved meeting is summary-only
    # Ranking only used the summary/notes docs: one per meeting, the first for each owner.
    assert sum(not c for c in allgone._is_chunk) == len(notes)


def test_gold_evidence() -> None:
    ctx = "[not_a:0001] Me: hi\n[not_b:0002] Them: yo"
    assert rt.gold_evidence(ctx, ["not_a:0001", "not_c:0003"]) == 0.5
    assert rt.gold_evidence(ctx, []) is None


def test_tokens_kept_and_retired_share(notes: list[MeetingNote]) -> None:  # noqa: F811
    meetings = [
        {"day": "2026-10-01", "tiers": {"L0": 100, "L1": 120, "L2": 10, "L3": 5}},
        {"day": "2026-10-20", "tiers": {"L0": 200, "L1": 240, "L2": 20, "L3": 10}},
        {"day": "2026-10-30", "tiers": {"L0": 999, "L1": 999, "L2": 99, "L3": 99}},  # future
    ]
    as_of = [date(2026, 10, 24)]
    full = rt.tokens_kept(meetings, as_of, None)
    assert full["total"] == 705 and full["share"] == 1.0 and full["transcript"] == 300
    week = rt.tokens_kept(meetings, as_of, 7)
    assert week["total"] == 15 + 470 and week["transcript"] == 200 and week["memory"] == 45
    assert rt.retired_share(notes, [date(2030, 1, 1)], 0) == 1.0
    assert rt.retired_share(notes, [date(2030, 1, 1)], None) == 0.0


def _rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for s, ok in (("concentrate@inf", [1, 1, 1, 0]), ("concentrate@7", [1, 0, 1, 0])):
        for i, c in enumerate(ok):
            rows.append(
                {
                    "system": s,
                    "qid": f"q{i}",
                    "category": "temporal" if i % 2 else "latest_state",
                    "correct": bool(c),
                    "input_tokens": 1000,
                }
            )
    for i in range(4):
        rows.append(
            {
                "system": "b1_summaries",
                "qid": f"q{i}",
                "category": "temporal" if i % 2 else "latest_state",
                "correct": i == 0,
                "input_tokens": 500,
            }
        )
    return rows


def test_curve_markdown_and_svg(tmp_path: Path) -> None:
    rows = _rows()
    ev = {("concentrate@inf", "q0"): 1.0, ("concentrate@7", "q0"): 0.0}
    cur = rt.curve(rows, ev, seed=1)
    assert cur["concentrate@inf"]["accuracy"] == 0.75
    assert cur["concentrate@7"]["by_category"]["temporal"]["accuracy"] == 0.0
    assert cur["concentrate@7"]["gold_evidence"] == 0.0
    tiers = {"day": "2026-10-01", "tiers": {"L0": 10, "L1": 10, "L2": 1, "L3": 1}}
    result = {
        "meta": {
            "date": "x",
            "git": {"sha": "abc", "dirty": False},
            "model": "m",
            "seed": 1,
            "split": "dev",
            "questions": 4,
            "prompts": {},
            "spent_usd": 0.0,
            "store": "s",
            "eval_report": "r",
        },
        "settings": {"concentrate@inf": None, "concentrate@7": 7},
        "order": ["inf", "7"],
        "kept": {
            n: rt.tokens_kept([tiers], [date(2026, 10, 24)], v)
            for n, v in (("inf", None), ("7", 7))
        },
        "retired_share": {"inf": 0.0, "7": 1.0},
        "curve": cur,
    }
    md = rt.to_markdown(result)
    assert "| concentrate@7 | 7 | 50% [" in md and "| b1_summaries | - |" in md
    assert "## Accuracy by category" in md and "| temporal | 2 |" in md
    svg = rt.to_svg(result, ["latest_state", "temporal"])
    assert svg.startswith("<svg") and svg.count("<polyline") == 3  # one concentrate line/panel
    assert "stroke-dasharray" in svg  # b1 reference line
