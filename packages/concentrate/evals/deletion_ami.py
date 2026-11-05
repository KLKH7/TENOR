"""The deletion simulation replicated on public AMI recordings (docs/benchmark/DELETION_SIM.md,
"AMI replication").

Same ladder as :mod:`concentrate.evals.deletion_sim`, on the AMI meetings played into Granola and
Wispr Flow instead of the author's private meetings, so it can be released and grows as more AMI
meetings are recorded:

* ``granola_chat`` (A): Granola's own Chat answers, recorded once
  (``data/ami/results/facts/answers_granola.clean.jsonl``) and replayed at $0.
* ``notes_only`` (B): Granola's notes for every recorded meeting, answered by ``answer.v1`` on
  the original run's answer model (≈ Chat after transcript deletion).
* ``notes_only_wispr`` (B'): the same, over Wispr's notes: the product comparison.
* ``notes_facts`` (C): B plus Concentrate's fact layer, extracted from Granola's transcript
  before deletion; every transcript then deleted.
* ``notes_facts_fallback`` (D): C plus Granola's transcript, read only when C falls short.

Every condition, A included, is judged by the stage-3 judge of "Anatomy of a wrong fact"
(``judge.v1`` on Sonnet, gold answer plus the AMI speaker roster, product boilerplate stripped:
:func:`~concentrate.evals.wrong_facts.judge_gold`, :func:`~concentrate.evals.wrong_facts.
clean_answer`). A's judge requests are byte-identical to stage 3's, so they replay from cache.

Meetings come from the synced product databases (whatever :func:`load_recordings` matches), and
a question is used when every meeting it covers was recorded by every product it needs and, for
A, Granola answered it. Nothing here names a meeting: n grows with the data.
"""

from __future__ import annotations

import json
import random
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from concentrate.agents.baselines import Answer, SystemResult
from concentrate.agents.concentrate_system import ConcentrateSystem
from concentrate.ami.products import match_recordings, read_overrides
from concentrate.ami.questions import AmiQuestion
from concentrate.core.models import MeetingNote
from concentrate.evals import deletion_sim as D
from concentrate.evals.retirement import meeting_day
from concentrate.evals.scoring import judge_request_for
from concentrate.evals.wrong_facts import DROP_MIC, clean_answer, judge_gold, own_transcript
from concentrate.llm.client import LLMClient, LLMRequest, cost_usd
from concentrate.memory.fallback import DEFAULT_FALLBACK_TOKENS

GRANOLA_CHAT = "granola_chat"
CONDITIONS: tuple[str, ...] = (
    GRANOLA_CHAT,
    "notes_only",
    "notes_only_wispr",
    "notes_facts",
    "notes_facts_fallback",
)
CONDITION_LABELS = {
    GRANOLA_CHAT: "A: Granola Chat (transcripts kept)",
    "notes_only": "B: Granola notes only (after deletion)",
    "notes_only_wispr": "B': Wispr notes only",
    "notes_facts": "C: Granola notes + fact layer",
    "notes_facts_fallback": "D: C + Granola transcript when notes fall short",
}
# Which product's recordings each condition reads (A reads none: its answers are recorded).
NEEDS: dict[str, tuple[str, ...]] = {
    GRANOLA_CHAT: ("granola",),
    "notes_only": ("granola",),
    "notes_only_wispr": ("wispr",),
    "notes_facts": ("granola",),
    "notes_facts_fallback": ("granola",),
}
# The original ladder, plus B' vs B (the product comparison: Wispr's notes vs Granola's).
PAIRS: tuple[D.Pair, ...] = (
    ("notes_only vs granola_chat", "notes_only", GRANOLA_CHAT),
    ("notes_only_wispr vs notes_only", "notes_only_wispr", "notes_only"),
    ("notes_facts vs notes_only", "notes_facts", "notes_only"),
    ("notes_facts_fallback vs notes_facts", "notes_facts_fallback", "notes_facts"),
)

ORIGINAL_AGGREGATE = Path("evals/reports/real-deletion-sim-dev.aggregate.json")
FALLBACK_READER = "claude-opus-5-5"  # only if the original's aggregate is missing
JUDGE_MODEL = "claude-sonnet-5-5"  # stage 3's judge (data/ami/results/facts/stage3.json meta)
ANSWERS = Path("data/ami/results/facts/answers_granola.clean.jsonl")
OUT_DIR = Path("data/ami/results/deletion")  # local, gitignored: rows hold product text
AGGREGATE = Path("evals/reports/ami/deletion-sim.aggregate.json")  # committed, numbers only
PILOT_N = 5
PILOT_MAX_USD = 1.50
INGEST_SAFETY = 1.5  # the fact-layer build is priced by a calibrated model, not counted

# Calibration from the real DEV run (docs/benchmark/DELETION_SIM.md, "The exact cost quote" and
# "Spend vs the quote"): what token counting can't see before a call is made.
ANSWER_OVERHEAD = 255  # answer.v1 structured-output overhead per call (recorded - counted)
ANSWER_OUT = (261.0, 456.0)  # answer output tokens incl. thinking: mean, p90
FIRE_RATE = 28 / 47  # share of C's answers that triggered D's fallback on the real run
# C's memory context per question when no AMI build exists yet: C - B input on the real run,
# (541,338 - 248,707) / 47. ASSUMED for AMI until the fact layer is built; exact afterwards.
MEMORY_TOKENS = 6_226
ANSWER_TEXT_TOKENS = 150  # ASSUMED: the answer text the judge reads, before answers exist
JUDGE_FALLBACK = (246, 65.0, 84.0)  # judge overhead, output mean, p90 (real run) if no cache
JUDGE_SAMPLE = 80  # cached judge calls measured for the judge calibration


def original_reader_model(path: Path = ORIGINAL_AGGREGATE) -> str:
    """The answer model of the original (real-meeting) run, read from its committed meta."""
    if path.is_file():
        model = json.loads(path.read_text()).get("meta", {}).get("model")
        if isinstance(model, str) and model:
            return model
    return FALLBACK_READER


# -- data ------------------------------------------------------------------------------------------


def load_recordings(
    dbs: Mapping[str, Path], overrides: Path, read: Callable[[Path], Iterable[MeetingNote]]
) -> dict[str, dict[str, MeetingNote]]:
    """Product -> AMI meeting ID -> note, for every AMI recording a synced DB holds (no fixed
    meeting list: a newly synced recording joins automatically). Missing DBs are skipped."""
    pins = read_overrides(overrides)
    out: dict[str, dict[str, MeetingNote]] = {}
    for product, db in dbs.items():
        if db.is_file():
            found = match_recordings(read(db), pins.get(product))
            out[product] = dict(sorted(found.items()))
    return out


def reader_notes(recordings: Mapping[str, MeetingNote], product: str) -> list[MeetingNote]:
    """The notes the reader sees, one per recorded meeting: titled ``AMI <id>`` (Wispr can
    leave a recording untitled), transcript as scored elsewhere (Granola's microphone turns
    and Wispr's ``<<<…>>>`` banner dropped, :func:`~concentrate.evals.wrong_facts.
    own_transcript`). Notes and typed notes are unchanged."""
    out = []
    for mid, note in sorted(recordings.items()):
        own = own_transcript(note, drop_mic=DROP_MIC.get(product, True))
        out.append(own.model_copy(update={"title": f"AMI {mid}"}))
    return out


@dataclass(frozen=True)
class DeletionQuestion:
    """An AMI question in the shape ``run_slice`` and the systems read. ``gold_answer`` is
    the judge's gold (AMI gold + speaker roster); systems never see it."""

    id: str
    category: str  # the AMI question type
    question: str
    gold_answer: str
    as_of: date
    split: str
    meetings: tuple[str, ...]
    unanswerable: bool = False
    status: str = "verified"
    key_terms: tuple[str, ...] = ()
    evidence_note_ids: tuple[str, ...] = ()  # no meeting-citation scoring on AMI


def adapt(
    q: AmiQuestion, as_of: date, gold: Callable[[AmiQuestion], str] = judge_gold
) -> DeletionQuestion:
    return DeletionQuestion(
        id=q.id,
        category=q.type,
        question=q.question,
        gold_answer=gold(q),
        as_of=as_of,
        split=q.split,
        meetings=tuple(q.meetings),
        unanswerable=q.unanswerable,
        key_terms=tuple(q.key_terms),
    )


def as_of_day(notes: Iterable[MeetingNote]) -> date:
    """The questions' "today": the day of the latest recording, so every meeting is held.
    (AMI questions carry a synthetic 2005 date that predates every recording.)"""
    return max(meeting_day(m) for m in notes)


def select_questions(
    questions: Iterable[AmiQuestion],
    recorded: Mapping[str, Iterable[str]],
    answered: Iterable[str],
    conditions: Sequence[str],
) -> tuple[list[AmiQuestion], dict[str, int]]:
    """Questions every condition can answer (paired design: same questions for all), and why
    the others were skipped. ``recorded``: product -> recorded meeting IDs; ``answered``:
    question IDs Granola Chat answered (needed only with A)."""
    have = {p: set(ms) for p, ms in recorded.items()}
    chat = set(answered)
    products = sorted({p for c in conditions for p in NEEDS[c]})
    kept: list[AmiQuestion] = []
    skipped: Counter[str] = Counter()
    for q in sorted(questions, key=lambda x: x.id):
        missing = [p for p in products if not set(q.meetings) <= have.get(p, set())]
        if missing:
            skipped[f"not recorded by {'+'.join(missing)}"] += 1
        elif GRANOLA_CHAT in conditions and q.id not in chat:
            skipped["no Granola Chat answer"] += 1
        else:
            kept.append(q)
    return kept, dict(sorted(skipped.items()))


def pilot_questions(qs: Sequence[DeletionQuestion], n: int, seed: int) -> list[DeletionQuestion]:
    """A seeded sample of ``n`` DEV questions (else any), in ID order."""
    pool = sorted([q for q in qs if q.split == "dev"] or qs, key=lambda q: q.id)
    picked = random.Random(f"{seed}:deletion-ami.pilot").sample(pool, min(n, len(pool)))
    return sorted(picked, key=lambda q: q.id)


def pilot_corpus(
    notes: Sequence[MeetingNote], questions: Iterable[DeletionQuestion]
) -> list[MeetingNote]:
    """The reader notes of the meetings ``questions`` cover: a pilot builds the fact layer
    over these only, so it stays cheap."""
    wanted = {f"AMI {m}" for q in questions for m in q.meetings}
    return [n for n in notes if n.title in wanted]


def question_tags(qs: Iterable[DeletionQuestion]) -> dict[str, list[str]]:
    """Tags for the by-tag summary: the split (DEV/TEST) and one- vs two-meeting questions."""
    return {
        q.id: [q.split, "multi_meeting" if len(q.meetings) > 1 else "single_meeting"] for q in qs
    }


# -- systems ---------------------------------------------------------------------------------------


class RecordedChatSystem:
    """A: a product's own Chat answers, recorded once and replayed ($0). Chat has no abstain
    flag, so ``abstain`` is False (hedges are read from the text by ``confident_error``)."""

    prompt_ids: tuple[str, ...] = ("granola:chat",)

    def __init__(self, answers: Mapping[str, str], name: str = GRANOLA_CHAT) -> None:
        self.answers, self.name = dict(answers), name

    def recorded(self, q: Any) -> bool:
        return q.id in self.answers

    def text(self, q: Any) -> str:
        """The answer as judged: product boilerplate stripped (same as stage 3)."""
        return clean_answer(self.answers[q.id])

    def run(self, q: Any) -> SystemResult:
        return SystemResult(answer=Answer(answer=self.text(q), abstain=False, citations=[]))

    def estimate(self, q: Any) -> tuple[int, int]:
        return 0, 0


def notes_only(client: LLMClient, notes: Sequence[MeetingNote], name: str, **kw: Any) -> Any:
    """B / B': :class:`~concentrate.evals.deletion_sim.NotesOnlySystem` under its AMI name."""
    s = D.NotesOnlySystem(client, notes, **kw)
    s.name = name
    return s


class CappedBuild(ConcentrateSystem):
    """The fact-layer build shares the answer client's ledger and hard cap (the stock build
    has its own uncapped ledger)."""

    def __init__(self, client: LLMClient, **kw: Any) -> None:
        super().__init__(client, **kw)
        self.ingest_ledger = client.ledger

    def _clients(self, cache_dir: Path) -> tuple[LLMClient, LLMClient | None]:
        extract, entail = super()._clients(cache_dir)
        for c in (extract, entail):
            if c is not None:
                c.max_usd = self.client.max_usd
        return extract, entail


class AmiNotesFacts(CappedBuild, D.NotesFactsSystem):
    """C on AMI: the original's C, with the build under the run's cap."""


class AmiNotesFactsFallback(CappedBuild, D.NotesFactsFallbackSystem):
    """D on AMI: the original's D, with the build under the run's cap."""


# -- the quote -------------------------------------------------------------------------------------


def calibration(judge_pairs: list[tuple[str, int, int]], counter: D.Counter) -> D.Calibration:
    """Answer side from the real DEV run (no AMI answers exist yet); judge side measured on
    cached Sonnet ``judge.v1`` calls (stage 3's), else the real run's numbers."""
    pairs = judge_pairs[:JUDGE_SAMPLE]
    if pairs:
        counts = counter([t for t, _, _ in pairs])
        overhead = round(
            statistics.median(r - c for (_, r, _), c in zip(pairs, counts, strict=True))
        )
        outs = [float(o) for _, _, o in pairs]
        judge_out = (statistics.fmean(outs), D._p90(outs))
    else:
        overhead, judge_out = JUDGE_FALLBACK[0], JUDGE_FALLBACK[1:]
    return D.Calibration(
        answer_overhead=ANSWER_OVERHEAD,
        judge_overhead=overhead,
        answer_out=ANSWER_OUT,
        judge_out=judge_out,
        answer_text=ANSWER_TEXT_TOKENS,
        fire_rate=FIRE_RATE,
        samples={"judge": len(pairs)},
    )


def _judges(
    line: D.QuoteLine,
    questions: Sequence[DeletionQuestion],
    judge: LLMClient,
    judge2: LLMClient | None,
    counter: D.Counter,
    cal: D.Calibration,
) -> tuple[float, float]:
    """Judge (and second-judge) calls on answers not written yet; fills ``line``."""
    exp = high = 0.0
    for jclient, attr in ((judge, "judge"), (judge2, "judge2")):
        if jclient is None or not questions:
            continue
        reqs: list[LLMRequest] = [
            judge_request_for(q.question, q.gold_answer, q.unanswerable, "", False)
            for q in questions
        ]
        texts = [f"{r.system}\n\n{r.user}" for r in reqs]
        j_in = round(sum(counter(texts)) + cal.answer_text * len(texts))
        j_in += cal.judge_overhead * len(texts)
        setattr(line, f"{attr}_calls", len(texts))
        setattr(line, f"{attr}_in", j_in)
        exp += cost_usd(jclient.model, j_in, round(cal.judge_out[0] * len(texts)))
        high += cost_usd(jclient.model, j_in, round(cal.judge_out[1] * len(texts)))
    return exp, high


def quote_memory_unbuilt(
    condition: str,
    notes_system: Any,
    questions: Sequence[DeletionQuestion],
    reader: LLMClient,
    judge: LLMClient,
    judge2: LLMClient | None,
    counter: D.Counter,
    cal: D.Calibration,
    *,
    split: str,
    first_call_shared: bool,
) -> D.QuoteLine:
    """C or D before the AMI fact layer exists: the notes part is counted exactly (it is B's
    prompt), the memory context is :data:`MEMORY_TOKENS` per question (ASSUMED, from the real
    run). D's fallback call adds at most :data:`~concentrate.memory.fallback.
    DEFAULT_FALLBACK_TOKENS` of transcript passages. D's first call is C's (shared)."""
    n = len(questions)
    base = sum(counter(["\n\n".join(notes_system.prompt_texts(q)) for q in questions]))
    first_in = base + (cal.answer_overhead + MEMORY_TOKENS) * n
    line = D.QuoteLine(condition, split, n, 0, 0, 0, note="memory context ASSUMED (not built)")
    exp = high = 0.0
    if condition == "notes_facts_fallback" and first_call_shared:
        line.answer_cached = n
    else:
        line.answer_calls, line.answer_in = n, first_in
        exp += cost_usd(reader.model, first_in, round(cal.answer_out[0] * n))
        high += cost_usd(reader.model, first_in, round(cal.answer_out[1] * n))
    if condition == "notes_facts_fallback":
        f_in = first_in + DEFAULT_FALLBACK_TOKENS * n
        line.fallback_calls_max, line.fallback_in_max = n, f_in
        exp += cal.fire_rate * cost_usd(reader.model, f_in, round(cal.answer_out[0] * n))
        high += cost_usd(reader.model, f_in, round(cal.answer_out[1] * n))
    je, jh = _judges(line, questions, judge, judge2, counter, cal)
    line.usd_expected, line.usd_high = exp + je, high + jh
    return line


def quote_build(memory: ConcentrateSystem, notes: list[MeetingNote], split: str) -> D.QuoteLine:
    """The fact-layer build over ``notes`` (extraction + entailment), $0 if already built.
    Priced by the build's calibrated cost model, not counted: high = expected x 1.5."""
    from concentrate.agents.concentrate_system import COMPLETE_MARKER

    line = D.QuoteLine("fact_layer_build", split, 0, 0, 0, 0)
    if (memory.store_dir(notes) / COMPLETE_MARKER).exists():
        line.note = "already built: $0"
        return line
    tin, tout = memory.estimate_ingest(notes)
    usd = memory.estimate_ingest_usd(notes)
    line.answer_calls, line.answer_in = len(notes), tin
    line.usd_expected, line.usd_high = usd, usd * INGEST_SAFETY
    line.note = (
        f"{len(notes)} transcripts, {memory.ingest_model} extraction (+ entailment); model "
        f"estimate ~{tout:,} output tokens, high = x{INGEST_SAFETY}"
    )
    return line


MEMORY_CONDITIONS = ("notes_facts", "notes_facts_fallback")


def quote_lines(
    conditions: Sequence[str],
    questions: Sequence[DeletionQuestion],
    systems: Mapping[str, Any],
    granola_notes_system: Any,
    reader: LLMClient,
    judge: LLMClient,
    judge2: LLMClient | None,
    counter: D.Counter,
    cal: D.Calibration,
    *,
    split: str,
    built: bool,
    build: D.QuoteLine | None = None,
) -> list[D.QuoteLine]:
    """Price every condition on ``questions``: exact counts where the request can be built
    (A's judge, B, B', and C/D once the fact layer is ``built``), else
    :func:`quote_memory_unbuilt`. ``build`` is the fact-layer build line, if C or D runs."""
    shared: set[Path] = set()  # identical requests across conditions are paid once
    lines = [build] if build is not None and set(MEMORY_CONDITIONS) & set(conditions) else []
    for c in conditions:
        if c == GRANOLA_CHAT:
            chat = systems[c]

            def known(q: Any, chat: Any = chat) -> tuple[str, bool] | None:
                return (chat.text(q), False) if chat.recorded(q) else None

            line = D.quote_condition(
                None, "granola", list(questions), reader, judge2, counter, cal, split=split,
                granola_answer=known, judge=judge,
            )  # fmt: skip
            line.condition, line.note = GRANOLA_CHAT, "answers recorded ($0); judge cached = $0"
        elif c in MEMORY_CONDITIONS and not built:
            line = quote_memory_unbuilt(
                c, granola_notes_system, questions, reader, judge, judge2, counter, cal,
                split=split, first_call_shared="notes_facts" in conditions,
            )  # fmt: skip
        else:
            line = D.quote_condition(
                systems[c], c, list(questions), reader, judge2, counter, cal, split=split,
                shared=shared, judge=judge,
            )  # fmt: skip
        lines.append(line)
    return lines


def quote_markdown(
    sections: Sequence[tuple[str, list[D.QuoteLine]]],
    cal: D.Calibration,
    *,
    reader: str,
    judge: str,
    judge2: str,
    how: str,
) -> str:
    """The quote as tables, one per section (aggregates only)."""
    out = [
        f"Answer model `{reader}` (the original run's); judge `{judge}` (stage 3's), second "
        f"judge `{judge2}`. Input: {how}; + structured-output overhead (answer "
        f"+{cal.answer_overhead}, judge +{cal.judge_overhead} tokens/call). Output: mean "
        f"(answer {cal.answer_out[0]:.0f}, judge {cal.judge_out[0]:.0f}) for 'expected', p90 "
        f"(answer {cal.answer_out[1]:.0f}, judge {cal.judge_out[1]:.0f}) for 'high'. Fallback "
        f"fires on {cal.fire_rate:.0%} expected (real run), 100% high. Cached calls are $0.",
    ]
    for title, lines in sections:
        out += [
            "",
            f"### {title}",
            "",
            "| condition | q | answer calls (cached) | answer in | fallback in (max) | "
            "judge calls (cached) | judge in | judge2 calls | expected | high |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for x in lines:
            out.append(
                f"| {CONDITION_LABELS.get(x.condition, x.condition)} | {x.questions} | "
                f"{x.answer_calls} ({x.answer_cached}) | {x.answer_in:,} | "
                f"{x.fallback_in_max:,} | {x.judge_calls} ({x.judge_cached}) | {x.judge_in:,} | "
                f"{x.judge2_calls} | ${x.usd_expected:.2f} | ${x.usd_high:.2f} |"
            )
        e, h = (sum(getattr(x, k) for x in lines) for k in ("usd_expected", "usd_high"))
        out.append(f"| **total** | | | | | | | | **${e:.2f}** | **${h:.2f}** |")
        out += [
            f"- {CONDITION_LABELS.get(x.condition, x.condition)}: {x.note}" for x in lines if x.note
        ]
    return "\n".join(out) + "\n"


def totals(lines: Iterable[D.QuoteLine]) -> tuple[float, float]:
    """(expected, high) USD over ``lines``."""
    ls = list(lines)
    return sum(x.usd_expected for x in ls), sum(x.usd_high for x in ls)


# -- results ---------------------------------------------------------------------------------------


@dataclass
class RunInfo:
    """Run metadata that goes into the committed aggregate (IDs here are public AMI IDs)."""

    reader_model: str
    judge_model: str
    judge2_model: str | None
    ingest_model: str | None
    pilot: bool
    skipped: dict[str, int]
    recordings: dict[str, int]  # product -> recorded AMI meetings
    build: dict[str, Any] = field(default_factory=dict)  # fact-layer build stats (numbers)


def build_stats(memory: ConcentrateSystem, notes: list[MeetingNote]) -> dict[str, Any]:
    """The numeric fields of the fact-layer build record (calls, tokens, cost, seconds)."""
    from concentrate.agents.concentrate_system import COMPLETE_MARKER

    marker = memory.store_dir(notes) / COMPLETE_MARKER
    if not marker.exists():
        return {}
    raw = json.loads(marker.read_text())
    return {k: v for k, v in raw.items() if isinstance(v, int | float) and not isinstance(v, bool)}


def aggregate(
    report: dict[str, Any], questions: Sequence[DeletionQuestion], info: RunInfo
) -> dict[str, Any]:
    """Numbers only, safe to commit: run meta, n meetings / questions, and the summaries."""
    meta = report["meta"]
    meetings = sorted({m for q in questions for m in q.meetings})
    return {
        "meta": {
            "corpus": "ami",
            "date": meta.get("date"),
            "git": meta.get("git"),
            "seed": meta.get("seed"),
            "split": meta.get("split"),
            "pilot": info.pilot,
            "questions": len(questions),
            "meetings": len(meetings),
            "meeting_ids": meetings,
            "recordings": info.recordings,
            "skipped_questions": info.skipped,
            "category_counts": dict(sorted(Counter(q.category for q in questions).items())),
            "conditions": meta.get("conditions"),
            "reader_model": info.reader_model,
            "judge_model": info.judge_model,
            "judge2_model": info.judge2_model,
            "ingest_model": info.ingest_model,
            "build": info.build,
            "prompts": meta.get("prompts"),
            "spent_usd": meta.get("spent_usd"),
            "stopped_early": meta.get("stopped_early"),
        },
        "metrics": {"judge1": report["metrics"], "deletion": report["deletion"]},
    }


def private_texts(questions: Sequence[DeletionQuestion], rows_jsonl: Path) -> list[str]:
    """Strings that must not appear in a committed aggregate: questions, gold, every answer."""
    out = [q.question for q in questions] + [q.gold_answer for q in questions]
    for line in rows_jsonl.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line)["answer"]["answer"])
    return out
