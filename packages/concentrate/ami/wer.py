"""Transcription accuracy against AMI's manual words: WER and content-word recall.

**WER** (word error rate) = the fewest word substitutions, deletions and insertions that turn
the product's transcript into the reference, divided by the reference length. It is the
Levenshtein edit distance over words. Lower is better; it can exceed 1 when a product inserts a
lot. Computed row by row in O(len(ref) x len(hyp)) time and O(len(hyp)) memory, vectorised with
numpy (see ``edit_distance``).

WER punishes word *order*. When two AMI speakers overlap, the reference interleaves their words
by start time, while a product may write one speaker's sentence and then the other's: same
words, more edits. So we also report **content-word recall**, which ignores order: of the
reference's content words (stop words and fillers removed), counted with multiplicity, the share
that also appear in the product's transcript.

Both sides go through the same ``normalise`` first, so formatting differences ("10" vs "ten",
"Okay." vs "okay", "uh") are not counted as errors. ``NORMALISER_VERSION`` is logged with every
result; bump it whenever ``normalise`` changes.
"""

from __future__ import annotations

import re
from collections import Counter

import numpy as np
from pydantic import BaseModel, ConfigDict

NORMALISER_VERSION = 1

# Hesitations and backchannel noises. AMI transcribes them; products usually drop them.
FILLERS = frozenset(
    [
        "uh",
        "um",
        "uhm",
        "erm",
        "er",
        "ah",
        "eh",
        "hmm",
        "hm",
        "mm",
        "mmm",
        "mhm",
        "mm-hmm",
        "uh-huh",
        "uh-uh",
        "huh",
        "ooh",
        "oh-oh",
    ]
)
# Function words left out of content-word recall (WER still counts them).
STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "so",
        "if",
        "then",
        "of",
        "to",
        "in",
        "on",
        "at",
        "for",
        "with",
        "from",
        "by",
        "as",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "am",
        "it",
        "its",
        "it's",
        "this",
        "that",
        "these",
        "those",
        "there",
        "here",
        "i",
        "you",
        "he",
        "she",
        "we",
        "they",
        "me",
        "him",
        "her",
        "us",
        "them",
        "my",
        "your",
        "his",
        "our",
        "their",
        "do",
        "does",
        "did",
        "don't",
        "doesn't",
        "didn't",
        "not",
        "no",
        "yes",
        "yeah",
        "okay",
        "just",
        "like",
        "well",
        "have",
        "has",
        "had",
        "i'm",
        "you're",
        "we're",
        "they're",
        "that's",
        "what's",
        "there's",
        "gonna",
        "wanna",
        "can",
        "could",
        "would",
        "should",
        "will",
        "i'll",
        "we'll",
        "you'll",
        "shall",
        "may",
        "might",
        "must",
        "also",
        "very",
        "really",
        "actually",
        "right",
    ]
)
_EQUIV = {"ok": "okay", "alright": "all right"}
_CURRENCY = {"€": "euros", "£": "pounds", "$": "dollars"}
_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["_", "_", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_NUMBER = re.compile(r"([€£$]?)(\d[\d,]*)(?:\.(\d+))?(%?)")
_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)*")


def spell_number(n: int) -> str:
    """Spell a non-negative integer below one billion in words: 125 -> 'one hundred twenty five'."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    for size, word in ((1_000_000, "million"), (1000, "thousand"), (100, "hundred")):
        if n >= size:
            head = f"{spell_number(n // size)} {word}"
            return head if n % size == 0 else f"{head} {spell_number(n % size)}"
    raise AssertionError("unreachable")


def _number_words(m: re.Match[str]) -> str:
    currency, whole, frac, pct = m.groups()
    digits = whole.replace(",", "")
    if len(digits) > 9:
        return " ".join(_ONES[int(d)] for d in digits)
    words = spell_number(int(digits))
    if frac:
        words += " point " + " ".join(_ONES[int(d)] for d in frac)
    if pct:
        words += " percent"
    if currency:
        words += " " + _CURRENCY[currency]
    return f" {words} "


def normalise(text: str) -> list[str]:
    """Lower-case, spell out numbers, drop punctuation and fillers; return the word list."""
    text = _NUMBER.sub(_number_words, text.lower())
    words: list[str] = []
    for raw in text.split():
        if raw.strip('.,!?;:"()[]') in FILLERS:
            continue
        for tok in _TOKEN.findall(raw.replace("-", " ").replace("/", " ")):
            tok = _EQUIV.get(tok, tok)
            if tok not in FILLERS:
                words.extend(tok.split())
    return words


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    """Word-level Levenshtein distance between ``ref`` and ``hyp``.

    Classic dynamic programme: ``row[j]`` is the distance between the first ``i`` reference words
    and the first ``j`` hypothesis words. Substitution and deletion only look at the previous
    row, so they vectorise directly. Insertion looks left within the same row
    (``row[j] = row[j-1] + 1``); that chain is a running minimum of ``tmp[k] + (j - k)``, which
    ``np.minimum.accumulate`` computes in one pass.
    """
    if not ref or not hyp:
        return max(len(ref), len(hyp))
    vocab: dict[str, int] = {}
    h = np.array([vocab.setdefault(w, len(vocab)) for w in hyp], dtype=np.int64)
    idx = np.arange(len(hyp) + 1, dtype=np.int64)
    row = idx.copy()
    tmp = np.empty_like(row)
    for i, word in enumerate(ref, start=1):
        r = vocab.get(word, -1)
        tmp[0] = i
        np.minimum(row[:-1] + (h != r), row[1:] + 1, out=tmp[1:])
        row = np.minimum.accumulate(tmp - idx) + idx
    return int(row[-1])


def content_recall(ref: list[str], hyp: list[str]) -> float:
    """Share of the reference's content words (with multiplicity) found in ``hyp``, any order."""
    want = Counter(w for w in ref if w not in STOP)
    have = Counter(w for w in hyp if w not in STOP)
    total = sum(want.values())
    return sum(min(n, have[w]) for w, n in want.items()) / total if total else 0.0


class WerScore(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref_words: int
    hyp_words: int
    edits: int
    wer: float
    content_recall: float


def score(reference: str, hypothesis: str) -> WerScore:
    """Normalise both transcripts and return WER and content-word recall."""
    ref, hyp = normalise(reference), normalise(hypothesis)
    edits = edit_distance(ref, hyp)
    return WerScore(
        ref_words=len(ref),
        hyp_words=len(hyp),
        edits=edits,
        wer=edits / len(ref) if ref else float(bool(hyp)),
        content_recall=content_recall(ref, hyp),
    )
