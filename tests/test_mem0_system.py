"""B4 Mem0 baseline with a fake memory backend and fake LLM (no network, no model downloads)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from concentrate.agents.baselines import ANSWER_PROMPT, ANSWER_SCHEMA
from concentrate.agents.mem0_system import (
    COMPLETE_MARKER,
    EXTRACTION_SCHEMA,
    PROGRESS_FILE,
    USER_ID,
    Mem0System,
    MemoryBackend,
    MeteredAnthropic,
    UsageMeter,
    corpus_hash,
    ingest_chunks,
)
from concentrate.core.models import MeetingNote
from concentrate.evals.runner import load_corpus
from concentrate.llm.client import LLMClient, TurnCompletion, TurnRequest, cost_usd

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "northwind"
MODEL = "claude-sonnet-5-5"


@pytest.fixture(scope="module")
def notes() -> list[MeetingNote]:
    by_size = sorted(load_corpus(FIXTURES), key=lambda m: len(m.utterances))
    return [by_size[0], by_size[-1]]  # one tiny, one long meeting


class FakeMemory:
    """Stands in for mem0.Memory: one fake LLM call per add, one memory per add."""

    def __init__(self, meter: UsageMeter) -> None:
        self.meter = meter
        self.added: list[dict[str, Any]] = []

    def add(
        self,
        messages: list[dict[str, str]],
        *,
        user_id: str,
        metadata: dict[str, Any],
        prompt: str | None = None,
    ) -> dict[str, Any]:
        self.meter.record(MODEL, 1000, 200, cache_read_tokens=12000, cache_write_tokens=0)
        self.added.append({"messages": messages, "user_id": user_id, "metadata": metadata})
        text = f"Memory from {metadata['chunk_id']}"
        return {"results": [{"id": str(len(self.added)), "memory": text, "event": "ADD"}]}

    def search(self, query: str, *, top_k: int, filters: dict[str, Any]) -> dict[str, Any]:
        assert filters == {"user_id": USER_ID}
        hits = [
            {"memory": f"Memory from {a['metadata']['chunk_id']}", "metadata": a["metadata"]}
            for a in reversed(self.added)
        ]
        return {"results": hits[:top_k]}


class Factory:
    def __init__(self) -> None:
        self.built: list[FakeMemory] = []

    def __call__(self, store: Path, meter: UsageMeter) -> MemoryBackend:
        mem = FakeMemory(meter)
        self.built.append(mem)
        return mem


class FakeLLM:
    def __init__(self) -> None:
        self.turns: list[TurnRequest] = []

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        self.turns.append(request)
        cited = request.messages[0]["content"].split("[", 1)[1].split("]")[0]
        text = json.dumps({"answer": "From memory.", "abstain": False, "citations": [cited]})
        return TurnCompletion([{"type": "text", "text": text}], text, "end_turn", model, 1500, 80)


def _system(tmp_path: Path, factory: Factory, llm: FakeLLM | None = None) -> Mem0System:
    client = LLMClient(llm, model="claude-opus-5-5", cache_dir=tmp_path / "llm")
    return Mem0System(
        client,
        backend=llm,
        memory_factory=factory,
        ingest_model=MODEL,
        k=5,
        cache_root=tmp_path / "mem0",
    )


def _q() -> SimpleNamespace:
    return SimpleNamespace(id="q", question="What was decided?", as_of=date(2026, 10, 24))


def test_ingest_chunks_carry_title_date_speakers(notes: list[MeetingNote]) -> None:
    long = notes[1]
    chunks = ingest_chunks(long, chunk_utterances=40)
    assert chunks[0].key == f"{long.note_id}:summary"
    assert len(chunks) == 1 + -(-len(long.utterances) // 40)
    day = (long.scheduled_start or long.created_at).date().isoformat()
    for c in chunks:
        assert long.title in c.content and day in c.content and long.note_id in c.content
        assert day in c.prompt and c.meeting_date == day
    assert "Me" in chunks[1].content or "Them" in chunks[1].content
    assert ":" in chunks[1].content.splitlines()[-1]


def test_corpus_hash_is_order_independent_and_tracks_updates(notes: list[MeetingNote]) -> None:
    assert corpus_hash(notes) == corpus_hash(list(reversed(notes)))
    bumped = notes[0].model_copy(update={"updated_at": notes[0].updated_at.replace(year=2030)})
    assert corpus_hash([bumped, notes[1]]) != corpus_hash(notes)


def test_ingest_accounts_cost_and_reuses_store(tmp_path: Path, notes: list[MeetingNote]) -> None:
    factory = Factory()
    sys1 = _system(tmp_path, factory)
    stats = sys1.ingest(notes)
    n_chunks = sum(len(ingest_chunks(m)) for m in notes)
    assert not stats.cached and stats.llm_calls == n_chunks
    assert stats.input_tokens == n_chunks * 13000 and stats.output_tokens == n_chunks * 200
    assert stats.cost_usd == pytest.approx(n_chunks * cost_usd(MODEL, 1000, 200, 12000))
    assert stats.detail["memories_created"] == n_chunks
    assert stats.detail["cache_read_tokens"] == n_chunks * 12000
    added = factory.built[0].added
    assert {a["user_id"] for a in added} == {USER_ID}
    assert {a["metadata"]["note_id"] for a in added} == {m.note_id for m in notes}
    assert all(a["metadata"]["meeting_date"] for a in added)
    store = sys1.store_dir(notes)
    assert (store / COMPLETE_MARKER).exists() and store.parent.name == corpus_hash(notes)

    factory2 = Factory()
    sys2 = _system(tmp_path, factory2)
    again = sys2.ingest(notes)
    assert again.cached and again.cost_usd == 0 and again.llm_calls == 0
    assert again.detail["built"]["llm_calls"] == n_chunks
    assert factory2.built and not factory2.built[0].added  # opened, nothing re-added


def test_ingest_resumes_after_interruption(tmp_path: Path, notes: list[MeetingNote]) -> None:
    factory = Factory()
    system = _system(tmp_path, factory)
    store = system.store_dir(notes)
    store.mkdir(parents=True)
    first = ingest_chunks(notes[0])[0].key
    prior = {
        "key": first,
        "memories_added": 2,
        "seconds": 1.0,
        "llm_calls": 1,
        "input_tokens": 500,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": 0.01,
        "truncated": 0,
        "refused": 0,
    }
    (store / PROGRESS_FILE).write_text(json.dumps(prior) + "\n")
    stats = system.ingest(notes)
    keys = [a["metadata"]["chunk_id"] for a in factory.built[0].added]
    assert first not in keys
    assert stats.llm_calls == len(keys) + 1 and stats.detail["memories_created"] == len(keys) + 2


def test_metered_client_makes_requests_valid_and_prices_usage() -> None:
    class Usage:
        input_tokens, output_tokens = 900, 300
        cache_read_input_tokens, cache_creation_input_tokens = 12000, 0

    class SDK:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []
            self.messages = SimpleNamespace(create=self.create)
            self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

        def create(self, **kw: Any) -> Any:
            self.sent.append(kw)
            return SimpleNamespace(usage=Usage(), model=kw["model"], stop_reason="end_turn")

    sdk, meter = SDK(), UsageMeter()
    client = MeteredAnthropic(sdk, meter, effort="medium", max_tokens=16000)
    client.messages.create(
        model=MODEL,
        system="Extract memories.",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=2000,
        temperature=0.1,
        top_p=0.9,
        top_k=1,
    )
    sent = sdk.sent[0]
    assert not {"temperature", "top_p", "top_k"} & set(sent)
    assert sent["max_tokens"] == 16000
    assert sent["output_config"]["effort"] == "medium"
    assert sent["output_config"]["format"]["schema"] == EXTRACTION_SCHEMA
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert sent["fallbacks"] == "default" and sent["betas"]
    rec = meter.records[0]
    assert rec.cost_usd == pytest.approx(cost_usd(MODEL, 900, 300, 12000, 0))
    assert meter.since(0)["input_tokens"] == 12900

    tooled = client.prepare({"model": "claude-haiku-5-5", "system": "", "tools": [{"name": "t"}]})
    assert "format" not in tooled["output_config"] and "system" not in tooled
    assert "fallbacks" not in tooled


def test_context_groups_memories_by_meeting_chronologically(
    tmp_path: Path, notes: list[MeetingNote]
) -> None:
    system = _system(tmp_path, Factory())
    system.ingest(notes)
    late, early = sorted(notes, key=lambda m: m.scheduled_start or m.created_at, reverse=True)
    mems = [
        {"memory": "late fact", "metadata": {"note_id": late.note_id}},
        {"memory": "early fact", "metadata": {"note_id": early.note_id}},
        {"memory": "orphan", "metadata": {"note_id": "not_gone", "meeting_date": "2020-01-01"}},
    ]
    ctx, retrieved = system.context(mems)
    assert retrieved == ["not_gone", early.note_id, late.note_id]
    assert ctx.index("early fact") < ctx.index("late fact")
    assert f"[{late.note_id}]" in ctx and late.title in ctx
    empty, none = system.context([])
    assert "(no memories found)" in empty and none == []


def test_run_uses_shared_answer_path(tmp_path: Path, notes: list[MeetingNote]) -> None:
    llm = FakeLLM()
    system = _system(tmp_path, Factory(), llm)
    with pytest.raises(RuntimeError):
        system.run(_q())
    system.ingest(notes)
    res = system.run(_q())
    turn = llm.turns[-1]
    assert turn.prompt_id == ANSWER_PROMPT and turn.schema == ANSWER_SCHEMA
    assert turn.stage == "eval.b4_mem0"
    user = turn.messages[0]["content"]
    assert "<memories>" in user and "Question: What was decided?" in user
    assert res.answer.citations[0].startswith("not_")
    assert set(res.retrieved) <= {m.note_id for m in notes} and res.retrieved
    assert res.input_tokens == 1500 and res.output_tokens == 80 and res.llm_calls == 1
    assert res.cost_usd == pytest.approx(cost_usd("claude-opus-5-5", 1500, 80))
    tin, tout = system.estimate(_q())
    assert tin > 0 and tout > 0


def test_estimate_ingest_is_offline_and_prices_cache(
    tmp_path: Path, notes: list[MeetingNote]
) -> None:
    system = _system(tmp_path, Factory())
    tin, tout = system.estimate_ingest(notes)
    assert tin > 0 and tout > 0
    full_price = cost_usd(MODEL, tin, tout)
    assert 0 < system.estimate_ingest_usd(notes) < full_price
    assert not (tmp_path / "mem0").exists()  # no store created, nothing called


def test_ingest_resumes_after_a_kill_with_a_torn_progress_line(
    tmp_path: Path, notes: list[MeetingNote]
) -> None:
    class Killed(BaseException):
        """Like SIGKILL/os._exit: not an Exception, so nothing downstream catches it."""

    class DyingMemory(FakeMemory):
        def add(self, messages: list[dict[str, str]], **kw: Any) -> dict[str, Any]:
            if len(self.added) == 2:
                raise Killed
            return super().add(messages, **kw)

    def dying(store: Path, meter: UsageMeter) -> MemoryBackend:
        return DyingMemory(meter)

    with pytest.raises(Killed):
        _system(tmp_path, dying).ingest(notes)  # type: ignore[arg-type]
    store = _system(tmp_path, Factory()).store_dir(notes)
    with (store / PROGRESS_FILE).open("a") as fh:
        fh.write('{"key": "torn')
    factory = Factory()
    stats = _system(tmp_path, factory).ingest(notes)
    chunks = [c.key for m in notes for c in ingest_chunks(m)]
    resumed = [a["metadata"]["chunk_id"] for a in factory.built[0].added]
    assert resumed == chunks[2:]  # nothing re-added, nothing skipped
    assert stats.llm_calls == len(chunks)
