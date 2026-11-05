"""Cross-product pieces: meeting pairing, the notes-faithfulness input, the outside agent.

All synthetic; the outside agent runs on a scripted fake model (no network, $0)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from concentrate.blackbox.base import Product, ToolNotAllowed
from concentrate.blackbox.notes_source import notes_case, paired_cases
from concentrate.blackbox.outside_agent import (
    GRANOLA_BINDING,
    WISPR_BINDING,
    OutsideAgentProduct,
    ToolBudget,
    bind_tools,
)
from concentrate.blackbox.pairing import Overrides, PairOverride, pair_meetings
from concentrate.core.models import Channel, MeetingNote, Person, Utterance, normalize_note
from concentrate.llm.client import LLMClient, TurnCompletion, TurnRequest
from concentrate.sources.ratelimit import FakeClock
from concentrate.sources.wispr_mcp import WisprMCPSource
from tests.fake_wispr import FakeWispr, meeting

T0 = datetime(2026, 11, 3, 15, tzinfo=UTC)
TALK = [
    "shall we start with the pilot timeline for the northwind account",
    "yes i think we should move the pilot to october after the audit",
    "october works if legal signs off by the fifteenth of the month",
]
OTHER = [
    "the retro covered the outage last week and the pager rotation",
    "we agreed to add a second reviewer for every database migration",
]


def note(
    nid: str,
    *,
    start: datetime = T0,
    title: str = "Pilot planning",
    lines: list[str] = TALK,
    emails: tuple[str, ...] = ("priya@example.com",),
    summary: str = "Pilot moves to October.",
) -> MeetingNote:
    return MeetingNote(
        note_id=nid,
        title=title,
        owner=Person(name="Kian Lakhani", email="kian@example.com"),
        created_at=start,
        updated_at=start,
        deleted_at=None,
        web_url="",
        event_title=title,
        calendar_event_id=None,
        scheduled_start=start,
        scheduled_end=None,
        organiser_email=None,
        invitee_emails=emails,
        attendees=tuple(Person(name=None, email=e) for e in emails),
        folders=(),
        space_ids=(),
        summary_markdown=summary,
        summary_text=summary,
        private_notes_markdown=None,
        utterances=tuple(
            Utterance(
                utterance_id=f"{nid}:{i:04d}",
                note_id=nid,
                index=i,
                channel=Channel.THEM,
                speaker_name=None,
                diarization_label=None,
                text=t,
                start_time=start,
                end_time=start,
            )
            for i, t in enumerate(lines)
        ),
    )


# -- pairing ---------------------------------------------------------------------------------


def test_pairs_the_same_meeting_recorded_by_both_products() -> None:
    granola = [note("g-1"), note("g-2", start=T0 + timedelta(hours=2), title="Retro", lines=OTHER)]
    wispr = [
        note("w-2", start=T0 + timedelta(hours=2, minutes=3), title="Team retro", lines=OTHER),
        note("w-1", start=T0 + timedelta(minutes=4), title="Pilot sync", lines=TALK[1:]),
    ]
    out = pair_meetings(granola, wispr)
    assert [(p.a, p.b, p.method) for p in out.pairs] == [
        ("g-1", "w-1", "auto"),
        ("g-2", "w-2", "auto"),
    ]
    assert out.pairs[0].signals["transcript"] == 1.0
    assert out.unpaired_a == out.unpaired_b == []


def test_time_window_is_a_hard_gate() -> None:
    out = pair_meetings([note("g-1")], [note("w-1", start=T0 + timedelta(minutes=11))])
    assert out.pairs == [] and out.unpaired_a == ["g-1"]


def test_back_to_back_meetings_pick_the_matching_conversation() -> None:
    granola = [note("g-1"), note("g-2", start=T0 + timedelta(minutes=8), lines=OTHER)]
    wispr = [note("w-x", start=T0 + timedelta(minutes=7), lines=OTHER)]
    out = pair_meetings(granola, wispr)
    assert [(p.a, p.b) for p in out.pairs] == [("g-2", "w-x")]


def test_unrelated_meetings_at_the_same_time_are_not_paired() -> None:
    other = note("w-1", title="Dentist", lines=OTHER, emails=("someone@example.org",))
    assert pair_meetings([note("g-1")], [other]).pairs == []


def test_overrides_force_and_block_pairs(tmp_path: Path) -> None:
    path = tmp_path / "pairs.override.json"
    path.write_text(
        Overrides(
            pairs=[PairOverride(a="g-1", b="w-far")], never=[PairOverride(a="g-2", b="w-2")]
        ).model_dump_json()
    )
    granola = [note("g-1"), note("g-2", start=T0 + timedelta(hours=1))]
    wispr = [
        note("w-far", start=T0 + timedelta(days=1)),
        note("w-2", start=T0 + timedelta(hours=1)),
    ]
    out = pair_meetings(granola, wispr, overrides=Overrides.load(path))
    assert [(p.a, p.b, p.method) for p in out.pairs] == [("g-1", "w-far", "manual")]
    assert out.unpaired_a == ["g-2"] and out.unpaired_b == ["w-2"]
    assert Overrides.load(tmp_path / "missing.json") == Overrides()


def test_pairing_output_holds_ids_and_scores_only() -> None:
    dumped = pair_meetings([note("g-1")], [note("w-1")]).model_dump_json()
    for private in ("Pilot", "october", "priya", "Kian"):
        assert private.lower() not in dumped.lower()


def test_pairs_a_real_wispr_sync_with_a_granola_note() -> None:
    src = WisprMCPSource(FakeWispr(meetings=[meeting("m-1")]), clock=FakeClock())
    w = normalize_note(src.get_note("m-1"))
    g = note(
        "g-1",
        lines=[
            "shall we start with the pilot timeline",
            "i think we should move the pilot to october",
        ],
    )
    out = pair_meetings([g], [w])
    assert [(p.a, p.b) for p in out.pairs] == [("g-1", "m-1")]


# -- notes-faithfulness input ----------------------------------------------------------------


def test_notes_case_is_product_neutral() -> None:
    c = notes_case("wispr", note("w-1", summary="Flow summary"))
    assert (c.notes, c.transcript_source, c.transcript_meeting_id) == (
        "Flow summary",
        "wispr",
        "w-1",
    )
    assert c.transcript[0].line_id == "w-1:0000" and c.transcript[0].speaker == "Them"


def test_paired_cases_share_one_reference_transcript() -> None:
    g, w = note("g-1", summary="G notes"), note("w-1", summary="W notes", lines=TALK[1:])
    cg, cw = paired_cases(("granola", g, "summary"), ("wispr", w, "summary"), reference="a")
    assert (cg.notes, cw.notes) == ("G notes", "W notes")
    assert cg.transcript == cw.transcript and cw.transcript_meeting_id == "g-1"


# -- outside agent ---------------------------------------------------------------------------

WISPR_TOOLS = [
    {"name": n, "description": f"{n} (as published)", "input_schema": {"type": "object"}}
    for n in ("search_meetings", "get_meeting", "list_folders")
]
GRANOLA_TOOLS = [
    {"name": n, "description": n, "input_schema": {"type": "object"}}
    for n in (
        "search_meetings",
        "get_meetings",
        "get_meeting_transcript",
        "search_transcripts",
        "query_granola_meetings",
        "update_private_notes",
    )
]


class ScriptedModel:
    """Plays a fixed list of tool calls, then answers citing ``cite``."""

    def __init__(self, script: list[tuple[str, dict[str, Any]]], cite: str = "m-1") -> None:
        self.script = script
        self.cite = cite
        self.requests: list[TurnRequest] = []

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        self.requests.append(request)
        step = sum(1 for m in request.messages if m["role"] == "assistant")
        if request.tools and step < len(self.script):
            name, args = self.script[step]
            use = {"type": "tool_use", "id": f"tu_{step}", "name": name, "input": args}
            return TurnCompletion([use], "", "tool_use", model, 100, 10)
        answer = {
            "answer": "Priya will chase legal.",
            "abstain": False,
            "citations": [f"{self.cite}:0003"],
        }
        text = json.dumps(answer)
        return TurnCompletion([{"type": "text", "text": text}], text, "end_turn", model, 100, 10)


def _client(tmp_path: Path) -> LLMClient:
    return LLMClient(model="claude-opus-5-5", cache_dir=tmp_path / "cache")


def test_outside_agent_answers_through_wispr_read_tools(tmp_path: Path) -> None:
    fake = FakeWispr(meetings=[meeting("m-1")])
    model = ScriptedModel(
        [
            ("search_meetings", {"query": "pilot"}),
            ("get_meeting", {"meeting_id": "m-1", "view_transcript": {}}),
        ]
    )
    agent = OutsideAgentProduct(WISPR_BINDING, fake, WISPR_TOOLS, _client(tmp_path), backend=model)
    assert isinstance(agent, Product)
    ans = agent.ask("Who is chasing legal?")
    assert ans.text == "Priya will chase legal." and ans.cited_ids == ["m-1"]
    assert ans.abstained is False and ans.cost_usd > 0
    assert [c[0] for c in fake.calls] == ["search_meetings", "get_meeting"]
    # the model is shown exactly the binding's tools, with the server's own descriptions
    assert [t["name"] for t in model.requests[0].tools] == ["search_meetings", "get_meeting"]
    assert model.requests[0].tools[0]["description"] == "search_meetings (as published)"
    assert model.requests[0].prompt_id == "outside_agent.v1"


def test_outside_agent_refuses_tools_outside_the_binding(tmp_path: Path) -> None:
    fake = FakeWispr(meetings=[meeting("m-1")])
    model = ScriptedModel([("delete_meeting", {"meeting_id": "m-1"}), ("list_folders", {})])
    agent = OutsideAgentProduct(WISPR_BINDING, fake, WISPR_TOOLS, _client(tmp_path), backend=model)
    ans = agent.ask("Delete it")
    assert fake.calls == []  # nothing was sent
    trace = json.loads(ans.raw)["trace"]
    assert [t["error"] for t in trace] == [True, True]


def test_tool_budget_is_enforced_identically(tmp_path: Path) -> None:
    fake = FakeWispr(meetings=[meeting("m-1")])
    model = ScriptedModel([("search_meetings", {})] * 6)
    agent = OutsideAgentProduct(
        WISPR_BINDING,
        fake,
        WISPR_TOOLS,
        _client(tmp_path),
        backend=model,
        budget=ToolBudget(max_tool_calls=3),
    )
    ans = agent.ask("Anything?")
    assert len(fake.calls) == 3
    assert [t["error"] for t in json.loads(ans.raw)["trace"]] == [False] * 3 + [True] * 3


def test_turn_limit_forces_a_final_answer(tmp_path: Path) -> None:
    fake = FakeWispr(meetings=[meeting("m-1")])
    model = ScriptedModel([("search_meetings", {})] * 50)
    agent = OutsideAgentProduct(
        WISPR_BINDING,
        fake,
        WISPR_TOOLS,
        _client(tmp_path),
        backend=model,
        budget=ToolBudget(max_turns=4),
    )
    assert agent.ask("Anything?").text == "Priya will chase legal."
    assert model.requests[-1].tools == ()


def test_granola_binding_uses_only_read_tools(tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeGranola:
        def call(self, tool: str, arguments: dict[str, Any]) -> str:
            calls.append(tool)
            return "<meetings></meetings>"

    tools = bind_tools(GRANOLA_BINDING, GRANOLA_TOOLS)
    assert [t["name"] for t in tools] == list(GRANOLA_BINDING.tools)
    model = ScriptedModel(
        [
            ("search_transcripts", {"keywords": ["legal"]}),
            ("query_granola_meetings", {"query": "x"}),  # Granola's own agent: not in the binding
            ("update_private_notes", {}),
            ("get_meeting_transcript", {"meeting_id": "g"}),
        ],
        cite="g-1",
    )
    agent = OutsideAgentProduct(
        GRANOLA_BINDING, FakeGranola(), GRANOLA_TOOLS, _client(tmp_path), backend=model
    )
    assert agent.name == "granola+outside-agent"
    assert agent.ask("Who is chasing legal?").cited_ids == ["g-1"]
    assert calls == ["search_transcripts", "get_meeting_transcript"]


def test_binding_fails_if_the_server_lacks_a_bound_tool() -> None:
    with pytest.raises(ToolNotAllowed):
        bind_tools(GRANOLA_BINDING, WISPR_TOOLS)


def test_unparseable_final_text_is_recorded_as_abstention(tmp_path: Path) -> None:
    class Prose(ScriptedModel):
        def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
            return TurnCompletion(
                [{"type": "text", "text": "no idea"}], "no idea", "end_turn", model, 1, 1
            )

    agent = OutsideAgentProduct(
        WISPR_BINDING, FakeWispr(), WISPR_TOOLS, _client(tmp_path), backend=Prose([])
    )
    ans = agent.ask("?")
    assert ans.abstained is True and ans.text == "no idea"


def test_pair_cli_writes_ids_and_scores(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from concentrate.cli import app
    from concentrate.core.store import NoteStore
    from concentrate.sources.poller import sync

    for name, mid in (("g.db", "g-1"), ("w.db", "m-1")):
        store = NoteStore(tmp_path / name)
        sync(WisprMCPSource(FakeWispr(meetings=[meeting(mid)]), clock=FakeClock()), store)
        store.close()
    out = tmp_path / "pairs.json"
    result = CliRunner().invoke(
        app,
        [
            "pair",
            "--granola-db",
            str(tmp_path / "g.db"),
            "--wispr-db",
            str(tmp_path / "w.db"),
            "--out",
            str(out),
            "--overrides",
            str(tmp_path / "none.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "g-1  m-1" in result.output and "October" not in result.output
    assert json.loads(out.read_text())["pairs"][0]["b"] == "m-1"
