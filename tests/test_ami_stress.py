"""A1 stress audio: splice offsets, determinism, shifted-timeline scoring, note matching."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from tenor.ami import stress as S
from tenor.ami.corpus import AmiMeeting, Word
from tenor.ami.products import ami_tag, match_recordings, match_stress, read_overrides
from tests.memory_helpers import make_note

RATE = 100  # a tiny sample rate keeps test WAVs small


def _phrase(i: int) -> str:
    """Five made-up content words, unique to phrase ``i``."""
    return " ".join(f"word{chr(97 + i)}{chr(97 + j)}" for j in range(5))


def _words(spans: list[tuple[float, str]], step: float = 1.0) -> list[Word]:
    """Each phrase starts at its time; one word per ``step`` seconds, each 0.8 s long."""
    out = []
    for t, phrase in spans:
        for i, tok in enumerate(phrase.split()):
            out.append(Word(agent="A", start=t + i * step, end=t + i * step + 0.8, text=tok))
    return out


def _meeting(mid: str, words: list[Word], duration: int) -> AmiMeeting:
    return AmiMeeting(
        meeting_id=mid, type="scenario", duration_s=duration, speakers=(), words=tuple(words),
        summary={},
    )  # fmt: skip


def _wav(path: Path, seconds: int) -> np.ndarray:
    samples = (np.arange(seconds * RATE) % 200 + 1).astype("<i2")  # never zero
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(samples.tobytes())
    return samples


def test_silences_merge_overlapping_speakers() -> None:
    words = [
        Word(agent="A", start=0.0, end=2.0, text="a"),
        Word(agent="B", start=1.0, end=3.0, text="b"),  # overlaps A
        Word(agent="A", start=5.0, end=6.0, text="c"),
        Word(agent="B", start=6.5, end=7.0, text="d"),
    ]
    assert S.silences(words) == [(3.0, 5.0), (6.0, 6.5)]


def test_pick_boundary_takes_the_longest_pause_near_the_target() -> None:
    words = _words([(0, "a b c"), (5, "d"), (12, "e f"), (16, "g h i j")])
    # pauses: 2.8-5 (2.2 s), 5.8-12 (6.2 s), 13.8-16 (2.2 s); middle of speech = 9.4 s
    assert S.pick_boundary(words, search_s=5) == (5.8, 12.0)
    assert S.pick_boundary(words, at_s=15, search_s=1.5) == (13.8, 16.0)
    with pytest.raises(ValueError):
        S.pick_boundary(words, min_after_s=100)


def test_voiced_counts_speech_not_pauses() -> None:
    words = _words([(0, "a b"), (10, "c d")])  # 0-0.8, 1-1.8, 10-10.8, 11-11.8
    assert S.voiced(words) == pytest.approx(3.2) and S.voiced(words, 5) == pytest.approx(1.6)


def test_default_meeting_is_the_shortest_with_enough_speech_after() -> None:
    short = _meeting("XX0001a", _words([(0, "a b"), (10, "c d e f g h")]), 20)
    long = _meeting("XX0002a", _words([(0, "a b"), (30, "c d e f g h i j k l")]), 70)
    pair = {"XX0001a": short, "XX0002a": long}
    assert S.default_meeting(pair, min_after_s=4) == "XX0001a"  # 6 words x 0.8 s after
    assert S.default_meeting(pair, min_after_s=6) == "XX0002a"  # 10 words x 0.8 s after


def test_splice_offsets_and_determinism(tmp_path: Path) -> None:
    src = tmp_path / "src.wav"
    original = _wav(src, 40)
    m = _meeting("XX0001a", _words([(1, "a b c"), (20, "d e"), (30, "f g h")]), 40)
    side = S.build_a1(m, src, tmp_path / "out", silence_s=5, min_after_s=0)
    # longest pause near the middle (1..37.8 -> 19.4): 3.8..20, cut at its middle, 11.9 s
    assert side.boundary == (3.8, 20.0) and side.splice_sample == 1190
    assert side.splice_at_s == 11.9 and side.silence_s == 5.0
    with wave.open(side.output_wav, "rb") as r:
        out = np.frombuffer(r.readframes(r.getnframes()), dtype="<i2")
    assert len(out) == len(original) + 500
    assert np.array_equal(out[:1190], original[:1190])
    assert not out[1190:1690].any()
    assert np.array_equal(out[1690:], original[1190:])
    assert side.output_duration_s == 45.0 and side.speech_after_s == 4.0
    again = S.build_a1(m, src, tmp_path / "out2", silence_s=5, min_after_s=0)
    assert again.output_sha256 == side.output_sha256 == S.sha256(Path(side.output_wav))
    assert side.to_ami(5.0) == 5.0 and side.to_ami(13.0) is None and side.to_ami(20.0) == 15.0
    assert side.to_stress(15.0) == 20.0 and side.to_stress(5.0) == 5.0
    reread = S.Sidecar.model_validate_json((tmp_path / "out" / "XX0001a.A1.json").read_text())
    assert reread == side


def _stress_meeting() -> tuple[AmiMeeting, S.Sidecar]:
    spans = [(i * 20.0, _phrase(i)) for i in range(12)]  # 0..225 s, every phrase distinct
    m = _meeting("XX0001a", _words(spans, step=2.0), 240)
    side = S.Sidecar(
        meeting_id="XX0001a", condition="A1", source_wav="s", source_sha256="0", output_wav="o",
        output_sha256="0", sample_rate=RATE, splice_sample=11000, splice_at_s=110.0,
        silence_s=600.0, boundary=(109.0, 120.0), source_duration_s=240.0,
        output_duration_s=840.0, speech_after_s=110.0, rule="test",
    )  # fmt: skip
    return m, side


def test_shift_words_moves_only_post_splice_speech() -> None:
    m, side = _stress_meeting()
    moved = S.shift_words(m.spoken(), side)
    assert [w.start for w in moved if w.start < 110] == [
        w.start for w in m.spoken() if w.start < 110
    ]
    assert min(w.start for w in moved if w.start >= 110) == 720.0  # 120 + 600


def test_score_stress_resumed_and_not_resumed() -> None:
    m, side = _stress_meeting()
    words = m.spoken()
    full = S.score_stress(words, m.reference_text(), side)
    assert full.coverage_before == full.coverage_after == 1.0
    assert full.resumed and full.time_to_resume_s == 0.0 and full.gaps == []
    assert all(t < 110 or t >= 720 for t, _ in full.windows)  # nothing inside the silence
    before_only = " ".join(w.text for w in words if w.start < 110)
    cut = S.score_stress(words, before_only, side)
    assert cut.coverage_before == 1.0 and cut.coverage_after == 0.0 and not cut.resumed
    assert cut.time_to_resume_s is None
    assert cut.gaps and cut.gaps[0][0] == 720.0  # the missed stretch, on the stressed timeline
    # capture comes back 40 s after speech returns
    late = before_only + " " + " ".join(w.text for w in words if w.start >= 160)
    r = S.score_stress(words, late, side)
    assert r.resumed and r.time_to_resume_s == 40.0


def test_stress_notes_are_not_baseline_notes(tmp_path: Path) -> None:
    base = make_note("n1", "2026-11-05", "AMI XX0001a", [("them", None, "hi")])
    a1 = make_note("n2", "2026-11-06", "AMI XX0001a A1", [("them", None, "hi")])
    assert ami_tag(base) == "XX0001a" and ami_tag(a1) is None
    assert list(match_recordings([base, a1])) == ["XX0001a"]
    assert match_stress([base, a1], "XX0001a", "A1") == a1
    assert match_stress([base], "XX0001a", "A1") is None
    a1c = make_note("n4", "2026-11-07", "AMI XX0001a A1c", [("them", None, "hi")])
    assert ami_tag(a1c) is None and match_stress([a1, a1c], "XX0001a", "A1c") == a1c
    assert match_stress([base, a1], "XX0001a", "A1", pinned="n1") == base
    with pytest.raises(ValueError):
        match_stress([a1, a1.model_copy(update={"note_id": "n3"})], "XX0001a", "A1")
    pins = tmp_path / "ids.txt"
    pins.write_text("XX0001a granola=abc granola_a1=def\n")
    assert read_overrides(pins) == {"granola": {"XX0001a": "abc"}, "granola_a1": {"XX0001a": "def"}}
