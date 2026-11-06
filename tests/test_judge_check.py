from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from tenor.evals.judge_check import (
    SavedAnswer,
    cohen_kappa,
    compare,
    hand_sample,
    judge_check_markdown,
    load_report,
    per_system,
    ranking,
    rejudge_request,
    score_hand_check,
    write_hand_check,
)
from tenor.evals.scoring import JudgeOutput, judge_request
from tenor.synth.questions import UNANSWERABLE, Question


def _answer(system: str, qid: str, verdict: str, **kw: object) -> SavedAnswer:
    base: dict[str, object] = {
        "system": system,
        "qid": qid,
        "category": "single_hop",
        "question": f"What about {qid}?",
        "gold": "$25 per seat",
        "unanswerable": False,
        "answer": "twenty five dollars a seat",
        "abstained": False,
        "verdict": verdict,
    }
    return SavedAnswer(**(base | kw))  # type: ignore[arg-type]


def test_kappa_known_values() -> None:
    assert cohen_kappa(["a", "b", "a", "b"], ["a", "b", "a", "b"]) == 1.0
    # 50% observed agreement with balanced marginals is exactly chance.
    assert cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"]) == 0.0
    # Textbook example: 20 yes/yes, 5 yes/no, 10 no/yes, 15 no/no -> kappa 0.4.
    a = ["y"] * 25 + ["n"] * 25
    b = ["y"] * 20 + ["n"] * 5 + ["y"] * 10 + ["n"] * 15
    assert cohen_kappa(a, b) == pytest.approx(0.4)
    with pytest.raises(ValueError):
        cohen_kappa([], [])


def test_compare_collapses_partial_to_not_correct() -> None:
    stats = compare(["correct", "partially_correct"], ["correct", "incorrect"])
    assert stats["agreement"] == 1.0  # both say "not correct" on the second answer
    assert stats["agreement_3way"] == 0.5
    assert stats["confusion"]["partially_correct"]["incorrect"] == 1


def test_rejudge_request_is_byte_identical_to_the_eval_judge_call() -> None:
    q = Question(
        id="q1",
        category="latest_state",
        question="What is the price?",
        answer="$25 per seat",
        answer_norm=["25"],
        evidence_keys=[],
        gold_utterance_ids=[],
        as_of=date(2026, 10, 1),
        sources=[],
        split="dev",
    )
    saved = _answer("b0", "q1", "correct", question=q.question, answer="$25", gold=q.answer)
    assert rejudge_request(saved) == judge_request(q, "$25", False)
    unans = q.model_copy(update={"answer_norm": [UNANSWERABLE]})
    saved_u = _answer("b0", "q1", "correct", question=q.question, answer="$25", unanswerable=True)
    assert rejudge_request(saved_u).user == judge_request(unans, "$25", False).user


def test_per_system_and_ranking_change_is_reported() -> None:
    answers = [
        _answer("b0", "q1", "correct"),
        _answer("b0", "q2", "correct"),
        _answer("c", "q1", "correct"),
        _answer("c", "q2", "incorrect"),
    ]
    second = ["incorrect", "incorrect", "correct", "correct"]
    systems = per_system(answers, second)
    assert systems["b0"] == {"primary": 1.0, "second": 0.0, "n": 2}
    assert ranking(systems, "primary") == ["b0", "c"]
    assert ranking(systems, "second") == ["c", "b0"]
    md = judge_check_markdown(
        answers,
        [JudgeOutput(verdict=v, reason="r") for v in second],  # type: ignore[arg-type]
        primary_model="opus",
        second_model="sonnet",
        report_name="r1",
    )
    assert "CHANGED" in md and "Disagreements (3)" in md


def test_load_report_reads_eval_jsonl(tmp_path: Path) -> None:
    row = {
        "system": "b0_topk",
        "qid": "q2",
        "category": "temporal",
        "question": "Q?",
        "gold": "G",
        "unanswerable": False,
        "answer": {"answer": "A", "abstain": True, "citations": []},
        "verdict": "incorrect",
        "correct": False,
    }
    p = tmp_path / "r.jsonl"
    p.write_text(json.dumps(row) + "\n")
    [a] = load_report(p)
    assert (a.key, a.answer, a.abstained, a.verdict) == ("b0_topk/q2", "A", True, "incorrect")


def test_hand_sample_is_stratified_blind_and_scorable(tmp_path: Path) -> None:
    answers = [_answer(s, f"q{i}", "correct") for s in ("b0", "b1", "c") for i in range(10)]
    sample = hand_sample(answers, 6, seed=1)
    assert sorted(a.system for a in sample) == ["b0", "b0", "b1", "b1", "c", "c"]
    assert hand_sample(answers, 6, seed=1) == sample  # deterministic

    sheet, key = tmp_path / "h.csv", tmp_path / "h.key.json"
    write_hand_check(sample, sheet, key)
    header = sheet.read_text().splitlines()[0]
    assert "system" not in header and "verdict," not in header  # blind

    rows = list(csv.DictReader(sheet.open()))
    for r, label in zip(rows, ["c", "c", "c", "i", "p", ""], strict=True):
        r["your_verdict"] = label
    with sheet.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    judges = {"primary": {a.key: a.verdict for a in answers}}
    s = score_hand_check(sheet, key, judges)["primary"]
    assert s["n"] == 5  # the blank label is skipped
    assert s["agreement"] == pytest.approx(3 / 5)


def test_hand_check_rejects_unknown_labels(tmp_path: Path) -> None:
    sheet, key = tmp_path / "h.csv", tmp_path / "h.key.json"
    write_hand_check([_answer("b0", "q1", "correct")], sheet, key)
    sheet.write_text(sheet.read_text().replace(",\r\n", ",maybe\r\n", 1).replace(",\n", ",maybe\n"))
    with pytest.raises(ValueError, match="unknown verdict"):
        score_hand_check(sheet, key, {"primary": {"b0/q1": "correct"}})
