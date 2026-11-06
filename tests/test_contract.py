"""Contract tests: fixtures must be shaped exactly like Granola API responses.

The same Pydantic models parse live responses (GranolaAPISource) and fixtures (FixtureSource).
Round-tripping each fixture through its model and back must reproduce the original JSON, which
catches unknown fields (silently ignored by the models), wrong types and wrong datetime formats.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tenor.core import granola
from tests.conftest import SAMPLE

SETS = [SAMPLE, SAMPLE.parent / "northwind"]
NOTE_FILES = sorted(p for root in SETS for p in (root / "notes").glob("*.json"))


@pytest.mark.parametrize("path", NOTE_FILES, ids=lambda p: f"{p.parent.parent.name}/{p.stem}")
def test_note_fixture_roundtrips_exactly(path: Path) -> None:
    raw = json.loads(path.read_text())
    note = granola.Note.model_validate(raw)
    assert note.id == path.stem
    assert note.model_dump(mode="json", exclude_unset=True) == raw


@pytest.mark.parametrize("root", SETS, ids=lambda p: p.name)
def test_folders_fixture_roundtrips_exactly(root: Path) -> None:
    raw = json.loads((root / "folders.json").read_text())
    parsed = granola.ListFoldersOutput.model_validate(raw)
    assert parsed.model_dump(mode="json", exclude_unset=True) == raw


@pytest.mark.parametrize("root", SETS, ids=lambda p: p.name)
def test_fixture_folder_membership_refers_to_known_folders(root: Path) -> None:
    known = {f["id"] for f in json.loads((root / "folders.json").read_text())["folders"]}
    for path in sorted((root / "notes").glob("*.json")):
        for f in json.loads(path.read_text())["folder_membership"]:
            assert f["id"] in known


def test_docs_example_transcript_item_parses() -> None:
    """Verbatim transcript example from the OpenAPI spec."""
    item = granola.TranscriptItem.model_validate(
        {
            "speaker": {"source": "microphone", "attribution": "me"},
            "text": "...",
            "start_time": "2026-01-27T15:30:00Z",
            "end_time": "2026-01-27T16:30:00Z",
        }
    )
    assert item.speaker.source == "microphone"


@pytest.mark.parametrize(
    "mutation",
    [
        {"id": "not_tooShort"},
        {"id": "note_1d3tmYTlCICgjy"},
        {"object": "document"},
        {"summary_text": None},
        {
            "transcript": [
                {
                    "speaker": {"source": "system"},
                    "text": "x",
                    "start_time": "2026-01-27T15:30:00Z",
                    "end_time": "2026-01-27T15:30:01Z",
                }
            ]
        },
    ],
    ids=["short-id", "wrong-prefix", "wrong-object", "null-summary-text", "bad-source-enum"],
)
def test_contract_rejects_off_spec_payloads(mutation: dict[str, Any]) -> None:
    raw = {**json.loads(NOTE_FILES[0].read_text()), **mutation}
    with pytest.raises(ValidationError):
        granola.Note.model_validate(raw)


def test_additive_api_fields_are_tolerated() -> None:
    """The API adds fields over time (v1.0→v1.6); live parsing must not break on them."""
    raw = {**json.loads(NOTE_FILES[0].read_text()), "some_future_field": {"x": 1}}
    granola.Note.model_validate(raw)
