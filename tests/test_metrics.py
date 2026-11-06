"""The $0 metrics and equal-price costing (synthetic rows, no network)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tenor.cli import app
from tenor.evals import economics as ec
from tenor.evals import metrics as mx
from tenor.llm.client import cost_usd
from tenor.synth.questions import UNANSWERABLE, Question

REPORT = Path("evals/reports/20261103T002456Z.jsonl")


def _row(system: str, qid: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "system": system,
        "qid": qid,
        "category": "single_hop",
        "correct": True,
        "verdict": "correct",
        "abstained": False,
        "unanswerable": False,
        "exact_match": 1.0,
        "retrieved": [],
        "input_tokens": 1_000,
        "output_tokens": 100,
        "cost_usd": cost_usd("claude-opus-5-5", 1_000, 100),
        "batched": False,
    }
    return base | kw


GOLD = {
    "q1": mx.Gold(frozenset({"m1"}), 1, False),
    "q2": mx.Gold(frozenset({"m1", "m2"}), 3, False),  # a list question over two meetings
    "q3": mx.Gold(frozenset(), 1, True),  # unanswerable
}


def _by(stats: list[Any]) -> dict[str, Any]:
    return {s.system: s for s in stats}


def test_gold_from_questions_takes_meetings_from_gold_lines() -> None:
    q = Question(
        id="q9", category="aggregation", question="?", answer="a, b", answer_norm=["a", "b"],
        evidence_keys=[], gold_utterance_ids=["not_A:0001", "not_A:0007", "not_B:0002"],
        as_of=date(2026, 1, 1), sources=[],
    )  # fmt: skip
    u = q.model_copy(update={"id": "q8", "answer_norm": [UNANSWERABLE], "gold_utterance_ids": []})
    g = mx.gold_from_questions({"q9": q, "q8": u})
    assert g["q9"] == mx.Gold(frozenset({"not_A", "not_B"}), 2, False)
    assert g["q8"].unanswerable and not g["q8"].meetings


def test_partial_credit_is_half_a_point() -> None:
    rows = [
        _row("s", "q1"),
        _row("s", "q2", correct=False, verdict="partially_correct", exact_match=0.5),
        _row("s", "q3", correct=False, verdict="incorrect", exact_match=0.0),
    ]
    c = _by(mx.credit_stats(rows))["s"]
    assert c.accuracy == pytest.approx(1 / 3) and c.partial == pytest.approx(1 / 3)
    assert c.partial_credit == pytest.approx(1.5 / 3) and c.exact_match == pytest.approx(0.5)


def test_source_recall_and_pulled() -> None:
    rows = [
        _row("s", "q1", retrieved=["m1", "m1", "m9"]),  # duplicates count once
        _row("s", "q2", retrieved=["m2", "m3"]),  # one of two gold meetings
        _row("s", "q3", retrieved=["m4"]),  # unanswerable: pulled, but not in recall
        _row("dark", "q1", retrieved=[]),
        _row("dark", "q2", retrieved=[]),
    ]
    st = _by(mx.source_stats(rows, GOLD))
    s = st["s"]
    assert s.exposed and s.n == 2
    assert s.recall_any == 1.0 and s.recall_all == 0.5
    assert s.pulled_mean == pytest.approx((2 + 2 + 1) / 3) and s.pulled_max == 2
    d = st["dark"]
    assert not d.exposed and d.recall_any is None and d.pulled_mean is None
    assert not _by(mx.source_stats([_row("b4_mem0", "q1")], GOLD))["b4_mem0"].line_citations


def test_list_completeness_only_on_multi_item_questions() -> None:
    rows = [
        _row("s", "q1", exact_match=0.0, correct=False),  # single item: ignored
        _row("s", "q2", exact_match=2 / 3, correct=False),
    ]
    x = _by(mx.list_stats(rows, GOLD))["s"]
    assert x.n == 1 and x.completeness == pytest.approx(2 / 3)
    assert x.complete == 0.0 and x.judged_correct == 0.0


def test_abstention_and_accuracy_when_answered() -> None:
    rows = [
        _row("s", "q3", unanswerable=True, abstained=True),  # right to decline
        _row("s", "q4", unanswerable=True, abstained=False, correct=False),  # should have
        _row("s", "q1", abstained=True, correct=False),  # declined an answerable one
        _row("s", "q2", correct=True),
        _row("s", "q5", correct=False),
        _row("granola_agent", "q1"),
    ]
    a = _by(mx.abstain_stats(rows))
    s = a["s"]
    assert (s.abstained, s.unanswerable) == (2, 2)
    assert s.precision == 0.5 and s.recall == 0.5
    assert s.false_abstain == pytest.approx(1 / 3)
    assert s.coverage == pytest.approx(3 / 5)
    assert s.accuracy == pytest.approx(2 / 5)
    assert s.accuracy_answered == pytest.approx(1 / 3)  # q4 wrong, q2 right, q5 wrong
    g = a["granola_agent"]
    assert not g.flag and g.precision is None and g.coverage is None and g.accuracy == 1.0


def test_equal_price_ignores_how_a_row_was_billed() -> None:
    full = cost_usd("claude-opus-5-5", 10_000, 200)
    rows = [
        _row("s", "q1", input_tokens=10_000, output_tokens=200, cost_usd=full),
        _row("s", "q2", input_tokens=10_000, output_tokens=200, cost_usd=full / 2, batched=True),
        _row("granola_agent", "q1", input_tokens=0, output_tokens=0, cost_usd=0.0),
    ]
    c = _by(mx.cost_stats(rows))
    s = c["s"]
    assert s.usd_interactive == pytest.approx(full)  # both rows at the same, full price
    assert s.usd_batch == pytest.approx(full / 2) and s.batch_saving == pytest.approx(full / 2)
    assert s.usd_recorded == pytest.approx(0.75 * full) and s.batched_share == 0.5
    assert not c["granola_agent"].observable
    sonnet = _by(mx.cost_stats(rows, "claude-sonnet-5-5"))["s"]
    assert sonnet.usd_interactive == pytest.approx(cost_usd("claude-sonnet-5-5", 10_000, 200))


def test_reprice_matches_every_full_price_row_of_the_saved_report() -> None:
    rows = [json.loads(x) for x in REPORT.read_text().splitlines() if x.strip()]
    full = [r for r in rows if not r["batched"]]
    assert full and all(mx.reprice(r) == pytest.approx(r["cost_usd"], rel=1e-9) for r in full)
    batched = [r for r in rows if r["batched"]]
    assert all(mx.reprice(r) == pytest.approx(2 * r["cost_usd"], rel=1e-9) for r in batched)


def test_ingest_of_a_cached_build_is_the_original_build_cost() -> None:
    report = {
        "meta": {
            "ingest": {
                "c": {"cost_usd": 0.0, "cached": True, "detail": {"built": {"cost_usd": 4.9}}},
                "g": {"cost_usd": 59.5, "cached": True, "detail": {}},
                "fresh": {"cost_usd": 3.0, "cached": False},
            }
        }
    }
    assert mx.ingest_usd(report, "c") == 4.9
    assert mx.ingest_usd(report, "g") == 59.5
    assert mx.ingest_usd(report, "fresh") == 3.0
    assert mx.ingest_usd(report, "none") == 0.0


def test_economics_does_not_mistake_the_batch_discount_for_cache_hits(tmp_path: Path) -> None:
    full = cost_usd("claude-opus-5-5", 50_000, 200)
    rows = [
        {"system": "b0_topk", "qid": f"q{i}", "correct": True, "input_tokens": 50_000,
         "output_tokens": 200, "latency_s": 1.0, "batched": i > 0,
         "cost_usd": full / 2 if i > 0 else full}
        for i in range(4)
    ]  # fmt: skip
    jl = tmp_path / "r.jsonl"
    jl.write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "r.json").write_text(json.dumps({"meta": {"model": "claude-opus-5-5"}}))
    _, answers = ec.load_answers(jl)
    u = answers["b0 top-k"].per_question
    assert u.cache_read_tokens == 0 and u.recorded_usd == pytest.approx(full)
    costs = ec.compute({}, answers, "claude-opus-5-5", ec.Scenario())
    b0 = next(c for c in costs if c.system == "b0 top-k")
    assert b0.answer_per_question == pytest.approx(full)
    assert b0.answer_batch_saving == pytest.approx(full / 2)


def test_cli_aggregate_only_writes_no_question_ids(tmp_path: Path) -> None:
    rows = [
        _row("concentrate", f"secret{i}", category="decision", correct=i % 3 > 0,
             verdict="correct" if i % 3 else "incorrect", question=f"private question {i}",
             gold="private gold", answer={"answer": "private answer", "abstain": False,
                                          "citations": []}, retrieved=["m1"],
             cite_precision=None, cite_recall=None, latency_s=1.0)
        for i in range(12)
    ]  # fmt: skip
    report = {
        "meta": {"date": "R0", "split": "real", "seed": 1, "model": "claude-opus-5-5"},
        "metrics": {"concentrate": {"accuracy": 8 / 12, "by_category": {}}},
    }
    (tmp_path / "R0.json").write_text(json.dumps(report))
    (tmp_path / "R0.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    qs = tmp_path / "questions.jsonl"
    qs.write_text(
        "".join(
            json.dumps(
                {"id": f"secret{i}", "category": "decision", "question": "?", "gold_answer": "g",
                 "evidence_note_ids": ["m1"], "as_of": "2026-01-01", "status": "verified"}
            ) + "\n"
            for i in range(12)
        )
    )  # fmt: skip
    out = tmp_path / "pub" / "real-R0"
    result = CliRunner().invoke(
        app,
        ["analyze", str(tmp_path / "R0.jsonl"), "--real-questions", str(qs), "--aggregate-only",
         "--out", str(out)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    written = sorted(p.name for p in out.parent.iterdir())
    assert written == ["real-R0.analysis.json", "real-R0.analysis.md"]
    for p in out.parent.iterdir():
        text = p.read_text()
        assert "secret" not in text and "private" not in text
    data = json.loads((out.parent / "real-R0.analysis.json").read_text())
    src = data["metrics"]["sources"][0]
    assert src["recall_any"] == 1.0 and src["n"] == 12
