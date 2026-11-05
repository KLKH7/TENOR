"""Capture-health detector: flag a silent capture failure live, without storing audio.

A meeting product already has two signals while it records:

- **(a) is someone speaking?** Voice activity detection (VAD) on the incoming audio;
- **(b) is the transcript growing?** The times at which new transcript blocks appear.

**Rule.** At second ``t``, raise "capture may have stopped" if no transcript block started in
the last ``T`` seconds *and* the VAD heard at least ``min_speech * T`` seconds of speech in
that same stretch. One alert per silence of the transcript (it re-arms when a block arrives).
Silence in the room never fires it, so a long pause (stress condition A1) is not a false alarm.

**VAD.** Two variants, to separate VAD error from rule error:

- ``energy``: a plain energy detector. 20 ms frames of the mono mix, RMS in dBFS. A frame is
  voiced when it is ``margin_db`` above a *causal* noise floor (the ``FLOOR_PCTL`` percentile
  of the previous ``FLOOR_WINDOW_S`` seconds of frames) and above ``MIN_DBFS``. Per second, the
  voiced share of its 50 frames. Only ``margin_db`` is tuned (on DEV);
- ``oracle``: AMI's human word timings as a perfect VAD (per second, the share covered by a
  word). Not available to a product; it bounds what a better VAD could do.

**Evaluation** (:func:`evaluate`). Product timing comes from Granola's transcript block start
times (``[m:ss]``, 1 s resolution), moved onto AMI time by one constant shift estimated from
unique word triples (:func:`estimate_shift`). Ground truth is the capture-coverage scorer
(:mod:`concentrate.ami.coverage`): a failure is real when that scorer finds an uncaptured run
of 30 s or more. Because the baseline replays have almost no real failures, failures are also
**injected**: a seeded stretch (30 s to 5 min) of the product transcript is deleted, the
coverage scorer re-run to confirm the stretch is now missed, and the detector run on the
remaining blocks. Measures: recall on confirmed faults, detection delay (alert time minus fault
start), and false alarms per hour on the *unmodified* transcripts (an alert counts as true only
if its silent stretch overlaps a real coverage gap).

Everything is seeded and deterministic; results hold numbers and AMI meeting IDs only.
"""

from __future__ import annotations

import math
import random
import wave
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict

from concentrate.ami import coverage as C
from concentrate.ami.corpus import AmiMeeting, Word
from concentrate.ami.wer import normalise
from concentrate.core.models import Channel, MeetingNote
from concentrate.sources.granola_mcp import WORDS_PER_SECOND

FRAME_S = 0.02
FRAMES_PER_S = 50
FLOOR_WINDOW_S = 120
FLOOR_PCTL = 10.0
MIN_DBFS = -60.0
SILENT_DBFS = -100.0  # digital zeros clamp here
FAULT_MIN_S = 30
FAULT_MAX_S = 300
# Tuning grid, searched on DEV only.
GRID_T = (10, 15, 20, 30, 45, 60)
GRID_MIN_SPEECH = (0.1, 0.25, 0.5)
GRID_MARGIN_DB = (3.0, 6.0, 10.0, 15.0)
FA_BUDGET_PER_H = 2.0  # DEV selection: false alarms per hour must not exceed this
METHOD = (
    "Alert at second t if no product transcript block started in (t-T, t] and the VAD heard "
    ">= min_speech*T s of speech in that stretch; one alert per transcript silence. Energy VAD: "
    "20 ms frames, voiced if > margin_db above the causal 10th-percentile floor of the previous "
    "120 s and > -60 dBFS. Oracle VAD: AMI word timings. Ground truth: coverage-scorer gaps "
    ">= 30 s. Faults: one seeded deletion (30-300 s) of product blocks per trial."
)


# -- signals -----------------------------------------------------------------------------------


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Mono 16-bit PCM -> float samples in [-1, 1], sample rate."""
    with wave.open(str(path), "rb") as r:
        if r.getnchannels() != 1 or r.getsampwidth() != 2:
            raise ValueError(f"{path}: expected mono 16-bit PCM")
        rate = r.getframerate()
        raw = r.readframes(r.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0, rate


def frame_dbfs(samples: np.ndarray, rate: int) -> np.ndarray:
    """RMS level of each 20 ms frame in dBFS, shaped (seconds, 50); a trailing part second is
    dropped. Digital silence clamps to ``SILENT_DBFS``."""
    per = round(rate * FRAME_S)
    n_sec = len(samples) // (per * FRAMES_PER_S)
    x = samples[: n_sec * per * FRAMES_PER_S].reshape(n_sec * FRAMES_PER_S, per)
    power = np.mean(x * x, axis=1)
    db = 10.0 * np.log10(np.maximum(power, 10.0 ** (SILENT_DBFS / 10.0)))
    out: np.ndarray = db.reshape(n_sec, FRAMES_PER_S)
    return out


def causal_floor(frames_db: np.ndarray, window_s: int = FLOOR_WINDOW_S) -> np.ndarray:
    """Per second, the ``FLOOR_PCTL`` percentile of frames in the previous ``window_s`` seconds
    (this one included): a noise floor a live product could compute."""
    n = frames_db.shape[0]
    return np.array(
        [np.percentile(frames_db[max(0, s - window_s + 1) : s + 1], FLOOR_PCTL) for s in range(n)]
    )


def energy_vad(frames_db: np.ndarray, floor_db: np.ndarray, margin_db: float) -> np.ndarray:
    """Per second, the share of 20 ms frames voiced (above floor + margin and ``MIN_DBFS``)."""
    thr = np.maximum(floor_db + margin_db, MIN_DBFS)[:, None]
    out: np.ndarray = np.mean(frames_db > thr, axis=1)
    return out


def oracle_vad(words: Iterable[Word], n_sec: int) -> np.ndarray:
    """Per second, the share covered by at least one AMI word (all speakers merged)."""
    cover = np.zeros(n_sec * 100, dtype=bool)  # 10 ms grid
    for w in words:
        a, b = max(0, round(w.start * 100)), min(n_sec * 100, round(w.end * 100))
        if b > a:
            cover[a:b] = True
    out: np.ndarray = cover.reshape(n_sec, 100).mean(axis=1)
    return out


def estimate_shift(words: Sequence[Word], blocks: Sequence[tuple[float, str]]) -> tuple[float, int]:
    """Constant offset from product time to AMI time: the median, over product blocks, of
    (AMI start of the earliest word triple the block shares, unique in the meeting) minus the
    block's time. Returns (shift in s, blocks matched)."""
    flat = [(t, w.start) for w in words for t in normalise(w.text)]
    seen: Counter[tuple[str, str, str]] = Counter()
    first: dict[tuple[str, str, str], float] = {}
    for (a, s), (b, _), (c, _) in zip(flat, flat[1:], flat[2:], strict=False):
        seen[(a, b, c)] += 1
        first.setdefault((a, b, c), s)
    diffs = []
    for t, text in blocks:
        n = normalise(text)
        hits = [first[k] for k in zip(n, n[1:], n[2:], strict=False) if k in first and seen[k] == 1]
        if hits:
            diffs.append(min(hits) - t)
    if not diffs:
        raise ValueError("no product block shares a unique word triple with the meeting")
    return round(median(diffs), 1), len(diffs)


# -- the rule ----------------------------------------------------------------------------------


class Alert(BaseModel):
    """One 'capture may have stopped' alert. Times in whole seconds."""

    model_config = ConfigDict(frozen=True)

    onset: int  # second the alert fires
    since: int  # last transcript growth before it (0 if none: the recording start)
    until: int  # next growth after it, or the end of the signal


def growth_times(blocks: Sequence[tuple[float, int]], mode: str) -> list[float]:
    """When the transcript grew, from (block start, words) pairs sorted by start.

    ``start``: at each block's start only (what the synced transcript records). ``span``: also
    every second while the block is being spoken, ending at the next block or after
    ``words / WORDS_PER_SECOND`` seconds, whichever is first (an estimate of a live stream that
    shows words as they come; the speech rate is ASSUMED).
    """
    if mode == "start":
        return [t for t, _ in blocks]
    if mode != "span":
        raise ValueError(f"unknown growth mode {mode!r}")
    out: list[float] = []
    for i, (t, words) in enumerate(blocks):
        end = t + words / WORDS_PER_SECOND
        if i + 1 < len(blocks):
            end = min(end, blocks[i + 1][0])
        out.extend(t + k for k in range(max(1, math.ceil(end - t))))
    return out


def alerts(growth: Iterable[float], vad: np.ndarray, t_s: int, min_speech: float) -> list[Alert]:
    """Run the rule over a whole signal. ``growth``: times (s) the transcript grew; ``vad``:
    per-second speech share. Causal: the alert at ``t`` uses only growth and audio up to ``t``."""
    n = len(vad)
    has = np.zeros(n, dtype=bool)
    for b in growth:
        if 0 <= b < n:
            has[int(b)] = True
    cum = np.concatenate([[0.0], np.cumsum(vad)])
    need = min_speech * t_s
    onsets: list[tuple[int, int]] = []
    last, fired = 0, False
    for t in range(n):
        if has[t]:
            last, fired = t, False
        elif not fired and t - last >= t_s and cum[t + 1] - cum[t + 1 - t_s] >= need - 1e-9:
            onsets.append((t, last))
            fired = True
    nxt = np.flatnonzero(has)
    out = []
    for onset, since in onsets:
        later = nxt[nxt > onset]
        out.append(Alert(onset=onset, since=since, until=int(later[0]) if len(later) else n))
    return out


# -- faults ------------------------------------------------------------------------------------


def sample_faults(
    seed: int, meeting_id: str, trials: int, first: float, last: float
) -> list[tuple[int, int]]:
    """``trials`` (start, length) faults in whole seconds, each from its own seeded RNG: length
    uniform in [FAULT_MIN_S, FAULT_MAX_S], start uniform so the fault lies within the speech."""
    out = []
    for i in range(trials):
        rng = random.Random(f"{seed}:{meeting_id}:{i}")
        length = rng.randint(FAULT_MIN_S, FAULT_MAX_S)
        hi = max(int(first), int(last) - length)
        out.append((rng.randint(math.ceil(first), hi), length))
    return out


def drop_blocks(
    blocks: Sequence[tuple[float, str]], start: float, length: float
) -> list[tuple[float, str]]:
    """The product transcript after a silent failure: blocks starting in [start, start+length)
    are gone."""
    return [(t, x) for t, x in blocks if not start <= t < start + length]


def overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    return a0 < b1 and b0 < a1


# -- evaluation --------------------------------------------------------------------------------


@dataclass(frozen=True)
class MeetingSignals:
    """Everything the detector and its ground truth need for one meeting (AMI time)."""

    meeting_id: str
    words: tuple[Word, ...]
    blocks: tuple[tuple[float, str], ...]  # product block (AMI time, text), mic turns dropped
    oracle: np.ndarray
    frames_db: np.ndarray
    floor_db: np.ndarray
    shift_s: float = 0.0
    matched: int = 0

    @property
    def n_sec(self) -> int:
        return int(self.frames_db.shape[0])

    @property
    def speech_span_h(self) -> float:
        """Hours from the first AMI word to the last: the exposure for false alarms."""
        return (self.words[-1].end - self.words[0].start) / 3600.0


def product_blocks(note: MeetingNote) -> list[tuple[float, str]]:
    """(seconds since the transcript's first block, text) per block; microphone turns dropped
    (AMI reaches the product as system audio only)."""
    us = sorted(note.utterances, key=lambda u: u.index)
    if not us:
        return []
    t0 = min(u.start_time for u in us)
    return [
        ((u.start_time - t0).total_seconds(), u.text) for u in us if u.channel is not Channel.ME
    ]


def load_signals(meeting: AmiMeeting, wav: Path, note: MeetingNote) -> MeetingSignals:
    """Read one meeting's audio and product transcript onto AMI time."""
    samples, rate = read_wav(wav)
    frames = frame_dbfs(samples, rate)
    words = tuple(meeting.spoken())
    raw = product_blocks(note)
    shift, matched = estimate_shift(words, raw)
    return MeetingSignals(
        meeting_id=meeting.meeting_id,
        words=words,
        blocks=tuple((round(t + shift, 1), x) for t, x in raw),
        oracle=oracle_vad(words, frames.shape[0]),
        frames_db=frames,
        floor_db=causal_floor(frames),
        shift_s=shift,
        matched=matched,
    )


def timed(blocks: Iterable[tuple[float, str]]) -> list[tuple[float, int]]:
    """(start, word count) per block."""
    return [(t, len(x.split())) for t, x in blocks]


def baseline_gaps(sig: MeetingSignals) -> list[tuple[float, float]]:
    """Coverage-scorer gaps (start, length) of the unmodified product transcript."""
    return C.score(sig.words, " ".join(x for _, x in sig.blocks)).gaps


@dataclass(frozen=True)
class Fault:
    meeting_id: str
    start: int
    length: int
    confirmed: bool  # the coverage scorer finds a >= 30 s gap overlapping it
    blocks: tuple[tuple[float, int], ...]  # remaining blocks: (start, words)


def make_faults(sig: MeetingSignals, seed: int, trials: int) -> list[Fault]:
    """Seeded faults for one meeting, each checked against the coverage scorer."""
    out = []
    for a, length in sample_faults(
        seed, sig.meeting_id, trials, sig.words[0].start, sig.words[-1].end
    ):
        kept = drop_blocks(sig.blocks, a, length)
        gaps = C.score(sig.words, " ".join(x for _, x in kept)).gaps
        hit = any(overlaps(g, g + n, a, a + length) for g, n in gaps)
        out.append(Fault(sig.meeting_id, a, length, hit, tuple(timed(kept))))
    return out


def detect(fault: Fault, al: Sequence[Alert]) -> int | None:
    """Detection delay (s) for one fault: the first alert whose silent stretch overlaps the
    fault and that fires by the fault's end; None if missed."""
    end = fault.start + fault.length
    hits = [x.onset for x in al if x.onset <= end and x.until > fault.start]
    return max(0, min(hits) - fault.start) if hits else None


def false_alarms(al: Sequence[Alert], gaps: Sequence[tuple[float, float]]) -> int:
    """Alerts on an unmodified transcript whose silent stretch overlaps no real coverage gap."""
    return sum(not any(overlaps(x.since, x.until, g, g + n) for g, n in gaps) for x in al)


class Params(BaseModel):
    model_config = ConfigDict(frozen=True)

    t_s: int
    min_speech: float
    margin_db: float | None = None  # energy VAD only


VARIANTS = ("energy/start", "oracle/start", "energy/span", "oracle/span")  # VAD/growth


def vad_for(sig: MeetingSignals, variant: str, p: Params) -> np.ndarray:
    if variant.split("/")[0] == "oracle":
        return sig.oracle
    if p.margin_db is None:
        raise ValueError("energy VAD needs margin_db")
    return energy_vad(sig.frames_db, sig.floor_db, p.margin_db)


def _pct(xs: Sequence[float], q: float) -> float | None:
    return round(float(np.percentile(xs, q)), 1) if xs else None


LENGTH_BINS = ((30, 60), (61, 120), (121, 300))


def score_split(
    sigs: Sequence[MeetingSignals],
    faults: dict[str, list[Fault]],
    variant: str,
    p: Params,
) -> dict[str, Any]:
    """Recall, delay and false alarms of one detector setting over a set of meetings."""
    delays: list[int] = []
    per_meeting: dict[str, dict[str, Any]] = {}
    fa_total, hours, conf_total, det_total, unconf_alerted, unconf_total = 0, 0.0, 0, 0, 0, 0
    by_len: dict[str, list[int]] = {f"{a}-{b}": [0, 0] for a, b in LENGTH_BINS}
    real: list[dict[str, Any]] = []
    for sig in sigs:
        vad = vad_for(sig, variant, p)
        gaps = baseline_gaps(sig)
        mode = variant.split("/")[1]
        base = alerts(growth_times(timed(sig.blocks), mode), vad, p.t_s, p.min_speech)
        fa = false_alarms(base, gaps)
        fa_total += fa
        hours += sig.speech_span_h
        for g, n in gaps:
            hit = [x.onset for x in base if overlaps(x.since, x.until, g, g + n)]
            real.append(
                {"meeting_id": sig.meeting_id, "start_s": g, "length_s": n, "alerted": bool(hit)}
            )
        conf = det = 0
        for f in faults[sig.meeting_id]:
            d = detect(f, alerts(growth_times(f.blocks, mode), vad, p.t_s, p.min_speech))
            if not f.confirmed:
                unconf_total += 1
                unconf_alerted += d is not None
                continue
            conf += 1
            key = next(f"{a}-{b}" for a, b in LENGTH_BINS if a <= f.length <= b)
            by_len[key][0] += 1
            if d is not None:
                det += 1
                delays.append(d)
                by_len[key][1] += 1
        conf_total += conf
        det_total += det
        per_meeting[sig.meeting_id] = {
            "recall": round(det / conf, 3) if conf else None,
            "confirmed": conf,
            "false_alarms": fa,
            "hours": round(sig.speech_span_h, 3),
        }
    return {
        "meetings": len(sigs),
        "hours": round(hours, 3),
        "faults_injected": sum(len(faults[s.meeting_id]) for s in sigs),
        "faults_confirmed": conf_total,
        "detected": det_total,
        "recall": round(det_total / conf_total, 3) if conf_total else None,
        "delay_s_median": _pct(delays, 50),
        "delay_s_p90": _pct(delays, 90),
        "false_alarms": fa_total,
        "false_alarms_per_hour": round(fa_total / hours, 2) if hours else None,
        "unconfirmed_faults": unconf_total,
        "unconfirmed_alerted": unconf_alerted,
        "recall_by_length_s": {
            k: {"n": n, "detected": d, "recall": round(d / n, 3) if n else None}
            for k, (n, d) in by_len.items()
        },
        "per_meeting": per_meeting,
        "real_gaps": real,
    }


def grid(variant: str) -> list[Params]:
    energy = variant.startswith("energy")
    margins: Sequence[float | None] = GRID_MARGIN_DB if energy else (None,)
    return [
        Params(t_s=t, min_speech=s, margin_db=m)
        for m in margins
        for t in GRID_T
        for s in GRID_MIN_SPEECH
    ]


def sensitivity(
    sigs: Sequence[MeetingSignals], faults: dict[str, list[Fault]], variant: str, p: Params
) -> dict[str, dict[str, Any]]:
    """The chosen setting with each T of the grid: for reporting only, never for selection."""
    out = {}
    for t in GRID_T:
        r = score_split(sigs, faults, variant, p.model_copy(update={"t_s": t}))
        out[str(t)] = {k: r[k] for k in ("recall", "false_alarms_per_hour", "delay_s_median")}
    return out


def tune(
    sigs: Sequence[MeetingSignals], faults: dict[str, list[Fault]], variant: str
) -> tuple[Params, list[dict[str, Any]]]:
    """Pick the setting on DEV: false alarms per hour within ``FA_BUDGET_PER_H``, then highest
    recall, then shortest median delay, then the most conservative (longer T, more speech)."""
    rows = []
    for p in grid(variant):
        r = score_split(sigs, faults, variant, p)
        rows.append(
            {
                "params": p.model_dump(),
                "recall": r["recall"],
                "false_alarms_per_hour": r["false_alarms_per_hour"],
                "delay_s_median": r["delay_s_median"],
            }
        )
    ok = [r for r in rows if (r["false_alarms_per_hour"] or 0.0) <= FA_BUDGET_PER_H] or rows

    def key(r: dict[str, Any]) -> tuple[float, float, float, float, float]:
        p = r["params"]
        return (
            -(r["recall"] or 0.0),
            r["delay_s_median"] if r["delay_s_median"] is not None else math.inf,
            -p["t_s"],
            -p["min_speech"],
            -(p["margin_db"] or 0.0),
        )

    best = min(ok, key=key)
    return Params(**best["params"]), rows


def vad_agreement(sigs: Sequence[MeetingSignals], margin_db: float) -> dict[str, float]:
    """Per-second agreement of the energy VAD with the oracle (a second is speech if >= half
    of it is voiced / covered)."""
    tp = fp = fn = tn = 0
    for sig in sigs:
        e = energy_vad(sig.frames_db, sig.floor_db, margin_db) >= 0.5
        o = sig.oracle >= 0.5
        tp += int(np.sum(e & o))
        fp += int(np.sum(e & ~o))
        fn += int(np.sum(~e & o))
        tn += int(np.sum(~e & ~o))
    return {
        "precision": round(tp / (tp + fp), 3) if tp + fp else 0.0,
        "recall": round(tp / (tp + fn), 3) if tp + fn else 0.0,
        "seconds": tp + fp + fn + tn,
    }


def example(
    sig: MeetingSignals,
    fault: Fault,
    params: dict[str, Params],
    pad_before: int = 90,
    pad_after: int = 60,
) -> dict[str, Any]:
    """One fault's timeline, numbers only, for the figure."""
    t0 = max(0, fault.start - pad_before)
    t1 = min(sig.n_sec, fault.start + fault.length + pad_after)
    e = energy_vad(sig.frames_db, sig.floor_db, params["energy/start"].margin_db or 0.0)
    onsets = {}
    for v, p in params.items():
        growth = growth_times(fault.blocks, v.split("/")[1])
        al = alerts(growth, vad_for(sig, v, p), p.t_s, p.min_speech)
        d = detect(fault, al)
        onsets[v] = None if d is None else fault.start + d
    return {
        "meeting_id": sig.meeting_id,
        "window_s": [t0, t1],
        "fault": [fault.start, fault.length],
        "oracle": [round(float(x), 2) for x in sig.oracle[t0:t1]],
        "energy": [round(float(x), 2) for x in e[t0:t1]],
        "blocks_kept": [round(t, 1) for t, _ in fault.blocks if t0 <= t < t1],
        "blocks_deleted": [
            round(t, 1) for t, _ in sig.blocks if fault.start <= t < fault.start + fault.length
        ],
        "alert_onset": onsets,
    }
