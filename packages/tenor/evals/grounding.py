"""The **notes grounding checker**: for each claim in a meeting's notes, a verdict and its evidence.

A reusable tool, not a leaderboard. Input: one meeting (a :class:`MeetingNote`, whose
utterances are the transcript) and one system's notes for it, as markdown. Output, per atomic
claim: ``supported`` / ``inferred`` / ``unsupported`` / ``contradicted``, the transcript lines
(``utterance_id``) that support or contradict it, and a short verbatim quote.

It is built to be cheap:

1. **Claims** come from :func:`tenor.evals.faithfulness.split_claims`.
2. **Candidate lines** are retrieved deterministically, per claim: the ``k`` lines sharing the
   most IDF-weighted, stemmed content words (:func:`faithfulness.nearest_lines`), or with
   ``span > 1`` the ``k`` best runs of ``span`` adjacent lines (:func:`nearest_spans`), each
   widened by ``window`` neighbours. The pre-check (numbers, dates, names the transcript never says)
   runs here too, at $0.
3. **One call per meeting** (excerpt pass) shows the model only the union of candidate lines.
4. **Full-transcript fallback**: claims with no candidate lines, and (by default) every claim
   the excerpt pass did not call ``supported``, are checked again in one call that sees the
   whole transcript. Its verdict is the one reported. So a missed retrieval can cost an extra
   call but cannot by itself turn a supported claim into an ungrounded one.

The quote is not generated: it is the cited line that shares the most words with the claim,
cut to 25 words, so it is always verbatim. Validation against AMI's human annotations:
:mod:`tenor.evals.grounding_ami`; write-up ``docs/method/GROUNDING_CHECKER.md``.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from tenor.core.models import MeetingNote, Utterance
from tenor.evals.faithfulness import (
    LABELS,
    Claim,
    Label,
    calendar_context,
    claims_message,
    content_terms,
    nearest_lines,
    precheck,
    speaker,
    split_claims,
)
from tenor.llm.client import PRICING, LLMClient, LLMRequest, estimate_tokens, load_prompt

PROMPT_ID = "grounding.v1"
MODEL = "claude-haiku-5-5"
EFFORT = "low"
K = 8  # candidate lines (or spans) per claim, before widening
WINDOW = 1  # neighbours added on each side of a candidate line
SPAN = 1  # adjacent lines scored together as one candidate (1 = single lines)
QUOTE_WORDS = 25
# Output tokens per claim (reason + label + a few line numbers), padded; used only for quotes.
OUT_TOKENS_PER_CLAIM = 80

Pass = Literal["excerpt", "full"]


class GroundVerdict(BaseModel):
    claim_id: str
    reason: str
    label: Label
    lines: list[int]


class GroundReply(BaseModel):
    verdicts: list[GroundVerdict]


GROUND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "claim_id": {"type": "string"},
                    "reason": {"type": "string"},
                    "label": {"type": "string", "enum": list(LABELS)},
                    "lines": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["claim_id", "reason", "label", "lines"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


class ClaimResult(BaseModel):
    """One claim's verdict and evidence."""

    claim_id: str
    section: str
    context: str
    text: str
    verdict: Label | None  # None only if no call returned a verdict for it
    evidence: list[str]  # utterance_ids of the cited lines
    lines: list[int]  # the same lines, as Utterance.index
    quote: str  # verbatim, from the cited line that shares most words with the claim
    quote_id: str  # utterance_id the quote comes from ("" if no line cited)
    reason: str
    decided_by: Pass | None
    excerpt_verdict: Label | None  # the excerpt pass's verdict, if it saw this claim
    uncited_support: bool  # "supported" with no valid line: relabelled "unsupported"
    candidates: list[int]  # retrieved lines (Utterance.index), before widening
    precheck: dict[str, list[str]]  # numbers / dates / names the transcript never says


class GroundingResult(BaseModel):
    """Everything the checker says about one meeting's notes."""

    meeting_id: str
    prompt: str
    model: str
    k: int
    window: int
    span: int = SPAN
    escalate: bool
    transcript_lines: int
    excerpt_lines: int
    calls: int
    escalated: int  # claims sent to the full-transcript pass
    counts: dict[str, int]  # verdict -> claims
    claims: list[ClaimResult]


# -- deterministic retrieval ---------------------------------------------------------------------


def term_weights(utterances: Sequence[Utterance]) -> dict[str, float]:
    """IDF of each stemmed content term over the meeting's lines: ``log(1 + N / df)``."""
    df: Counter[str] = Counter()
    for u in utterances:
        df.update(content_terms(u.text, stemmed=True))
    n = len(utterances)
    return {t: math.log(1 + n / d) for t, d in df.items()}


def nearest_spans(
    query: str,
    utterances: Sequence[Utterance],
    k: int,
    span: int,
    *,
    weights: Mapping[str, float],
) -> list[int]:
    """Line positions covered by the ``k`` best runs of ``span`` adjacent lines.

    A run scores the IDF weight of the query's stemmed content terms found anywhere in it, so a
    point made across several short turns (a fragmented transcript) can outrank one line that
    repeats a single rare word. Runs adding no new line are skipped; ties go to the earlier run.
    With ``span=1`` this is :func:`faithfulness.nearest_lines` with ``weights``.
    """
    if span <= 1:
        return nearest_lines(query, utterances, k, weights=weights, stemmed=True)
    want = content_terms(query, stemmed=True)
    terms = [want & content_terms(u.text, stemmed=True) for u in utterances]
    n = len(utterances)
    scored: list[tuple[float, int]] = []
    for s in range(max(1, n - span + 1)):
        shared = set().union(*terms[s : s + span])
        score = sum(weights.get(t, 0.0) for t in shared)
        if score > 0:
            scored.append((score, -s))
    out: set[int] = set()
    taken = 0
    for _, neg in sorted(scored, reverse=True):
        lines = set(range(-neg, min(n, -neg + span)))
        if lines <= out:
            continue
        out |= lines
        taken += 1
        if taken == k:
            break
    return sorted(out)


@dataclass(frozen=True)
class Plan:
    """The $0 part of a check: claims, their candidate lines and pre-check flags."""

    note: MeetingNote
    claims: tuple[Claim, ...]
    candidates: dict[str, list[int]]  # claim_id -> top-k lines (or span lines), before widening
    excerpt: list[int]  # union of widened candidates, sorted
    prechecks: dict[str, dict[str, list[str]]]

    def excerpt_claims(self) -> list[Claim]:
        return [c for c in self.claims if self.candidates[c.claim_id]]


def plan(
    note: MeetingNote,
    claims: Sequence[Claim],
    *,
    k: int = K,
    window: int = WINDOW,
    span: int = SPAN,
) -> Plan:
    """Retrieve candidate lines and run the pre-check for every claim ($0, deterministic)."""
    weights = term_weights(note.utterances)
    n = len(note.utterances)
    spoken = " ".join(u.text for u in note.utterances)
    # Speaker names the transcript carries count as known, like the calendar context.
    labels = sorted({u.speaker_name for u in note.utterances if u.speaker_name})
    ctx = f"{calendar_context(note)}\nSpeakers: {', '.join(labels)}"
    cands: dict[str, list[int]] = {}
    shown: set[int] = set()
    pre: dict[str, dict[str, list[str]]] = {}
    for c in claims:
        query = f"{c.context} {c.text}".strip()
        hits = nearest_spans(query, note.utterances, k, span, weights=weights)
        cands[c.claim_id] = hits
        for i in hits:
            shown.update(range(max(0, i - window), min(n, i + window + 1)))
        p = precheck(c.text, spoken, ctx)
        pre[c.claim_id] = {
            "numbers": list(p.numbers),
            "dates": list(p.dates),
            "names": list(p.names),
        }
    return Plan(note, tuple(claims), cands, sorted(shown), pre)


# -- the calls -----------------------------------------------------------------------------------


def transcript_lines(note: MeetingNote, lines: Sequence[int] | None = None) -> str:
    """``[i] Speaker: text`` for the given line indices (all if None); ``…`` marks gaps."""
    idx = range(len(note.utterances)) if lines is None else sorted(lines)
    out: list[str] = []
    prev = -1
    for i in idx:
        if lines is not None and i != prev + 1:
            out.append("…")
        u = note.utterances[i]
        out.append(f"[{u.index}] {speaker(u)}: {u.text}")
        prev = i
    if lines is not None and prev != len(note.utterances) - 1:
        out.append("…")
    return "\n".join(out)


def request(
    note: MeetingNote,
    claims: Sequence[Claim],
    lines: Sequence[int] | None,
    *,
    model: str = MODEL,
    labels_note: str | None = None,
) -> LLMRequest:
    """One check call: an excerpt (``lines``) or the full transcript (``lines=None``).

    ``labels_note``, when given, is shown after the calendar context as ``<speaker_labels>``:
    input about how the claims' speaker labels relate to the transcript's (the prompt is
    unchanged).
    """
    scope: Pass = "full" if lines is None else "excerpt"
    labels = f"<speaker_labels>\n{labels_note}\n</speaker_labels>\n\n" if labels_note else ""
    system = (
        f"{load_prompt(PROMPT_ID)}\n\n<scope>{scope}</scope>\n\n"
        f"<calendar>\n{calendar_context(note)}\n</calendar>\n\n{labels}"
        f"<transcript>\n{transcript_lines(note, lines)}\n</transcript>"
    )
    return LLMRequest(
        stage=f"grounding.{scope}",
        prompt_id=PROMPT_ID,
        system=system,
        user=claims_message(claims),
        schema=GROUND_SCHEMA,
        max_tokens=2000 + 200 * len(claims),
        effort=EFFORT,
        cache_system=True,
    )


@dataclass
class _Verdict:
    label: Label
    lines: list[int]
    reason: str
    uncited: bool


def _read(reply: GroundReply, claims: Sequence[Claim], valid: set[int]) -> dict[str, _Verdict]:
    """Verdicts by claim id (first wins). ``supported`` with no valid line becomes
    ``unsupported`` (only claim what you can point to)."""
    want = {c.claim_id for c in claims}
    out: dict[str, _Verdict] = {}
    for v in reply.verdicts:
        if v.claim_id not in want or v.claim_id in out:
            continue
        lines = sorted({i for i in v.lines if i in valid})
        uncited = v.label == "supported" and not lines
        out[v.claim_id] = _Verdict("unsupported" if uncited else v.label, lines, v.reason, uncited)
    return out


def _quote(claim: str, note: MeetingNote, lines: Sequence[int]) -> tuple[str, str]:
    """The cited line sharing the most (weighted) words with the claim, cut to QUOTE_WORDS."""
    if not lines:
        return "", ""
    cited = [note.utterances[i] for i in lines]
    best = nearest_lines(claim, cited, 1, weights=term_weights(note.utterances), stemmed=True)
    u = note.utterances[best[0]] if best else cited[0]
    words = u.text.split()
    text = " ".join(words[:QUOTE_WORDS]) + (" …" if len(words) > QUOTE_WORDS else "")
    return text, u.utterance_id


def ground_claims(
    note: MeetingNote,
    claims: Sequence[Claim],
    client: LLMClient,
    *,
    escalate: bool = True,
    k: int = K,
    window: int = WINDOW,
    span: int = SPAN,
    labels_note: str | None = None,
) -> GroundingResult:
    """Check ``claims`` against ``note``'s transcript: excerpt pass, then full-transcript pass."""
    p = plan(note, claims, k=k, window=window, span=span)
    n = len(note.utterances)
    calls = 0
    first: dict[str, _Verdict] = {}
    in_excerpt = p.excerpt_claims()
    if in_excerpt:
        req = request(note, in_excerpt, p.excerpt, labels_note=labels_note)
        reply = client.structured(req, GroundReply)
        first = _read(reply, in_excerpt, set(p.excerpt))
        calls += 1
    retry = [
        c
        for c in claims
        if c.claim_id not in first or (escalate and first[c.claim_id].label != "supported")
    ]
    final: dict[str, tuple[_Verdict, Pass]] = {cid: (v, "excerpt") for cid, v in first.items()}
    if retry and n:
        reply = client.structured(request(note, retry, None, labels_note=labels_note), GroundReply)
        final |= {cid: (v, "full") for cid, v in _read(reply, retry, set(range(n))).items()}
        calls += 1
    results: list[ClaimResult] = []
    for c in claims:
        got = final.get(c.claim_id)
        v, by = got if got else (None, None)
        lines = v.lines if v else []
        quote, quote_id = _quote(c.text, note, lines)
        results.append(
            ClaimResult(
                claim_id=c.claim_id,
                section=c.section,
                context=c.context,
                text=c.text,
                verdict=v.label if v else None,
                evidence=[note.utterances[i].utterance_id for i in lines],
                lines=lines,
                quote=quote,
                quote_id=quote_id,
                reason=v.reason if v else "",
                decided_by=by,
                excerpt_verdict=first[c.claim_id].label if c.claim_id in first else None,
                uncited_support=bool(v and v.uncited),
                candidates=p.candidates[c.claim_id],
                precheck=p.prechecks[c.claim_id],
            )
        )
    counts: Counter[str] = Counter(r.verdict or "missing" for r in results)
    return GroundingResult(
        meeting_id=note.note_id,
        prompt=PROMPT_ID,
        model=client.model,
        k=k,
        window=window,
        span=span,
        escalate=escalate,
        transcript_lines=n,
        excerpt_lines=len(p.excerpt),
        calls=calls,
        escalated=len(retry),
        counts={lab: counts[lab] for lab in (*LABELS, "missing") if counts[lab]},
        claims=results,
    )


def ground(note: MeetingNote, markdown: str, client: LLMClient, **kw: Any) -> GroundingResult:
    """Split ``markdown`` notes into claims and check each one (see :func:`ground_claims`)."""
    return ground_claims(note, split_claims(markdown).claims, client, **kw)


# -- pricing -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    """Offline token estimate for checking one set of claims (~3.5 chars per token)."""

    excerpt_input: int
    full_input: int  # worst case: every claim escalates
    claims: int
    excerpt_lines: int
    transcript_lines: int

    def usd(self, model: str = MODEL, escalate_share: float = 1.0) -> float:
        """Estimated cost; the system prompt is priced as a cache write (1.25x input)."""
        p_in, p_out = PRICING[model]
        tokens_in = self.excerpt_input + escalate_share * self.full_input
        out = OUT_TOKENS_PER_CLAIM * self.claims * (1 + escalate_share)
        return (tokens_in * p_in * 1.25 + out * p_out) / 1e6


def quote(
    note: MeetingNote,
    claims: Sequence[Claim],
    *,
    k: int = K,
    window: int = WINDOW,
    span: int = SPAN,
    labels_note: str | None = None,
) -> Quote:
    """Price a check without calling anything: excerpt pass plus a worst-case full pass."""
    p = plan(note, claims, k=k, window=window, span=span)
    ex = p.excerpt_claims()
    ln = labels_note
    ex_in = (
        estimate_tokens(request(note, ex, p.excerpt, labels_note=ln).system + claims_message(ex))
        if ex
        else 0
    )
    full_in = estimate_tokens(
        request(note, claims, None, labels_note=ln).system + claims_message(claims)
    )
    return Quote(ex_in, full_in, len(claims), len(p.excerpt), len(note.utterances))
