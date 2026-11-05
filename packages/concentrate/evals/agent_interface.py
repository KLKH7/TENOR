"""The agent-interface quality track: what a meeting product's MCP hands an external agent.

Product-neutral and LLM-free. Every answer goes through the product's black-box adapter
(:mod:`concentrate.blackbox.products`), so any product the black-box track can ask can be
scored here: answer types (Granola's four-way taxonomy), evidence granularity, payload signals
(hedges, coverage caveats, abstention, instructions to the caller), repeat-ask stability and
latency. See docs/tracks/AGENT_INTERFACE.md (the task) and docs/results/AGENT_INTERFACE.md.

Per-question output stays under ``data/real/`` (private); :func:`aggregate` is the committable
view and is checked by :func:`~concentrate.blackbox.kit.check_committable`.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from concentrate.blackbox.base import Product, ProductAnswer
from concentrate.evals.figures import wilson

OUT_DIR = Path("data/real/agent_interface")  # private: per-question rows and repeats

# -- payload anatomy -------------------------------------------------------------------------------

# A citation that points inside a meeting: a line/utterance ID (``not_x:0042``, ``<uuid>:0042``).
_LINE_REF = re.compile(r"[A-Za-z0-9_-]{6,}:\d{3,4}\b")
# Other in-meeting pointers in the payload: transcript chunk IDs or timestamps.
_SPAN_REF = re.compile(r"\bchunk_ids?\b|\[\d{1,2}:\d{2}(?::\d{2})?\]|#t=\d")
# Markers to drop from prose before reading it: markdown links and bracketed line IDs.
_MARKERS = re.compile(r"\[\[\d+\]\]\([^)]*\)|\[[A-Za-z0-9_-]{6,}:\d{3,4}\]|\[\d+\]")
# The payload tells the calling agent to call another tool before answering.
_INSTRUCTS = re.compile(r"\b(?:call|use|run)\s+`?[a-z]+_[a-z_]+`?", re.I)
_WORD = re.compile(r"\w[\w'\u2019-]*")
_TRANSCRIPT_WORD = re.compile(r"\btranscripts?\b", re.I)
_QUOTE = re.compile(r"\u201c([^\u201d]+)\u201d|\"([^\"]+)\"")

# Uncertainty about the answer itself.
_HEDGE = re.compile(
    r"\b(?:may|might|perhaps|possibly|probably|likely|unclear|not clear|appears? to|seems? to"
    r"|it'?s possible|I(?:'m| am) not (?:sure|certain)|uncertain)\b",
    re.I,
)
# A caveat about what was searched: the answer is scoped to what the product could see.
_COVERAGE = re.compile(
    r"\b(?:based on|in|from|across) (?:the )?(?:available|accessible|latest|recorded) "
    r"(?:meetings?|notes?|records?|update)|\bmay be incomplete\b|\bnot (?:be )?exhaustive\b",
    re.I,
)
# The record lacks something the question asks about (often the answer to a status question).
_GAP = re.compile(
    r"\b(?:do(?:es)?|did)(?:n['\u2019]t| not)\s+(?:\w+\s+){0,3}"
    r"(?:mention|cover|record|contain|include|say|state|specify|show|indicate|name|identify"
    r"|confirm)"
    r"|\bno (?:later |further |explicit )?(?:record|mention|information|indication|confirmation"
    r"|findings?|update|details?)\b"
    r"|\bnot (?:mentioned|recorded|covered|stated|specified|named|identified|confirmed)\b"
    r"|\bcould(?:n['\u2019]t| not) (?:find|tell|determine|confirm)\b"
    r"|\bI found no\b|\bthere(?:['\u2019]s| is) no (?:later |further )?(?:record|mention|"
    r"confirmation|indication)\b",
    re.I,
)
# Declines to answer outright, in prose: "I couldn't find any ..." at the start of the answer.
_ABSTAIN = re.compile(
    r"^\W*(?:I (?:could(?:n['\u2019]t| not)|can(?:no|['\u2019])t|was unable to) (?:find|locate"
    r"|see|determine)|(?:there (?:are|is) )?no (?:accessible |matching )?(?:meeting|note)s?"
    r" (?:mention|cover|discuss)|I don['\u2019]t (?:see|have))",
    re.I,
)

Granularity = Literal["line", "meeting", "none"]


class Profile(BaseModel):
    """The measurable anatomy of one answer payload."""

    chars: int  # answer text as the adapter parsed it
    words: int  # prose words, citation markers removed
    meetings_cited: int  # distinct meetings the adapter reports as cited
    line_refs: int  # distinct in-meeting pointers (line IDs) anywhere in the payload
    span_refs: int  # transcript-chunk or timestamp pointers in the payload
    granularity: Granularity  # finest evidence level the payload carries
    instructs_caller: bool  # outside the answer, the payload tells the agent to call a tool
    mentions_transcript: bool  # the answer text refers to a transcript
    quotes: int  # quoted spans of >= 3 words
    verbatim_quotes: int  # of those, found word-for-word in the cited meetings' text
    abstain_field: bool  # the payload has a machine-readable abstain flag
    abstain: bool  # the flag if present, else the prose heuristic
    hedge: bool
    coverage_caveat: bool
    gap_statement: bool
    latency_s: float

    @property
    def flagged(self) -> bool:
        """Signals incompleteness in any way: hedge, coverage caveat, gap statement or abstain."""
        return self.hedge or self.coverage_caveat or self.gap_statement or self.abstain

    @property
    def flagged_strict(self) -> bool:
        """As :attr:`flagged` but without gap statements, which are often the answer itself
        ("no later confirmation it was sent") rather than a caveat on it."""
        return self.hedge or self.coverage_caveat or self.abstain


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def prose(text: str) -> str:
    """Answer text with citation markers removed."""
    return _MARKERS.sub(" ", text)


def quoted_spans(text: str, min_words: int = 3) -> list[str]:
    """Quoted spans of at least ``min_words`` words (straight or curly double quotes)."""
    spans = [a or b for a, b in _QUOTE.findall(text)]
    return [s for s in spans if len(s.split()) >= min_words]


def text_flags(text: str) -> dict[str, bool]:
    """Hedge, coverage caveat, gap statement and prose abstention in answer text."""
    body = prose(text)
    return {
        "hedge": bool(_HEDGE.search(body)),
        "coverage_caveat": bool(_COVERAGE.search(body)),
        "gap_statement": bool(_GAP.search(body)),
        "abstain_text": bool(_ABSTAIN.search(body)),
    }


def profile(ans: ProductAnswer, source_text: str = "") -> Profile:
    """Profile one parsed answer. ``source_text`` is the text of the cited meetings (notes and
    transcript), used only to test quotes for verbatim matches."""
    body = prose(ans.text)
    outside = ans.raw.replace(ans.text, " ") if ans.text else ans.raw
    lines = set(_LINE_REF.findall(ans.raw))
    spans = len(_SPAN_REF.findall(outside)) + len(_SPAN_REF.findall(body))
    qs = quoted_spans(body)
    src = _norm(source_text)
    f = text_flags(ans.text)
    granularity: Granularity = "line" if lines or spans else "meeting" if ans.cited_ids else "none"
    return Profile(
        chars=len(ans.text),
        words=len(_WORD.findall(body)),
        meetings_cited=len(ans.cited_ids),
        line_refs=len(lines),
        span_refs=spans,
        granularity=granularity,
        instructs_caller=bool(_INSTRUCTS.search(outside)),
        mentions_transcript=bool(_TRANSCRIPT_WORD.search(body)),
        quotes=len(qs),
        verbatim_quotes=sum(bool(src) and _norm(s) in src for s in qs),
        abstain_field=ans.abstained is not None,
        abstain=ans.abstained if ans.abstained is not None else f["abstain_text"],
        hedge=f["hedge"],
        coverage_caveat=f["coverage_caveat"],
        gap_statement=f["gap_statement"],
        latency_s=ans.latency_s,
    )


# -- Granola's four answer types -------------------------------------------------------------------

Bucket = Literal[
    "useful_comprehensive", "useful_incomplete", "incomplete_clearly", "wrong_confident"
]
BUCKETS: tuple[Bucket, ...] = (
    "useful_comprehensive",
    "useful_incomplete",
    "incomplete_clearly",
    "wrong_confident",
)


def bucket(verdict: str, flagged: bool, unanswerable: bool) -> Bucket:
    """Our rule for Granola's taxonomy (their names, our mapping):

    1. judged ``correct`` -> useful and comprehensive;
    2. judged ``partially_correct`` or ``incorrect`` and ``flagged`` (hedge, coverage caveat,
       gap statement or abstention) -> incomplete and clearly so;
    3. judged ``partially_correct``, not flagged, answerable -> useful but incomplete;
    4. otherwise (``incorrect`` not flagged, or an unflagged non-correct answer to an
       unanswerable question) -> wrong but confident.
    """
    if verdict == "correct":
        return "useful_comprehensive"
    if flagged:
        return "incomplete_clearly"
    if verdict == "partially_correct" and not unanswerable:
        return "useful_incomplete"
    return "wrong_confident"


# -- stability across repeat asks ------------------------------------------------------------------


def key_term_hits(text: str, terms: Sequence[str]) -> tuple[bool, ...]:
    """Which gold key terms appear in ``text`` (case- and punctuation-insensitive)."""
    t = _norm(text)
    return tuple(_norm(k) in t for k in terms)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    """Jaccard similarity of two ID sets; 1.0 when both are empty."""
    sa, sb = set(a), set(b)
    return 1.0 if not sa | sb else len(sa & sb) / len(sa | sb)


class Stability(BaseModel):
    """How the answers to one question vary across asks (the recorded one plus repeats)."""

    asks: int
    key_terms_stable: bool  # same key-term hit pattern every time
    any_key_term_flip: bool  # some key term present in one answer and absent in another
    flags_stable: bool  # same flagged / abstain status every time
    citations_identical: bool  # same set of cited meetings every time
    citation_jaccard: float  # mean pairwise
    latency_sd: float
    latency_cv: float
    needs_human_check: bool
    reason: str  # generic, no content: why a human should look


def _flag_state(a: ProductAnswer) -> tuple[bool, bool]:
    p = profile(a)
    return p.flagged, p.abstain


def stability(
    answers: Sequence[ProductAnswer], terms: Sequence[str], unanswerable: bool
) -> Stability:
    """Stability of ``answers`` to one question against its gold key ``terms``.

    String-level only. A human check is requested when the key-term pattern changes, when the
    flag status changes while key terms don't, when an answerable question has no key terms,
    or when some but not all key terms are present (a partial match the heuristic can't read).
    """
    hits = [key_term_hits(a.text, terms) for a in answers]
    flags = [_flag_state(a) for a in answers]
    cites = [a.cited_ids for a in answers]
    lat = [a.latency_s for a in answers]
    pairs = list(combinations(range(len(answers)), 2))
    jac = statistics.fmean(jaccard(cites[i], cites[j]) for i, j in pairs) if pairs else 1.0
    sd = statistics.pstdev(lat) if len(lat) > 1 else 0.0
    mean = statistics.fmean(lat) if lat else 0.0
    kt_stable = len(set(hits)) <= 1
    fl_stable = len(set(flags)) <= 1
    reasons = []
    if not kt_stable:
        reasons.append("key-term pattern changed")
    if kt_stable and not fl_stable:
        reasons.append("flag changed, key terms did not")
    if not terms and not unanswerable:
        reasons.append("no key terms")
    if any(0 < sum(h) < len(h) for h in hits):
        reasons.append("partial key-term match")
    return Stability(
        asks=len(answers),
        key_terms_stable=kt_stable,
        any_key_term_flip=any(len({h[i] for h in hits}) > 1 for i in range(len(terms))),
        flags_stable=fl_stable,
        citations_identical=len({frozenset(c) for c in cites}) <= 1,
        citation_jaccard=jac,
        latency_sd=sd,
        latency_cv=sd / mean if mean else 0.0,
        needs_human_check=bool(reasons),
        reason="; ".join(reasons),
    )


def stratified_sample(rows: Sequence[dict[str, Any]], n: int = 12, seed: int = 42) -> list[str]:
    """Question IDs to re-ask: every answer not judged correct, then the remaining slots spread
    round-robin over categories (unanswerables first, then the larger categories), each
    category's order fixed by a seeded hash. ``rows`` carry ``qid``, ``category``, ``verdict``."""

    def key(qid: str) -> str:
        return hashlib.sha256(f"{seed}:{qid}".encode()).hexdigest()

    picked = sorted((r["qid"] for r in rows if r["verdict"] != "correct"), key=key)[:n]
    by_cat: dict[str, list[str]] = {}
    for r in sorted(rows, key=lambda r: key(r["qid"])):
        if r["qid"] not in picked:
            by_cat.setdefault(r["category"], []).append(r["qid"])
    order = sorted(by_cat, key=lambda c: (c != "unanswerable", -len(by_cat[c]), c))
    while len(picked) < n and any(by_cat.values()):
        for c in order:
            if by_cat[c] and len(picked) < n:
                picked.append(by_cat[c].pop(0))
    return picked


def repeat_path(product: str, qid: str, i: int, root: Path = OUT_DIR) -> Path:
    """Where repeat ``i`` (1-based) of ``qid`` for ``product`` is recorded (private)."""
    return root / product / "repeats" / f"{qid}-r{i}.json"


def record_repeats(
    product: Product,
    questions: Sequence[Any],
    qids: Sequence[str],
    repeats: int,
    max_calls: int,
    root: Path = OUT_DIR,
    clock: Callable[[], float] | None = None,
) -> int:
    """Ask each question in ``qids`` until it has ``repeats`` recorded repeats, never making more
    than ``max_calls`` live calls; already-recorded repeats are reused. Returns calls made."""
    import time
    from datetime import UTC, datetime

    tick = clock or time.perf_counter
    by_id = {q.id: q for q in questions}
    calls = 0
    for qid in qids:
        for i in range(1, repeats + 1):
            path = repeat_path(product.name, qid, i, root)
            if path.exists():
                continue
            if calls >= max_calls:
                return calls
            start = tick()
            ans = product.ask(by_id[qid].question)
            calls += 1
            rec = {
                "qid": qid,
                "repeat": i,
                **ans.model_dump(),
                "latency_s": tick() - start,
                "asked_at": datetime.now(UTC).isoformat(),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rec, indent=1))
    return calls


def load_repeats(product: str, qid: str, repeats: int, root: Path = OUT_DIR) -> list[ProductAnswer]:
    """The recorded repeats of ``qid`` for ``product``, in order (missing ones skipped)."""
    out = []
    for i in range(1, repeats + 1):
        p = repeat_path(product, qid, i, root)
        if p.exists():
            r = json.loads(p.read_text())
            out.append(
                ProductAnswer.model_validate(
                    {k: r[k] for k in ProductAnswer.model_fields if k in r}
                )
            )
    return out


# -- products as track entries ---------------------------------------------------------------------


@dataclass
class Entry:
    """One product's answers to the questions, parsed by its adapter, with judge verdicts.

    ``answers`` maps question ID to the adapter's :class:`ProductAnswer`; ``verdicts`` maps
    question ID to (judge 1, judge 2) verdicts from ``judge_report``."""

    product: str
    interface: str  # e.g. "query_granola_meetings via Granola's public MCP"
    payload_source: str  # "recorded MCP payload" or how it was reconstructed
    judge_report: str  # report file name the verdicts came from
    answers: dict[str, ProductAnswer]
    verdicts: dict[str, tuple[str, str | None]]
    notes: list[str] = field(default_factory=list)


def verdicts_from_report(path: Path, system: str) -> dict[str, tuple[str, str | None]]:
    """(judge 1, judge 2) verdicts for ``system`` in a report ``.jsonl``; judge 2 is None where
    the run had no second judge."""
    out = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if r.get("system") == system:
            out[r["qid"]] = (r["verdict"], r.get("judge2_verdict"))
    return out


def answers_from_recordings(
    parse: Callable[[str, float], ProductAnswer], dirs: Sequence[Path]
) -> dict[str, ProductAnswer]:
    """Recorded raw payloads (``{qid, raw, latency_s}``) re-parsed by the product's adapter;
    the first directory holding a question wins."""
    out: dict[str, ProductAnswer] = {}
    for d in dirs:
        for f in sorted(d.glob("*.json")) if d.exists() else []:
            rec = json.loads(f.read_text())
            out.setdefault(rec["qid"], parse(rec["raw"], float(rec["latency_s"])))
    return out


def answers_from_report(
    path: Path,
    system: str,
    to_raw: Callable[[dict[str, Any]], str],
    parse: Callable[[str, float], ProductAnswer],
) -> dict[str, ProductAnswer]:
    """Answers recorded in-process in a report ``.jsonl``, rebuilt into the payload the product's
    MCP tool would return (``to_raw``) and parsed by its adapter."""
    out = {}
    for line in path.read_text().splitlines():
        r = json.loads(line)
        if r.get("system") == system:
            out[r["qid"]] = parse(to_raw(r["answer"]), float(r["latency_s"]))
    return out


# -- per-question rows and the aggregate -----------------------------------------------------------


def meeting_text(db: Path, note_ids: Sequence[str]) -> str:
    """Notes and transcript text of ``note_ids`` from the local mirror (for verbatim checks)."""
    import sqlite3

    ids = [n.split(":")[0] for n in note_ids]
    if not ids or not db.exists():
        return ""
    qs = ",".join("?" * len(ids))
    with sqlite3.connect(db) as con:
        notes = con.execute(f"SELECT raw_json FROM notes WHERE note_id IN ({qs})", ids)
        utts = con.execute(f"SELECT text FROM utterances WHERE note_id IN ({qs})", ids)
        return "\n".join([r[0] or "" for r in notes] + [r[0] or "" for r in utts])


def build_rows(
    entry: Entry, questions: Sequence[Any], tags: dict[str, list[str]], db: Path
) -> list[dict[str, Any]]:
    """One private row per answered and judged question: profile, verdicts, three bucketings."""
    rows = []
    for q in questions:
        a, v = entry.answers.get(q.id), entry.verdicts.get(q.id)
        if a is None or v is None:
            continue
        p = profile(a, meeting_text(db, a.cited_ids))
        rows.append(
            {
                "qid": q.id,
                "product": entry.product,
                "category": q.category,
                "tag": (tags.get(q.id) or ["untagged"])[0],
                "unanswerable": q.unanswerable,
                "verdict": v[0],
                "verdict_judge2": v[1],
                "profile": p.model_dump(),
                "bucket": bucket(v[0], p.flagged, q.unanswerable),
                "bucket_strict": bucket(v[0], p.flagged_strict, q.unanswerable),
                "bucket_judge2": None if v[1] is None else bucket(v[1], p.flagged, q.unanswerable),
            }
        )
    return rows


def _rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    ci = [max(0.0, lo), min(1.0, hi)] if n else None  # clamp float error at 0 and 1
    return {"k": k, "n": n, "rate": k / n if n else None, "ci95": ci}


def _pct(xs: Sequence[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    i = q * (len(s) - 1)
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def _profile_block(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    p = [r["profile"] for r in rows]
    lat = [x["latency_s"] for x in p]
    return {
        "n": n,
        "chars_median": statistics.median(x["chars"] for x in p),
        "words_median": statistics.median(x["words"] for x in p),
        "meetings_cited_mean": statistics.fmean(x["meetings_cited"] for x in p),
        "line_refs_mean": statistics.fmean(x["line_refs"] for x in p),
        "granularity": {
            g: _rate(sum(x["granularity"] == g for x in p), n) for g in ("line", "meeting", "none")
        },
        "instructs_caller": _rate(sum(x["instructs_caller"] for x in p), n),
        "mentions_transcript": _rate(sum(x["mentions_transcript"] for x in p), n),
        "any_quote": _rate(sum(x["quotes"] > 0 for x in p), n),
        "verbatim_quote": _rate(sum(x["verbatim_quotes"] > 0 for x in p), n),
        "abstain_field": _rate(sum(x["abstain_field"] for x in p), n),
        "abstain": _rate(sum(x["abstain"] for x in p), n),
        "hedge": _rate(sum(x["hedge"] for x in p), n),
        "coverage_caveat": _rate(sum(x["coverage_caveat"] for x in p), n),
        "gap_statement": _rate(sum(x["gap_statement"] for x in p), n),
        "flagged": _rate(sum(Profile(**x).flagged for x in p), n),
        "latency_p50": _pct(lat, 0.5),
        "latency_p90": _pct(lat, 0.9),
    }


def _bucket_block(rows: Sequence[dict[str, Any]], key: str = "bucket") -> dict[str, Any]:
    n = len(rows)
    return {b: _rate(sum(r[key] == b for r in rows), n) for b in BUCKETS}


def _group(rows: Sequence[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return dict(sorted(out.items()))


def _stability_block(stab: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    if not stab:
        return None
    s = [x["stability"] for x in stab]
    m = len(s)
    rep_lat = [t for x in stab for t in x["repeat_latencies"]]
    return {
        "questions": m,
        "asks_per_question": s[0]["asks"],
        "sample_by_category": {k: len(v) for k, v in _group(stab, "category").items()},
        "sample_by_tag": {k: len(v) for k, v in _group(stab, "tag").items()},
        "key_terms_stable": _rate(sum(x["key_terms_stable"] for x in s), m),
        "any_key_term_flip": _rate(sum(x["any_key_term_flip"] for x in s), m),
        "flags_stable": _rate(sum(x["flags_stable"] for x in s), m),
        "citations_identical": _rate(sum(x["citations_identical"] for x in s), m),
        "citation_jaccard_mean": statistics.fmean(x["citation_jaccard"] for x in s),
        "latency_cv_median": statistics.median(x["latency_cv"] for x in s),
        "latency_sd_median": statistics.median(x["latency_sd"] for x in s),
        "repeat_latency_p50": _pct(rep_lat, 0.5),
        "repeat_latency_p90": _pct(rep_lat, 0.9),
        "needs_human_check": sum(x["needs_human_check"] for x in s),
        "key_terms_stable_by_category": {
            k: _rate(sum(x["stability"]["key_terms_stable"] for x in v), len(v))
            for k, v in _group(stab, "category").items()
        },
        "key_term_flips_on_initially_noncorrect": sum(
            x["stability"]["any_key_term_flip"] and x["verdict"] != "correct" for x in stab
        ),
    }


def product_block(
    entry: Entry, rows: Sequence[dict[str, Any]], stab: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Everything committable about one product: counts and rates only."""
    by_cat, by_tag = _group(rows, "category"), _group(rows, "tag")
    return {
        "interface": entry.interface,
        "payload_source": entry.payload_source,
        "judge_report": entry.judge_report,
        "questions": len(rows),
        "notes": entry.notes,
        "verdicts_judge1": {
            v: sum(r["verdict"] == v for r in rows) for v in sorted({r["verdict"] for r in rows})
        },
        "profile": {
            "overall": _profile_block(rows),
            "by_category": {k: _profile_block(v) for k, v in by_cat.items()},
            "by_tag": {k: _profile_block(v) for k, v in by_tag.items()},
        },
        "four_types": {
            "overall": _bucket_block(rows),
            "overall_strict_flags": _bucket_block(rows, "bucket_strict"),
            "overall_judge2": _bucket_block(rows, "bucket_judge2")
            if rows and all(r["bucket_judge2"] for r in rows)
            else None,
            "by_tag": {k: _bucket_block(v) for k, v in by_tag.items()},
            "by_category": {k: _bucket_block(v) for k, v in by_cat.items()},
        },
        "stability": _stability_block(stab),
    }


def paired_block(a: Sequence[dict[str, Any]], b: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Four-type distributions of two products restricted to the questions both answered."""
    shared = sorted({r["qid"] for r in a} & {r["qid"] for r in b})
    pa = [r for r in a if r["qid"] in shared]
    pb = [r for r in b if r["qid"] in shared]
    return {"questions": len(shared), "a": _bucket_block(pa), "b": _bucket_block(pb)}


def write_private(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Per-question rows as JSONL, under ``data/real/`` only (gitignored)."""
    if "data/real" not in path.as_posix():
        raise ValueError(f"{path}: per-question rows must stay under data/real/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))


def score_entry(
    entry: Entry,
    questions: Sequence[Any],
    tags: dict[str, list[str]],
    db: Path,
    sample_n: int,
    repeats: int,
    seed: int,
    root: Path = OUT_DIR,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Rows, stability rows (from whatever repeats are recorded) and human-check lines for one
    product. Writes the private files under ``root/<product>/``."""
    rows = build_rows(entry, questions, tags, db)
    by_id = {q.id: q for q in questions}
    row_by_id = {r["qid"]: r for r in rows}
    sample = stratified_sample(rows, sample_n, seed)
    stab, checks = [], []
    for qid in sample:
        reps = load_repeats(entry.product, qid, repeats, root)
        if not reps:
            continue
        q, r = by_id[qid], row_by_id[qid]
        s = stability([entry.answers[qid], *reps], q.key_terms, q.unanswerable)
        stab.append(
            {
                "qid": qid,
                "category": r["category"],
                "tag": r["tag"],
                "verdict": r["verdict"],
                "stability": s.model_dump(),
                "repeat_latencies": [a.latency_s for a in reps],
            }
        )
        if s.needs_human_check:
            checks.append(f"- {qid} ({r['category']}, judged {r['verdict']}): {s.reason}")
    for r in rows:
        p = r["profile"]
        if (
            r["unanswerable"]
            and r["verdict"] == "correct"
            and not (p["gap_statement"] or p["abstain"])
        ):
            checks.append(f"- {r['qid']} (unanswerable, judged correct): no gap/abstain signal")
        if r["verdict_judge2"] is not None and r["verdict"] != r["verdict_judge2"]:
            checks.append(
                f"- {r['qid']} ({r['category']}): judges disagree "
                f"({r['verdict']} vs {r['verdict_judge2']})"
            )
    d = root / entry.product
    write_private(d / "profile.jsonl", rows)
    write_private(d / "stability.jsonl", stab)
    (d / "needs_human_check.md").write_text(
        f"# Needs human check: {entry.product} (private)\n\n" + "\n".join(checks) + "\n"
    )
    return rows, stab, sample


# -- registering products --------------------------------------------------------------------------


class _NoNetwork:
    """A caller for parsing recorded payloads: any live call is a bug."""

    def call(self, tool: str, arguments: dict[str, Any]) -> str:
        raise RuntimeError(f"offline scoring tried to call {tool!r}")


def offline_parser(name_or_path: str) -> Callable[[str, float], ProductAnswer]:
    """The adapter's ``parse`` for a product, with no connection (built-in name or spec)."""
    from concentrate.blackbox.products import (
        ConcentrateProduct,
        GranolaProduct,
        SpecProduct,
        load_spec,
    )

    if name_or_path == "granola":
        return GranolaProduct(_NoNetwork()).parse
    if name_or_path == "concentrate":
        return ConcentrateProduct(_NoNetwork()).parse
    return SpecProduct(load_spec(name_or_path), _NoNetwork()).parse


def ask_memory_payload(answer: dict[str, Any]) -> str:
    """The ``ask_memory(answer=true)`` response shape around an in-process ``answer.v1`` answer
    (the server returns the same model output under ``answer``; see mcp_server/service.py)."""
    return json.dumps({"answer": answer})


def recorded_entry(name: str, report: Path, system: str | None = None) -> Entry:
    """Any product recorded by ``real-kit run --product <name>``: payloads under
    ``data/real/answers/<name>/`` and verdicts from its black-box report."""
    from concentrate.blackbox.kit import ANSWERS_DIR, _legacy

    parse = offline_parser(name)
    sys_name = system or name
    return Entry(
        product=sys_name if name.endswith(".json") else name,
        interface=f"{name} MCP via the black-box adapter",
        payload_source="recorded MCP payload",
        judge_report=report.name,
        answers=answers_from_recordings(parse, [ANSWERS_DIR / name, *_legacy(name)]),
        verdicts=verdicts_from_report(report, sys_name),
    )


GRANOLA_REPORT = Path("data/real/reports/20261104T012414Z.deletion.jsonl")
CONCENTRATE_REPORT = Path("data/real/reports/20261103T004019Z.jsonl")


def granola_entry(report: Path = GRANOLA_REPORT) -> Entry:
    """Granola's agent: recorded ``query_granola_meetings`` payloads, deletion-sim DEV verdicts."""
    e = recorded_entry("granola", report, system="granola_agent")
    e.interface = "query_granola_meetings via Granola's public MCP"
    return e


def concentrate_entry(report: Path = CONCENTRATE_REPORT) -> Entry:
    """Concentrate: the real-slice run's in-process answers (same ``answer.v1`` path that
    ``ask_memory(answer=true)`` serves: memory slice with transcript lines, no fallback), wrapped
    in the ``ask_memory`` response shape and parsed by Concentrate's adapter."""
    return Entry(
        product="concentrate",
        interface="ask_memory(answer=true) via Concentrate's MCP (real-kit serve)",
        payload_source="in-process answer.v1 output in the ask_memory shape (not over MCP)",
        judge_report=report.name,
        answers=answers_from_report(
            report, "concentrate", ask_memory_payload, offline_parser("concentrate")
        ),
        verdicts=verdicts_from_report(report, "concentrate"),
        notes=[
            "latency excludes MCP transport",
            "run stopped on budget after 39 of 47 questions: none of the 6 unanswerables",
            "no repeat asks: each costs an answer-model call",
            "the run had no second judge",
        ],
    )


def aggregate(
    results: dict[str, tuple[Entry, list[dict[str, Any]], list[dict[str, Any]]]],
    meta: dict[str, Any],
    pairs: Sequence[tuple[str, str, Entry, Entry]] = (),
    questions: Sequence[Any] = (),
    tags: dict[str, list[str]] | None = None,
    db: Path = Path("data/real.db"),
) -> dict[str, Any]:
    """The committable track result: one block per product, plus paired four-type comparisons
    on shared questions judged in the same run (``pairs``: label, label, entry, entry)."""
    out: dict[str, Any] = {
        "meta": meta,
        "rule": "concentrate.evals.agent_interface.bucket; docs/tracks/AGENT_INTERFACE.md",
        "products": {n: product_block(e, r, s) for n, (e, r, s) in results.items()},
    }
    if pairs:
        out["paired_same_judge_run"] = {
            f"{la}__vs__{lb}": {
                "judge_report": ea.judge_report,
                **paired_block(
                    build_rows(ea, questions, tags or {}, db),
                    build_rows(eb, questions, tags or {}, db),
                ),
            }
            for la, lb, ea, eb in pairs
        }
    return out


def product_key(name_or_path: str) -> str:
    """The name a product's results are filed under (built-in name or its spec's name)."""
    from concentrate.blackbox.products import product_name

    return product_name(name_or_path)
