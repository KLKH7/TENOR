"""Capture coverage: did a product's transcript catch each 15-second stretch of an AMI meeting?

A silent recording failure (a dropped minute, a stalled capture) leaves a hole in the product's
transcript that whole-meeting WER averages away. Coverage looks for holes directly:

1. Cut AMI's timed human words into windows of ``window_s`` seconds (a window starts at its
   first word and closes once a word starts ``window_s`` or more after that).
2. Per window, take the human words' *distinctive* bigrams: word pairs where at least one word
   is not a stop word and is longer than 3 characters ("the budget", not "it is").
3. The window's share is the fraction of those bigrams found anywhere in the product's
   transcript (order-free, so product timestamps aren't needed). Windows with fewer than
   ``MIN_BIGRAMS`` distinctive bigrams are skipped: too little speech to judge.
4. A window counts as captured if its share is at least ``threshold``; runs of uncaptured
   windows lasting ``MIN_GAP_S`` or more are reported as gaps.

A negative control scores the *human* transcript of an unrelated AMI meeting as if it were the
product's: shared stock phrases alone should give near-zero coverage, so a high control would
mean the measure is too lenient.

Results are numbers and AMI meeting IDs only: no transcript text, note IDs or titles.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import pairwise

from pydantic import BaseModel, ConfigDict

from tenor.ami.corpus import AmiMeeting, Word
from tenor.ami.wer import STOP, normalise

WINDOW_S = 15.0
THRESHOLD = 0.4
MIN_GAP_S = 30.0
MIN_BIGRAMS = 3
METHOD = (
    "Per 15 s window of AMI's timed human words: share of its distinctive bigrams (at least one "
    "word not a stop word and longer than 3 characters) found anywhere in the product "
    "transcript; windows with fewer than 3 such bigrams are skipped; share >= threshold counts "
    "as captured. Control: an unrelated meeting's human transcript scored as the product."
)

Bigram = tuple[str, str]


class Coverage(BaseModel):
    """One transcript scored against one meeting."""

    model_config = ConfigDict(frozen=True)

    windows: list[tuple[float, float]]  # (window start in seconds, share of bigrams caught)
    coverage: float  # share of windows with share >= threshold
    gaps: list[tuple[float, float]]  # (start s, length s) of uncaptured runs >= MIN_GAP_S


class MeetingCoverage(BaseModel):
    model_config = ConfigDict(frozen=True)

    meeting_id: str
    duration_s: int
    products: dict[str, Coverage | None]  # None: not recorded by that product
    control_meeting: str
    control: Coverage


def _distinctive(word: str) -> bool:
    return word not in STOP and len(word) > 3


def distinctive_bigrams(tokens: Sequence[str]) -> set[Bigram]:
    """Adjacent word pairs in which at least one word is distinctive."""
    return {(a, b) for a, b in pairwise(tokens) if _distinctive(a) or _distinctive(b)}


def windows(words: Iterable[Word], window_s: float = WINDOW_S) -> list[tuple[float, list[str]]]:
    """Group timed words into (start, texts) windows of about ``window_s`` seconds."""
    out: list[tuple[float, list[str]]] = []
    cur: list[str] = []
    t0: float | None = None
    for w in words:
        if t0 is None:
            t0 = w.start
        if w.start - t0 >= window_s:
            out.append((t0, cur))
            cur, t0 = [], w.start
        cur.append(w.text)
    if cur and t0 is not None:
        out.append((t0, cur))
    return out


def window_shares(
    words: Iterable[Word], product_text: str, window_s: float = WINDOW_S
) -> list[tuple[float, float]]:
    """(window start, share of its distinctive bigrams found in ``product_text``) per window."""
    have = distinctive_bigrams(normalise(product_text))
    rows: list[tuple[float, float]] = []
    for t0, texts in windows(words, window_s):
        want = distinctive_bigrams(normalise(" ".join(texts)))
        if len(want) < MIN_BIGRAMS:
            continue
        rows.append((round(t0, 1), round(len(want & have) / len(want), 3)))
    return rows


def find_gaps(
    shares: Sequence[tuple[float, float]],
    threshold: float = THRESHOLD,
    window_s: float = WINDOW_S,
    min_gap_s: float = MIN_GAP_S,
) -> list[tuple[float, float]]:
    """(start, length) of each run of consecutive uncaptured windows lasting ``min_gap_s`` or more.

    A run's length runs from its first window's start to its last window's end.
    """
    gaps: list[tuple[float, float]] = []
    run: list[float] = []
    for t0, share in [*shares, (float("inf"), 1.0)]:  # sentinel closes a trailing run
        if share < threshold:
            run.append(t0)
            continue
        if run and run[-1] + window_s - run[0] >= min_gap_s:
            gaps.append((round(run[0], 1), round(run[-1] + window_s - run[0], 1)))
        run = []
    return gaps


def score(
    words: Sequence[Word],
    product_text: str,
    threshold: float = THRESHOLD,
    window_s: float = WINDOW_S,
) -> Coverage:
    """Score one transcript against one meeting's timed words."""
    rows = window_shares(words, product_text, window_s)
    caught = sum(share >= threshold for _, share in rows)
    return Coverage(
        windows=rows,
        coverage=round(caught / len(rows), 3) if rows else 0.0,
        gaps=find_gaps(rows, threshold, window_s),
    )


def control_for(meeting_id: str, meeting_ids: Sequence[str]) -> str:
    """The unrelated meeting used as a negative control: IB4003, or IS1008a for IB4003 itself."""
    pick = "IB4003" if meeting_id != "IB4003" else "IS1008a"
    if pick not in meeting_ids:
        raise ValueError(f"control meeting {pick} not in the set")
    return pick


def score_meeting(
    meeting: AmiMeeting,
    product_texts: dict[str, str | None],
    control: AmiMeeting,
    threshold: float = THRESHOLD,
    window_s: float = WINDOW_S,
) -> MeetingCoverage:
    """Score each product's transcript (None = not recorded) and the control for one meeting."""
    words = meeting.spoken()
    return MeetingCoverage(
        meeting_id=meeting.meeting_id,
        duration_s=meeting.duration_s,
        products={
            p: None if text is None else score(words, text, threshold, window_s)
            for p, text in product_texts.items()
        },
        control_meeting=control.meeting_id,
        control=score(words, control.reference_text(), threshold, window_s),
    )


def pooled(meetings: Sequence[MeetingCoverage], product: str, threshold: float) -> float:
    """Coverage across all of a product's recorded windows, every meeting pooled."""
    shares = [
        s for m in meetings if (c := m.products.get(product)) is not None for _, s in c.windows
    ]
    return round(sum(s >= threshold for s in shares) / len(shares), 3) if shares else 0.0
