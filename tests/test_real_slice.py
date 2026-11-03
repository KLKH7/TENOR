"""Real-data slice: read-only Granola access, question kit, scoring (fake LLM, no network)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from concentrate.evals.judge_check import load_report
from concentrate.evals.real_slice import (
    READ_ONLY_TOOLS,
    DraftItem,
    DraftOutput,
    GranolaAgentSystem,
    ReadOnlyCaller,
    RealQuestion,
    ToolNotAllowed,
    aggregate_only,
    coverage,
    draft_request,
    drafts_to_questions,
    key_term_match,
    load_questions,
    meeting_citation_scores,
    parse_granola_answer,
    real_directory,
    run_slice,
    save_questions,
)
from concentrate.evals.runner import load_corpus
from concentrate.llm.client import Completion, LLMClient, LLMRequest

FIXTURES = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "northwind"
NOTE = "00000000-0000-4000-8000-000000000001"
RAW = (
    "<transcript_note>This answer comes from meeting notes…</transcript_note>\n\n"
    f"Alex owns the launch. [[1]](https://notes.granola.ai/d/{NOTE}) "
    f"Again [[2]](https://notes.granola.ai/d/{NOTE})"
)


class FakeMcp:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        self.calls.append((tool, arguments))
        return RAW


class FakeJudge:
    def complete(self, request: LLMRequest, model: str) -> Completion:
        verdict = "correct" if "Alex" in request.user.split("System answer:")[-1] else "incorrect"
        return Completion(json.dumps({"verdict": verdict, "reason": "fake"}), model, 300, 20, "x")


def _q(qid: str = "real-001", **kw: Any) -> RealQuestion:
    base: dict[str, Any] = {
        "id": qid,
        "category": "commitment_status",
        "question": "Who owns the launch?",
        "gold_answer": "Alex",
        "key_terms": ["alex"],
        "evidence_note_ids": [NOTE],
        "as_of": date(2026, 11, 2),
        "status": "verified",
    }
    return RealQuestion(**(base | kw))


def test_read_only_caller_refuses_every_write_tool() -> None:
    inner = FakeMcp()
    caller = ReadOnlyCaller(inner)
    for tool in (
        "update_private_notes",
        "create_folder",
        "update_folder",
        "add_note_to_folder",
        "remove_note_from_folder",
        "generate_summary",
        "give_feedback",
        "sign_out",
        "list_templates",
    ):
        with pytest.raises(ToolNotAllowed):
            caller.call(tool, {})
    assert inner.calls == []
    caller.call("query_granola_meetings", {"query": "x"})
    assert inner.calls == [("query_granola_meetings", {"query": "x"})]
    assert "query_granola_meetings" in READ_ONLY_TOOLS and len(READ_ONLY_TOOLS) == 7


def test_parse_granola_answer_strips_disclaimer_and_dedupes_citations() -> None:
    text, cited = parse_granola_answer(RAW)
    assert "transcript_note" not in text and text.startswith("Alex owns")
    assert cited == [NOTE]


def test_granola_system_records_once_then_replays(tmp_path: Path) -> None:
    mcp = FakeMcp()
    ticks = iter([10.0, 13.5])
    s = GranolaAgentSystem(mcp, record_dir=tmp_path, clock=lambda: next(ticks))
    first = s.run(_q())
    assert first.latency_s == pytest.approx(3.5) and first.answer.citations == [NOTE]
    replay = GranolaAgentSystem(None, record_dir=tmp_path).run(_q())
    assert replay.answer == first.answer and replay.latency_s == first.latency_s
    assert len(mcp.calls) == 1
    with pytest.raises(RuntimeError):
        GranolaAgentSystem(None, record_dir=tmp_path).run(_q(question="A different question?"))


def test_key_terms_and_meeting_citations() -> None:
    q = _q(key_terms=["alex", "friday"])
    assert key_term_match(q, "Alex, by Friday", False) == 1.0
    assert key_term_match(q, "Alex", False) == 0.5
    assert key_term_match(q, "Alex", True) == 0.0
    u = _q(category="unanswerable", gold_answer="UNANSWERABLE", key_terms=[])
    assert u.unanswerable and key_term_match(u, "not discussed", True) == 1.0
    assert meeting_citation_scores(q, [f"[{NOTE}:0012]", "[not_other]"]) == (0.5, 1.0)
    assert meeting_citation_scores(q, [f"https://notes.granola.ai/d/{NOTE}"]) == (1.0, 1.0)
    assert meeting_citation_scores(q, []) == (None, 0.0)
    assert meeting_citation_scores(u, [NOTE]) == (None, None)


def test_question_store_roundtrip_coverage_and_draft_dedupe(tmp_path: Path) -> None:
    path = tmp_path / "q.jsonl"
    existing = [_q("real-001"), _q("real-002", status="draft", category="temporal")]
    save_questions(existing, path)
    assert load_questions(path) == existing
    cov = coverage(existing)
    assert cov["commitment_status"]["verified"] == 1 and cov["temporal"]["draft"] == 1
    out = DraftOutput(
        questions=[
            DraftItem(
                category="decision",
                question="Who owns the launch?",  # duplicate of real-001
                gold_answer="Alex",
                key_terms=["alex"],
                evidence_note_ids=[NOTE],
                evidence="[x:0001]",
            ),
            DraftItem(
                category="unanswerable",
                question="What is the budget?",
                gold_answer="no idea",
                key_terms=["budget"],
                evidence_note_ids=[],
                evidence="never discussed",
            ),
        ]
    )
    new = drafts_to_questions(out, existing, date(2026, 11, 2))
    assert [q.id for q in new] == ["real-003"]
    assert new[0].gold_answer == "UNANSWERABLE" and new[0].key_terms == []
    assert new[0].status == "draft" and new[0].origin == "llm_draft"


def test_draft_request_and_directory_use_only_the_given_meetings() -> None:
    notes = load_corpus(FIXTURES)[:2]
    req = draft_request(notes, {"who_said": 2, "temporal": 0}, date(2026, 11, 2))
    assert "who_said" in req.user and "- temporal" not in req.user
    assert all(m.note_id in req.user for m in notes)
    d = real_directory(notes)
    emails = {p.email for m in notes for p in (m.owner, *m.attendees)}
    assert {p["email"] for p in d["people"]} == {e.lower() for e in emails}
    assert d["orgs"] == [] and d["projects"] == []


def test_run_slice_writes_judge_check_compatible_report(tmp_path: Path) -> None:
    client = LLMClient(FakeJudge(), model="claude-opus-5-5", cache_dir=tmp_path / "cache")
    granola = GranolaAgentSystem(FakeMcp(), record_dir=tmp_path / "g")
    qs = [_q(), _q("real-002", status="draft")]  # drafts are never scored
    report = run_slice(
        [granola],  # type: ignore[list-item]
        qs,
        client,
        seed=42,
        budget_usd=1.0,
        out_dir=tmp_path / "r",
    )
    m = report["metrics"]["granola_agent"]
    assert report["meta"]["questions"] == 1 and m["accuracy"] == 1.0
    assert m["cite_recall"] == 1.0 and m["cost_total"] == 0.0
    saved = load_report(Path(report["paths"]["jsonl"]))
    assert [(a.system, a.qid, a.verdict) for a in saved] == [
        ("granola_agent", "real-001", "correct")
    ]
    agg = json.dumps(aggregate_only(report))
    assert "Who owns the launch" not in agg and "Alex" not in agg


def test_probes_are_timed_errors_recorded_and_writes_refused(tmp_path: Path) -> None:
    from concentrate.evals.real_slice import run_probes

    class Flaky(FakeMcp):
        def call(self, tool: str, arguments: dict[str, Any]) -> str:
            if tool == "search_meetings":
                raise RuntimeError("boom")
            return super().call(tool, arguments)

    ticks = iter([0.0, 2.0, 5.0, 6.0, 7.0, 7.5])
    out = tmp_path / "p.jsonl"
    res = run_probes(
        Flaky(),
        [
            {"id": "a", "tool": "query_granola_meetings", "args": {"query": "x"}, "repeat": 2},
            {"id": "b", "tool": "search_meetings", "args": {}},
        ],
        out,
        clock=lambda: next(ticks),
    )
    assert [r["latency_s"] for r in res] == [2.0, 1.0, 0.5]
    assert res[2]["error"].startswith("RuntimeError") and len(out.read_text().splitlines()) == 3
    with pytest.raises(ToolNotAllowed):
        run_probes(FakeMcp(), [{"id": "w", "tool": "create_folder", "args": {}}], out)


def test_real_store_lookup_needs_a_complete_build(tmp_path: Path) -> None:
    from concentrate.evals.real_slice import find_real_store

    notes = load_corpus(FIXTURES)[:2]
    with pytest.raises(FileNotFoundError):
        find_real_store(notes, cache_root=tmp_path)


def test_spotcheck_lists_disagreements_first() -> None:
    from concentrate.evals.real_slice import spotcheck_markdown

    def row(s: str, qid: str, v: str) -> dict[str, Any]:
        return {"system": s, "qid": qid, "category": "decision", "verdict": v,
                "answer": {"answer": "x"}, "judge": {"reason": "r"}}  # fmt: skip

    md = spotcheck_markdown(
        [row("a", "real-001", "correct"), row("a", "real-002", "incorrect")],
        [_q("real-001"), _q("real-002")],
    )
    assert "1 of 2 questions" in md and md.index("| real-002") < md.index("| real-001")
    assert "## real-002" in md and "## real-001" not in md


def test_interleave_round_robins_categories() -> None:
    from concentrate.evals.real_slice import interleave

    qs = [_q("a1"), _q("a2"), _q("t1", category="temporal"), _q("u1", category="unanswerable")]
    assert [q.id for q in interleave(qs)] == ["a1", "t1", "u1", "a2"]
