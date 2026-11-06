"""enhance-v2: the cross-meeting task cue, the diarized-owner prior, and version selection."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tenor.core.models import MeetingNote
from tenor.enhance import Directory, enhance, enhance_all
from tenor.enhance.context import Priors
from tenor.enhance.tasks import task_phrases
from tenor.memory.enhance_stub import real_enhancer
from tests.test_enhance import BASE, OWNER, T0, _note, _p


@pytest.fixture(scope="module")
def directory() -> Directory:
    return Directory.load(BASE)


def test_task_phrases_take_and_status_lines() -> None:
    assert task_phrases("I'll take the skills matrix, done by Thursday.") == ["skills matrix"]
    assert task_phrases("Yeah, the invoice export is done, that went out last week.") == [
        "invoice export"
    ]
    assert task_phrases("The um skills matrix slipped, I'll get it done next week.") == [
        "skills matrix"
    ]
    assert task_phrases("The growth plan draft for Tomás is done.") == [
        "growth plan draft for tomas"
    ]
    assert task_phrases("I think the thing is done.") == []  # names no task
    assert task_phrases("We talked about the skills matrix.") == []  # no take/status verb


def _later(note: MeetingNote, days: int) -> MeetingNote:
    t = T0 + timedelta(days=days)
    return note.model_copy(update={"scheduled_start": t, "created_at": t})


def _series(directory: Directory) -> tuple[MeetingNote, MeetingNote]:
    megan = _p("megan.l@northwind.dev", "Megan Liu")
    remote = _note(
        [
            ("me", None, "What's on your plate this week?"),
            ("them", "Megan Liu", "I'll take the skills matrix, done by Thursday."),
        ],
        attendees=[OWNER, megan],
        title="Team sync",
        note_id="not_v2remote0001",
    )
    in_person = _note(
        [
            ("me", None, "How's your week going?"),
            ("me", None, "Pretty good. The skills matrix is done."),
        ],
        attendees=[OWNER, megan],
        title="Priya / Megan 1:1",
        note_id="not_v2inperson01",
    )
    return remote, _later(in_person, 7)


def test_task_cue_credits_who_reported_the_task_elsewhere(directory: Directory) -> None:
    remote, in_person = _series(directory)
    line = in_person.utterances[1].utterance_id
    v1 = enhance(in_person, directory, [remote])
    v2 = enhance(in_person, directory, [remote], version="enhance-v2")
    assert v1.speakers[line].person_id is None  # v1: "? priya|megan"
    assert v2.speakers[line].person_id == "megan"
    assert v2.speakers[line].method == "task_continuity"
    # Without the earlier meeting there is no evidence, and v2 abstains like v1.
    assert enhance(in_person, directory, version="enhance-v2").speakers[line].person_id is None


def test_lookahead_uses_later_meetings(directory: Directory) -> None:
    remote, in_person = _series(directory)
    early_in_person = _later(in_person, -7)  # the 1:1 now happens *before* the remote call
    line = early_in_person.utterances[1].utterance_id
    online = enhance_all([remote, early_in_person], directory, version="enhance-v2")
    batch = enhance_all([remote, early_in_person], directory, version="enhance-v2", lookahead=True)
    assert online[early_in_person.note_id].speakers[line].person_id is None
    assert batch[early_in_person.note_id].speakers[line].person_id == "megan"


def test_task_index_only_learns_from_identified_lines(directory: Directory) -> None:
    remote, in_person = _series(directory)
    priors = Priors.from_history([remote, in_person], directory)
    assert dict(priors.tasks["skills matrix"]) == {"megan": 1}  # not the in-person line


def test_v2_diarized_two_person_meeting_clears_the_threshold(directory: Directory) -> None:
    note = _note(
        [
            ("me", None, "How are things going this week? Anything new on your side?", "A"),
            ("me", None, "Busy but good.", "B"),
            ("me", None, "Great to hear. Let's go through the plan for next quarter then.", "A"),
            ("me", None, "Sounds good.", "B"),
        ],
        attendees=[OWNER, _p("alex.k@northwind.dev")],
        title="Priya / Alex 1:1",
    )
    v1 = enhance(note, directory)
    v2 = enhance(note, directory, version="enhance-v2")
    assert all(r.person_id is None for r in v1.speakers.values())
    assert [r.person_id for r in v2.speakers.values()] == ["priya", "alex_k"] * 2


def test_enhancer_version_is_selectable_and_validated() -> None:
    assert real_enhancer().name == "enhance-v1"
    assert real_enhancer("enhance-v2").name == "enhance-v2"
    with pytest.raises(ValueError):
        real_enhancer("enhance-v9")
