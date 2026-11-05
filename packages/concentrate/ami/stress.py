"""Stress audio for the capture track: condition A1, a long digital silence spliced mid-meeting.

A1 asks whether a product's capture resumes when speech returns after a long silence (an
inactivity timeout would stop it), and whether the user is told. Granola's help docs (read
2026-11-05) say transcription stops after 15 minutes without new audio, so A1 inserts 17 min;
A1c inserts 12 min, below that timeout, as a control. We build the input from an
AMI headset mix with ``wave`` only:

1. **Where to cut.** At a turn boundary: the longest stretch with no AMI word being spoken
   (all speakers merged, from the forced-alignment timings) within ``SEARCH_S`` of the target
   time. The default target is the middle of the speech; with ``min_after_s`` the cut must also
   leave at least that much voiced speech after it (union of word intervals) to watch capture
   resume. The cut is snapped to a sample.
2. **What to insert.** ``silence_s`` of digital zeros (exact silence, not room tone).
3. **A sidecar JSON** records the exact offsets and checksums, so scoring maps the stressed
   timeline back to AMI time: ``ami = t`` before the cut, ``ami = t - silence_s`` after it.

Scoring (:func:`score_stress`) reuses the capture-coverage windows on the shifted timeline and
reports coverage before and after the silence, whether capture resumed and how fast.
Everything is deterministic: the same inputs give byte-identical WAV and sidecar.
"""

from __future__ import annotations

import hashlib
import wave
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from concentrate.ami import coverage as C
from concentrate.ami.corpus import AmiMeeting, Word

SEARCH_S = 60.0  # how far from the target time to look for a turn boundary
MIN_AFTER_S = 600.0  # A1 default: at least 10 min of speech after the cut
# condition -> default minutes of silence: A1 crosses a documented 15-min no-audio timeout,
# A1c (control) stays below it.
CONDITIONS = {"A1": 17.0, "A1c": 12.0}


class Sidecar(BaseModel):
    """Exact splice offsets for one stressed WAV. Times in seconds."""

    model_config = ConfigDict(frozen=True)

    meeting_id: str
    condition: str
    source_wav: str
    source_sha256: str
    output_wav: str
    output_sha256: str
    sample_rate: int
    splice_sample: int  # silence starts at this sample of the source
    splice_at_s: float  # = splice_sample / sample_rate, in AMI time
    silence_s: float
    boundary: tuple[float, float]  # the no-speech stretch the cut sits in (AMI time)
    source_duration_s: float
    output_duration_s: float
    speech_after_s: float  # voiced time after the cut: union of AMI word intervals
    rule: str

    def to_ami(self, t: float) -> float | None:
        """Stressed-timeline time -> AMI time; None inside the inserted silence."""
        if t < self.splice_at_s:
            return t
        if t < self.splice_at_s + self.silence_s:
            return None
        return t - self.silence_s

    def to_stress(self, t: float) -> float:
        """AMI time -> stressed-timeline time."""
        return t if t < self.splice_at_s else t + self.silence_s


def silences(words: Sequence[Word]) -> list[tuple[float, float]]:
    """(start, end) of every stretch where no word is being spoken, all speakers merged."""
    out: list[tuple[float, float]] = []
    busy_until: float | None = None
    for w in sorted(words, key=lambda x: x.start):
        if busy_until is not None and w.start > busy_until:
            out.append((busy_until, w.start))
        busy_until = w.end if busy_until is None else max(busy_until, w.end)
    return out


def voiced(words: Sequence[Word], since: float = 0.0) -> float:
    """Seconds during which at least one word starting at or after ``since`` is spoken."""
    later = sorted((w for w in words if w.start >= since), key=lambda w: w.start)
    if not later:
        return 0.0
    gaps = sum(b - a for a, b in silences(later))
    return max(w.end for w in later) - later[0].start - gaps


def pick_boundary(
    words: Sequence[Word],
    at_s: float | None = None,
    *,
    min_after_s: float = 0.0,
    search_s: float = SEARCH_S,
) -> tuple[float, float]:
    """The no-speech stretch to cut in: the longest within ``search_s`` of the target.

    Target: ``at_s``, or the middle of the speech (first word start to last word end). Only
    stretches with at least ``min_after_s`` of speech (:func:`voiced`) after them qualify.
    Ties go to the earlier stretch. Raises ValueError if none qualifies.
    """
    spoken = sorted(words, key=lambda w: w.start)
    if not spoken:
        raise ValueError("no words")
    last = max(w.end for w in spoken)
    target = at_s if at_s is not None else (spoken[0].start + last) / 2
    ok = [
        (a, b)
        for a, b in silences(spoken)
        if abs((a + b) / 2 - target) <= search_s and voiced(spoken, b) >= min_after_s
    ]
    if not ok:
        raise ValueError(
            f"no turn boundary within {search_s:.0f} s of {target:.0f} s leaving "
            f"{min_after_s:.0f} s of speech after it"
        )
    return max(ok, key=lambda s: (round(s[1] - s[0], 3), -s[0]))


def default_meeting(meetings: Mapping[str, AmiMeeting], min_after_s: float = MIN_AFTER_S) -> str:
    """The shortest meeting whose middle turn boundary leaves ``min_after_s`` after it."""
    for mid in sorted(meetings, key=lambda m: (meetings[m].duration_s, m)):
        try:
            pick_boundary(meetings[mid].spoken(), min_after_s=min_after_s)
        except ValueError:
            continue
        return mid
    raise ValueError(f"no meeting leaves {min_after_s:.0f} s after its middle")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def splice_silence(src: Path, dst: Path, at_sample: int, silence_samples: int) -> None:
    """Write ``src`` with ``silence_samples`` of digital zeros inserted at ``at_sample``."""
    with wave.open(str(src), "rb") as r:
        params = r.getparams()
        if params.comptype != "NONE":
            raise ValueError(f"{src}: compressed WAV not supported")
        if not 0 <= at_sample <= params.nframes:
            raise ValueError(f"splice sample {at_sample} outside 0..{params.nframes}")
        head = r.readframes(at_sample)
        tail = r.readframes(params.nframes - at_sample)
    frame = params.sampwidth * params.nchannels
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(params.nchannels)
        w.setsampwidth(params.sampwidth)
        w.setframerate(params.framerate)
        w.writeframes(head + b"\x00" * (silence_samples * frame) + tail)


def build_a1(
    meeting: AmiMeeting,
    src: Path,
    out_dir: Path,
    *,
    silence_s: float,
    at_s: float | None = None,
    min_after_s: float = MIN_AFTER_S,
    condition: str = "A1",
) -> Sidecar:
    """Build a silence-splice WAV and its sidecar (``<id>.<condition>.wav`` / ``.json``)."""
    words = meeting.spoken()
    a, b = pick_boundary(words, at_s, min_after_s=0.0 if at_s is not None else min_after_s)
    with wave.open(str(src), "rb") as r:
        rate, nframes = r.getframerate(), r.getnframes()
    sample = round((a + b) / 2 * rate)
    n_silence = round(silence_s * rate)
    dst = out_dir / f"{meeting.meeting_id}.{condition}.wav"
    splice_silence(src, dst, sample, n_silence)
    rule = (
        f"cut at the middle of the longest no-speech stretch within {SEARCH_S:.0f} s of "
        + (f"{at_s:.0f} s" if at_s is not None else "the middle of the speech")
        + (f", leaving >= {min_after_s:.0f} s of speech after it" if at_s is None else "")
    )
    side = Sidecar(
        meeting_id=meeting.meeting_id,
        condition=condition,
        source_wav=str(src),
        source_sha256=sha256(src),
        output_wav=str(dst),
        output_sha256=sha256(dst),
        sample_rate=rate,
        splice_sample=sample,
        splice_at_s=sample / rate,
        silence_s=n_silence / rate,
        boundary=(round(a, 3), round(b, 3)),
        source_duration_s=nframes / rate,
        output_duration_s=(nframes + n_silence) / rate,
        speech_after_s=round(voiced(words, sample / rate), 3),
        rule=rule,
    )
    sidecar = out_dir / f"{meeting.meeting_id}.{condition}.json"
    sidecar.write_text(side.model_dump_json(indent=1) + "\n")
    return side


class StressCoverage(BaseModel):
    """Capture coverage of one product on a stressed timeline (times on that timeline)."""

    model_config = ConfigDict(frozen=True)

    coverage_before: float  # share of windows captured, before the silence
    coverage_after: float  # after the silence
    windows_before: int
    windows_after: int
    resumed: bool  # at least one window captured after the silence
    time_to_resume_s: float | None  # first captured window after - first word after the silence
    gaps: list[tuple[float, float]]  # (start, length) uncaptured runs >= MIN_GAP_S, stressed time
    windows: list[tuple[float, float]]


def shift_words(words: Sequence[Word], side: Sidecar) -> list[Word]:
    """AMI words moved onto the stressed timeline."""
    return [
        w.model_copy(update={"start": side.to_stress(w.start), "end": side.to_stress(w.end)})
        if w.start >= side.splice_at_s
        else w
        for w in words
    ]


def score_stress(
    words: Sequence[Word],
    product_text: str,
    side: Sidecar,
    threshold: float = C.THRESHOLD,
    window_s: float = C.WINDOW_S,
) -> StressCoverage:
    """Coverage windows on the stressed timeline, split at the inserted silence."""
    moved = shift_words(words, side)
    rows = C.window_shares(moved, product_text, window_s)
    resume_at = side.splice_at_s + side.silence_s
    before = [s for t, s in rows if t < side.splice_at_s]
    after = [(t, s) for t, s in rows if t >= resume_at]
    first_word_after = next((w.start for w in moved if w.start >= resume_at), None)
    first_caught = next((t for t, s in after if s >= threshold), None)

    def share(xs: Sequence[float]) -> float:
        return round(sum(x >= threshold for x in xs) / len(xs), 3) if xs else 0.0

    return StressCoverage(
        coverage_before=share(before),
        coverage_after=share([s for _, s in after]),
        windows_before=len(before),
        windows_after=len(after),
        resumed=first_caught is not None,
        time_to_resume_s=(
            round(first_caught - first_word_after, 1)
            if first_caught is not None and first_word_after is not None
            else None
        ),
        gaps=C.find_gaps(rows, threshold, window_s),
        windows=rows,
    )
