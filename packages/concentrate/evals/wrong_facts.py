"""Stages 2 and 3 of "Anatomy of a wrong fact" (``docs/method/WRONG_FACTS.md``).

**Stage 2, note-writing.** Each claim in a product's notes on an AMI meeting is checked twice
by the grounding checker (:mod:`concentrate.evals.grounding`, prompt unchanged): against the
product's *own* transcript, and against AMI's *human* transcript. The pair of verdicts says
where a wrong claim came from (``bad`` = unsupported or contradicted):

==============  ==================  ======================================================
own transcript  human transcript    class
==============  ==================  ======================================================
supported       supported           ``ok``
supported       bad                 ``transcript_propagated``: the note faithfully repeats a
                                    transcription error
supported       inferred            ``borderline``: one notch apart, mostly checker noise
inferred        supported           ``borderline`` (the same, the other way round)
bad             supported           ``human_only``: the meeting said it, the product's
                                    transcript lost it, the note has it anyway
bad             bad or inferred     ``note_error``: the note-writer added it
inferred        bad or inferred     ``inferred``: overreach, not a hard fact error
==============  ==================  ======================================================

Both checks see the same claims, rendered the same way (:data:`RENDER`): AMI's transcript
labels each speaker by role plus the name they introduced themselves by
(:func:`facts.spoken_names`), and both calls carry :data:`LABELS_NOTE`, which says the notes'
speaker labels are arbitrary and must be matched by content.

**Stage 3, answering.** Given product answers to the ``recorded`` AMI questions, the shared
judge (``judge.v1``) grades each. For every wrong answer we ask whether the gold fact was
still there upstream: in the product's transcript (stage-1 machinery: the gold evidence
dialogue acts, anchored and recovered at $0) and in the product's notes (the grounding checker
with the notes as the "transcript" and the gold answer as the claim, plus a $0 lexical recall).
The answer is then attributed to ``transcription``, ``notes_or_answering`` or ``answering``.

Everything here is pure or goes through :class:`concentrate.llm.client.LLMClient`; the CLI
(``concentrate facts``) reads the synced databases read-only and writes local results only.
"""

from __future__ import annotations

import functools
import json
import random
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from concentrate.ami import facts as F
from concentrate.ami.corpus import AmiMeeting
from concentrate.ami.questions import AmiQuestion
from concentrate.ami.wer import content_recall, normalise
from concentrate.core.models import Channel, MeetingNote, Utterance
from concentrate.evals import grounding as G
from concentrate.evals.faithfulness import Claim, Label, split_claims, wilson
from concentrate.evals.scoring import JUDGE_PROMPT, JudgeOutput, judge_request_for
from concentrate.llm.client import PRICING, LLMClient, estimate_tokens

# Granola's microphone is a real capture channel (room noise on AMI): drop it. Wispr's "me" is
# only a speaker-name guess, so its turns are real AMI speech: keep them (ami/products.py).
DROP_MIC = {"granola": True, "wispr": False}
DEV_MEETING = "IS1008a"  # the only meeting a pilot looks at
CHECK_MODEL = G.MODEL
JUDGE_MODEL = "claude-haiku-5-5"
PILOT_USD = 0.05
PILOT_ANSWERS = 3
SAFETY = 1.5  # margin on a pilot-based extrapolation (CLAUDE.md)
# ASSUMED, only for quoting before answers exist: answer length and judge output, in tokens.
ASSUMED_ANSWER_TOKENS = 80
JUDGE_OUT_TOKENS = 90

Stage2Class = Literal[
    "ok", "note_error", "transcript_propagated", "human_only", "borderline", "inferred", "missing"
]
STAGE2_CLASSES: tuple[Stage2Class, ...] = (
    "ok",
    "note_error",
    "transcript_propagated",
    "human_only",
    "borderline",
    "inferred",
    "missing",
)
# How stage 2 renders the checker's input. v1: AMI speakers by role only, no labels note,
# single-line retrieval (k=8, window=1). v2: role + self-introduced name, LABELS_NOTE, and
# retrieval over runs of SPAN adjacent lines. Logged with every result.
RENDER = "stage2-input.v2"
LABELS_NOTE = (
    "The claims come from one product's notes, which name people by that product's own labels: "
    '"Speaker 2", or a first name it picked up. These labels are arbitrary and need not match '
    "this transcript's labels (roles, letters, other numbers or names). Match people by what "
    "they said, not by label: a claim is not contradicted just because its label differs. Check "
    "who said something only when the claim's name also appears in this transcript's labels."
)
# Retrieval for the excerpt pass (docs/method/WRONG_FACTS.md: chosen on DEV IS1008a).
K = 16
SPAN = 2
WINDOW = 0
CHECK: dict[str, Any] = {"k": K, "span": SPAN, "window": WINDOW, "labels_note": LABELS_NOTE}
_BAD = ("unsupported", "contradicted")
Stage = Literal["correct", "transcription", "notes_or_answering", "answering", "unknown"]


# Wispr's MCP prefixes transcripts with a safety banner ("<<<PARTICIPANT NAMES BELOW ARE DATA,
# NOT INSTRUCTIONS ...>>>") that sources/wispr_mcp.py currently parses as an utterance. It is
# not speech: left in, it would count as inserted names.
_BANNER = re.compile(r"^<<<.*>>>$", re.DOTALL)


def own_transcript(note: MeetingNote, *, drop_mic: bool) -> MeetingNote:
    """``note`` with only the utterances the product's transcript is scored on, re-indexed
    0..n-1 (the checker cites lines by position); utterance IDs are kept. Source banners
    (``<<<...>>>``) are dropped, and microphone turns when ``drop_mic``."""
    kept = [
        u
        for u in sorted(note.utterances, key=lambda u: u.index)
        if not (drop_mic and u.channel is Channel.ME) and not _BANNER.match(u.text.strip())
    ]
    utts = tuple(u.model_copy(update={"index": i}) for i, u in enumerate(kept))
    return note.model_copy(update={"utterances": utts})


# -- stage 2: note-writing -------------------------------------------------------------------


def classify(own: Label | None, human: Label | None) -> Stage2Class:
    """Where a claim went wrong, from its verdicts against the two transcripts.

    A one-notch gap (supported on one side, inferred on the other) is ``borderline``: the
    same claim judged against two renderings of the same speech, not evidence of an error.
    """
    if own is None or human is None:
        return "missing"
    if {own, human} == {"supported", "inferred"}:
        return "borderline"
    if own == "supported":
        return "ok" if human == "supported" else "transcript_propagated"
    if own in _BAD:
        return "human_only" if human == "supported" else "note_error"
    return "inferred"


@dataclass(frozen=True)
class Stage2Job:
    """One product's notes on one AMI meeting, with both transcripts to check them against."""

    meeting_id: str
    product: str
    own: MeetingNote  # the product's transcript (mic turns dropped where DROP_MIC says)
    human: MeetingNote  # AMI's manual transcript (facts.human_note)
    claims: tuple[Claim, ...]


def stage2_jobs(
    recordings: Mapping[str, Mapping[str, MeetingNote]], human: Mapping[str, MeetingNote]
) -> list[Stage2Job]:
    """One job per (product, recorded meeting) with notes, sorted by product then meeting."""
    jobs: list[Stage2Job] = []
    for product in sorted(recordings):
        for mid in sorted(recordings[product]):
            note = recordings[product][mid]
            claims = split_claims(note.summary_markdown or "").claims
            if mid in human and claims:
                own = own_transcript(note, drop_mic=DROP_MIC.get(product, True))
                jobs.append(Stage2Job(mid, product, own, human[mid], claims))
    return jobs


def pilot(jobs: Sequence[Stage2Job]) -> list[Stage2Job]:
    """The sandbox run: the DEV meeting (else the first meeting), every product."""
    mids = sorted({j.meeting_id for j in jobs})
    if not mids:
        return []
    mid = DEV_MEETING if DEV_MEETING in mids else mids[0]
    return [j for j in jobs if j.meeting_id == mid]


def quote_stage2(jobs: Sequence[Stage2Job], model: str = CHECK_MODEL) -> dict[str, Any]:
    """Offline price of checking every job against both transcripts ($0 to compute)."""
    rows: list[dict[str, Any]] = []
    for j in jobs:
        own, hum = G.quote(j.own, j.claims, **CHECK), G.quote(j.human, j.claims, **CHECK)
        rows.append(
            {
                "meeting": j.meeting_id,
                "product": j.product,
                "claims": len(j.claims),
                "own_lines": own.transcript_lines,
                "human_lines": hum.transcript_lines,
                "usd_no_escalation": round(own.usd(model, 0.0) + hum.usd(model, 0.0), 4),
                "usd_worst": round(own.usd(model, 1.0) + hum.usd(model, 1.0), 4),
            }
        )
    return {
        "model": model,
        "render": RENDER,
        "jobs": len(rows),
        "claims": sum(r["claims"] for r in rows),
        "calls_max": 4 * len(rows),
        "usd_no_escalation": round(sum(r["usd_no_escalation"] for r in rows), 4),
        "usd_worst": round(sum(r["usd_worst"] for r in rows), 4),
        "by_job": rows,
    }


class ClaimRow(BaseModel):
    """One note claim with both verdicts. Holds product note text: keep it local."""

    model_config = ConfigDict(frozen=True)

    meeting_id: str
    product: str
    claim_id: str
    section: str
    text: str
    own: Label | None
    human: Label | None
    cls: Stage2Class
    own_lines: list[int]  # positions in the job's own transcript (banner/mic dropped, re-indexed)
    human_lines: list[int]
    # The same lines as utterance IDs: unambiguous against the raw note (own_lines are not).
    own_ids: list[str] = []
    human_ids: list[str] = []
    own_precheck: dict[str, list[str]]  # numbers/dates/names the product transcript never says
    human_precheck: dict[str, list[str]]  # ... that AMI's manual transcript never says


def run_stage2(job: Stage2Job, client: LLMClient) -> list[ClaimRow]:
    """Check one job's claims against both transcripts and classify each claim."""
    own = G.ground_claims(job.own, job.claims, client, **CHECK)
    hum = G.ground_claims(job.human, job.claims, client, **CHECK)
    rows = []
    for c, o, h in zip(job.claims, own.claims, hum.claims, strict=True):
        rows.append(
            ClaimRow(
                meeting_id=job.meeting_id,
                product=job.product,
                claim_id=c.claim_id,
                section=c.section,
                text=c.text,
                own=o.verdict,
                human=h.verdict,
                cls=classify(o.verdict, h.verdict),
                own_lines=o.lines,
                human_lines=h.lines,
                own_ids=o.evidence,
                human_ids=h.evidence,
                own_precheck=o.precheck,
                human_precheck=h.precheck,
            )
        )
    return rows


def reclassify(rows: Iterable[ClaimRow]) -> list[ClaimRow]:
    """``rows`` with ``cls`` recomputed from their stored verdicts by :func:`classify` ($0)."""
    return [r.model_copy(update={"cls": classify(r.own, r.human)}) for r in rows]


def load_claim_rows(path: Path) -> list[ClaimRow]:
    """Read a ``stage2*.claims.jsonl`` (rows from before ``own_ids`` existed load too)."""
    return [ClaimRow.model_validate_json(x) for x in path.read_text().splitlines() if x.strip()]


def _share(k: int, n: int) -> dict[str, Any]:
    return {"k": k, "n": n, "rate": round(k / n, 4) if n else None, "ci95": wilson(k, n)}


def aggregate_stage2(rows: Sequence[ClaimRow]) -> dict[str, Any]:
    """Class shares (with Wilson 95% CIs) per product, overall and per meeting, plus the full
    own x human verdict matrix so other class boundaries can be recomputed. Numbers only."""
    out: dict[str, Any] = {}
    for product in sorted({r.product for r in rows}):
        rs = [r for r in rows if r.product == product]
        meetings = sorted({r.meeting_id for r in rs})
        out[product] = {
            "claims": len(rs),
            "classes": _classes(rs),
            "matrix": dict(Counter(f"{r.own}/{r.human}" for r in rs)),
            "by_meeting": {m: _classes([r for r in rs if r.meeting_id == m]) for m in meetings},
        }
    return out


def _classes(rs: Sequence[ClaimRow]) -> dict[str, Any]:
    counts = Counter(r.cls for r in rs)
    return {c: _share(counts[c], len(rs)) for c in STAGE2_CLASSES}


# -- stage 3: answering ----------------------------------------------------------------------


class Answer(BaseModel):
    """One product Chat answer to an AMI question (input JSONL row)."""

    model_config = ConfigDict(extra="forbid")

    question_id: str
    product: str
    answer: str


def load_answers(path: Path) -> list[Answer]:
    return [Answer.model_validate_json(x) for x in path.read_text().splitlines() if x.strip()]


def recorded(questions: Iterable[AmiQuestion]) -> dict[str, AmiQuestion]:
    return {q.id: q for q in questions if q.subset == "recorded"}


def check_answers(answers: Sequence[Answer], qs: Mapping[str, AmiQuestion]) -> None:
    """Raise if an answer is for a question outside the ``recorded`` subset."""
    bad = sorted({a.question_id for a in answers if a.question_id not in qs})
    if bad:
        raise ValueError(f"not recorded-subset question ids: {', '.join(bad[:5])}")


def placeholder_answers(qs: Mapping[str, AmiQuestion], products: Sequence[str]) -> list[Answer]:
    """Stand-in answers of ASSUMED_ANSWER_TOKENS each, to quote before answers exist."""
    filler = "word " * round(ASSUMED_ANSWER_TOKENS * 3.5 / 5)
    return [Answer(question_id=q, product=p, answer=filler) for p in products for q in sorted(qs)]


def pilot_answers(
    answers: Sequence[Answer], qs: Mapping[str, AmiQuestion], seed: int
) -> list[Answer]:
    """A seeded sample of PILOT_ANSWERS answers to DEV questions (else any)."""
    dev = [a for a in answers if qs[a.question_id].split == "dev"] or list(answers)
    return random.Random(f"{seed}:facts.pilot").sample(dev, min(PILOT_ANSWERS, len(dev)))


class TranscriptCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    acts: int  # gold evidence dialogue acts
    recovered: int  # ... found in the product transcript (facts.act_recovered)
    mean_recall: float | None
    present: bool | None  # >= half the acts recovered; None if unknown (no evidence/recording)


@dataclass
class Evidence:
    """$0 lookups shared across stage-3 rows: AMI meetings and product transcript indexes."""

    meetings: Mapping[str, AmiMeeting]
    recordings: Mapping[str, Mapping[str, MeetingNote]]
    _index: dict[tuple[str, str], F.NgramIndex] = field(default_factory=dict)
    _names: dict[str, set[str]] = field(default_factory=dict)
    _acts: dict[str, dict[str, str]] = field(default_factory=dict)

    def index(self, product: str, mid: str) -> F.NgramIndex | None:
        """N-gram index of ``product``'s transcript of ``mid`` (None if not recorded)."""
        note = self.recordings.get(product, {}).get(mid)
        if note is None:
            return None
        if (product, mid) not in self._index:
            own = own_transcript(note, drop_mic=DROP_MIC.get(product, True))
            s = F.stream(u.text for u in own.utterances)
            self._index[(product, mid)] = F.NgramIndex(s.tokens)
        return self._index[(product, mid)]

    def names(self, mid: str) -> set[str]:
        if mid not in self._names:
            human = F.human_note(self.meetings[mid])
            self._names[mid] = F.name_vocab(F.stream(u.text for u in human.utterances))
        return self._names[mid]

    def act_text(self, mid: str, da_id: str) -> str | None:
        if mid not in self._acts:
            self._acts[mid] = {a.da_id: a.text for a in self.meetings[mid].dialogue_acts}
        return self._acts[mid].get(da_id)

    def transcript(self, q: AmiQuestion, product: str) -> TranscriptCheck:
        """Was the gold evidence still in ``product``'s transcript? (stage-1 machinery, $0)"""
        recalls: list[float] = []
        found = 0
        for da in q.gold_evidence:
            mid = da.split(".", 1)[0]
            idx = self.index(product, mid)
            text = self.act_text(mid, da) if mid in self.meetings else None
            if idx is None or text is None:
                return TranscriptCheck(
                    acts=len(q.gold_evidence), recovered=0, mean_recall=None, present=None
                )
            recall, ok = F.act_recovered(text, self.names(mid), idx)
            recalls.append(recall)
            found += ok
        n = len(q.gold_evidence)
        return TranscriptCheck(
            acts=n,
            recovered=found,
            mean_recall=round(sum(recalls) / n, 4) if n else None,
            present=(found * 2 >= n) if n else None,
        )

    def notes(self, q: AmiQuestion, product: str) -> str:
        """The product's notes for every meeting the question covers ("" if none)."""
        parts = []
        for mid in q.meetings:
            note = self.recordings.get(product, {}).get(mid)
            if note is not None and note.summary_markdown:
                parts.append(f"# {mid}\n{note.summary_markdown}")
        return "\n\n".join(parts)


NOTES_LEXICAL = 0.5  # gold content-word recall in the notes that counts as "present" ($0 check)


def notes_as_transcript(meeting_id: str, markdown: str, base: MeetingNote) -> MeetingNote:
    """Notes as a checker "transcript": one line per notes claim, so the grounding checker can
    test whether the gold answer is supported by the notes."""
    claims = split_claims(markdown).claims
    t = base.created_at
    utts = tuple(
        Utterance(
            utterance_id=f"{meeting_id}:notes:{i:04d}",
            note_id=base.note_id,
            index=i,
            channel=Channel.THEM,
            speaker_name="Notes",
            diarization_label=None,
            text=" > ".join(p for p in (c.section, c.context, c.text) if p),
            start_time=t,
            end_time=t,
        )
        for i, c in enumerate(claims)
    )
    return base.model_copy(update={"utterances": utts})


def gold_claim(q: AmiQuestion) -> list[Claim]:
    return [Claim("c001", "", q.question, q.gold_answer)]


class AnswerRow(BaseModel):
    """One judged answer and, if wrong, where the fact went missing. Numbers and labels only."""

    model_config = ConfigDict(frozen=True)

    question_id: str
    product: str
    split: str
    type: str
    verdict: str
    transcript: TranscriptCheck | None  # wrong answers only
    notes_lexical_recall: float | None
    notes_verdict: Label | None  # grounding checker on the notes; None when not run
    in_notes: bool | None
    stage: Stage


def attribute(verdict: str, transcript: bool | None, notes: bool | None) -> Stage:
    """Which stage a wrong answer is pinned on."""
    if verdict == "correct":
        return "correct"
    if transcript is None:
        return "unknown"
    if not transcript:
        return "transcription"
    return "answering" if notes else "notes_or_answering"


_ROLE = {
    "PM": "Project Manager",
    "UI": "User Interface Designer",
    "ID": "Industrial Designer",
    "ME": "Marketing Expert",
}
_BOILERPLATE = re.compile(r"<transcript_note>.*?</transcript_note>", re.S)


@functools.lru_cache(maxsize=1)
def _spoken() -> dict[str, str]:
    from concentrate.ami.corpus import load_meeting
    from concentrate.ami.facts import spoken_names
    from concentrate.ami.products import AMI_SET

    return spoken_names(load_meeting(m) for m in AMI_SET)


def roster(meeting_ids: Sequence[str]) -> str:
    """Who is who in AMI: role -> the name the person introduced themselves by.

    AMI gold answers name people by role ("the Project Manager"); products name them as heard
    ("Shreedhar"). The judge gets this roster so a correct name is not marked wrong. Empty
    when AMI has no roles or self-introductions for the meeting.
    """
    from concentrate.ami.corpus import load_meeting

    try:
        names = _spoken()
        speakers = load_meeting(meeting_ids[0]).speakers
    except (KeyError, OSError):  # not an AMI meeting, or the annotations zip isn't present
        return ""
    parts: list[str] = []
    for s in speakers:
        role = _ROLE.get(s.role or "")
        name = names.get(s.participant)
        if role and name:
            parts.append(f"{role} = {name}")
    if not parts:
        return ""
    return (
        "Speaker roster (from the meeting's own introductions; products may spell names "
        "differently, so treat close spellings as the same person): " + "; ".join(parts) + "."
    )


def judge_gold(q: AmiQuestion) -> str:
    """The gold answer plus the speaker roster, for the judge."""
    r = roster(q.meetings)
    return f"{q.gold_answer}\n\n{r}" if r else q.gold_answer


def clean_answer(answer: str) -> str:
    """Drop product boilerplate that is not part of the answer (Granola's <transcript_note>)."""
    return _BOILERPLATE.sub("", answer).strip()


def run_stage3(
    answers: Sequence[Answer],
    qs: Mapping[str, AmiQuestion],
    ev: Evidence,
    judge: LLMClient,
    checker: LLMClient | None,
) -> list[AnswerRow]:
    """Judge every answer; for each wrong one, check transcript ($0) and notes.

    ``checker=None`` uses only the $0 lexical notes check.
    """
    rows = []
    for a in answers:
        q = qs[a.question_id]
        v = judge.structured(
            judge_request_for(
                q.question, judge_gold(q), q.unanswerable, clean_answer(a.answer), False
            ),
            JudgeOutput,
        ).verdict
        tc: TranscriptCheck | None = None
        lex: float | None = None
        nv: Label | None = None
        in_notes: bool | None = None
        if v != "correct":
            tc = ev.transcript(q, a.product)
            md = ev.notes(q, a.product)
            lex = round(content_recall(normalise(q.gold_answer), normalise(md)), 4) if md else 0.0
            in_notes = lex >= NOTES_LEXICAL
            base = ev.recordings.get(a.product, {}).get(q.meetings[0])
            if checker is not None and md and base is not None:
                note = notes_as_transcript(q.meetings[0], md, base)
                nv = G.ground_claims(note, gold_claim(q), checker).claims[0].verdict
                in_notes = nv == "supported"
        rows.append(
            AnswerRow(
                question_id=q.id,
                product=a.product,
                split=q.split,
                type=q.type,
                verdict=v,
                transcript=tc,
                notes_lexical_recall=lex,
                notes_verdict=nv,
                in_notes=in_notes,
                stage=attribute(v, tc.present if tc else None, in_notes),
            )
        )
    return rows


def quote_stage3(
    answers: Sequence[Answer],
    qs: Mapping[str, AmiQuestion],
    ev: Evidence,
    *,
    judge_model: str = JUDGE_MODEL,
    check_model: str = CHECK_MODEL,
) -> dict[str, Any]:
    """Offline price: the judge on every answer, plus (worst case) a notes check on every one."""
    from concentrate.llm.client import load_prompt

    system = load_prompt(JUDGE_PROMPT)
    j_in = j_out = 0
    check_worst = 0.0
    for a in answers:
        q = qs[a.question_id]
        req = judge_request_for(
            q.question, judge_gold(q), q.unanswerable, clean_answer(a.answer), False
        )
        j_in += estimate_tokens(system + req.user)
        j_out += JUDGE_OUT_TOKENS
        md = ev.notes(q, a.product)
        base = ev.recordings.get(a.product, {}).get(q.meetings[0])
        if md and base is not None:
            note = notes_as_transcript(q.meetings[0], md, base)
            check_worst += G.quote(note, gold_claim(q)).usd(check_model, 1.0)
    p_in, p_out = PRICING[judge_model]
    judge_usd = (j_in * p_in + j_out * p_out) / 1e6
    return {
        "answers": len(answers),
        "judge_model": judge_model,
        "check_model": check_model,
        "judge_usd": round(judge_usd, 4),
        "notes_check_usd_worst": round(check_worst, 4),
        "usd_worst": round(judge_usd + check_worst, 4),
        "assumed": {"judge_out_tokens": JUDGE_OUT_TOKENS},
    }


def aggregate_stage3(rows: Sequence[AnswerRow]) -> dict[str, Any]:
    """Per product and split: verdicts, and the stage each wrong answer is pinned on."""
    out: dict[str, Any] = {}
    for product in sorted({r.product for r in rows}):
        out[product] = {}
        for split in ("all", "dev", "test"):
            rs = [r for r in rows if r.product == product and split in ("all", r.split)]
            wrong = [r for r in rs if r.verdict != "correct"]
            stages = Counter(r.stage for r in wrong)
            out[product][split] = {
                "answers": len(rs),
                "verdicts": dict(Counter(r.verdict for r in rs)),
                "wrong": len(wrong),
                "stage": {
                    s: _share(stages[s], len(wrong))
                    for s in ("transcription", "notes_or_answering", "answering", "unknown")
                },
            }
    return out


def write_jsonl(rows: Iterable[BaseModel], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r.model_dump(), ensure_ascii=False) + "\n")
