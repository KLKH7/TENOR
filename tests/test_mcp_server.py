"""The Concentrate MCP server, called in-process through a real MCP client (no network).

A tiny memory is built with the scripted fake LLM (three meetings: a price that changes, a
commitment made by an unnamed "Sam" and later done), then served read-only and queried through
the protocol, the way Claude Desktop/Code would.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from tenor.agents.concentrate_system import DB_FILE
from tenor.llm.client import LLMClient
from tenor.mcp_server.server import build_server
from tenor.mcp_server.service import Answerer, MemoryService, find_store
from tenor.memory.entities import load_directory
from tenor.memory.store import MemoryStore
from tests.memory_helpers import ScriptedLLM
from tests.test_concentrate_system import DIRECTORY, A, B, C, _notes, _script, _system

TOOLS = {
    "ask_memory",
    "get_facts",
    "get_commitments",
    "resolve_entity",
    "get_meeting",
    "get_transcript_lines",
}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """Build the tiny memory once; returns (cache_root, build dir)."""
    tmp = tmp_path_factory.mktemp("mcp")
    system = _system(tmp, ScriptedLLM(_script()))
    system.ingest(_notes())
    system.store.close()
    return tmp / "concentrate", system.store_dir(_notes())


def _service(build_dir: Path, tmp: Path, answer: bool = False) -> MemoryService:
    notes = _notes()
    answerer = None
    if answer:
        client = LLMClient(model="claude-opus-5-5", cache_dir=tmp / "answers")
        answerer = Answerer(client, backend=ScriptedLLM({}))
    return MemoryService(
        MemoryStore(build_dir / DB_FILE, read_only=True),
        load_directory(DIRECTORY, notes),
        notes,
        answerer=answerer,
    )


def _call(svc: MemoryService, tool: str, args: dict[str, Any]) -> tuple[bool, Any]:
    """(is_error, structured result or error text) for one tool call over MCP."""

    async def go() -> tuple[bool, Any]:
        async with Client(build_server(svc)) as client:
            r = await client.call_tool(tool, args)
            if r.is_error:
                return True, r.content[0].text  # type: ignore[union-attr]
            return False, r.structured_content

    return asyncio.run(go())


def test_lists_six_read_only_tools_and_a_resource(built: tuple[Path, Path], tmp_path: Path) -> None:
    svc = _service(built[1], tmp_path)

    async def go() -> tuple[Any, Any]:
        async with Client(build_server(svc)) as client:
            tools = await client.list_tools()
            res = await client.read_resource("concentrate://memory")
            return tools.tools, res.contents[0]

    tools, info = asyncio.run(go())
    assert {t.name for t in tools} == TOOLS
    assert all(t.annotations and t.annotations.read_only_hint for t in tools)
    assert all(t.annotations and t.annotations.destructive_hint is False for t in tools)
    summary = json.loads(info.text)
    assert summary["meetings_in_memory"] == 3
    assert summary["default_as_of"] == "2026-10-06"


def test_store_is_opened_read_only(built: tuple[Path, Path]) -> None:
    store = MemoryStore(built[1] / DB_FILE, read_only=True)
    with pytest.raises(Exception, match="readonly"):
        store.note_entity("x", "2026-01-01")


def test_get_facts_is_bitemporal(built: tuple[Path, Path], tmp_path: Path) -> None:
    svc = _service(built[1], tmp_path)
    err, now = _call(svc, "get_facts", {"entity": "teams", "predicate": "price"})
    assert not err
    rows = {r["value"]: r for r in now["facts"]}
    assert rows["$20 per seat per month"]["state"] == "superseded"
    assert rows["$20 per seat per month"]["valid_to"] == "2026-09-29"
    assert rows["$25 per seat per month"]["state"] == "current"
    assert rows["$25 per seat per month"]["reason"] == "SSO included"
    assert rows["$25 per seat per month"]["lines"] == [f"{B}:0000"]
    # As of mid-September the memory hadn't heard of $25 yet: hidden, and $20 is current.
    err, then = _call(svc, "get_facts", {"entity": "teams", "as_of": "2026-09-15"})
    assert [r["value"] for r in then["facts"] if r["predicate"] == "price_per_seat"] == [
        "$20 per seat per month"
    ]
    assert then["facts"][0]["state"] == "current"
    assert then["learnt_after_as_of"] >= 1


def test_ambiguous_name_is_an_error_naming_candidates(
    built: tuple[Path, Path], tmp_path: Path
) -> None:
    svc = _service(built[1], tmp_path)
    err, text = _call(svc, "get_commitments", {"person": "Sam"})
    assert err and "ambiguous" in text and "sam_p" in text and "sam_o" in text
    err, text = _call(svc, "get_facts", {"entity": "nobody at all"})
    assert err and "no entity matches" in text
    err, text = _call(svc, "get_facts", {"entity": "teams", "as_of": "last week"})
    assert err and "ISO date" in text


def test_resolve_entity_lists_evidence(built: tuple[Path, Path], tmp_path: Path) -> None:
    svc = _service(built[1], tmp_path)
    err, out = _call(svc, "resolve_entity", {"name": "Sam"})
    assert not err and out["ambiguous"]
    by_id = {c["id"]: c for c in out["candidates"]}
    assert {"sam_p", "sam_o"} <= set(by_id)
    assert by_id["sam_p"]["role"] == "Backend Engineer"
    assert by_id["sam_p"]["commitments_owned"] == 1
    err, out = _call(svc, "resolve_entity", {"name": "Marcus Chen"})
    assert [c["id"] for c in out["candidates"]] == ["marcus"]
    assert out["candidates"][0]["meetings_attended"] == 3


def test_commitment_status_follows_the_as_of_date(built: tuple[Path, Path], tmp_path: Path) -> None:
    svc = _service(built[1], tmp_path)
    err, before = _call(svc, "get_commitments", {"person": "sam_p", "as_of": "2026-09-30"})
    assert not err
    [c] = before["commitments"]
    assert c["status"] == "no_follow_up" and c["events"] == []
    assert c["lines"] == [f"{A}:0001", f"{A}:0002"]
    err, after = _call(svc, "get_commitments", {"person": "Sam Patel"})
    assert after["commitments"][0]["status"] == "done"
    assert after["commitments"][0]["events"][0]["lines"] == [f"{C}:0000"]
    err, out = _call(svc, "get_commitments", {"status": "unfinished"})
    assert out["commitments"] == []
    err, text = _call(svc, "get_commitments", {"status": "maybe"})
    assert err and "status must be one of" in text


def test_meeting_and_transcript_lines(built: tuple[Path, Path], tmp_path: Path) -> None:
    svc = _service(built[1], tmp_path)
    err, m = _call(svc, "get_meeting", {"meeting_id": f"https://notes.granola.ai/d/{A}"})
    assert not err
    assert m["title"] == "Pricing review" and m["day"] == "2026-09-08"
    assert m["memory_digest"].startswith("Price set at $20")
    assert [c["task"] for c in m["commitments_made_here"]] == ["write the cutover script"]
    assert {a["entity_id"] for a in m["attendees"]} == {"priya", "marcus"}
    err, out = _call(svc, "get_transcript_lines", {"ids": [f"{A}:0001", "junk"], "context": 1})
    assert [r["id"] for r in out["lines"]] == [f"{A}:0000", f"{A}:0001", f"{A}:0002"]
    assert out["lines"][1]["channel"] == "me"
    assert out["lines"][1]["text"].startswith("Sam, can you write")
    assert out["invalid_ids"] == ["junk"]
    err, out = _call(svc, "get_transcript_lines", {"ids": [f"{A}:0099"]})
    assert out["missing"] == [f"{A}:0099"]
    err, text = _call(svc, "get_meeting", {"meeting_id": "not_ZZZZZZZZZZZZZZ"})
    assert err and "unknown meeting" in text


def test_ask_memory_context_and_optional_answer(built: tuple[Path, Path], tmp_path: Path) -> None:
    svc = _service(built[1], tmp_path, answer=True)
    q = {"question": "What is the Teams price per seat now?"}
    err, out = _call(svc, "ask_memory", q)
    assert not err and "answer" not in out
    assert "$25 per seat per month" in out["context"] and f"{B}:0000" in out["context"]
    assert out["as_of"] == "2026-10-06"
    err, out = _call(svc, "ask_memory", {**q, "answer": True})
    assert out["answer"]["citations"] and out["answer"]["citations"][0].startswith("not_")
    assert out["answer_model"] == "claude-opus-5-5"
    # Without an answer model the context still comes back.
    err, out = _call(_service(built[1], tmp_path), "ask_memory", {**q, "answer": True})
    assert not err and "answer_error" in out and out["context"]


def test_find_store_prefers_complete_builds(built: tuple[Path, Path]) -> None:
    cache_root, build_dir = built
    assert find_store(cache_root, _notes()) == build_dir
    with pytest.raises(FileNotFoundError, match="no finished Concentrate build"):
        find_store(cache_root / "elsewhere", _notes())
