"""Synthetic dataset: determinism, fidelity to Granola's shape, and question validity."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from concentrate.core import granola
from concentrate.core.store import NoteStore
from concentrate.llm.client import Completion, LLMClient, LLMRequest
from concentrate.sources.fixtures import FixtureSource
from concentrate.sources.poller import sync
from concentrate.synth.pipeline import build, generate_dataset
from concentrate.synth.polish import check_turn
from concentrate.synth.questions import Question
from concentrate.synth.world import Beat

REPO_SET = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "northwind"


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("nw")
    result = generate_dataset(42, out)
    assert result.validation.errors == []
    return out


def _files(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*.json*"))}


def test_same_seed_is_byte_identical(dataset: Path, tmp_path: Path) -> None:
    generate_dataset(42, tmp_path)
    assert _files(dataset) == _files(tmp_path)


def test_committed_fixtures_match_generator(dataset: Path) -> None:
    """data/fixtures/northwind must be exactly what `concentrate synth --seed 42` produces."""
    assert _files(REPO_SET) == _files(dataset)


@pytest.mark.parametrize("seed", [7, 1234])
def test_other_seeds_also_validate(seed: int, tmp_path: Path) -> None:
    result = generate_dataset(seed, tmp_path)
    assert result.validation.errors == []
    assert result.meetings == 40


def test_world_has_planted_difficulties() -> None:
    w = build(42)
    price = w.facts_about("teams", "price_per_seat")
    assert [f.value_norm for f in price] == ["20", "25"]
    assert price[0].valid_to == price[1].valid_from
    launch = w.facts_about("teams", "launch_date")
    assert [f.value_norm for f in launch] == ["2026-10-14", "2026-10-28", "2026-11-11"]
    assert all(f.reason for f in launch[1:])
    assert [f.value_norm for f in w.facts_about("falcon", "owner")] == ["sam_p", "tomas"]
    outcomes = Counter(c.outcome for c in w.commitments)
    assert {"kept", "slipped", "dropped", "reassigned"} <= set(outcomes)
    both_sams = [m for m in w.meetings if {"sam_p", "sam_o"} <= set(m.attendees)]
    assert both_sams, "at least one meeting must contain both Sams"
    assert {f.privacy for f in w.facts if f.privacy} == {"health", "family", "compensation"}


def test_notes_parse_and_follow_granola_channel_rules(dataset: Path) -> None:
    world = json.loads((dataset / "world.json").read_text())
    mode = {m["note_id"]: m["mode"] for m in world["meetings"]}
    for path in (dataset / "notes").glob("*.json"):
        raw = json.loads(path.read_text())
        note = granola.Note.model_validate(raw)
        assert note.model_dump(mode="json", exclude_unset=True) == raw
        sources = {i.speaker.source for i in note.transcript or []}
        if mode[note.id] == "in_person":
            assert sources == {"microphone"}
        else:
            assert sources == {"microphone", "speaker"}
        starts = [i.start_time for i in note.transcript or []]
        assert starts == sorted(starts)
        ancestors = {f.parent_folder_id for f in note.folder_membership} - {None}
        assert ancestors <= {f.id for f in note.folder_membership}


def test_fixture_set_syncs_through_fixture_source(dataset: Path) -> None:
    store = NoteStore(":memory:")
    report = sync(FixtureSource(dataset), store)
    assert report.fetched == 40
    assert store.counts()["utterances"] > 2000


def test_questions_are_well_formed(dataset: Path) -> None:
    qs = [
        Question.model_validate_json(line)
        for line in (dataset / "questions.jsonl").read_text().splitlines()
    ]
    cats = Counter(q.category for q in qs)
    assert len(cats) == 9
    assert 0.08 <= cats["unanswerable"] / len(qs) <= 0.15
    for cat, n in cats.items():
        dev = sum(1 for q in qs if q.category == cat and q.split == "dev")
        assert 0 < dev < n, cat
    world = json.loads((dataset / "world.json").read_text())
    private = {f["id"] for f in world["facts"] if f["privacy"]}
    assert not any(set(q.sources) & private for q in qs)


def test_private_content_is_in_transcripts_for_redaction_evals(dataset: Path) -> None:
    text = " ".join(
        i["text"]
        for p in (dataset / "notes").glob("*.json")
        for i in json.loads(p.read_text())["transcript"]
    )
    for phrase in ("physical therapy for my back", "parental leave", "one hundred sixty five k"):
        assert phrase in text


# -- polish ---------------------------------------------------------------------------------


def test_check_turn_rules() -> None:
    fact = Beat(
        speaker="marcus", text="Decision: twenty dollars per seat.", protect=("twenty dollars",)
    )
    assert check_turn(fact, "Alright, decision is twenty dollars per seat.", set()) is None
    assert "protected" in (check_turn(fact, "Decision: $20 per seat.", set()) or "")
    assert "numbers" in (check_turn(fact, "twenty dollars, maybe thirty later.", set()) or "")
    assert "names" in (check_turn(fact, "Megan agrees, twenty dollars.", set()) or "")
    assert "dates" in (check_turn(fact, "twenty dollars from Friday.", set()) or "")
    filler = Beat(speaker="priya", text="Can you share your screen?", filler=True)
    assert check_turn(filler, "Could you throw the deck up on screen?", set()) is None
    assert check_turn(filler, "Share the two slides?", set()) is not None
    assert check_turn(filler, "Sam, share your screen?", set()) is not None
    assert check_turn(filler, "I'll share mine.", set()) is not None
    assert check_turn(filler, "Is this about Falcon?", set()) is not None
    assert check_turn(filler, "Is this about Falcon?", {"Falcon"}) is None


class _PolishBackend:
    """Rewrites every turn; deliberately breaks every 5th fact turn and every 7th filler."""

    def complete(self, request: LLMRequest, model: str) -> Completion:
        turns = json.loads(request.user)["turns"]
        out = []
        for t in turns:
            text = f"So, {t['text'][0].lower()}{t['text'][1:]}"
            if t["kind"] == "fact" and t.get("keep") and t["i"] % 5 == 0:
                text = "I don't remember the details."  # drops protected spans
            if t["kind"] == "filler" and t["i"] % 7 == 0:
                text = "We have two more of those."  # filler with a number
            out.append({"i": t["i"], "text": text})
        return Completion(json.dumps({"turns": out}), model, 2000, 1500, "end_turn")


def test_polish_with_fake_llm_rejects_bad_rewrites_and_still_validates(tmp_path: Path) -> None:
    llm = LLMClient(_PolishBackend(), model="claude-opus-5-5", cache_dir=tmp_path / "cache")
    result = generate_dataset(42, tmp_path / "out", llm=llm)
    stats = result.polish
    assert stats is not None
    assert 0 < stats.accepted < stats.turns
    assert stats.rejected.get("dropped protected span", 0) > 0
    assert stats.rejected.get("filler has numbers", 0) > 0
    assert result.validation.errors == []
    assert result.llm_spent_usd > 0
    # Second run is served entirely from cache and reproduces the files byte-for-byte.
    llm2 = LLMClient(None, model="claude-opus-5-5", cache_dir=tmp_path / "cache", offline=True)
    generate_dataset(42, tmp_path / "out2", llm=llm2)
    assert _files(tmp_path / "out") == _files(tmp_path / "out2")
    assert llm2.ledger.spent_usd() == 0


def test_transcripts_match_live_granola_format(dataset: Path) -> None:
    """Calibrated on real MCP-synced meetings: ~82% of "Them" passages named, many very short
    remote passages, long single-microphone in-person blocks, some nameless attendees."""
    named_meetings = remote = short = total = nameless = them_total = them_named = mixed = 0
    for path in (dataset / "notes").glob("*.json"):
        note = granola.Note.model_validate_json(path.read_bytes())
        items = note.transcript or []
        them = [i for i in items if i.speaker.source == "speaker"]
        assert not any(i.speaker.name for i in items if i.speaker.source == "microphone")
        if them:
            remote += 1
            named_meetings += any(i.speaker.name for i in them)
            them_total += len(them)
            them_named += sum(1 for i in them if i.speaker.name)
            mixed += any(i.speaker.name for i in them) and not all(i.speaker.name for i in them)
        short += sum(1 for i in items if len(i.text.split()) <= 5)
        total += len(items)
        nameless += sum(1 for a in note.attendees if a.name is None)
        if note.attendees:
            assert note.attendees[0].name is not None  # the owner is always named
    assert 0 < named_meetings <= remote
    assert 0.65 <= them_named / them_total <= 0.95  # real: 0.82
    assert mixed > 0  # identified speakers still appear as bare "System audio" sometimes
    assert short / total > 0.3
    assert nameless > 0
