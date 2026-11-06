"""Batch API mode: prefetch into the LLM cache, 50% accounting, fallbacks, honest latency.

A fake batch backend implements create/retrieve/results; nothing touches the network.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import pytest

from tenor.agents.baselines import AgenticSystem, MeetingRetriever, TopKSystem
from tenor.core.models import MeetingNote
from tenor.evals.runner import estimate, load_corpus, load_questions, run_eval
from tenor.evals.scoring import JUDGE_SCHEMA, JudgeOutput
from tenor.llm import client as llm_client
from tenor.llm.client import (
    BatchItemResult,
    Completion,
    LLMClient,
    LLMRequest,
    TurnCompletion,
    TurnRequest,
    cost_usd,
    prefetch_structured,
    prefetch_turns,
    run_turn,
    turn_cache_path,
)
from tests.test_baselines import FIXTURES, FakeLLM, HashEmbedder

MODEL = "claude-opus-5-5"


def _message(params: dict[str, Any]) -> dict[str, Any]:
    """What the fake 'model' answers for a batch request (mirrors FakeLLM's replies)."""
    schema = params["output_config"].get("format", {}).get("schema", {})
    if "verdict" in schema.get("properties", {}):
        verdict = "correct" if "Tomás" in params["messages"][0]["content"] else "incorrect"
        text = json.dumps({"verdict": verdict, "reason": "fake"})
        usage = {"input_tokens": 300, "output_tokens": 20}
    else:
        text = json.dumps(
            {"answer": "Tomás Herrera owns Falcon.", "abstain": False, "citations": []}
        )
        usage = {"input_tokens": 1000, "output_tokens": 100}
    return {
        "model": params["model"],
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": usage,
    }


class FakeBatches:
    """In-memory Message Batches: results come back reversed (any order is legal)."""

    def __init__(
        self,
        in_progress_polls: int = 0,
        outcome: Callable[[str, dict[str, Any]], str] = lambda cid, params: "succeeded",
    ) -> None:
        self.batches: dict[str, list[dict[str, Any]]] = {}
        self.polls: dict[str, int] = {}
        self.in_progress_polls = in_progress_polls
        self.outcome = outcome

    def create(self, requests: list[dict[str, Any]]) -> str:
        ids = [r["custom_id"] for r in requests]
        assert len(ids) == len(set(ids)), "custom_ids must be unique within a batch"
        assert all("fallbacks" not in r["params"] for r in requests)  # rejected by the API
        batch_id = f"msgbatch_{len(self.batches)}"
        self.batches[batch_id] = requests
        self.polls[batch_id] = 0
        return batch_id

    def retrieve(self, batch_id: str) -> str:
        self.polls[batch_id] += 1
        return "ended" if self.polls[batch_id] > self.in_progress_polls else "in_progress"

    def results(self, batch_id: str) -> Iterable[BatchItemResult]:
        for r in reversed(self.batches[batch_id]):
            kind = self.outcome(r["custom_id"], r["params"])
            if kind == "succeeded":
                yield BatchItemResult(r["custom_id"], "succeeded", _message(r["params"]))
            elif kind == "refusal":
                msg = _message(r["params"]) | {"stop_reason": "refusal"}
                yield BatchItemResult(r["custom_id"], "succeeded", msg)
            else:
                yield BatchItemResult(r["custom_id"], kind, error="boom")

    @property
    def submitted(self) -> int:
        return sum(len(v) for v in self.batches.values())


class TimedLLM(FakeLLM):
    """Interactive fake that takes real (wall-clock) time, so measured rows are recognisable;
    batch-served rows carry latency 0."""

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        time.sleep(0.02)
        return super().turn(request, model)


def _turn(user: str) -> TurnRequest:
    return TurnRequest(
        stage="eval.b0_topk",
        prompt_id="answer.v1",
        system="sys",
        messages=({"role": "user", "content": user},),
        schema={"type": "object"},
        max_tokens=8000,
    )


def _judge_req(user: str) -> LLMRequest:
    return LLMRequest("eval.judge", "judge.v1", "judge", user, JUDGE_SCHEMA, 2000, "low")


def _no_sleep(_: float) -> None:
    pass


@pytest.fixture(scope="module")
def notes() -> list[MeetingNote]:
    return load_corpus(FIXTURES)


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # never count tokens over the network


def test_turn_cache_file_is_identical_to_interactive_path(tmp_path: Path) -> None:
    req = _turn("Who owns Falcon?")
    message = _message({**llm_client.turn_params(req, MODEL)})

    class Interactive:
        def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
            return llm_client.turn_completion(message)

    live = LLMClient(model=MODEL, cache_dir=tmp_path / "live")
    run_turn(live, req, Interactive())
    batched = LLMClient(model=MODEL, cache_dir=tmp_path / "batch")
    report = prefetch_turns(batched, [req], FakeBatches(), sleep=_no_sleep)
    assert report.succeeded == 1 and report.submitted == 1

    a, b = turn_cache_path(live, req), turn_cache_path(batched, req)
    assert a.relative_to(tmp_path / "live") == b.relative_to(tmp_path / "batch")
    live_entry, batch_entry = json.loads(a.read_text()), json.loads(b.read_text())
    assert batch_entry["completion"].pop("batch") is True
    assert live_entry["completion"].pop("batch") is False
    assert batch_entry == live_entry

    # Replay hits the cache: a backend that would fail is never called.
    class Boom:
        def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
            raise AssertionError("cache miss")

    assert run_turn(batched, req, Boom()).text == message["content"][0]["text"]


def test_structured_cache_file_is_identical_to_interactive_path(tmp_path: Path) -> None:
    req = _judge_req("Gold answer: Tomás")
    message = _message(llm_client.structured_params(req, MODEL))

    class Interactive:
        def complete(self, request: LLMRequest, model: str) -> Completion:
            text = message["content"][0]["text"]
            return Completion(text, model, 300, 20, "end_turn")

    live = LLMClient(Interactive(), model=MODEL, cache_dir=tmp_path / "live")
    live.structured(req, JudgeOutput)
    batched = LLMClient(model=MODEL, cache_dir=tmp_path / "batch")
    prefetch_structured(batched, [req], FakeBatches(), sleep=_no_sleep)
    live_entry = json.loads(live.cache_path(req).read_text())
    batch_entry = json.loads(batched.cache_path(req).read_text())
    assert live.cache_path(req).name == batched.cache_path(req).name
    assert batch_entry["completion"].pop("batch") is True
    assert live_entry["completion"].pop("batch") is False
    assert batch_entry == live_entry
    replay = LLMClient(model=MODEL, cache_dir=tmp_path / "batch", offline=True)
    assert replay.structured(req, JudgeOutput).verdict == "correct"
    rec = replay.ledger.records[0]
    assert rec.cached and rec.batch and rec.latency_s == 0.0
    assert rec.cost_usd == pytest.approx(cost_usd(MODEL, 300, 20) / 2)


def test_batch_cost_is_half_and_counted_once(tmp_path: Path) -> None:
    client = LLMClient(model=MODEL, cache_dir=tmp_path)
    reqs = [_turn("q1"), _turn("q2")]
    report = prefetch_turns(client, reqs, FakeBatches(), sleep=_no_sleep)
    full = cost_usd(MODEL, 1000, 100)
    assert report.cost_usd == pytest.approx(2 * full * 0.5)
    assert client.ledger.spent_usd() == pytest.approx(full)  # 2 calls at half price
    assert all(r.stage == "eval.b0_topk.batch" and r.batch for r in client.ledger.records)
    for r in reqs:
        run_turn(client, r, None)  # replays: no backend needed
    replays = client.ledger.records[2:]
    assert all(r.cached and r.batch and r.latency_s == 0.0 for r in replays)
    assert [r.cost_usd for r in replays] == pytest.approx([full * 0.5] * 2)
    assert client.ledger.spent_usd() == pytest.approx(full)  # replays add no spend


def test_failed_items_fall_back_to_interactive(tmp_path: Path) -> None:
    reqs = [_turn("ok"), _turn("err"), _turn("exp"), _turn("refused")]
    keys = {llm_client._turn_key(MODEL, r): r.messages[0]["content"] for r in reqs}
    kinds = {"ok": "succeeded", "err": "errored", "exp": "expired", "refused": "refusal"}
    fake = FakeBatches(outcome=lambda cid, params: kinds[keys[cid]])
    client = LLMClient(model=MODEL, cache_dir=tmp_path)
    report = prefetch_turns(client, reqs, fake, sleep=_no_sleep)
    assert report.succeeded == 1
    assert report.fallback == {"errored": 1, "expired": 1, "refusal": 1}
    # The refused item was billed (recorded as spend) even though it is not cached.
    assert len([r for r in client.ledger.records if r.batch]) == 2
    llm = FakeLLM()
    for r in reqs:
        run_turn(client, r, llm)
    assert [t.messages[0]["content"] for t in llm.turns] == ["err", "exp", "refused"]


def test_prefetch_skips_cached_and_duplicate_requests(tmp_path: Path) -> None:
    client = LLMClient(model=MODEL, cache_dir=tmp_path)
    run_turn(client, _turn("a"), FakeLLM())  # already cached interactively
    fake = FakeBatches()
    report = prefetch_turns(
        client, [_turn("a"), _turn("b"), _turn("b"), _turn("c")], fake, sleep=_no_sleep
    )
    assert (report.requested, report.already_cached, report.submitted) == (3, 1, 2)
    assert fake.submitted == 2
    again = prefetch_turns(client, [_turn("a"), _turn("b")], fake, sleep=_no_sleep)
    assert again.submitted == 0 and again.batch_ids == [] and len(fake.batches) == 1


def test_polls_with_backoff_and_chunks_large_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_client, "BATCH_MAX_REQUESTS", 2)
    slept: list[float] = []
    fake = FakeBatches(in_progress_polls=2)
    client = LLMClient(model=MODEL, cache_dir=tmp_path)
    report = prefetch_turns(client, [_turn(str(i)) for i in range(5)], fake, sleep=slept.append)
    assert len(report.batch_ids) == 3 and report.succeeded == 5
    assert slept == [5.0, 7.5]
    for cid in (r["custom_id"] for b in fake.batches.values() for r in b):
        assert len(cid) == 64  # the cache key itself is the custom_id


def test_run_eval_batch_mode_latency_only_from_interactive_sample(
    notes: list[MeetingNote], tmp_path: Path
) -> None:
    llm = TimedLLM()
    client = LLMClient(llm, model=MODEL, cache_dir=tmp_path / "cache")
    qs = load_questions(FIXTURES, "dev", limit=5)
    systems = [
        TopKSystem(client, MeetingRetriever(notes, HashEmbedder()), k=3, backend=llm),
        AgenticSystem(client, notes, backend=llm),
    ]
    fake = FakeBatches()
    report = run_eval(
        systems,
        qs,
        client,
        seed=42,
        split="dev",
        budget_usd=100,
        out_dir=tmp_path / "reports",
        batch=True,
        latency_sample=2,
        batch_backend=fake,
    )
    b = report["meta"]["batch"]
    assert b["enabled"] and b["latency_sample"] == 2 and b["batched_systems"] == ["b0_topk"]
    assert len(fake.batches) == 2  # one for the system answers, one for the judge
    assert b["systems"]["submitted"] == 3  # b0 on the 3 non-sample questions
    assert b["judge"]["submitted"] == 3 * 2  # both systems' answers, non-sample questions
    assert b["fallback_interactive"] == 0
    # Interactive b0 calls happened only for the 2 sample questions.
    assert sum(t.prompt_id == "answer.v1" for t in llm.turns) == 2
    assert llm.judged == 2 * 2  # sample questions judged interactively (2 systems)
    m = report["metrics"]
    assert m["b0_topk"]["n"] == 5 and m["b0_topk"]["latency_n"] == 2
    assert m["b0_topk"]["latency_p50"] >= 0.02  # only interactive rows (batch rows are 0.0)
    assert m["b3_agentic"]["latency_n"] == 5  # not batchable: every row is a measurement
    rows = [json.loads(line) for line in Path(report["paths"]["jsonl"]).read_text().splitlines()]
    batched_rows = [r for r in rows if r["batched"]]
    assert len(batched_rows) == 3 and not any(r["latency_measured"] for r in batched_rows)
    md = Path(report["paths"]["md"]).read_text()
    assert "Batch mode" in md and "first 2 questions" in md and "[2]" in md

    # Replaying the same run is free and submits nothing new.
    spent = client.ledger.spent_usd()
    again = run_eval(
        systems,
        qs,
        client,
        seed=42,
        split="dev",
        budget_usd=100,
        out_dir=tmp_path / "r2",
        batch=True,
        latency_sample=2,
        batch_backend=fake,
    )
    assert len(fake.batches) == 2 and client.ledger.spent_usd() == pytest.approx(spent)
    assert again["metrics"]["b0_topk"]["accuracy"] == m["b0_topk"]["accuracy"]


def test_run_eval_refuses_batch_over_budget(notes: list[MeetingNote], tmp_path: Path) -> None:
    llm = FakeLLM()
    client = LLMClient(llm, model=MODEL, cache_dir=tmp_path / "cache")
    qs = load_questions(FIXTURES, "dev", limit=4)
    fake = FakeBatches()
    report = run_eval(
        [TopKSystem(client, MeetingRetriever(notes, HashEmbedder()), k=20, backend=llm)],
        qs,
        client,
        seed=42,
        split="dev",
        budget_usd=0.05,
        out_dir=tmp_path / "reports",
        batch=True,
        latency_sample=0,
        batch_backend=fake,
    )
    assert "nothing was submitted" in report["meta"]["stopped_early"]
    assert fake.batches == {} and llm.turns == [] and report["meta"]["spent_usd"] == 0


def test_estimate_applies_batch_discount(notes: list[MeetingNote], tmp_path: Path) -> None:
    client = LLMClient(FakeLLM(), model=MODEL, cache_dir=tmp_path)
    qs = load_questions(FIXTURES, "dev", limit=10)
    systems = [
        TopKSystem(client, MeetingRetriever(notes, HashEmbedder()), k=20),
        AgenticSystem(client, notes),
    ]
    full = {e.system: e for e in estimate(systems, qs, MODEL)}
    half = {e.system: e for e in estimate(systems, qs, MODEL, batch=True, latency_sample=0)}
    assert half["b0_topk"].batch and not half["b3_agentic"].batch
    assert half["b0_topk"].usd == pytest.approx(full["b0_topk"].usd / 2)
    assert half["b3_agentic"].usd < full["b3_agentic"].usd  # only its judge is discounted
    some = {e.system: e for e in estimate(systems, qs, MODEL, batch=True, latency_sample=4)}
    assert half["b0_topk"].usd < some["b0_topk"].usd < full["b0_topk"].usd
