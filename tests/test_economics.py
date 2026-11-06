"""Unit economics: re-pricing, ingest profiles from progress records, break-even, the CLI table."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tenor.evals import economics as ec
from tenor.llm.client import cost_usd


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_price_matches_client_and_applies_cache_and_batch() -> None:
    u = ec.Usage(
        input_tokens=1000, output_tokens=100, cache_read_tokens=5000, cache_write_tokens=200
    )
    m = "claude-sonnet-5-5"
    assert ec.price(u, m) == pytest.approx(cost_usd(m, 1000, 100, 5000, 200))
    assert ec.price(u, m, batch=True) == pytest.approx(ec.price(u, m) / 2)
    full_hit = ec.price(u, m, cache_hit=1.0)
    assert full_hit == pytest.approx(cost_usd(m, 0, 100, 6200, 0))


def test_infer_model_recovers_the_pricing_model() -> None:
    u = ec.Usage(input_tokens=10_000, output_tokens=500)
    u = ec.Usage(
        input_tokens=10_000,
        output_tokens=500,
        recorded_usd=cost_usd("claude-haiku-5-5", 10_000, 500),
    )
    assert ec.infer_model(u) == ("claude-haiku-5-5", pytest.approx(0.0))


def test_ingest_profile_drops_the_partial_last_meeting_and_uncounts_cache(tmp_path: Path) -> None:
    m = "claude-sonnet-5-5"
    rows = []
    for note in ("n1", "n1", "n2", "n3"):  # n3 is in flight: the build isn't complete
        # mem0 convention: input_tokens counts the whole prompt (cache included)
        rows.append(
            {
                "key": f"{note}:t000",
                "llm_calls": 1,
                "input_tokens": 1500,
                "output_tokens": 100,
                "cache_read_tokens": 1000,
                "cache_write_tokens": 0,
                "cost_usd": cost_usd(m, 500, 100, 1000, 0),
            }
        )
    _write_jsonl(tmp_path / "mem0" / "c" / "b" / "progress.jsonl", rows)
    (p,) = ec.load_ingest(tmp_path)
    assert (p.system, p.meetings, p.complete, p.model) == ("mem0", 2, False, m)
    assert p.model_error == pytest.approx(0.0)
    assert p.per_meeting.input_tokens == pytest.approx(750)  # (2 x 500 + 500) / 2
    assert p.per_meeting.cache_read_tokens == pytest.approx(1500)


def test_split_cache_recovers_the_cached_share() -> None:
    m = "claude-opus-5-5"
    usd = cost_usd(m, 20_000, 300, 80_000, 0)
    u = ec.split_cache(ec.Usage(input_tokens=100_000, output_tokens=300, recorded_usd=usd), m)
    assert u.cache_read_tokens == pytest.approx(80_000)
    assert u.input_tokens == pytest.approx(20_000)
    plain = ec.Usage(input_tokens=1000, output_tokens=10, recorded_usd=cost_usd(m, 1000, 10))
    assert ec.split_cache(plain, m) == plain


def test_breakeven_is_ingest_over_per_question_saving() -> None:
    assert ec.breakeven_questions(4.0, 0.01, 0.05) == pytest.approx(100)
    assert ec.breakeven_questions(4.0, 0.05, 0.01) is None


def _report(tmp_path: Path) -> Path:
    jl = tmp_path / "r.jsonl"
    m = "claude-opus-5-5"
    rows = []
    for i in range(4):
        rows.append(
            {
                "system": "b0_topk",
                "qid": f"q{i}",
                "correct": i < 3,
                "input_tokens": 50_000,
                "output_tokens": 200,
                "cost_usd": cost_usd(m, 50_000, 200),
                "latency_s": 4.0,
                "tool_calls": 0,
            }
        )
        rows.append(
            {
                "system": "concentrate",
                "qid": f"q{i}",
                "correct": True,
                "input_tokens": 5_000,
                "output_tokens": 300,
                "cost_usd": cost_usd(m, 5_000, 300),
                "latency_s": 3.0,
                "tool_calls": 0,
            }
        )
    _write_jsonl(jl, rows)
    jl.with_suffix(".json").write_text(json.dumps({"meta": {"model": m}}))
    return jl


def test_compute_prices_systems_and_breakeven(tmp_path: Path) -> None:
    rows = [
        {
            "note_id": f"n{i}",
            "llm_calls": 1,
            "input_tokens": 10_000,
            "output_tokens": 500,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": cost_usd("claude-sonnet-5-5", 10_000, 500),
        }
        for i in range(3)
    ]
    build = tmp_path / "cache" / "concentrate" / "c" / "b"
    _write_jsonl(build / "progress.jsonl", rows)
    (build / "complete.json").write_text("{}")
    model, answers = ec.load_answers(_report(tmp_path))
    assert answers["b0 top-k"].accuracy == 0.75
    sc = ec.Scenario(meetings=10, questions=20)
    costs = {
        c.system: c
        for c in ec.compute(ec.pick_builds(ec.load_ingest(tmp_path / "cache")), answers, model, sc)
    }
    c, b0 = costs["concentrate"], costs["b0 top-k"]
    per_meeting = cost_usd("claude-sonnet-5-5", 10_000, 500) / 2  # batch ingest
    assert c.ingest_per_meeting == pytest.approx(per_meeting)
    assert c.monthly == pytest.approx(10 * per_meeting + 20 * c.answer_per_question)
    assert b0.monthly == pytest.approx(20 * cost_usd(model, 50_000, 200))
    assert c.breakeven["b0 top-k"] == pytest.approx(
        10 * per_meeting / (b0.answer_per_question - c.answer_per_question)
    )
    assert costs["mem0"].answer_source.startswith("planning")  # no build, no rows: estimate
    # Re-pricing ingest on Haiku makes it 20x cheaper (same tokens).
    haiku = {
        x.system: x
        for x in ec.compute(
            ec.pick_builds(ec.load_ingest(tmp_path / "cache")),
            answers,
            model,
            ec.Scenario(meetings=10, questions=20, ingest_model="claude-haiku-5-5"),
        )
    }
    assert haiku["concentrate"].ingest_per_meeting == pytest.approx(per_meeting / 20)
    md = ec.to_markdown(list(costs.values()), {}, ec.sensitivity({}, answers, model, sc), sc, "r")
    assert "Break-even" in md and "| concentrate |" in md
