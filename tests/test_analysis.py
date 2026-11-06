"""Report v2: paired stats, power, Pareto, failure taxonomy (synthetic rows, no network)."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from tenor.cli import app
from tenor.evals import stats
from tenor.evals.analyze import load_rows, pairwise, write_analysis
from tenor.evals.failures import (
    GroundTruth,
    classify,
    failure_table,
    failures,
    llm_request,
)
from tenor.evals.pareto import SystemPoint, breakeven_volume, frontier

# -- stats ---------------------------------------------------------------------------------------


def test_binomial_and_mcnemar_exact() -> None:
    assert stats.binom_two_sided_p(0, 5) == pytest.approx(2 / 32)
    assert stats.binom_two_sided_p(0, 0) == 1.0
    # 10 questions only A got right, 2 only B: p = 2 * P(X <= 2 | n=12, 1/2)
    a = [True] * 10 + [False] * 2 + [True] * 5
    b = [False] * 10 + [True] * 2 + [True] * 5
    assert stats.mcnemar_exact(a, b) == pytest.approx(2 * (1 + 12 + 66) / 4096)
    assert stats.mcnemar_exact(a, b) == stats.mcnemar_exact(b, a)
    # concordant questions carry no information
    assert stats.mcnemar_exact(a + [True] * 50, b + [True] * 50) == stats.mcnemar_exact(a, b)


def test_holm_matches_hand_computation() -> None:
    assert stats.holm([0.01, 0.04, 0.03]) == pytest.approx([0.03, 0.06, 0.06])
    assert stats.holm([]) == []
    assert stats.holm([0.9, 0.8]) == pytest.approx([1.0, 1.0])


def test_paired_bootstrap_is_seeded_and_paired() -> None:
    rng = random.Random(0)
    a = [rng.random() < 0.8 for _ in range(100)]
    assert stats.paired_bootstrap(a, a, seed=1) == (0.0, 0.0, 0.0)  # identical: no spread
    b = [x if i % 5 else False for i, x in enumerate(a)]
    r1, r2 = stats.paired_bootstrap(a, b, seed=7), stats.paired_bootstrap(a, b, seed=7)
    assert r1 == r2
    diff, lo, hi = r1
    assert lo <= diff <= hi and lo >= 0  # B is never better than A


def test_sample_size_formula_and_inverse() -> None:
    # Connor (1987) by hand: (1.96*sqrt(.2) + 0.8416*sqrt(.19))^2 / .01 = 154.6
    assert stats.mcnemar_sample_size(0.10, 0.20) == 155
    assert stats.mcnemar_sample_size(0.05, 0.20) > 4 * 150  # halving the gap ~quadruples n
    assert stats.mcnemar_sample_size(0.10, 0.0) == stats.mcnemar_sample_size(0.10, 0.10)
    mde = stats.mcnemar_mde(206, 0.2)
    assert stats.mcnemar_sample_size(mde, 0.2) <= 206 < stats.mcnemar_sample_size(mde * 0.97, 0.2)
    # pairing helps: correlated systems need fewer questions than independent ones
    assert stats.mcnemar_sample_size(0.1, 0.2) < stats.unpaired_sample_size(0.85, 0.75)


def test_sample_size_gives_about_80_percent_power() -> None:
    """Monte Carlo: at the planned n, the exact test rejects ~80% of the time (it is a bit
    conservative, so slightly less)."""
    n = stats.mcnemar_sample_size(0.10, 0.20)
    rng = random.Random(3)
    hits = 0
    trials = 400
    for _ in range(trials):
        # discordant share 0.2, split 0.15 (A only) / 0.05 (B only): gap 0.10
        draws = [rng.random() for _ in range(n)]
        a = [d < 0.15 or d >= 0.20 for d in draws]
        b = [0.15 <= d < 0.20 or d >= 0.20 for d in draws]
        hits += stats.mcnemar_exact(a, b) < 0.05
    assert 0.65 < hits / trials < 0.9


# -- pareto --------------------------------------------------------------------------------------


def test_frontier_and_latency_view() -> None:
    pts = [
        SystemPoint("cheap", 0.70, 0.01, latency_p50=1.0),
        SystemPoint("best", 0.95, 0.10, latency_p50=5.0),
        SystemPoint("worse", 0.90, 0.20, latency_p50=0.5),  # dominated on cost-accuracy only
        SystemPoint("tie", 0.70, 0.01, latency_p50=None),  # equal point: not dominated
    ]
    rows = {r.name: r for r in frontier(pts, volume=1000)}
    assert rows["cheap"].on_frontier and rows["best"].on_frontier and rows["tie"].on_frontier
    assert rows["worse"].dominated_by == ["best"]
    assert rows["worse"].dominated_by_latency == []  # it is the fastest
    assert rows["tie"].dominated_by_latency == []  # unknown latency: never dominated on it


def test_ingest_amortisation_moves_the_frontier() -> None:
    write = SystemPoint("memory", 0.9, 0.01, ingest_usd=50.0)
    query = SystemPoint("topk", 0.9, 0.06)
    assert breakeven_volume(write, query) == 1000
    assert {r.name for r in frontier([write, query], 100) if r.on_frontier} == {"topk"}
    assert {r.name for r in frontier([write, query], 10_000) if r.on_frontier} == {"memory"}
    assert breakeven_volume(SystemPoint("m", 0.9, 0.1, ingest_usd=5), query) is None


# -- failure taxonomy ----------------------------------------------------------------------------


@pytest.fixture
def gt(tmp_path: Path) -> GroundTruth:
    directory = {
        "people": [
            {"id": "sam_p", "name": "Sam Patel"},
            {"id": "sam_o", "name": "Sam Okafor"},
            {"id": "tomas", "name": "Tomás Herrera"},
        ],
        "orgs": [{"id": "beacon_freight", "name": "Beacon Freight"}],
        "projects": [{"id": "beacon", "name": "Beacon"}, {"id": "falcon", "name": "Falcon"}],
    }
    fact = {"kind": "ownership", "subject": "falcon", "predicate": "owner"}
    world = {
        "facts": [
            fact | {"id": "f1", "value_norm": "sam_p", "valid_from": "2026-08-31"},
            fact | {"id": "f2", "value_norm": "tomas", "valid_from": "2026-09-21"},
            {"id": "f3", "subject": "beacon", "predicate": "owner", "value_norm": "sam_p",
             "valid_from": "2026-09-01"},
        ],
        "commitments": [],
    }  # fmt: skip
    q = {"evidence_keys": [], "as_of": "2026-10-24", "split": "dev"}
    questions = [
        q | {"id": "q1", "category": "latest_state", "question": "Who owns Falcon now?",
             "answer": "Tomás Herrera", "answer_norm": ["tomas"],
             "gold_utterance_ids": ["not_B:0002"], "sources": ["f2"]},
        q | {"id": "q2", "category": "entity_disambiguation", "question": "Who owns Beacon?",
             "answer": "Sam Patel", "answer_norm": ["sam_p"],
             "gold_utterance_ids": ["not_C:0001"], "sources": ["f3"]},
        q | {"id": "q3", "category": "aggregation", "question": "List the projects.",
             "answer": "Falcon; Beacon; Otter", "answer_norm": ["falcon", "beacon", "otter"],
             "gold_utterance_ids": ["not_A:0001", "not_D:0003"], "sources": []},
        q | {"id": "q4", "category": "unanswerable", "question": "What is the office wifi?",
             "answer": "UNANSWERABLE", "answer_norm": ["UNANSWERABLE"],
             "gold_utterance_ids": [], "sources": []},
    ]  # fmt: skip
    (tmp_path / "directory.json").write_text(json.dumps(directory))
    (tmp_path / "world.json").write_text(json.dumps(world))
    (tmp_path / "questions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in questions))
    return GroundTruth.load(tmp_path)


def _row(qid: str, text: str, **kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "system": "s",
        "qid": qid,
        "category": "x",
        "answer": {"answer": text, "abstain": False, "citations": []},
        "verdict": "incorrect",
        "correct": False,
        "abstained": False,
        "unanswerable": False,
        "exact_match": 0.0,
        "cite_recall": None,
        "retrieved": [],
    }
    return base | kw


def test_stale_value(gt: GroundTruth) -> None:
    row = _row("q1", "Sam Patel owns Falcon.", question="Who owns Falcon now?")
    f = classify(row, gt)
    assert f.primary == "stale_value"
    assert "older" in f.tags[0].detail
    assert classify(_row("q1", "Tomás Herrera, after Sam Patel"), gt).primary != "stale_value"


def test_wrong_entity(gt: GroundTruth) -> None:
    f = classify(_row("q2", "That's Sam Okafor."), gt)
    assert f.primary == "wrong_entity"
    assert "Sam Okafor instead of Sam Patel" in f.tags[0].detail
    # the project Beacon is not "mentioned" inside the customer name Beacon Freight
    f = classify(_row("q2", "Sam handles it for Beacon Freight"), gt)
    assert f.tags[0].detail == "named Beacon Freight instead of Beacon"
    assert classify(_row("q2", "Sam handles the Beacon project"), gt).primary != "wrong_entity"


def test_partial_list_and_retrieval(gt: GroundTruth) -> None:
    cites = {"answer": "Falcon and Beacon", "abstain": False, "citations": ["not_A:0001"]}
    f = classify(_row("q3", "", answer=cites, exact_match=2 / 3), gt)
    assert f.primary == "partial_list" and f.tags[0].detail == "2/3 gold items"
    assert [t.type for t in f.tags] == ["partial_list", "reasoning_or_reading"]

    def retrieval(citations: list[str], retrieved: list[str] = ()) -> str:  # type: ignore[assignment]
        ans = {"answer": "Falcon", "abstain": False, "citations": citations}
        tags = classify(_row("q3", "", answer=ans, retrieved=list(retrieved)), gt).tags
        return next(t.detail for t in tags if t.type == "retrieval_miss")

    assert retrieval([]) == "no citations"
    assert retrieval(["not_Z:0001"], ["not_Z"]) == "wrong meeting; gold meeting not among retrieved"
    assert retrieval(["not_A:0009"]) == "right meeting, no gold line"
    meeting_only = {"answer": "Falcon", "abstain": False, "citations": ["not_D"]}
    f = classify(_row("q3", "", answer=meeting_only), gt)
    assert f.primary == "reasoning_or_reading"  # meeting-level citers are judged by meeting


def test_abstention_types_and_no_ground_truth(gt: GroundTruth) -> None:
    assert classify(_row("q1", "", abstained=True), gt).primary == "abstained_wrongly"
    assert classify(_row("q4", "It's hunter2", unanswerable=True), gt).primary == (
        "should_have_abstained"
    )
    # without ground truth, fall back to the row's own citation recall
    assert classify(_row("q1", "x", cite_recall=0.0), None).primary == "retrieval_miss"
    assert classify(_row("q1", "x"), None).primary == "unclassified"
    # a report from another fixture set: question text differs, ground truth ignored
    assert classify(_row("q1", "Sam Patel", question="Other?"), gt).primary == "unclassified"


def test_failure_table_and_llm_request(gt: GroundTruth) -> None:
    rows = [
        _row("q1", "Sam Patel"),
        _row("q2", "Sam Okafor"),
        _row("q2", "Sam Okafor", system="t"),
        _row("q3", "ok", correct=True, verdict="correct"),
    ]
    fails = failures(rows, gt)
    assert len(fails) == 3
    table = failure_table(fails)
    assert table["s"] == {"stale_value": (1, ["q1"]), "wrong_entity": (1, ["q2"])}
    req = llm_request(rows[0] | {"question": "Q?", "gold": "Tomás"}, fails[0])
    assert req.prompt_id == "failure_classify.v1" and req.effort == "low"
    assert "stale_value" in req.user and "Tomás" in req.user


# -- end to end ----------------------------------------------------------------------------------


def _synthetic_report(tmp_path: Path, n: int = 120) -> Path:
    """Three systems on n questions: 'strong' clearly beats 'weak'; 'mid' is close to 'strong'."""
    rng = random.Random(42)
    acc = {"strong": 0.9, "mid": 0.86, "weak": 0.6}
    rows, metrics = [], {}
    cats = ["single_hop", "temporal"]
    hard = [rng.random() for _ in range(n)]  # shared difficulty: errors are correlated
    # $/q at the interactive Opus price ($4/Mtok in): strong 0.30, mid 0.05, weak 0.02
    tokens = {"strong": 75_000, "mid": 12_500, "weak": 5_000}
    for s, p in acc.items():
        # 'mid' errs independently of the others, so its gap to 'strong' is mostly noise
        correct = [(rng.random() if s == "mid" else h) < p for h in hard]
        for i, c in enumerate(correct):
            rows.append(_row(f"q{i:03d}", "a", system=s, category=cats[i % 2], correct=c,
                             verdict="correct" if c else "incorrect", question=f"Q{i}?",
                             gold="g", cost_usd=0.01, input_tokens=tokens[s],
                             output_tokens=0))  # fmt: skip
        metrics[s] = {
            "accuracy": sum(correct) / n,
            "cost_per_q": {"strong": 0.3, "mid": 0.05, "weak": 0.02}[s],
            "latency_p50": {"strong": 9.0, "mid": 2.0, "weak": float("nan")}[s],
            "by_category": {
                c: {"n": n // 2, "accuracy": sum(correct[j::2]) / (n // 2)}
                for j, c in enumerate(cats)
            },
        }  # fmt: skip
    report = {
        "meta": {"date": "T0", "split": "test", "seed": 42, "model": "m", "git": {"sha": "abc"},
                 "ingest": {"mid": {"cost_usd": 20.0}}},
        "metrics": metrics,
    }  # fmt: skip
    (tmp_path / "T0.json").write_text(json.dumps(report))
    (tmp_path / "T0.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return tmp_path / "T0"


def test_pairwise_detects_a_real_gap(tmp_path: Path) -> None:
    rows = load_rows(_synthetic_report(tmp_path).with_suffix(".jsonl"))
    fams = pairwise(rows, seed=1)
    overall = {(r.a, r.b): r for r in fams["overall"]}
    big = overall[("strong", "weak")]
    assert big.significant and big.b_only == 0 and big.ci_low > 0.15
    assert not overall[("strong", "mid")].significant
    assert set(fams) == {"overall", "single_hop", "temporal"}
    assert all(r.p_holm >= r.p for f in fams.values() for r in f)


def test_write_analysis_is_deterministic(tmp_path: Path) -> None:
    stem = _synthetic_report(tmp_path)
    report = json.loads(stem.with_suffix(".json").read_text())
    rows = load_rows(stem.with_suffix(".jsonl"))
    outs = []
    for _ in range(2):
        paths = write_analysis(report, rows, failures(rows, None), stem, seed=3, volume=1000,
                               gt_note="none")  # fmt: skip
        outs.append({k: p.read_text() for k, p in paths.items()})
    assert outs[0] == outs[1]
    md = outs[0]["md"]
    assert "strong beats weak by" in md and "## 4. Failure taxonomy" in md
    assert "Break-even" in md and "mid vs strong: after" in md
    data = json.loads(outs[0]["json"])
    assert data["power"]["n_now"] == 120
    assert len(outs[0]["failures"].splitlines()) == sum(not r["correct"] for r in rows)


def test_cli_analyze(tmp_path: Path, gt: GroundTruth) -> None:
    stem = _synthetic_report(tmp_path / "r") if (tmp_path / "r").mkdir() is None else None
    assert stem is not None
    result = CliRunner().invoke(app, ["analyze", str(stem.with_suffix(".json")),
                                      "--fixtures", str(tmp_path), "--volume", "500"])  # fmt: skip
    assert result.exit_code == 0, result.output
    md = stem.with_name("T0.analysis.md").read_text()
    assert "÷ 500 questions" in md and "mid (ingest $20.00) pays back" in md
    assert "0/120 report questions matched" in md  # fixture questions differ: noted, not trusted
