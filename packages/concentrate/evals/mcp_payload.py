"""What Granola's MCP hands an external agent: a deterministic, LLM-free profile of recorded
``query_granola_meetings`` answers, a bucketing into Granola's own four answer types, and a
repeat-ask stability check. See docs/results/MCP_PAYLOAD.md.

Everything here is string-level heuristics over the raw tool text. Per-question output stays
under ``data/real/`` (private); :func:`aggregate` is the only committable view.
"""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections.abc import Iterable, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from concentrate.blackbox.products import parse_granola_answer
from concentrate.evals.figures import wilson

# -- payload anatomy -------------------------------------------------------------------------------

_NOTE_LINK = re.compile(r"\]\(https?://notes\.granola\.ai/d/[A-Za-z0-9_-]+\)")
# Anything that would point *inside* a meeting: a transcript chunk, a line/utterance ID, or a
# timestamp. None of these has been seen in a Granola answer; they are counted so a change shows.
_SPAN_REF = re.compile(
    r"chunk_ids?|\butterance_id\b|\[[A-Za-z0-9_-]+:\d{3,4}\]|\[\d{1,2}:\d{2}(?::\d{2})?\]"
    r"|#t=\d|\btranscript_version\b"
)
_DISCLAIMER = re.compile(r"<transcript_note>.*?</transcript_note>", re.S)
_TRANSCRIPT_WORD = re.compile(r"\btranscripts?\b", re.I)
_QUOTE = re.compile(r"“([^”]+)”|\"([^\"]+)\"")

# Uncertainty about the answer itself.
_HEDGE = re.compile(
    r"\b(?:may|might|perhaps|possibly|probably|likely|unclear|not clear|appears? to|seems? to"
    r"|it'?s possible|I(?:'m| am) not (?:sure|certain)|uncertain)\b",
    re.I,
)
# A caveat about what was searched: the answer is scoped to what Granola could see.
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
# Declines to answer outright: "I couldn't find any ..." at the start of the answer.
_ABSTAIN = re.compile(
    r"^\W*(?:I (?:could(?:n['\u2019]t| not)|can(?:no|['\u2019])t|was unable to) (?:find|locate"
    r"|see|determine)|(?:there (?:are|is) )?no (?:accessible |matching )?(?:meeting|note)s?"
    r" (?:mention|cover|discuss)|I don['\u2019]t (?:see|have))",
    re.I,
)


class Profile(BaseModel):
    """The measurable anatomy of one answer payload."""

    chars: int  # answer text, standing disclaimer removed
    words: int
    has_disclaimer: bool  # the standing <transcript_note> telling the caller to read transcripts
    citations: int  # note-link citation markers, with repeats
    distinct_cited: int  # distinct meetings cited
    span_refs: int  # transcript-chunk / line / timestamp references (pointers inside a meeting)
    mentions_transcript: bool  # the answer text itself refers to a transcript
    quotes: int  # quoted spans of >= 3 words
    verbatim_quotes: int  # of those, found word-for-word in the cited meetings' text
    hedge: bool
    coverage_caveat: bool
    gap_statement: bool
    abstain: bool
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


def quoted_spans(text: str, min_words: int = 3) -> list[str]:
    """Quoted spans of at least ``min_words`` words (straight or curly double quotes)."""
    spans = [a or b for a, b in _QUOTE.findall(text)]
    return [s for s in spans if len(s.split()) >= min_words]


def profile(raw: str, latency_s: float, source_text: str = "") -> Profile:
    """Profile one raw ``query_granola_meetings`` payload. ``source_text`` is the text of the
    cited meetings (notes and transcript), used only to test quotes for verbatim matches."""
    text, cited = parse_granola_answer(raw)
    body = re.sub(r"\[\[\d+\]\]\([^)]*\)", " ", text)  # citation markers aren't prose
    spans = quoted_spans(body)
    src = _norm(source_text)
    return Profile(
        chars=len(text),
        words=len(body.split()),
        has_disclaimer=bool(_DISCLAIMER.search(raw)),
        citations=len(_NOTE_LINK.findall(raw)),
        distinct_cited=len(cited),
        span_refs=len(_SPAN_REF.findall(_DISCLAIMER.sub("", raw))),
        mentions_transcript=bool(_TRANSCRIPT_WORD.search(body)),
        quotes=len(spans),
        verbatim_quotes=sum(bool(src) and _norm(s) in src for s in spans),
        hedge=bool(_HEDGE.search(body)),
        coverage_caveat=bool(_COVERAGE.search(body)),
        gap_statement=bool(_GAP.search(body)),
        abstain=bool(_ABSTAIN.search(body)),
        latency_s=latency_s,
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


def stability(
    answers: Sequence[tuple[str, list[str], float]], terms: Sequence[str], unanswerable: bool
) -> Stability:
    """Stability of ``answers`` (text, cited IDs, latency) against the gold ``terms``.

    String-level only. A human check is requested when the key-term pattern changes, when the
    flag status changes while key terms don't, when an answerable question has no key terms,
    or when some but not all key terms are present (a partial match the heuristic can't read).
    """
    hits = [key_term_hits(t, terms) for t, _, _ in answers]
    flags = [(profile_flags(t)) for t, _, _ in answers]
    cites = [c for _, c, _ in answers]
    lat = [x for _, _, x in answers]
    pairs = list(combinations(range(len(answers)), 2))
    jac = statistics.fmean(jaccard(cites[i], cites[j]) for i, j in pairs) if pairs else 1.0
    sd = statistics.pstdev(lat) if len(lat) > 1 else 0.0
    mean = statistics.fmean(lat) if lat else 0.0
    kt_stable = len(set(hits)) <= 1
    fl_stable = len(set(flags)) <= 1
    flip = any(len({h[i] for h in hits}) > 1 for i in range(len(terms)))
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
        any_key_term_flip=flip,
        flags_stable=fl_stable,
        citations_identical=len({frozenset(c) for c in cites}) <= 1,
        citation_jaccard=jac,
        latency_sd=sd,
        latency_cv=sd / mean if mean else 0.0,
        needs_human_check=bool(reasons),
        reason="; ".join(reasons),
    )


def profile_flags(text: str) -> tuple[bool, bool]:
    """(flagged, abstain) for answer text, as :func:`profile` would set them."""
    body = re.sub(r"\[\[\d+\]\]\([^)]*\)", " ", text)
    flagged = any(p.search(body) for p in (_HEDGE, _COVERAGE, _GAP, _ABSTAIN))
    return flagged, bool(_ABSTAIN.search(body))


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


# -- aggregate (the committable view) --------------------------------------------------------------


def _rate(k: int, n: int) -> dict[str, Any]:
    lo, hi = wilson(k, n)
    ci = [max(0.0, lo), min(1.0, hi)] if n else None  # clamp float error at 0 and 1
    return {"k": k, "n": n, "rate": k / n if n else None, "ci95": ci}


def _pct(xs: Sequence[float], q: float) -> float:
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
        "citations_mean": statistics.fmean(x["citations"] for x in p),
        "distinct_cited_mean": statistics.fmean(x["distinct_cited"] for x in p),
        "zero_citations": _rate(sum(x["distinct_cited"] == 0 for x in p), n),
        "span_refs": _rate(sum(x["span_refs"] > 0 for x in p), n),
        "mentions_transcript": _rate(sum(x["mentions_transcript"] for x in p), n),
        "has_disclaimer": _rate(sum(x["has_disclaimer"] for x in p), n),
        "any_quote": _rate(sum(x["quotes"] > 0 for x in p), n),
        "verbatim_quote": _rate(sum(x["verbatim_quotes"] > 0 for x in p), n),
        "hedge": _rate(sum(x["hedge"] for x in p), n),
        "coverage_caveat": _rate(sum(x["coverage_caveat"] for x in p), n),
        "gap_statement": _rate(sum(x["gap_statement"] for x in p), n),
        "abstain": _rate(sum(x["abstain"] for x in p), n),
        "latency_p50": _pct(lat, 0.5),
        "latency_p90": _pct(lat, 0.9),
    }


def _bucket_block(rows: Sequence[dict[str, Any]], field: str) -> dict[str, Any]:
    n = len(rows)
    return {b: _rate(sum(r[field] == b for r in rows), n) for b in BUCKETS}


def _group(rows: Sequence[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r[key], []).append(r)
    return dict(sorted(out.items()))


def aggregate(
    rows: Sequence[dict[str, Any]], stab: Sequence[dict[str, Any]], meta: dict[str, Any]
) -> dict[str, Any]:
    """Counts and rates only. ``rows`` hold ``category``, ``tag``, ``verdict``, ``profile``,
    ``bucket``, ``bucket_strict``, ``bucket_judge2``; ``stab`` hold ``category``, ``tag``,
    ``stability`` and ``repeat_latencies``."""
    by_cat, by_tag = _group(rows, "category"), _group(rows, "tag")
    verdicts = {
        v: sum(r["verdict"] == v for r in rows) for v in sorted({r["verdict"] for r in rows})
    }
    s = [x["stability"] for x in stab]
    m = len(s)
    rep_lat = [t for x in stab for t in x["repeat_latencies"]]
    return {
        "meta": meta,
        "profile": {
            "overall": _profile_block(rows),
            "by_category": {k: _profile_block(v) for k, v in by_cat.items()},
            "by_tag": {k: _profile_block(v) for k, v in by_tag.items()},
        },
        "verdicts_judge1": verdicts,
        "four_types": {
            "rule": "see concentrate.evals.mcp_payload.bucket and docs/results/MCP_PAYLOAD.md",
            "overall": _bucket_block(rows, "bucket"),
            "overall_strict_flags": _bucket_block(rows, "bucket_strict"),
            "overall_judge2": _bucket_block(rows, "bucket_judge2"),
            "by_tag": {k: _bucket_block(v, "bucket") for k, v in by_tag.items()},
            "by_category": {k: _bucket_block(v, "bucket") for k, v in by_cat.items()},
        },
        "stability": {
            "questions": m,
            "asks_per_question": s[0]["asks"] if s else 0,
            "sample_by_category": {k: len(v) for k, v in _group(stab, "category").items()},
            "sample_by_tag": {k: len(v) for k, v in _group(stab, "tag").items()},
            "key_terms_stable": _rate(sum(x["key_terms_stable"] for x in s), m),
            "any_key_term_flip": _rate(sum(x["any_key_term_flip"] for x in s), m),
            "flags_stable": _rate(sum(x["flags_stable"] for x in s), m),
            "citations_identical": _rate(sum(x["citations_identical"] for x in s), m),
            "citation_jaccard_mean": statistics.fmean(x["citation_jaccard"] for x in s)
            if s
            else None,
            "latency_cv_median": statistics.median(x["latency_cv"] for x in s) if s else None,
            "latency_sd_median": statistics.median(x["latency_sd"] for x in s) if s else None,
            "repeat_latency_p50": _pct(rep_lat, 0.5) if rep_lat else None,
            "repeat_latency_p90": _pct(rep_lat, 0.9) if rep_lat else None,
            "needs_human_check": sum(x["needs_human_check"] for x in s),
            "by_category_key_terms_stable": {
                k: _rate(sum(x["stability"]["key_terms_stable"] for x in v), len(v))
                for k, v in _group(stab, "category").items()
            },
        },
    }


def write_private(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Per-question rows as JSONL, under ``data/real/`` only (gitignored)."""
    if "data/real" not in path.as_posix():
        raise ValueError(f"{path}: per-question payload rows must stay under data/real/")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))


# -- the run (offline over recordings, plus optional live repeats) ---------------------------------

PAYLOAD_DIR = Path("data/real/mcp_payload")


def meeting_text(db: Path, note_ids: Sequence[str]) -> str:
    """Notes and transcript text of ``note_ids`` from the local mirror (for verbatim checks)."""
    import sqlite3

    if not note_ids or not db.exists():
        return ""
    qs = ",".join("?" * len(note_ids))
    with sqlite3.connect(db) as con:
        notes = con.execute(f"SELECT raw_json FROM notes WHERE note_id IN ({qs})", list(note_ids))
        utts = con.execute(f"SELECT text FROM utterances WHERE note_id IN ({qs})", list(note_ids))
        return "\n".join([r[0] or "" for r in notes] + [r[0] or "" for r in utts])


def build_rows(
    questions: Sequence[Any],
    records: dict[str, dict[str, Any]],
    judged: dict[str, dict[str, Any]],
    tags: dict[str, list[str]],
    db: Path,
) -> list[dict[str, Any]]:
    """One private row per recorded answer: profile, judge verdicts and the three bucketings."""
    rows = []
    for q in questions:
        rec, j = records.get(q.id), judged.get(q.id)
        if rec is None or j is None:
            continue
        _, cited = parse_granola_answer(rec["raw"])
        p = profile(rec["raw"], float(rec["latency_s"]), meeting_text(db, cited))
        rows.append(
            {
                "qid": q.id,
                "category": q.category,
                "tag": (tags.get(q.id) or ["untagged"])[0],
                "unanswerable": q.unanswerable,
                "verdict": j["verdict"],
                "verdict_judge2": j.get("judge2_verdict") or j["verdict"],
                "cited": cited,
                "profile": p.model_dump(),
                "bucket": bucket(j["verdict"], p.flagged, q.unanswerable),
                "bucket_strict": bucket(j["verdict"], p.flagged_strict, q.unanswerable),
                "bucket_judge2": bucket(
                    j.get("judge2_verdict") or j["verdict"], p.flagged, q.unanswerable
                ),
            }
        )
    return rows


def repeat_path(qid: str, i: int, out_dir: Path = PAYLOAD_DIR / "repeats") -> Path:
    """Where repeat ``i`` (1-based) of question ``qid`` is recorded (private)."""
    return out_dir / f"{qid}-r{i}.json"


def record_repeats(
    product: Any,
    questions: Sequence[Any],
    qids: Sequence[str],
    repeats: int,
    max_calls: int,
    out_dir: Path = PAYLOAD_DIR / "repeats",
) -> int:
    """Ask each question in ``qids`` until it has ``repeats`` recorded repeats, never making more
    than ``max_calls`` live calls; already-recorded repeats are reused. Returns calls made."""
    import time
    from datetime import UTC, datetime

    by_id = {q.id: q for q in questions}
    calls = 0
    for qid in qids:
        for i in range(1, repeats + 1):
            path = repeat_path(qid, i, out_dir)
            if path.exists():
                continue
            if calls >= max_calls:
                return calls
            start = time.perf_counter()
            ans = product.ask(by_id[qid].question)
            calls += 1
            rec = {
                "qid": qid,
                "repeat": i,
                **ans.model_dump(),
                "latency_s": time.perf_counter() - start,
                "asked_at": datetime.now(UTC).isoformat(),
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rec, indent=1))
    return calls


def run(
    report_jsonl: Path,
    tags_path: Path,
    db: Path,
    meta: dict[str, Any],
    sample_n: int = 12,
    repeats: int = 2,
    seed: int = 42,
    product: Any = None,
    max_calls: int = 24,
) -> tuple[dict[str, Any], list[str]]:
    """Profile the recorded Granola answers judged in ``report_jsonl`` (a deletion-sim report),
    re-ask a stratified sample through ``product`` if given (else reuse recorded repeats), and
    return the aggregate plus the private texts it must not contain. Writes private rows and a
    human-check list under :data:`PAYLOAD_DIR`."""
    from concentrate.blackbox.kit import ANSWERS_DIR, check_committable
    from concentrate.evals.deletion_sim import load_tags
    from concentrate.evals.real_slice import load_questions

    questions = load_questions()
    by_id = {q.id: q for q in questions}
    judged = {
        r["qid"]: r
        for r in map(json.loads, report_jsonl.read_text().splitlines())
        if r["system"] == "granola_agent"
    }
    records: dict[str, dict[str, Any]] = {}
    for d in (Path("data/real/granola"), ANSWERS_DIR / "granola"):
        for f in sorted(d.glob("*.json")) if d.exists() else []:
            rec = json.loads(f.read_text())
            records.setdefault(rec["qid"], rec)
    tags = load_tags(tags_path)
    rows = build_rows(questions, records, judged, tags, db)
    sample = stratified_sample(list(judged.values()), sample_n, seed)
    calls = record_repeats(product, questions, sample, repeats, max_calls) if product else 0

    stab, checks = [], []
    row_by_id = {r["qid"]: r for r in rows}
    for qid in sample:
        reps = [
            json.loads(p.read_text())
            for i in range(1, repeats + 1)
            if (p := repeat_path(qid, i)).exists()
        ]
        if not reps or qid not in row_by_id:
            continue
        orig = records[qid]
        text, cited = parse_granola_answer(orig["raw"])
        answers = [(text, cited, float(orig["latency_s"]))]
        answers += [(r["text"], list(r["cited_ids"]), float(r["latency_s"])) for r in reps]
        q = by_id[qid]
        s = stability(answers, q.key_terms, q.unanswerable)
        r = row_by_id[qid]
        stab.append(
            {
                "qid": qid,
                "category": r["category"],
                "tag": r["tag"],
                "verdict": r["verdict"],
                "stability": s.model_dump(),
                "repeat_latencies": [a[2] for a in answers[1:]],
            }
        )
        if s.needs_human_check:
            checks.append(f"- {qid} ({r['category']}, judged {r['verdict']}): {s.reason}")
    # Flag-vs-verdict disagreements a human should read: an unanswerable judged correct with no
    # gap/abstain signal, or a non-correct answer the classifier saw no caveat in.
    for r in rows:
        p = r["profile"]
        if (
            r["unanswerable"]
            and r["verdict"] == "correct"
            and not (p["gap_statement"] or p["abstain"])
        ):
            checks.append(
                f"- {r['qid']} (unanswerable, judged correct): no gap/abstain signal found"
            )
        if r["verdict"] != r["verdict_judge2"]:
            checks.append(
                f"- {r['qid']} ({r['category']}): judges disagree "
                f"({r['verdict']} vs {r['verdict_judge2']})"
            )

    write_private(PAYLOAD_DIR / "profile.jsonl", rows)
    write_private(PAYLOAD_DIR / "stability.jsonl", stab)
    (PAYLOAD_DIR / "needs_human_check.md").write_text(
        "# Needs human check (private)\n\n" + "\n".join(checks) + "\n"
    )
    agg = aggregate(
        rows, stab, {**meta, "seed": seed, "live_calls_this_run": calls, "questions": len(rows)}
    )
    private = [q.question for q in questions] + [
        q.gold_answer for q in questions if not q.unanswerable
    ]
    private += [parse_granola_answer(rec["raw"])[0] for rec in records.values()]
    check_committable(agg, private)
    return agg, private
