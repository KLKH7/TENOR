"""B5 Graphiti baseline with fakes: no network, no model downloads."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from graphiti_core.prompts.dedupe_edges import EdgeDuplicate
from graphiti_core.prompts.models import Message

from concentrate.agents.baselines import ANSWER_PROMPT, ANSWER_SCHEMA
from concentrate.agents.graphiti_system import (
    AccountedAnthropicClient,
    Entity,
    Episode,
    EpisodeRef,
    Fact,
    GraphitiSystem,
    IngestLedger,
    KuzuGraphiti,
    build_context,
    episodes_for,
)
from concentrate.core.models import MeetingNote
from concentrate.evals.runner import load_corpus
from concentrate.llm.client import LLMClient, cost_usd
from tests.test_baselines import FIXTURES, FakeLLM, HashEmbedder, _q

W2, W5 = "not_TifZvvCb7IQQHS", "not_i0kpeW6NCQ4Jpe"  # Teams pricing reviews ($20, then $25)


def _minimal(schema: dict[str, Any], defs: dict[str, Any]) -> Any:
    """The smallest JSON value satisfying a (strict-transformed) schema."""
    if "$ref" in schema:
        return _minimal(defs[schema["$ref"].split("/")[-1]], defs)
    if "anyOf" in schema:
        return None if {"type": "null"} in schema["anyOf"] else _minimal(schema["anyOf"][0], defs)
    t = schema.get("type")
    if t == "object":
        props = schema.get("properties", {})
        return {k: _minimal(props[k], defs) for k in schema.get("required", [])}
    return {"array": [], "string": "", "integer": 0, "number": 0, "boolean": False}.get(t)


class FakeSDK:
    """Stands in for ``anthropic.AsyncAnthropic``: answers any schema minimally, with usage."""

    def __init__(self, replies: list[str] | None = None, usage: dict[str, int] | None = None):
        self.requests: list[dict[str, Any]] = []
        self.replies = list(replies or [])
        self.usage = usage or {"input_tokens": 1000, "output_tokens": 100}
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if self.replies:
            text = self.replies.pop(0)
        else:
            schema = kwargs["output_config"].get("format", {}).get("schema", {"type": "object"})
            text = json.dumps(_minimal(schema, schema.get("$defs", {})))
        usage = SimpleNamespace(
            cache_read_input_tokens=0, cache_creation_input_tokens=0, **self.usage
        )
        return SimpleNamespace(
            model=kwargs["model"],
            usage=usage,
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=text)],
        )


@pytest.fixture(scope="module")
def notes() -> list[MeetingNote]:
    return load_corpus(FIXTURES)


def _pricing(notes: list[MeetingNote]) -> list[MeetingNote]:
    return [m for m in notes if m.note_id in (W2, W5)]


def test_episodes_carry_header_speakers_and_meeting_time(notes: list[MeetingNote]) -> None:
    m = next(n for n in notes if n.note_id == W2)
    eps = episodes_for(m, size=40)
    assert len(eps) == 5  # 170 utterances: 4 x 40 + 10
    assert all(e.reference_time == m.scheduled_start for e in eps)
    assert eps[0].body.startswith("Meeting: Pricing review: Northwind Teams\nDate: Tue 2026-09-08")
    assert "Marcus Chen" in eps[0].body and "Them (Olivia Grant): Morning all." in eps[0].body
    assert f"{W2}:0000" not in eps[0].body  # no line IDs in what Graphiti sees
    assert sum(e.body.count("\n") for e in eps) >= len(m.utterances)


def test_requests_are_valid_for_current_models() -> None:
    llm = AccountedAnthropicClient("claude-sonnet-5-5", FakeSDK(), IngestLedger())
    kw = llm.request_kwargs(
        [Message(role="system", content="s"), Message(role="user", content="u")],
        EdgeDuplicate,
        16000,
        "claude-sonnet-5-5",
    )
    assert "tool_choice" not in kw and "tools" not in kw  # forced tool use 400s on 5.5 models
    assert "temperature" not in kw and "top_p" not in kw  # non-default sampling 400s on Sonnet 5.5
    assert kw["thinking"] == {"type": "between_tools"}
    fmt = kw["output_config"]["format"]
    assert fmt["type"] == "json_schema" and fmt["schema"]["additionalProperties"] is False
    assert kw["fallbacks"] == "default" and kw["system"] == "s"


def test_cost_accounting_from_fake_usage() -> None:
    usage = {"input_tokens": 1200, "output_tokens": 300}
    sdk = FakeSDK(replies=["not json", '{"duplicate_facts": [], "contradicted_facts": [1]}'])
    sdk.usage = usage
    ledger = IngestLedger()
    llm = AccountedAnthropicClient("claude-sonnet-5-5", sdk, ledger)
    import asyncio

    out = asyncio.run(
        llm.generate_response(
            [Message(role="system", content="s"), Message(role="user", content="u")],
            response_model=EdgeDuplicate,
        )
    )
    assert out["contradicted_facts"] == [1]
    assert len(ledger.calls) == 2  # the failed (unparseable) attempt is still paid for
    t = ledger.totals()
    assert t["input_tokens"] == 2400 and t["output_tokens"] == 600
    assert t["cost_usd"] == pytest.approx(2 * cost_usd("claude-sonnet-5-5", 1200, 300))
    assert ledger.by_prompt()["EdgeDuplicate"]["calls"] == 2


class FakeGraph:
    """A GraphBackend that makes one accounted LLM call per episode and serves fixed facts."""

    def __init__(self, llm: AccountedAnthropicClient, fail_on: int | None = None) -> None:
        self.llm, self.fail_on = llm, fail_on
        self.added: list[Episode] = []

    async def add_episode(self, ep: Episode) -> str:
        if self.fail_on is not None and len(self.added) == self.fail_on:
            raise RuntimeError("simulated crash")
        await self.llm.generate_response(
            [Message(role="system", content="s"), Message(role="user", content=ep.body)],
            response_model=EdgeDuplicate,
        )
        self.added.append(ep)
        return f"uuid-{ep.key}"

    async def search(
        self, query: str, k_edges: int, k_nodes: int
    ) -> tuple[list[Fact], list[Entity]]:
        d = datetime(2026, 9, 8, tzinfo=UTC)
        later = datetime(2026, 9, 29, tzinfo=UTC)
        return [
            Fact("f2", "Teams costs $25 per seat", "PRICE", later, None, None, (f"uuid-{W5}#0",)),
            Fact("f1", "Teams costs $20 per seat", "PRICE", d, later, later, (f"uuid-{W2}#0",)),
        ], [Entity("Northwind Teams", "A product priced per seat.")]

    async def counts(self) -> dict[str, int]:
        return {"episodes": len(self.added), "entities": 2, "facts": 2}


def _system(
    tmp_path: Path, client: LLMClient, graphs: list[FakeGraph], fail_on: int | None = None
) -> GraphitiSystem:
    def factory(path: Path, llm: AccountedAnthropicClient, emb: Any, fresh: bool) -> FakeGraph:
        g = FakeGraph(llm, fail_on)
        graphs.append(g)
        return g

    return GraphitiSystem(
        client,
        ingest_model="claude-sonnet-5-5",
        embedder=HashEmbedder(),
        anthropic_client=FakeSDK(),
        graph_factory=factory,
        cache_root=tmp_path / "graphiti",
    )


@pytest.fixture
def client(tmp_path: Path) -> LLMClient:
    return LLMClient(FakeLLM(), model="claude-opus-5-5", cache_dir=tmp_path / "llm")


def test_ingest_persists_resumes_and_reuses(
    notes: list[MeetingNote], tmp_path: Path, client: LLMClient
) -> None:
    corpus = _pricing(notes)
    graphs: list[FakeGraph] = []
    crashing = _system(tmp_path, client, graphs, fail_on=2)
    with pytest.raises(RuntimeError):
        crashing.ingest(corpus)
    assert len(graphs[-1].added) == 2

    resumed = _system(tmp_path, client, graphs).ingest(corpus)
    assert len(graphs[-1].added) == 7  # only the seven remaining episodes (9 in total)
    assert not resumed.cached and resumed.detail["resumed"]
    assert resumed.llm_calls == 9 and resumed.input_tokens == 9000  # earlier spend still counted
    assert resumed.cost_usd == pytest.approx(9 * cost_usd("claude-sonnet-5-5", 1000, 100))
    assert resumed.detail["episodes"] == 7  # this fake only counts its own session

    again = _system(tmp_path, client, graphs).ingest(corpus)
    assert again.cached and not graphs[-1].added
    assert again.llm_calls == 9 and again.cost_usd == pytest.approx(resumed.cost_usd)

    other = _system(tmp_path, client, graphs).ingest(corpus[:1])  # different corpus, new graph
    assert not other.cached and len(graphs[-1].added) == 5


def test_context_lists_facts_with_validity_and_sources() -> None:
    manifest = {
        f"uuid-{W2}#0": EpisodeRef(W2, "Pricing review: Northwind Teams", "2026-09-08"),
        f"uuid-{W5}#0": EpisodeRef(W5, "Pricing review: Teams v2", "2026-09-29"),
    }
    d, later = datetime(2026, 9, 8, tzinfo=UTC), datetime(2026, 9, 29, tzinfo=UTC)
    facts = [
        Fact("b", "Teams costs $25", "PRICE", later, None, None, (f"uuid-{W5}#0",)),
        Fact("a", "Teams costs $20", "PRICE", d, later, later, (f"uuid-{W2}#0", "unknown")),
        Fact("c", "Undated fact", "X", None, None, None, ()),
    ]
    body, cited = build_context(
        facts, [Entity("Teams", "Per-seat plan."), Entity("E", " ")], manifest
    )
    assert cited == [W2, W5]  # chronological: the $20 fact comes first
    assert body.index("$20") < body.index("$25") < body.index("Undated")
    assert "valid: 2026-09-08 -> 2026-09-29; superseded by a later meeting" in body
    assert "valid: 2026-09-29 -> present\n" in body
    assert f"source: [{W5}] Pricing review: Teams v2 (2026-09-29)" in body
    assert "valid: unknown -> present\n  source: unknown" in body
    assert "- Teams: Per-seat plan." in body and "- E:" not in body


def test_run_uses_shared_answer_path(
    notes: list[MeetingNote], tmp_path: Path, client: LLMClient
) -> None:
    llm = FakeLLM()
    graphs: list[FakeGraph] = []
    s = _system(tmp_path, client, graphs)
    s.backend = llm
    s.ingest(_pricing(notes))
    r = s.run(_q("What does Teams cost per seat now?"))
    turn = llm.turns[-1]
    assert turn.prompt_id == ANSWER_PROMPT and turn.schema == ANSWER_SCHEMA
    assert turn.system == s.instructions and not turn.tools
    user = turn.messages[0]["content"]
    assert "<graph_facts>" in user and "Today's date: 2026-10-24" in user
    assert r.retrieved == [W2, W5]
    assert (r.input_tokens, r.output_tokens, r.llm_calls) == (1000, 100, 1)
    assert r.cost_usd == pytest.approx(cost_usd("claude-opus-5-5", 1000, 100))
    est_in, est_out = s.estimate(_q())
    assert est_in > 1000 and est_out > 0
    assert s.estimate_ingest(_pricing(notes))[0] > 0


def test_run_before_ingest_raises(client: LLMClient, tmp_path: Path) -> None:
    with pytest.raises(RuntimeError):
        _system(tmp_path, client, []).run(_q())


def test_real_graphiti_on_embedded_kuzu_offline(notes: list[MeetingNote], tmp_path: Path) -> None:
    """End to end through graphiti-core and Kuzu, with a fake LLM that extracts nothing."""
    m = next(n for n in notes if n.note_id == W2)
    sdk = FakeSDK()
    s = GraphitiSystem(
        LLMClient(FakeLLM(), model="claude-opus-5-5", cache_dir=tmp_path / "llm"),
        ingest_model="claude-sonnet-5-5",
        embedder=HashEmbedder(),
        anthropic_client=sdk,
        cache_root=tmp_path / "graphiti",
    )
    stats = s.ingest([m])
    assert stats.detail["episodes"] == 5 and stats.detail["facts"] == 0
    assert stats.llm_calls == len(sdk.requests) > 0
    assert all("tool_choice" not in r for r in sdk.requests)
    assert isinstance(s.graph, KuzuGraphiti)
    facts, _ = s._run(s.graph.search("pricing", 5, 5))  # full-text indexes exist
    assert facts == []
    s.close()


def test_resume_survives_a_kill_mid_progress_write(
    notes: list[MeetingNote], tmp_path: Path, client: LLMClient
) -> None:
    corpus = _pricing(notes)
    graphs: list[FakeGraph] = []
    with pytest.raises(RuntimeError):
        _system(tmp_path, client, graphs, fail_on=2).ingest(corpus)
    progress = next((tmp_path / "graphiti").glob("*/progress.jsonl"))
    with progress.open("a") as fh:  # the watchdog's os._exit landed mid-write
        fh.write('{"key": "torn", "uuid": "x", "ref": {"note_id"')
    resumed = _system(tmp_path, client, graphs).ingest(corpus)
    full: list[FakeGraph] = []
    _system(tmp_path / "full", client, full).ingest(corpus)
    # Exactly the episodes after the two that landed before the kill: none re-added or skipped.
    assert [e.key for e in graphs[-1].added] == [e.key for e in full[-1].added][2:]
    assert resumed.llm_calls > 0
    assert all(json.loads(line) for line in progress.read_text().splitlines())
