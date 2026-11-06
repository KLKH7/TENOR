"""Enhance (stage 1): string similarity, mention linking, speaker attribution, evaluation."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tenor.core.models import Channel, MeetingNote, Person, Utterance
from tenor.enhance import Directory, EnhancedNote, enhance, enhanced_line
from tenor.enhance.evaluate import evaluate, format_report
from tenor.enhance.strings import (
    double_metaphone,
    fold,
    jaro_winkler,
    name_similarity,
    sounds_alike,
)

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "data" / "fixtures" / "northwind"
PKG = ROOT / "packages" / "concentrate" / "enhance"
T0 = datetime(2026, 10, 5, 15, 0, tzinfo=UTC)
OWNER = Person(name="Priya Raman", email="priya@northwind.dev")


@pytest.fixture(scope="module")
def directory() -> Directory:
    return Directory.load(BASE)


def _note(
    lines: list[tuple[str, str | None, str] | tuple[str, str | None, str, str]],
    attendees: list[Person] | None = None,
    title: str = "Sync",
    note_id: str = "not_test0000001",
) -> MeetingNote:
    """lines: (channel 'me'|'them', platform name or None, text[, diarization label])."""
    utts = []
    for i, line in enumerate(lines):
        channel, name, text = line[0], line[1], line[2]
        label = line[3] if len(line) == 4 else None
        utts.append(
            Utterance(
                utterance_id=f"{note_id}:{i:04d}",
                note_id=note_id,
                index=i,
                channel=Channel(channel),
                speaker_name=name,
                diarization_label=label,
                text=text,
                start_time=T0 + timedelta(seconds=3 * i),
                end_time=T0 + timedelta(seconds=3 * i + 2),
            )
        )
    people = attendees if attendees is not None else [OWNER]
    return MeetingNote(
        note_id=note_id,
        title=title,
        owner=OWNER,
        created_at=T0,
        updated_at=T0,
        deleted_at=None,
        web_url="https://notes.granola.ai/d/x",
        event_title=title,
        calendar_event_id=None,
        scheduled_start=T0,
        scheduled_end=T0 + timedelta(minutes=30),
        organiser_email=OWNER.email,
        invitee_emails=tuple(p.email for p in people),
        attendees=tuple(people),
        folders=(),
        space_ids=(),
        summary_markdown=None,
        summary_text="",
        private_notes_markdown=None,
        utterances=tuple(utts),
    )


def _p(email: str, name: str | None = None) -> Person:
    return Person(name=name, email=email)


def _links(e: EnhancedNote, index: int) -> list[tuple[str, str, str | None]]:
    uid = f"{e.note_id}:{index:04d}"
    return [(m.text, m.kind, m.entity_id) for m in e.mentions if m.utterance_id == uid]


# -- strings -----------------------------------------------------------------------------------


def test_jaro_winkler_reference_values() -> None:
    assert jaro_winkler("martha", "marhta") == pytest.approx(0.961, abs=1e-3)
    assert jaro_winkler("dwayne", "duane") == pytest.approx(0.84, abs=1e-3)
    assert jaro_winkler("dixon", "dicksonx") == pytest.approx(0.813, abs=1e-3)
    assert jaro_winkler("abc", "abc") == 1.0
    assert jaro_winkler("abc", "xyz") == 0.0


def test_double_metaphone_catches_asr_mishearings() -> None:
    assert double_metaphone("Coffee")[0] == double_metaphone("Kofi")[0] == "KF"
    assert sounds_alike("Thomas", "Tomás")  # TH has a secondary 'T' code
    assert sounds_alike("Meghan", "Megan")
    assert sounds_alike("Inez", "Inês")
    assert sounds_alike("Joseph", "Yusuf")  # J's secondary code is a vowel sound
    assert not sounds_alike("Dana", "Kofi")
    assert fold("Nuñez") == "nunez"


def test_name_similarity_ranks_true_variant_first() -> None:
    assert name_similarity("Coffee", "Kofi") > 0.8 > name_similarity("Okay", "Kofi")
    assert name_similarity("Meghan", "Megan") > name_similarity("Meghan", "Elena")


# -- mention linking ---------------------------------------------------------------------------


def test_longest_match_and_spoken_ticket_codes(directory: Directory) -> None:
    note = _note(
        [
            ("me", None, "Beacon Freight wants SSO before the Beacon launch."),
            ("me", None, "PAY two twelve is mine, the payments migration, so Falcon is on me."),
            ("me", None, "Is pay 212 the same as PAY-212?"),
        ]
    )
    e = enhance(note, directory)
    assert _links(e, 0) == [
        ("Beacon Freight", "org", "beacon_freight"),
        ("Beacon", "project", "beacon"),
    ]
    assert [x[2] for x in _links(e, 1)] == ["falcon", "falcon", "falcon"]
    assert [x[2] for x in _links(e, 2)] == ["falcon", "falcon"]


def test_misheard_names_need_a_name_like_context(directory: Directory) -> None:
    note = _note(
        [
            ("me", None, "Coffee, what's on your plate?"),
            ("me", None, "My coffee machine broke. Coffee always helps."),
            ("me", None, "I'll ask Thomas about the on-call rotation."),
            ("me", None, "We'll talk to Lucy Park after the final round."),
            ("me", None, "Sorry, right, same here. Teams is launching."),
        ]
    )
    e = enhance(note, directory)
    assert _links(e, 0) == [("Coffee", "person", "kofi")]
    assert _links(e, 1) == []  # 'coffee' is a word this note uses; no name context
    assert _links(e, 2) == [("Thomas", "person", "tomas")]
    assert _links(e, 3) == []  # an unknown full name, and a contraction
    assert [x[0] for x in _links(e, 4)] == []


def test_shared_first_names_use_attendees_else_stay_ambiguous(directory: Directory) -> None:
    line = [("me", None, "Sam, can you take the prototype?")]
    with_designer = _note(line, attendees=[OWNER, _p("sam.o@northwind.dev")])
    assert _links(enhance(with_designer, directory), 0) == [("Sam", "person", "sam_o")]
    nobody = _note(line)
    link = enhance(nobody, directory).mentions[0]
    assert link.entity_id is None and "sam_o" in link.reason and "sam_p" in link.reason


# -- speaker attribution -----------------------------------------------------------------------


def test_remote_owner_mic_platform_names_and_email_only_attendees(directory: Directory) -> None:
    note = _note(
        [
            ("me", None, "Okay, let's start."),
            ("them", "Megan Liu", "I shipped the login fix."),
        ],
        attendees=[OWNER, _p("megan.l@northwind.dev"), _p("kofi.m@northwind.dev")],
    )
    e = enhance(note, directory)
    assert e.attendee_ids == ["priya", "megan", "kofi"]  # null-name attendees via email
    first, second = (e.speakers[u.utterance_id] for u in note.utterances)
    assert (first.person_id, first.method) == ("priya", "owner_mic")
    assert (second.person_id, second.method) == ("megan", "platform_name")
    assert enhanced_line(note.utterances[1], e).startswith(
        "[not_test0000001:0001] Megan Liu (Frontend Engineer): "
    )


def test_addressing_cue_names_the_next_unnamed_speaker(directory: Directory) -> None:
    attendees = [OWNER, _p("alex.k@northwind.dev"), _p("kofi.m@northwind.dev"),
                 _p("ruth.a@northwind.dev")]  # fmt: skip
    note = _note(
        [
            ("them", "Ruth Abebe", "Release checklist is done."),
            ("them", "Alex Kim", "Kofi, what's on your plate?"),
            ("them", None, "The events table, done by Friday."),
            ("me", None, "Great, thanks everyone for the updates today."),
            ("them", None, "Sounds good."),
        ],
        attendees=attendees,
    )
    e = enhance(note, directory)
    answer = e.speakers[note.utterances[2].utterance_id]
    assert answer.person_id == "kofi"
    assert answer.method == "addressed"
    assert e.speakers[note.utterances[4].utterance_id].candidates  # never empty when guessing


def test_fragment_continuation_follows_the_named_speaker(directory: Directory) -> None:
    attendees = [OWNER, _p("sam.p@northwind.dev"), _p("ruth.a@northwind.dev")]
    note = _note(
        [
            ("them", "Ruth Abebe", "I checked the dashboards."),
            ("them", "Sam Patel", "The cutover script needs another few days, the refund"),
            ("them", None, "path is messier than I thought."),
            ("them", "Sam Patel", "Friday at the latest."),
        ],
        attendees=attendees,
    )
    r = enhance(note, directory).speakers[note.utterances[2].utterance_id]
    assert (r.person_id, r.method) == ("sam_p", "turn_continuity")
    assert r.confidence > 0.8


def test_unresolved_lines_list_candidates(directory: Directory) -> None:
    note = _note(
        [("me", None, "Hey, thanks for grabbing a room."), ("me", None, "Sure, happy to.")],
        attendees=[],
        title="Sam / Priya",
    )
    e = enhance(note, directory)
    r = e.speakers[note.utterances[1].utterance_id]
    assert {"sam_p", "sam_o"} <= set(r.candidates) or r.person_id == "priya"
    blank = {k: v.model_copy(update={"person_id": None}) for k, v in e.speakers.items()}
    unresolved = e.model_copy(update={"speakers": blank})
    line = enhanced_line(note.utterances[1], unresolved)
    assert line.startswith("[not_test0000001:0001] Me (? ")
    assert "Sam Okafor" in line and "Sam Patel" in line


def test_diarization_clusters_propagate_a_confident_cue(directory: Directory) -> None:
    attendees = [OWNER, _p("alex.k@northwind.dev")]
    note = _note(
        [
            ("me", None, "Alex, how are things going this week?", "Speaker A"),
            ("me", None, "Busy but good, the hiring loop is nearly done.", "Speaker B"),
            ("me", None, "Great to hear. Anything blocking you?", "Speaker A"),
            ("me", None, "Not really, just the backend job description.", "Speaker B"),
            ("me", None, "Alex, can you send me the plan?", "Speaker A"),
            ("me", None, "Will do.", "Speaker B"),
        ],
        attendees=attendees,
        title="Priya / Alex 1:1",
    )
    e = enhance(note, directory)
    got = [e.speakers[u.utterance_id] for u in note.utterances]
    assert [r.person_id for r in got] == ["priya", "alex_k"] * 3
    assert {r.method for r in got} == {"diarization_propagation"}


def test_every_utterance_gets_exactly_one_resolution(directory: Directory) -> None:
    from tenor.core.models import normalize_note
    from tenor.sources.fixtures import FixtureSource

    src = FixtureSource(BASE)
    note = normalize_note(src.get_note(next(iter(src.list_notes())).id))
    e = enhance(note, directory)
    assert set(e.speakers) == {u.utterance_id for u in note.utterances}
    for r in e.speakers.values():
        assert 0.0 <= r.confidence <= 1.0
        assert (r.person_id is None) == (r.method == "unresolved")


def test_enhance_never_reads_ground_truth() -> None:
    """Only the evaluator may touch the synth generator, world.json or ASR variants."""
    for path in PKG.glob("*.py"):
        if path.name == "evaluate.py":
            continue
        source = path.read_text()
        assert "asr_variants" not in source and "world.json" not in source, path.name
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("tenor.synth"), path.name


# -- evaluation --------------------------------------------------------------------------------


def test_evaluation_on_base_fixtures() -> None:
    r = evaluate(BASE)
    assert r.utterances == sum(s.n for k, s in r.speaker.items() if k in {"remote", "in_person"})
    assert r.speaker["remote/them/named"].precision == 1.0
    assert r.speaker["remote/me"].precision == 1.0
    assert r.speaker["overall"].precision > 0.95
    assert r.mentions_person.precision > 0.95 and r.mentions_person.recall > 0.9
    assert r.entity_traps["'Beacon Freight' -> customer org"].accuracy == 1.0
    assert "Coffee" in "".join(r.mention_traps)
    assert "| overall |" in format_report(r)
