"""Speech and ASR noise at controlled, seeded rates.

Applied after polish, to non-protected text only, so planted facts stay verifiable while the
surrounding transcript looks like real speech-to-text: fillers ("um", "uh"), repetitions,
false starts, misheard names (Megan → Meghan), homophone swaps and dropped short words.
Rates are recorded with each fixture set (``NoiseRates``) so results can cite them.
"""

from __future__ import annotations

import random
import re

from pydantic import BaseModel

from concentrate.synth.roster import PEOPLE


class NoiseRates(BaseModel):
    """Per-opportunity probabilities, calibrated on real Granola output
    (docs/benchmark/FIDELITY_V2.md).

    v1 defaults (filler 0.04, repeat 0.015, false starts 0.06) gave 5-8x the real filler rate
    and hyphenated false starts that Granola's ASR never emits; real remote passages also often
    end without punctuation or trail off with "...", and numbers come out as digits, $ and %.
    """

    filler_per_word: float = 0.0035  # um/uh/you know/I mean, inserted before a word
    like_per_word: float = 0.012  # discourse "like" (real speech: ~6 per 100 words, all senses)
    repeat_per_word: float = 0.006
    false_start_per_turn: float = 0.0  # "wo- word": never seen in real Granola transcripts
    name_mishear: float = 0.5  # per mention of a name that has known ASR variants
    homophone: float = 0.05
    drop_short_word: float = 0.01
    no_end_punct: float = 0.12  # per remote passage: final period dropped
    trail_off: float = 0.06  # per remote passage: ends with "..."
    digit_share: float = 0.75  # per turn: numbers written as digits, $ and % (core.spoken)


_FILLERS = ["um", "um", "uh", "uh", "you know", "I mean"]
_HOMOPHONES = {"their": "there", "there": "their", "to": "too", "then": "than", "its": "it's",
               "your": "you're", "know": "no", "right": "write"}  # fmt: skip
_SHORT = {"the", "a", "of", "to", "and", "that", "is"}
_MISHEAR = {
    p.first: [v for v in p.asr_variants if v != p.first] for p in PEOPLE.values() if p.asr_variants
}


def _segments(text: str, protect: tuple[str, ...]) -> list[tuple[str, bool]]:
    """Split ``text`` into (chunk, is_protected) pieces."""
    spans: list[tuple[int, int]] = []
    for p in protect:
        start = text.find(p)
        if start >= 0:
            spans.append((start, start + len(p)))
    spans.sort()
    out, pos = [], 0
    for a, b in spans:
        if a < pos:  # overlapping spans: merge
            a = pos
        if a > pos:
            out.append((text[pos:a], False))
        if b > a:
            out.append((text[a:b], True))
        pos = max(pos, b)
    if pos < len(text):
        out.append((text[pos:], False))
    return out


def _noisy_words(chunk: str, rng: random.Random, r: NoiseRates) -> str:
    words = chunk.split(" ")
    out: list[str] = []
    for w in words:
        core = re.sub(r"\W", "", w)
        if core in _MISHEAR and _MISHEAR[core] and rng.random() < r.name_mishear:
            w = w.replace(core, rng.choice(_MISHEAR[core]))
        elif core.lower() in _HOMOPHONES and rng.random() < r.homophone:
            w = w.replace(core, _HOMOPHONES[core.lower()])
        if core.lower() in _SHORT and rng.random() < r.drop_short_word:
            continue
        if core and rng.random() < r.filler_per_word:  # not before bare punctuation
            out.append(rng.choice(_FILLERS) + ",")
        elif core and out and rng.random() < r.like_per_word:
            out.append("like")
        out.append(w)
        if core and core == w and rng.random() < r.repeat_per_word:  # "the the", not "feel? feel?"
            out.append(w)
    return " ".join(out)


def noisify(text: str, protect: tuple[str, ...], rng: random.Random, rates: NoiseRates) -> str:
    """Apply speech/ASR noise to ``text`` outside ``protect`` spans."""
    pieces = [
        chunk if prot else _noisy_words(chunk, rng, rates)
        for chunk, prot in _segments(text, protect)
    ]
    out = "".join(pieces)
    if rng.random() < rates.false_start_per_turn:
        first = out.split(" ", 1)[0]
        if first and not any(out.startswith(p) for p in protect):
            out = f"{first.rstrip(',.?!')}- {out}"
    return re.sub(r"\s+", " ", out).strip()


def end_style(passage: str, rng: random.Random, rates: NoiseRates) -> str:
    """Real remote passages often stop mid-thought: no final period, or a trailing "..."."""
    if not passage.endswith("."):
        return passage
    r = rng.random()
    if r < rates.trail_off:
        return passage[:-1] + "..."
    if r < rates.trail_off + rates.no_end_punct:
        return passage[:-1]
    return passage


def split_sentences(text: str) -> list[str]:
    """Split a turn into transcript segments, like Granola's per-phrase items."""
    parts = re.split(r"(?<=[.?!])\s+", text.strip())
    return [p for p in parts if p]


def fragment(
    text: str,
    protect: tuple[str, ...],
    rng: random.Random,
    split_rate: float = 0.35,
    keep_whole_rate: float = 0.45,
) -> list[str]:
    """Split a turn into transcript passages shaped like Granola's live (remote) transcripts.

    Real remote passages are bimodal (live probe, 2026-11-02): many are 1-5 words, but a long
    tail are whole monologues (p75 ≈ 17, p90 ≈ 38 words). So a turn is kept whole with
    probability ``keep_whole_rate``; otherwise it is split into sentences, and sentences longer
    than 7 words are, with probability ``split_rate``, cut at one or two word boundaries
    (preferring commas). Cuts never fall inside a protected span.
    """
    if len(text.split()) > 40:  # monologues mostly arrive as one long passage
        keep_whole_rate = max(keep_whole_rate, 0.7)
    if rng.random() < keep_whole_rate:
        return [text.strip()] if text.strip() else []
    out: list[str] = []
    for sent in split_sentences(text):
        words = sent.split(" ")
        if len(words) < 8 or rng.random() >= split_rate:
            out.append(sent)
            continue
        spans = [(sent.find(p), sent.find(p) + len(p)) for p in protect if p in sent]
        bounds = [m.start() for m in re.finditer(" ", sent)]
        ok = [b for b in bounds if not any(a <= b < e for a, e in spans)]
        commas = [b for b in ok if sent[b - 1] == ","]
        pool = commas or ok
        if not pool:
            out.append(sent)
            continue
        cuts = sorted(rng.sample(pool, min(1 if len(words) < 16 else 2, len(pool))))
        start = 0
        for c in [*cuts, len(sent)]:
            piece = sent[start:c].strip()
            if piece.endswith(",") and rng.random() < 0.7:  # ASR passages rarely end on ","
                piece = piece[:-1]
            if piece:
                out.append(piece)
            start = c
    return out


LEAD_INS = ["So", "Yeah", "Um", "Okay", "Right", "I mean", "And"]
