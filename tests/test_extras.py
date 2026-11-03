"""Benchmark v2.1 extras: v2 stays unchanged, and each phenomenon is rendered as designed."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from concentrate.core import granola
from concentrate.synth.extras import PREFIX, ROOM, ROOM_EMAIL
from concentrate.synth.extras_checks import probe
from concentrate.synth.noise import NoiseRates
from concentrate.synth.pipeline import EXTRAS_VERSION, build, generate_dataset
from concentrate.synth.questions import NEEDS_TRANSCRIPT, Question
from concentrate.synth.render import GoldMap, folder_models, render_meeting
from concentrate.synth.world import World

REPO_SET = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "northwind"


@pytest.fixture(scope="module")
def extras_set(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("nw21")
    result = generate_dataset(42, out, extras=True)
    assert result.validation.errors == []
    return out


def _notes(root: Path) -> list[granola.Note]:
    return [granola.Note.model_validate_json(p.read_text()) for p in (root / "notes").glob("*")]


def _questions(root: Path) -> list[Question]:
    lines = (root / "questions.jsonl").read_text().splitlines()
    return [Question.model_validate_json(line) for line in lines]


def _by_id(root: Path) -> dict[str, Question]:
    return {q.id: q for q in _questions(root)}


def test_extras_leave_v2_byte_identical(extras_set: Path) -> None:
    for p in (REPO_SET / "notes").glob("*.json"):
        assert (extras_set / "notes" / p.name).read_bytes() == p.read_bytes()
    v2 = (REPO_SET / "questions.jsonl").read_text().splitlines()
    assert (extras_set / "questions.jsonl").read_text().splitlines()[: len(v2)] == v2
    assert json.loads((extras_set / "manifest.json").read_text())["generator"] == EXTRAS_VERSION
    v2_world = json.loads((REPO_SET / "world.json").read_text())
    assert "extras" not in v2_world  # v2's world.json gains no key


def test_extras_are_deterministic(extras_set: Path, tmp_path: Path) -> None:
    generate_dataset(42, tmp_path, extras=True)
    for p in sorted(extras_set.rglob("*.json*")):
        assert (tmp_path / p.relative_to(extras_set)).read_bytes() == p.read_bytes()


def _render(world: World, key: str) -> tuple[granola.Note, GoldMap]:
    m = world.meeting(key)
    gold: GoldMap = {}
    note, _ = render_meeting(
        m, [b.text for b in m.beats], world, 42, NoiseRates(), folder_models(42), gold
    )
    return note, gold


def test_hybrid_room_is_one_channel_labelled_by_room() -> None:
    world = build(42, extras=True)
    assert world.extras is not None and len(world.extras.rooms) == 4
    for key, room in world.extras.rooms.items():
        note, gold = _render(world, key)
        assert note.calendar_event is not None
        assert ROOM_EMAIL in {i.email for i in note.calendar_event.invitees}
        for i, item in enumerate(note.transcript or []):
            who = gold[f"{note.id}:{i:04d}"].speaker
            if who in room.members:  # in-room voices: system audio, never a personal name
                assert item.speaker.source == "speaker"
                assert item.speaker.name in (None, ROOM)
            elif who == "jordan":
                assert item.speaker.name in (None, "Jordan Blake")


def test_rename_shows_old_identity_before_and_new_after(extras_set: Path) -> None:
    seen: dict[bool, set[str]] = {True: set(), False: set()}
    for n in _notes(extras_set):
        before = n.created_at.date() < date(2026, 9, 28)
        names = {i.speaker.name for i in n.transcript or [] if i.speaker.name}
        names |= {a.name for a in n.attendees if a.name}
        names |= {a.email for a in n.attendees}
        seen[before] |= {x for x in names if x.startswith("Rosa") or x.startswith("rosa")}
    assert seen[True] and seen[True] <= {"Rosa Delgado", "rosa.d@northwind.dev"}
    assert seen[False] and seen[False] <= {"Rosa Whitfield", "rosa.w@northwind.dev"}
    people = json.loads((extras_set / "directory.json").read_text())["people"]
    assert [p["name"] for p in people if p["id"] == "rosa"] == ["Rosa Whitfield"]
    assert not any("Delgado" in p["name"] for p in people)


def test_contractor_and_namesakes(extras_set: Path) -> None:
    people = json.loads((extras_set / "directory.json").read_text())["people"]
    assert not any(p["id"] == "jamie" or "personal-mail" in p["email"] for p in people)
    chos = [p for p in people if p["name"] == "Daniel Cho"]
    assert {p["org"] for p in chos} == {"Corvid Bank"} and len(chos) == 2
    for n in _notes(extras_set):
        assert all(a.name != "Jamie Okonkwo" for a in n.attendees)
        assert all(i.speaker.name != "Jamie Okonkwo" for i in n.transcript or [])


def test_extras_gold_answers(extras_set: Path) -> None:
    qs = [q for q in _questions(extras_set) if q.id.startswith("x")]
    by_text = {q.question: q for q in qs}
    assert {q.category for q in qs} == {
        "room_attribution",
        "identity_resolution",
        "correction",
        "commitment_tracking",
        "long_horizon",
        "exact_quote",
        "speaker_attribution",
    }
    assert {q.split for q in qs} == {"dev", "test"}
    moves = next(q for q in qs if q.question.startswith("How many times did the Halyard"))
    assert moves.answer_norm[0] == "3"  # the November 5 misstatement is not a move
    assert "2026-11-05" not in moves.answer_norm
    assert by_text["How many endpoints does the Halyard partner sandbox expose?"].answer_norm == [
        "9"
    ]
    assert by_text["How many pilot users is Corvid Bank planning for Halyard?"].answer_norm == [
        "340"
    ]
    terms = next(q for q in qs if "partner terms" in q.question)
    assert terms.answer_norm == ["dropped"] and len(terms.gold_utterance_ids) >= 1
    keys = next(q for q in qs if q.question.startswith("Did Felix Moreau find out"))
    assert keys.answer_norm == ["done"] and "outside meetings" in keys.answer
    who = next(q for q in qs if "pushing the Halyard launch to November 12" in q.question)
    assert who.answer_norm == ["felix"]


def test_extras_meetings_have_own_prefix_and_world_truth() -> None:
    world = build(42, extras=True)
    extra = [m for m in world.meetings if m.key.startswith(PREFIX)]
    assert len(extra) == 10
    assert world.extras is not None
    assert len(world.extras.retracted) == 3
    assert len(world.extras.out_of_band) == 3 and len(world.extras.implicit) == 3
    assert not any(m.key.startswith(PREFIX) for m in build(42).meetings)


def test_free_probes_show_the_intended_difficulty(extras_set: Path) -> None:
    r = probe(extras_set)
    assert r["label_attribution"]["room_attribution"]["correct"] == 0.0
    assert r["bm25_meeting_recall@5"]["room_attribution"] == 1.0  # findable, still hard
    assert set(r["directory"]["unresolved_names"]) == {"Daniel Cho", ROOM, "Rosa Delgado"}
    assert all(t["passages"] >= 1 for t in r["retracted_traps"].values())


def test_transcript_dependent_families(extras_set: Path) -> None:
    qs = [q for q in _questions(extras_set) if q.category in NEEDS_TRANSCRIPT]
    world = World.model_validate_json((extras_set / "world.json").read_text())
    modes = {m.note_id: m.mode for m in world.meetings}
    quotes = [q for q in qs if q.category == "exact_quote"]
    assert len(quotes) >= 8 and all(q.answer.startswith('"') for q in quotes)
    said = [q for q in qs if q.category == "speaker_attribution"]
    assert len(said) >= 8
    in_person = [q for q in said if modes[q.gold_utterance_ids[0].rsplit(":", 1)[0]] != "remote"]
    assert in_person  # attribution on one shared microphone, no channel to lean on
    assert any("Halyard weekly sync" in q.question for q in said)  # hybrid room
    # The x001-x026 block is untouched by the new families (ids and splits are stable).
    first = {q.id: q.category for q in _questions(extras_set) if q.id.startswith("x")}
    assert [first[f"x{i:03d}"] for i in (1, 26)] == ["room_attribution", "long_horizon"]
