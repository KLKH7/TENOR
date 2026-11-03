"""Scale curve: nested prefixes, eligibility, the fixed question set, and store rewind."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from concentrate.evals import scale_curve as scv
from concentrate.synth.questions import Question
from tests.memory_helpers import ScriptedLLM, make_note
from tests.test_concentrate_system import _notes, _script, _system


def _q(qid: str, cat: str, gold: list[str]) -> Question:
    return Question(
        id=qid,
        category=cat,
        question="?",
        answer="a",
        answer_norm=["a"],  # type: ignore[arg-type]
        evidence_keys=[],
        gold_utterance_ids=[f"{g}:0001" for g in gold],
        as_of=date(2026, 10, 1),
        sources=[],
        split="dev",
    )


def _corpus() -> list:
    days = ["2026-09-01", "2026-09-03", "2026-09-02", "2026-09-04"]  # n3 is before n2
    return [
        make_note(f"n{i + 1}", d, f"m{i + 1}", [("me", None, "hi")]) for i, d in enumerate(days)
    ]


def test_prefixes_are_by_date_and_nested() -> None:
    notes = _corpus()
    assert [m.note_id for m in scv.prefix(notes, 0.5)] == ["n1", "n3"]
    assert [m.note_id for m in scv.prefix(notes, 0.75)] == ["n1", "n3", "n2"]
    assert len(scv.prefix(notes, 1.0)) == 4


def test_plan_runs_the_fixed_set_where_eligible() -> None:
    qs = [
        _q("q1", "single_hop", ["n1"]),
        _q("q2", "temporal", ["n2"]),
        _q("q3", "unanswerable", []),
        _q("q4", "multi_hop", ["n1", "n4"]),
    ]
    plan = scv.plan(_corpus(), qs, (0.5, 0.75, 1.0), anchor=0.75, n=0)
    ids = {f: [q.id for q in p[1]] for f, p in plan.items()}
    assert ids == {0.5: ["q1", "q3"], 0.75: ["q1", "q2", "q3"], 1.0: ["q1", "q2", "q3"]}


def test_stratified_sample_is_seeded_and_spreads_categories() -> None:
    qs = [_q(f"q{i}", ["single_hop", "temporal", "aggregation"][i % 3], []) for i in range(30)]
    s1, s2 = scv.stratified_sample(qs, 6, 1), scv.stratified_sample(qs, 6, 1)
    assert [q.id for q in s1] == [q.id for q in s2]
    assert (
        sorted(q.category for q in s1)
        == ["aggregation"] * 2 + ["single_hop"] * 2 + ["temporal"] * 2
    )


def _dump(db: Path) -> dict[str, list[tuple[object, ...]]]:
    con = sqlite3.connect(db)
    try:
        return {
            "facts": con.execute("SELECT * FROM facts ORDER BY id").fetchall(),
            "commitments": con.execute("SELECT * FROM commitments ORDER BY id").fetchall(),
            "events": con.execute("SELECT * FROM commitment_events").fetchall(),
            "evidence": con.execute("SELECT * FROM evidence").fetchall(),
            "meetings": con.execute("SELECT note_id, day, seq FROM meetings").fetchall(),
            "lines": con.execute("SELECT * FROM lines ORDER BY utterance_id").fetchall(),
        }
    finally:
        con.close()


def test_rewind_equals_building_on_the_prefix(tmp_path: Path) -> None:
    """Ingestion is causal, so rolling back transaction time must reproduce the prefix build:
    the $25 price un-supersedes the $20 one, the 'done' event disappears."""
    notes = _notes()
    full = _system(tmp_path, ScriptedLLM(_script()))
    full.ingest(notes)
    prefix = _system(tmp_path, ScriptedLLM(_script()))
    prefix.ingest(notes[:1])
    keep = {notes[0].note_id}
    out = tmp_path / "rewound"
    removed = scv.rewind_store(full.store_dir(notes) / scv.DB_FILE, out / scv.DB_FILE, keep)
    assert removed["facts"] >= 1
    got, want = _dump(out / scv.DB_FILE), _dump(prefix.store_dir(notes[:1]) / scv.DB_FILE)
    assert got == want
    marker = scv.place_rewound(full.store_dir(notes), tmp_path / "placed", keep)
    assert marker["meetings"] == 1 and (tmp_path / "placed" / scv.COMPLETE_MARKER).exists()


def test_summarize_curve_counts_paired_flips() -> None:
    def rows(correct: dict[str, bool]) -> list[dict[str, object]]:
        return [
            {
                "system": "b0_topk",
                "qid": q,
                "correct": c,
                "cost_usd": 0.01,
                "input_tokens": 100,
                "cite_recall": 1.0,
            }
            for q, c in correct.items()
        ]

    results = {
        0.5: {"meetings": 2, "rows": rows({"q1": True, "q3": True})},
        1.0: {"meetings": 4, "rows": rows({"q1": False, "q2": True, "q3": True})},
    }
    s = scv.summarize_curve(results)
    assert s["common_questions"] == ["q1", "q3"]
    pts = s["systems"]["b0_topk"]["points"]
    assert [p["accuracy_common"] for p in pts] == [1.0, 0.5]
    assert s["systems"]["b0_topk"]["flips"] == [
        {"from": 0.5, "to": 1.0, "shared": 2, "lost": 1, "gained": 0}
    ]
    assert "Scale curve" in scv.to_markdown(
        s, {"model": "m", "k": 20, "seed": 1, "git": "x", "date": "d", "anchor": 0.5}
    )
