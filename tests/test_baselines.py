"""Phase 3 baselines and eval harness, with a fake LLM (no network, no model downloads)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path

import numpy as np
import pytest

from concentrate.agents.baselines import (
    AgenticSystem,
    AgentTools,
    FullContextSystem,
    MeetingRetriever,
    OracleSystem,
    SummariesOnlySystem,
    TopKSystem,
)
from concentrate.core.models import MeetingNote
from concentrate.evals.runner import estimate, load_corpus, load_questions, run_eval
from concentrate.evals.scoring import bootstrap_ci, citation_scores, exact_match, judge_request
from concentrate.llm.client import (
    Completion,
    LLMClient,
    LLMRequest,
    TurnCompletion,
    TurnRequest,
    _turn_key,
)
from concentrate.synth.questions import Question

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "northwind"
UID = re.compile(r"not_[A-Za-z0-9]{14}:\d{4}")


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder (stands in for sentence-transformers)."""

    name = "hash-test"

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                out[i, int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.where(norms == 0, 1, norms)


class FakeLLM:
    """Answers by citing the first transcript line it was shown; as an agent, searches first."""

    def __init__(self) -> None:
        self.turns: list[TurnRequest] = []
        self.judged = 0

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        self.turns.append(request)
        tool_results = [m for m in request.messages if isinstance(m["content"], list)]
        if request.tools and not tool_results:
            content = [
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "search_transcripts",
                    "input": {"keywords": ["Falcon"], "meeting_ids": []},
                }
            ]
            return TurnCompletion(content, "", "tool_use", model, 500, 50)
        seen = UID.findall(request.system + json.dumps(list(request.messages)))
        answer = {
            "answer": "Tomás Herrera owns Falcon.",
            "abstain": not seen,
            "citations": seen[:1],
        }
        text = json.dumps(answer)
        return TurnCompletion(
            [{"type": "text", "text": text}],
            text,
            "end_turn",
            model,
            1000,
            100,
            cache_read_tokens=800 if request.cache_system else 0,
        )

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.judged += 1
        verdict = "correct" if "Tomás" in request.user else "incorrect"
        return Completion(
            json.dumps({"verdict": verdict, "reason": "fake"}), model, 300, 20, "end_turn"
        )


@pytest.fixture(scope="module")
def notes() -> list[MeetingNote]:
    return load_corpus(FIXTURES)


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def client(tmp_path: Path, llm: FakeLLM) -> LLMClient:
    return LLMClient(llm, model="claude-opus-5-5", cache_dir=tmp_path / "cache")


def _q(text: str = "Who owns Falcon now?") -> Question:
    return Question(
        id="qx",
        category="latest_state",
        question=text,
        answer="Tomás Herrera",
        answer_norm=["tomas"],
        evidence_keys=[],
        gold_utterance_ids=[],
        as_of="2026-10-24",
        sources=[],
    )


def test_retriever_finds_relevant_meetings_and_dedupes(notes: list[MeetingNote]) -> None:
    r = MeetingRetriever(notes, HashEmbedder())
    top = r.top_meetings("Falcon cutover PAY-212 payments migration", k=5)
    assert len(top) == 5 and len({m.note_id for m in top}) == 5
    assert any("Falcon" in (m.title or "") for m in top)
    assert len(r.top_meetings("anything", k=100)) == len(notes)


def test_topk_and_summaries_contexts(
    notes: list[MeetingNote], client: LLMClient, llm: FakeLLM
) -> None:
    r = MeetingRetriever(notes, HashEmbedder())
    b0 = TopKSystem(client, r, k=3, backend=llm).run(_q())
    assert len(b0.retrieved) == 3 and b0.answer.citations and UID.match(b0.answer.citations[0])
    assert "## Transcript" in json.dumps(llm.turns[-1].messages)
    b1 = SummariesOnlySystem(client, r, k=3, backend=llm).run(_q())
    assert "## Transcript" not in json.dumps(llm.turns[-1].messages)
    assert b1.answer.abstain  # summaries carry no line IDs, so the fake can't cite


def test_full_context_uses_cached_system_prompt(
    notes: list[MeetingNote], client: LLMClient, llm: FakeLLM
) -> None:
    b2 = FullContextSystem(client, notes, backend=llm)
    b2.run(_q("Who owns Falcon now?"))
    b2.run(_q("What is the launch date?"))
    assert all(t.cache_system for t in llm.turns)
    assert llm.turns[0].system == llm.turns[1].system  # identical prefix → cache hits
    assert all(n.note_id in llm.turns[0].system for n in notes)


def test_agent_runs_tools_and_answers(
    notes: list[MeetingNote], client: LLMClient, llm: FakeLLM
) -> None:
    res = AgenticSystem(client, notes, backend=llm).run(_q())
    assert res.tool_calls == 1 and res.llm_calls == 2
    second = llm.turns[-1].messages
    assert second[1]["role"] == "assistant" and second[2]["content"][0]["tool_use_id"] == "tu_1"
    assert "passages" in second[2]["content"][0]["content"]
    # Search passages carry no line IDs (as in Granola's MCP), so an agent that only searches
    # has nothing line-level to cite: it must read the transcript to ground its answer.
    assert res.answer.citations == []


def test_agent_tool_budget_exhaustion_is_a_failed_answer(
    notes: list[MeetingNote], client: LLMClient
) -> None:
    class Looping(FakeLLM):
        def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
            content = [
                {
                    "type": "tool_use",
                    "id": f"tu_{len(request.messages)}",
                    "name": "search_meetings",
                    "input": {
                        "keywords": ["x"],
                        "participant": None,
                        "from_date": None,
                        "to_date": None,
                    },
                }
            ]
            return TurnCompletion(content, "", "tool_use", model, 100, 10)

    res = AgenticSystem(client, notes, backend=Looping(), max_turns=3).run(_q())
    assert res.answer.abstain and "budget" in res.answer.answer and res.tool_calls == 3


def test_agent_tools_mirror_mcp_semantics(notes: list[MeetingNote]) -> None:
    tools = AgentTools(notes)
    meetings = json.loads(tools.search_meetings(["pricing"], None, None, None))["meetings"]
    assert meetings and all({"id", "title", "date", "participants"} <= set(m) for m in meetings)
    late = json.loads(tools.search_meetings([], None, "2026-10-19", None))["meetings"]
    assert late and all(m["date"] >= "2026-10-19" for m in late)
    passages = json.loads(tools.search_transcripts(["Falcon"], []))["passages"]
    assert passages and all(len(p["passage"]) <= 240 for p in passages)
    assert not any(UID.search(p["passage"]) for p in passages)  # no speakers / line IDs
    read = json.loads(
        tools.get_meeting_transcript(passages[0]["meeting_id"], [passages[0]["chunk_id"]])
    )
    assert UID.search(read["chunks"][0]["lines"]) and (
        "Me:" in read["chunks"][0]["lines"] or "Them" in read["chunks"][0]["lines"]
    )
    assert "not found" in tools.get_meeting_transcript("not_missing0000000", [0]).lower()


def test_exact_match_handles_dates_numbers_people() -> None:
    q = _q()
    assert (
        exact_match(
            q.model_copy(update={"answer_norm": ["2026-10-28"]}), "It moved to Oct 28th.", False
        )
        == 1.0
    )
    assert exact_match(q.model_copy(update={"answer_norm": ["312000"]}), "ARR was $312k", False)
    assert exact_match(q.model_copy(update={"answer_norm": ["25"]}), "twenty five dollars", False)
    assert exact_match(q, "Tomás Herrera", False) == 1.0
    assert exact_match(q, "Tomás Herrera", True) == 0.0
    un = q.model_copy(update={"answer_norm": ["UNANSWERABLE"]})
    assert exact_match(un, "not discussed", True) == 1.0


def test_citation_scores_and_bootstrap() -> None:
    q = _q().model_copy(
        update={"gold_utterance_ids": ["not_AAAAAAAAAAAAAA:0001", "not_AAAAAAAAAAAAAA:0002"]}
    )
    p, r = citation_scores(q, ["[not_AAAAAAAAAAAAAA:0001]", "not_BBBBBBBBBBBBBB:0009"])
    assert (p, r) == (0.5, 0.5)
    assert bootstrap_ci([1, 0, 1, 1], seed=1) == bootstrap_ci([1, 0, 1, 1], seed=1)


def test_turn_request_keeps_pre_batch_cache_keys(
    notes: list[MeetingNote], client: LLMClient, llm: FakeLLM
) -> None:
    """``turn_request(q)`` (used by batch prefetch) and ``run(q)`` send the same request, and
    its cache key equals the one computed before the refactor, so existing caches still hit.
    Keys pinned from commit 3aeebb5 (pre-batch) with this fixture and HashEmbedder; re-pinned when
    the base fixture was regenerated by generator northwind-1.1 (content change only)."""
    pinned = {
        "b0_topk": "8e5290d612c3916dec9a5351d65db8152d8f57cabea2b37728c4799ce9e0e87d",
        "b1_summaries": "3058c93059030793244e3934e894e150a49582e188a3d238b5540c2788901392",
        "b2_full": "9fdd10ef078e0cd350ae37518c43d3d776904cb2f18ef5b33c840f87f1b30d15",
        "b2o_oracle": "d6e5c1a5592f52c532262d57e83a0409c8c414ae95ee7761caa73879506fffcb",
    }
    r = MeetingRetriever(notes, HashEmbedder())
    q = load_questions(FIXTURES, "dev", limit=1)[0]
    systems = [
        TopKSystem(client, r, k=3, backend=llm),
        SummariesOnlySystem(client, r, k=3, backend=llm),
        FullContextSystem(client, notes, backend=llm),
        OracleSystem(client, notes, r, backend=llm),
    ]
    for s in systems:
        req = s.turn_request(q)
        s.run(q)
        assert llm.turns[-1] == req
        assert _turn_key("claude-opus-5-5", req) == pinned[s.name]
    judge_key = LLMClient(model="claude-opus-5-5")._key(
        judge_request(_q(), "Tomás Herrera owns Falcon.", False)
    )
    assert judge_key == "21f42ec7e9ede1ae0d6591e70164e02ba76da9c7280b551bb39c36700bb90d7d"


def test_estimate_prices_cached_full_context_below_topk(
    notes: list[MeetingNote], client: LLMClient
) -> None:
    qs = load_questions(FIXTURES, "dev", limit=10)
    systems = [
        TopKSystem(client, MeetingRetriever(notes, HashEmbedder()), k=20),
        FullContextSystem(client, notes),
    ]
    est = {e.system: e for e in estimate(systems, qs, "claude-opus-5-5")}
    assert est["b2_full"].input_tokens > est["b0_topk"].input_tokens  # more context...
    assert est["b2_full"].usd < est["b0_topk"].usd  # ...but cheaper thanks to caching


def test_run_eval_writes_report_and_respects_budget(
    notes: list[MeetingNote], client: LLMClient, llm: FakeLLM, tmp_path: Path
) -> None:
    qs = load_questions(FIXTURES, "dev", limit=4)
    systems = [
        FullContextSystem(client, notes, backend=llm),
        AgenticSystem(client, notes, backend=llm),
    ]
    report = run_eval(
        systems, qs, client, seed=42, split="dev", budget_usd=100, out_dir=tmp_path / "reports"
    )
    assert set(report["metrics"]) == {"b2_full", "b3_agentic"}
    assert report["meta"]["prompts"]["judge"] == ["judge.v1"]
    md = Path(report["paths"]["md"]).read_text()
    assert "Accuracy [95% CI]" in md and "b3_agentic" in md
    assert len(Path(report["paths"]["jsonl"]).read_text().splitlines()) == 8
    capped = run_eval(
        systems,
        qs,
        LLMClient(llm, model="claude-opus-5-5", cache_dir=tmp_path / "c2"),
        seed=42,
        split="dev",
        budget_usd=0.0,
        out_dir=tmp_path / "r2",
    )
    assert capped["meta"]["stopped_early"] and capped["meta"]["questions"] == 4
