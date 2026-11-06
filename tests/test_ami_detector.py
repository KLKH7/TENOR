"""Capture-health detector: the rule on synthetic signals, VAD, fault-injection determinism."""

from __future__ import annotations

import numpy as np

from tenor.ami import detector as D
from tenor.ami.corpus import Word


def test_alert_fires_once_when_speech_continues_but_transcript_stops() -> None:
    vad = np.ones(300)
    blocks = [float(t) for t in range(0, 100, 5)] + [200.0, 205.0]
    al = D.alerts(blocks, vad, t_s=20, min_speech=0.5)
    assert [(a.onset, a.since, a.until) for a in al] == [(115, 95, 200), (225, 205, 300)]


def test_no_alert_in_silence() -> None:
    """A long pause (stress condition A1) is not a failure: nobody is speaking."""
    vad = np.ones(300)
    vad[100:200] = 0.0
    blocks = [float(t) for t in range(0, 100, 5)] + [float(t) for t in range(200, 300, 5)]
    assert D.alerts(blocks, vad, t_s=20, min_speech=0.5) == []
    # ...but if capture never came back, it fires T seconds after speech resumes
    al = D.alerts([float(t) for t in range(0, 100, 5)], vad, t_s=20, min_speech=0.5)
    assert [a.onset for a in al] == [209]  # (189, 209] holds 10 s of speech


def test_min_speech_gates_the_alert() -> None:
    vad = np.full(100, 0.3)
    assert D.alerts([0.0], vad, t_s=10, min_speech=0.5) == []
    assert [a.onset for a in D.alerts([0.0], vad, t_s=10, min_speech=0.25)] == [10]


def test_growth_span_fills_the_block_but_not_the_silence_after() -> None:
    blocks = [(0.0, 26), (5.0, 2), (100.0, 1)]  # 26 words = 10 s at 2.6 words/s
    assert D.growth_times(blocks, "start") == [0.0, 5.0, 100.0]
    assert D.growth_times(blocks, "span") == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 100.0]


def test_energy_vad_on_a_tone_and_digital_silence() -> None:
    rate = 16000
    t = np.arange(10 * rate) / rate
    x = 0.001 * np.sin(2 * np.pi * 200 * t)  # quiet floor
    x[4 * rate : 6 * rate] = 0.3 * np.sin(2 * np.pi * 200 * t[4 * rate : 6 * rate])  # loud
    x[8 * rate :] = 0.0  # digital silence
    frames = D.frame_dbfs(x, rate)
    assert frames.shape == (10, 50) and frames[9].max() == D.SILENT_DBFS
    vad = D.energy_vad(frames, D.causal_floor(frames), margin_db=10.0)
    assert vad[4] == vad[5] == 1.0
    assert vad[:4].max() == 0.0 and vad[8:].max() == 0.0


def test_oracle_vad_covers_word_time() -> None:
    words = [Word(agent="A", start=0.5, end=1.5, text="a"), Word(agent="B", start=3.0, end=3.25,
                                                               text="b")]  # fmt: skip
    assert D.oracle_vad(words, 5).tolist() == [0.5, 0.5, 0.0, 0.25, 0.0]


def _signals() -> D.MeetingSignals:
    """Six minutes of speech, one distinct phrase per 10 s; product blocks match it 2 s late."""
    words, blocks = [], []
    for i in range(36):
        phrase = [f"term{chr(97 + i // 26)}{chr(97 + i % 26)}{chr(97 + j)}" for j in range(6)]
        t0 = 10.0 * i
        words += [
            Word(agent="A", start=t0 + j, end=t0 + j + 0.9, text=w) for j, w in enumerate(phrase)
        ]
        blocks.append((t0 + 2.0, " ".join(phrase)))
    n = 370
    frames = np.full((n, D.FRAMES_PER_S), -50.0)
    oracle = D.oracle_vad(words, n)
    return D.MeetingSignals(
        meeting_id="XX0001a", words=tuple(words), blocks=tuple(blocks), oracle=oracle,
        frames_db=frames, floor_db=np.full(n, -55.0),
    )  # fmt: skip


def test_estimate_shift_recovers_a_constant_offset() -> None:
    sig = _signals()
    product = [(t - 2.0 - 30.0, x) for t, x in sig.blocks]  # product clock starts 30 s late
    assert D.estimate_shift(sig.words, product) == (30.0, 36)


def test_fault_injection_is_deterministic_and_bounded() -> None:
    a = D.sample_faults(7, "XX0001a", 20, 5.0, 900.0)
    assert a == D.sample_faults(7, "XX0001a", 20, 5.0, 900.0)
    assert a != D.sample_faults(8, "XX0001a", 20, 5.0, 900.0)
    assert a[:5] == D.sample_faults(7, "XX0001a", 5, 5.0, 900.0)  # trials are independent
    assert all(D.FAULT_MIN_S <= n <= D.FAULT_MAX_S and s >= 5 and s + n <= 900 for s, n in a)


def test_injected_fault_is_confirmed_and_detected() -> None:
    sig = _signals()
    faults = D.make_faults(sig, seed=3, trials=4)
    assert faults == D.make_faults(sig, seed=3, trials=4)
    fault = D.Fault("XX0001a", 100, 90, True, tuple(D.timed(D.drop_blocks(sig.blocks, 100, 90))))
    al = D.alerts(D.growth_times(fault.blocks, "start"), sig.oracle, t_s=20, min_speech=0.5)
    assert D.detect(fault, al) == 12  # last block at 92, so the alert fires at 112
    clean = D.alerts([t for t, _ in sig.blocks], sig.oracle, t_s=20, min_speech=0.5)
    assert clean == [] and D.false_alarms(clean, []) == 0
    assert D.baseline_gaps(sig) == []
    big = D.make_faults(sig, seed=3, trials=10)
    assert any(f.confirmed for f in big)


def test_score_split_counts_recall_and_false_alarms() -> None:
    sig = _signals()
    faults = {"XX0001a": D.make_faults(sig, seed=1, trials=6)}
    p = D.Params(t_s=20, min_speech=0.5)
    r = D.score_split([sig], faults, "oracle/start", p)
    assert r["faults_injected"] == 6 and r["false_alarms"] == 0
    assert r["recall"] == 1.0 and r == D.score_split([sig], faults, "oracle/start", p)
