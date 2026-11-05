"""Aggregate style statistics for transcript corpora (docs/archive/northwind/FIDELITY.md,
docs/archive/northwind/FIDELITY_V2.md).

Used to compare the synthetic corpus with real Granola meetings and public meeting corpora.
Only aggregate numbers come out: no text is returned, so the same code can run over private
real meetings (``data/real.db``) without leaking content.

A corpus is a list of meetings; a meeting is a list of :class:`Passage` (one transcript item).
Times are optional (public corpora such as QMSum have none).
"""

from __future__ import annotations

import itertools
import re
import statistics
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from concentrate.core import granola
from concentrate.core.models import Channel
from concentrate.core.store import NoteStore

_TOKEN = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")
_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
    "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
    "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "million", "percent", "dollars",
}  # fmt: skip
_PHRASES = {
    "um_uh": re.compile(r"\b(?:um+|uh+|erm|er)\b"),
    "you_know": re.compile(r"\byou know\b"),
    "i_mean": re.compile(r"\bi mean\b"),
    "like": re.compile(r"\blike\b"),
    "yeah": re.compile(r"\b(?:yeah|yep|yes)\b"),
    "so": re.compile(r"\bso\b"),
}
_FALSE_START = re.compile(r"\b[a-z]+- ")
_REPEAT = re.compile(r"\b([a-z']+) \1\b")


@dataclass(frozen=True)
class Passage:
    """One transcript item: its text and, when known, its timing and channel."""

    text: str
    start: datetime | None = None
    end: datetime | None = None
    source: str | None = None  # "microphone" | "speaker" | None


def tokens(text: str) -> list[str]:
    """Lower-cased word tokens (digits kept, punctuation dropped)."""
    return _TOKEN.findall(text.lower())


def _pct(values: Sequence[float], qs: Iterable[int]) -> list[float]:
    if not values:
        return []
    s = sorted(values)
    return [s[min(len(s) - 1, int(q / 100 * len(s)))] for q in qs]


def _windows(toks: list[str], size: int) -> list[list[str]]:
    return [toks[i : i + size] for i in range(0, len(toks) - size + 1, size)]


def ttr(toks: list[str], size: int) -> float | None:
    """Mean type/token ratio over non-overlapping ``size``-token windows."""
    ws = _windows(toks, size)
    return statistics.fmean(len(set(w)) / size for w in ws) if ws else None


def distinct_trigrams(toks: list[str], size: int = 5000) -> float | None:
    """Mean share of distinct word trigrams over ``size``-token windows."""
    ws = _windows(toks, size)
    if not ws:
        return None
    return statistics.fmean(
        len({tuple(w[i : i + 3]) for i in range(len(w) - 2)}) / (len(w) - 2) for w in ws
    )


def _ending(text: str) -> str:
    t = text.rstrip()
    if t.endswith("..."):
        return "..."
    return t[-1] if t and t[-1] in ".?!," else "none"


def corpus_stats(meetings: Sequence[Sequence[Passage]]) -> dict[str, float | list[float] | None]:
    """Style statistics over ``meetings`` (definitions: docs/archive/northwind/FIDELITY.md)."""
    passages = [p for m in meetings for p in m]
    lengths = [len(tokens(p.text)) for p in passages]
    all_toks = [t for p in passages for t in tokens(p.text)]
    n_words = max(1, len(all_toks))
    joined = " ".join(" ".join(tokens(p.text)) for p in passages)
    raw = " ".join(p.text for p in passages).lower()
    long_keys = [" ".join(tokens(p.text)) for p in passages if len(tokens(p.text)) >= 6]
    exact = Counter(long_keys)
    first6 = Counter(" ".join(k.split()[:6]) for k in long_keys)
    gaps: list[float] = []
    spans: list[float] = []
    for m in meetings:
        timed = [p for p in m if p.start and p.end]
        for a, b in itertools.pairwise(timed):
            assert a.end is not None and b.start is not None
            gaps.append(max(0.0, (b.start - a.end).total_seconds()))
        if timed:
            first, last = timed[0].start, max(p.end for p in timed if p.end)
            assert first is not None
            spans.append((last - first).total_seconds() / 60)
    endings = Counter(_ending(p.text) for p in passages)
    digits = len(re.findall(r"\d+(?:[.,]\d+)?", raw))
    per100 = {k: 100 * len(rx.findall(joined)) / n_words for k, rx in _PHRASES.items()}
    out: dict[str, float | list[float] | None] = {
        "meetings": len(meetings),
        "passages": len(passages),
        "words": len(all_toks),
        "words_per_passage_p10_25_50_75_90": _pct(lengths, (10, 25, 50, 75, 90)),
        "share_le5": sum(n <= 5 for n in lengths) / max(1, len(lengths)),
        "share_ge38": sum(n >= 38 for n in lengths) / max(1, len(lengths)),
        "share_1word": sum(n == 1 for n in lengths) / max(1, len(lengths)),
        "passages_per_meeting_median": statistics.median(len(m) for m in meetings),
        "words_per_meeting_median": statistics.median(
            sum(len(tokens(p.text)) for p in m) for m in meetings
        ),
        "span_minutes_median": statistics.median(spans) if spans else None,
        "gap_s_p10_50_90": _pct(gaps, (10, 50, 90)),
        "ttr_2k": ttr(all_toks, 2000),
        "ttr_5k": ttr(all_toks, 5000),
        "distinct_trigram_5k": distinct_trigrams(all_toks),
        "dup6_exact": sum(c for c in exact.values() if c > 1) / max(1, len(long_keys)),
        "dup6_first6": sum(c for c in first6.values() if c > 1) / max(1, len(long_keys)),
        "false_start_per100": 100 * len(_FALSE_START.findall(raw)) / n_words,
        "repeat_per100": 100 * len(_REPEAT.findall(joined)) / n_words,
        "digits_per1k": 1000 * digits / n_words,
        "number_words_per1k": 1000 * sum(t in _NUMBER_WORDS for t in all_toks) / n_words,
        "dollar_or_pct_per1k": 1000 * len(re.findall(r"\$\d|\d%", raw)) / n_words,
        "ellipsis_share": sum("..." in p.text for p in passages) / max(1, len(passages)),
    }
    out.update({f"{k}_per100": round(v, 3) for k, v in per100.items()})
    for e in (".", "none", "?", ",", "..."):
        out[f"ending_{e}"] = endings[e] / max(1, len(passages))
    return out


def summary_stats(markdowns: Sequence[str]) -> dict[str, float]:
    """Median words, ``#`` sections, bullets and nested bullets per summary."""

    def one(md: str) -> tuple[int, int, int, int]:
        lines = md.splitlines()
        return (
            len(md.split()),
            sum(line.lstrip().startswith("#") for line in lines),
            sum(bool(re.match(r"\s*[-*] ", line)) for line in lines),
            sum(bool(re.match(r"\s{2,}[-*] ", line)) for line in lines),
        )

    rows = [one(md) for md in markdowns if md]
    if not rows:
        return {}
    return {
        name: float(statistics.median(r[i] for r in rows))
        for i, name in enumerate(("words", "sections", "bullets", "nested_bullets"))
    }


Corpus = tuple[list[list[Passage]], list[str]]  # (meetings, summary markdowns)


def fixture_corpus(root: Path) -> Corpus:
    """Meetings and summaries of a fixture set (``<root>/notes/*.json``)."""
    meetings, summaries = [], []
    for path in sorted((root / "notes").glob("*.json")):
        note = granola.Note.model_validate_json(path.read_text())
        meetings.append(
            [
                Passage(t.text, t.start_time, t.end_time, t.speaker.source)
                for t in note.transcript or []
            ]
        )
        summaries.append(note.summary_markdown or "")
    return meetings, summaries


def store_corpus(db: Path) -> Corpus:
    """Meetings and summaries of a synced ``NoteStore`` (e.g. the private ``data/real.db``)."""
    meetings, summaries = [], []
    store = NoteStore(db)
    try:
        for n in store.iter_notes():
            meetings.append(
                [
                    Passage(u.text, u.start_time, u.end_time,
                            "microphone" if u.channel == Channel.ME else "speaker")
                    for u in n.utterances
                ]
            )  # fmt: skip
            summaries.append(n.summary_markdown or "")
    finally:
        store.close()
    return meetings, summaries


def _round(v: object) -> object:
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, list):
        return [_round(x) for x in v]
    if isinstance(v, dict):
        return {k: _round(x) for k, x in v.items()}
    return v


def report(corpus: Corpus) -> dict[str, object]:
    """Aggregate-only report: remote meetings (any system-audio passage), all, summaries."""
    meetings, summaries = corpus
    remote = [m for m in meetings if any(p.source == "speaker" for p in m)]
    out = {
        "remote": corpus_stats(remote) if remote else {},
        "all": corpus_stats(meetings) if meetings else {},
        "summary": summary_stats(summaries),
    }
    return {k: _round(v) for k, v in out.items()}
