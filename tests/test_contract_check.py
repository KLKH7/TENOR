"""MCP contract check: shapes are values-free, and the 2026-11-05 Granola change is caught."""

from __future__ import annotations

import json
from typing import Any

from tenor.sources.contract import diff, probe_granola, response_shape, text_shape

MID = "11111111-1111-4111-8111-111111111111"
LISTING = (
    f'<meetings_data count="1"><meeting id="{MID}" title="Secret title" date="Nov 5">'
    "<known_participants>Alice (note creator)</known_participants></meeting></meetings_data>"
    '<pagination has_more="false" completion_status="exhausted" />'
)
OLD_CHUNKS = {
    "status": "ok",
    "total_chunks": 1,
    "chunks": [
        {
            "chunk_id": 0,
            "passages": [{"speaker": "System audio", "time": "2026-11-04T10:00:00Z", "text": "hi"}],
        }
    ],
}
NEW_CHUNKS = {
    "status": "ok",
    "total_chunks": 1,
    "chunks": [
        {"chunk_id": 0, "transcript": "[0:00] System audio: hi\n\n[0:05] Microphone: secret"}
    ],
}


def _fake(chunks: dict[str, Any]) -> Any:
    def call(tool: str, args: dict[str, Any]) -> str:
        if tool == "search_meetings":
            return LISTING
        if tool == "get_meetings":
            return LISTING
        if tool == "get_meeting_transcript":
            if "chunk_ids" in args:
                return json.dumps(chunks)
            return json.dumps({"id": MID, "transcript": "System audio: a\n\nMicrophone: b"})
        return json.dumps({"meetings": [], "status": "ok"})

    return call


def test_shapes_hold_no_values() -> None:
    shapes = probe_granola(_fake(NEW_CHUNKS))
    dumped = json.dumps(shapes)
    for secret in (MID, "Secret title", "Alice", "secret", "hi"):
        assert secret not in dumped
    assert shapes["search_meetings"]["markup"]["meeting"] == ["date", "id", "title"]


def test_text_shape() -> None:
    assert text_shape("one line") == "str"
    assert (
        text_shape("[0:00] System audio: a\n[1:02:03] Microphone: b") == "text[[m:ss] Label: text]"
    )
    assert text_shape("System audio: a\nfree text") == "text[Label: text | plain]"


def test_the_granola_transcript_change_is_reported() -> None:
    old = probe_granola(_fake(OLD_CHUNKS))
    new = probe_granola(_fake(NEW_CHUNKS))
    changes = diff(old, new)
    assert changes == [
        "removed  get_meeting_transcript.chunks.json.chunks[].passages",
        "added    get_meeting_transcript.chunks.json.chunks[].transcript: text[[m:ss] Label: text]",
    ]
    assert diff(new, new) == []


def test_list_elements_merge() -> None:
    shape = response_shape(json.dumps({"xs": [{"a": 1}, {"b": "x"}, {"a": None}]}))
    assert shape == {"json": {"xs": [{"a": "null | number", "b": "str"}]}}


def test_skips_meetings_still_recording() -> None:
    live = "22222222-2222-4222-8222-222222222222"
    listing = LISTING.replace(
        f'<meeting id="{MID}"', f'<meeting id="{live}"></meeting><meeting id="{MID}"'
    )
    seen: list[str] = []

    def call(tool: str, args: dict[str, Any]) -> str:
        if tool == "get_meeting_transcript" and "chunk_ids" in args:
            seen.append(args["meeting_id"])
            if args["meeting_id"] == live:
                return json.dumps({"status": "unavailable", "transcript": None})
        return _fake(NEW_CHUNKS)(tool, args) if tool != "search_meetings" else listing

    shapes = probe_granola(call)
    assert seen == [live, MID]
    assert "transcript" in shapes["get_meeting_transcript.chunks"]["json"]["chunks"][0]


def test_null_is_not_drift() -> None:
    assert diff({"a": "number"}, {"a": "null"}) == []
    assert diff({"a": "number"}, {"a": "str"}) == ["changed  a: number -> str"]
