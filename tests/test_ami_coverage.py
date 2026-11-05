"""Capture coverage on synthetic timed words (no corpus, no network)."""

from __future__ import annotations

import pytest

from concentrate.ami import coverage as C
from concentrate.ami.corpus import AmiMeeting, Word

TOPICS = (
    "remote control budget twenty euros",
    "rubber buttons feel cheaper today",
    "kinetic battery charger design",
    "fruit vegetable theme marketing",
    "speech recognition chip pricing",
    "yellow banana shaped casing",
)


def _words(phrases: list[str], step: float = 1.0) -> list[Word]:
    """One word per ``step`` seconds, phrases back to back."""
    out, t = [], 0.0
    for phrase in phrases:
        for tok in phrase.split():
            out.append(Word(agent="A", start=t, end=t + 0.5, text=tok))
            t += step
    return out


def _meeting(mid: str, phrases: list[str], step: float = 1.0) -> AmiMeeting:
    return AmiMeeting(
        meeting_id=mid,
        type="scenario",
        duration_s=60,
        speakers=(),
        words=tuple(_words(phrases, step)),
        summary={},
    )


def test_distinctive_bigrams_need_one_content_word() -> None:
    got = C.distinctive_bigrams(["it", "is", "the", "budget", "for", "it"])
    assert got == {("the", "budget"), ("budget", "for")}


def test_windows_split_on_start_time() -> None:
    w = _words(["a b c d e f g h"], step=5.0)  # starts 0, 5, ..., 35
    got = C.windows(w, window_s=15.0)
    assert [t for t, _ in got] == [0.0, 15.0, 30.0]
    assert [len(x) for _, x in got] == [3, 3, 2]


def test_full_transcript_is_fully_covered_and_control_is_not() -> None:
    m = _meeting("XX0001a", [TOPICS[i % 3] for i in range(9)], step=1.5)
    other = _meeting("XX0002a", [TOPICS[3 + i % 3] for i in range(9)])
    row = C.score_meeting(m, {"p": m.reference_text(), "q": None}, other)
    p = row.products["p"]
    assert p is not None and p.coverage == 1.0 and p.gaps == []
    assert row.products["q"] is None
    assert row.control.coverage == 0.0
    assert row.control_meeting == "XX0002a"


def test_dropped_stretch_shows_as_a_gap() -> None:
    # 12 windows of 5 made-up content words, 3 s per word: one window per 15 s.
    letters = "abcdefghijkl"
    unique = [
        " ".join(f"{w}{c}" for w in ("alpha", "bravo", "charlie", "delta", "echo")) for c in letters
    ]
    m = _meeting("XX0001a", unique, step=3.0)
    heard = " ".join(u for i, u in enumerate(unique) if i not in (4, 5, 6))  # 45 s lost
    cov = C.score(m.spoken(), heard)
    assert len(cov.windows) == 12
    assert cov.coverage == pytest.approx(9 / 12, abs=1e-3)
    assert cov.gaps == [(60.0, 45.0)]  # windows 4-6: start 60 s, 45 s long


def test_find_gaps_ignores_short_runs_and_closes_trailing_run() -> None:
    shares = [(0.0, 1.0), (15.0, 0.1), (30.0, 1.0), (45.0, 0.0), (60.0, 0.2)]
    assert C.find_gaps(shares, threshold=0.4, window_s=15.0, min_gap_s=30.0) == [(45.0, 30.0)]


def test_control_and_pooled() -> None:
    ids = ("IS1008a", "IB4003", "EN2002b")
    assert C.control_for("EN2002b", ids) == "IB4003"
    assert C.control_for("IB4003", ids) == "IS1008a"
    a = C.Coverage(windows=[(0.0, 1.0), (15.0, 0.0)], coverage=0.5, gaps=[])
    b = C.Coverage(windows=[(0.0, 0.5), (15.0, 0.9)], coverage=1.0, gaps=[])
    rows = [
        C.MeetingCoverage(
            meeting_id=m, duration_s=30, products={"p": c}, control_meeting="x", control=a
        )
        for m, c in (("m1", a), ("m2", b))
    ]
    assert C.pooled(rows, "p", 0.4) == 0.75
    assert C.pooled(rows, "missing", 0.4) == 0.0
