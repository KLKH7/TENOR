"""Evidence compression: how much of a meeting do you need to keep?

Plan: docs/benchmark/EVIDENCE_COMPRESSION.md.

One reader gets each question's meeting notes plus a **kept layer** of the transcript, and
answers with ``answer.v1``; ``judge.v1`` grades it. Layers are built by :func:`build_layers`
from the meetings alone, **before any question is seen**: a compressor's signature takes a
meeting and nothing else, so it cannot look at a question.

Conditions built here ($0 at ingest):

* ``notes_only``: no transcript lines (the floor);
* ``full_transcript``: every line (the ceiling);
* ``random_05`` .. ``random_40``: seeded random lines up to 5/10/20/40% of transcript tokens.
  One permutation per meeting, so a smaller budget's lines are a subset of a larger one's;
* ``closed_book``: no meeting at all (``closed_book.v1``), to flag questions the model knows.

Measures per layer: kept tokens as % of transcript, **verbatim share** (% of kept words inside a
5-word run copied from the transcript), and gold-evidence recall (share of the question's gold
dialogue acts that survived). The LLM compressors of §2 (notes-anchored, salience, enhanced,
facts) are not built here; :func:`quote` prices them from their budgets.

The meeting is chosen for the reader (oracle selection: the question's own meetings), so the
test isolates compression from retrieval. Token sizes use :func:`estimate_tokens` (chars/3.5),
the same for every condition; :func:`quote` counts exactly.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from concentrate.agents import corpus as cv
from concentrate.agents.baselines import _Base, _Prompt, _user_prompt
from concentrate.ami.corpus import AmiMeeting, meeting_note
from concentrate.ami.questions import CLOSED_BOOK_PROMPT, AmiQuestion, closed_book_user
from concentrate.core.models import Channel, Utterance, utterance_id
from concentrate.evals.agent_interface import bucket, text_flags
from concentrate.evals.deletion_sim import _paired, _rates, confident_error
from concentrate.evals.scoring import JUDGE_PROMPT, JudgeOutput, judge_request_for
from concentrate.evals.stats import paired_bootstrap
from concentrate.llm.client import (
    LLMClient,
    ToolBackend,
    TurnRequest,
    estimate_tokens,
    load_prompt,
)

BUDGETS = (0.05, 0.10, 0.20, 0.40)
FLOOR, CEILING, CLOSED = "notes_only", "full_transcript", "closed_book"
VERBATIM_N = 5
NOTES_SOURCE_AMI = "ami_abstractive_summary"  # stand-in until Granola/Wispr notes are synced
SECOND_JUDGE_SHARE = 0.30


def random_name(budget: float) -> str:
    return f"random_{round(budget * 100):02d}"


ZERO_COST_CONDITIONS: tuple[str, ...] = (FLOOR, CEILING, *(random_name(b) for b in BUDGETS))


# -- meetings and layers ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Material:
    """What the reader can be shown for one meeting: its notes and its transcript lines."""

    meeting_id: str
    notes: str
    notes_source: str
    utterances: tuple[Utterance, ...]
    act_to_line: Mapping[str, str] = field(default_factory=dict)  # dialogue act -> utterance id

    def line_tokens(self) -> list[int]:
        return [estimate_tokens(cv.transcript_lines([u])) for u in self.utterances]

    def transcript_text(self) -> str:
        return cv.transcript_lines(self.utterances)


TURN_GAP_S = 1.0


def word_turns(m: AmiMeeting) -> tuple[Utterance, ...]:
    """Utterances for a meeting with no dialogue acts (EN2002b): spoken words grouped into
    turns, a new turn at a change of speaker or a pause of :data:`TURN_GAP_S` or more."""
    groups: list[list[Any]] = []
    for w in m.spoken():
        last = groups[-1][-1] if groups else None
        if last is None or w.agent != last.agent or w.start - last.end >= TURN_GAP_S:
            groups.append([w])
        else:
            groups[-1].append(w)
    epoch = datetime(2005, 1, 1, tzinfo=UTC)
    return tuple(
        Utterance(
            utterance_id=utterance_id(m.meeting_id, i),
            note_id=m.meeting_id,
            index=i,
            channel=Channel.THEM,
            speaker_name=f"Speaker {g[0].agent}",
            diarization_label=g[0].agent,
            text=" ".join(w.text for w in g),
            start_time=epoch + timedelta(seconds=g[0].start),
            end_time=epoch + timedelta(seconds=g[-1].end),
        )
        for i, g in enumerate(groups)
    )


def ami_materials(meetings: Iterable[AmiMeeting]) -> dict[str, Material]:
    """AMI meetings with AMI's own abstractive summary as the notes. A meeting without
    dialogue acts gets :func:`word_turns` (it has no questions, but counts for sizes).

    The summary is a **stand-in**: it is the text most gold answers were taken from, so
    ``notes_only`` is near its ceiling on summary-derived questions until it is replaced by a
    product's notes (:func:`with_notes`)."""
    out: dict[str, Material] = {}
    for m in meetings:
        note = meeting_note(m)
        out[m.meeting_id] = Material(
            meeting_id=m.meeting_id,
            notes=note.summary_markdown or "(no notes)",
            notes_source=NOTES_SOURCE_AMI,
            utterances=note.utterances or word_turns(m),
            act_to_line={
                a.da_id: u.utterance_id
                for a, u in zip(m.dialogue_acts, note.utterances, strict=True)
            },
        )
    return out


def with_notes(
    materials: Mapping[str, Material], notes: Mapping[str, str], source: str
) -> dict[str, Material]:
    """Swap in another notes source (e.g. Granola's notes once synced); meetings without one
    keep theirs. The transcript, and so every kept layer, is unchanged."""
    return {
        mid: Material(m.meeting_id, notes[mid], source, m.utterances, m.act_to_line)
        if mid in notes
        else m
        for mid, m in materials.items()
    }


@dataclass(frozen=True)
class KeptLayer:
    """One condition's kept transcript for one meeting. ``line_ids`` is None for abstractive
    layers (their text is not a subset of lines)."""

    condition: str
    meeting_id: str
    text: str
    line_ids: tuple[str, ...] | None
    kept_tokens: int
    transcript_tokens: int
    verbatim_share: float | None  # None when nothing is kept

    @property
    def kept_pct(self) -> float:
        return self.kept_tokens / self.transcript_tokens if self.transcript_tokens else 0.0


_LINE_PREFIX = re.compile(r"^\[[^\]]+\]\s+[^:\n]{0,80}:\s*", re.MULTILINE)
_WORD = re.compile(r"[a-z0-9']+")


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def verbatim_share(kept: str, transcript: str, n: int = VERBATIM_N) -> float | None:
    """Share of the kept layer's words that sit inside an ``n``-word run also found in the
    transcript (line IDs and speaker labels stripped first). Lines shorter than ``n`` words are
    matched whole. 1.0 for any subset of transcript lines; lower for a rewrite."""
    src = _words(_LINE_PREFIX.sub("", transcript))
    lines = [_words(x) for x in _LINE_PREFIX.sub("", kept).splitlines()]
    total = sum(len(x) for x in lines)
    if total == 0:
        return None
    grams: dict[int, set[tuple[str, ...]]] = {}

    def has(g: tuple[str, ...]) -> bool:
        k = len(g)
        if k not in grams:
            grams[k] = {tuple(src[i : i + k]) for i in range(len(src) - k + 1)}
        return g in grams[k]

    covered = 0
    for ws in lines:
        k = min(n, len(ws))
        hit = [False] * len(ws)
        for i in range(len(ws) - k + 1):
            if has(tuple(ws[i : i + k])):
                hit[i : i + k] = [True] * k
        covered += sum(hit)
    return covered / total


def extractive_layer(condition: str, m: Material, keep: Iterable[int]) -> KeptLayer:
    """The layer keeping utterances ``keep`` (any order; shown in time order)."""
    idx = sorted(set(keep))
    utts = [m.utterances[i] for i in idx]
    text = cv.transcript_lines(utts)
    tokens = m.line_tokens()
    return KeptLayer(
        condition=condition,
        meeting_id=m.meeting_id,
        text=text,
        line_ids=tuple(u.utterance_id for u in utts),
        kept_tokens=sum(tokens[i] for i in idx),
        transcript_tokens=sum(tokens),
        verbatim_share=verbatim_share(text, m.transcript_text()),
    )


def keep_random(m: Material, budget: float, seed: int) -> list[int]:
    """Seeded random lines up to ``budget`` of transcript tokens. One permutation per
    (seed, meeting), walked greedily (a line that would overflow is skipped), so budgets nest."""
    tokens = m.line_tokens()
    cap = budget * sum(tokens)
    order = list(range(len(tokens)))
    random.Random(f"{seed}:{m.meeting_id}:random_lines").shuffle(order)
    kept, used = [], 0
    for i in order:
        if used + tokens[i] <= cap:
            kept.append(i)
            used += tokens[i]
    return kept


Compressor = Callable[[Material], KeptLayer]  # a meeting in, a layer out: no question


def zero_cost_compressors(seed: int) -> dict[str, Compressor]:
    """The $0 conditions' compressors (``closed_book`` has no layer: it has no meeting)."""
    out: dict[str, Compressor] = {
        FLOOR: lambda m: extractive_layer(FLOOR, m, []),
        CEILING: lambda m: extractive_layer(CEILING, m, range(len(m.utterances))),
    }
    for b in BUDGETS:
        out[random_name(b)] = _random_compressor(b, seed)
    return out


def _random_compressor(budget: float, seed: int) -> Compressor:
    name = random_name(budget)
    return lambda m: extractive_layer(name, m, keep_random(m, budget, seed))


Layers = Mapping[tuple[str, str], KeptLayer]  # (condition, meeting id) -> layer


def build_layers(
    materials: Mapping[str, Material], compressors: Mapping[str, Compressor]
) -> Layers:
    """Every (condition, meeting) layer, built from meetings only, before questions are read."""
    return {
        (c, mid): compress(m)
        for c, compress in compressors.items()
        for mid, m in sorted(materials.items())
    }


def evidence_recall(
    q: AmiQuestion, layers: Layers, condition: str, materials: Mapping[str, Material]
) -> float | None:
    """Share of the question's gold dialogue acts kept by ``condition`` (None: no gold evidence
    or an abstractive layer)."""
    gold = [
        materials[mid].act_to_line[da]
        for mid in q.meetings
        for da in q.gold_evidence
        if da in materials[mid].act_to_line
    ]
    kept: set[str] = set()
    for mid in q.meetings:
        ids = layers[(condition, mid)].line_ids
        if ids is None:
            return None
        kept |= set(ids)
    return sum(g in kept for g in gold) / len(gold) if gold else None


def sizes(
    layers: Layers, questions: Sequence[AmiQuestion], materials: Mapping[str, Material]
) -> dict[str, dict[str, Any]]:
    """Per condition: mean kept % and verbatim share over meetings, and mean gold-evidence
    recall over questions that have gold evidence ($0, computed before any answer)."""
    out: dict[str, dict[str, Any]] = {}
    for c in dict.fromkeys(c for c, _ in layers):
        ls = [v for (cc, _), v in layers.items() if cc == c]
        vs = [x.verbatim_share for x in ls if x.verbatim_share is not None]
        rec = [r for q in questions if (r := evidence_recall(q, layers, c, materials)) is not None]
        out[c] = {
            "meetings": len(ls),
            "kept_pct_mean": sum(x.kept_pct for x in ls) / len(ls),
            "kept_pct_by_meeting": {x.meeting_id: round(x.kept_pct, 4) for x in ls},
            "verbatim_share_mean": sum(vs) / len(vs) if vs else None,
            "evidence_recall_mean": sum(rec) / len(rec) if rec else None,
            "evidence_questions": len(rec),
        }
    return out


# -- readers ---------------------------------------------------------------------------------------


def meeting_block(m: Material, layer: KeptLayer) -> str:
    """One meeting as the reader sees it: notes, then the kept lines and how much they are."""
    head = f"# Meeting {m.meeting_id} [{m.meeting_id}]\n## Notes\n{m.notes.strip()}"
    if not layer.text:
        return f"{head}\n## Transcript\n(no transcript lines kept)"
    n = len(layer.line_ids) if layer.line_ids is not None else None
    what = f"{n} of {len(m.utterances)} lines, " if n is not None else ""
    return (
        f"{head}\n## Kept transcript ({what}{layer.kept_pct:.0%} of the transcript, "
        f"selected before any question was asked)\n{layer.text}"
    )


class LayerReader(_Base):
    """Notes plus one condition's kept layer for each of the question's meetings, one
    ``answer.v1`` call. The same reader for every condition; only the layer differs."""

    def __init__(
        self,
        client: LLMClient,
        condition: str,
        materials: Mapping[str, Material],
        layers: Layers,
        backend: ToolBackend | None = None,
    ) -> None:
        super().__init__(client, backend)
        self.name = condition
        self.materials, self.layers = materials, layers

    def _user(self, q: AmiQuestion) -> str:
        blocks = [
            meeting_block(self.materials[mid], self.layers[(self.name, mid)]) for mid in q.meetings
        ]
        return "<meetings>\n" + "\n\n".join(blocks) + f"\n</meetings>\n\n{_user_prompt(q)}"

    def _prompt(self, q: Any) -> _Prompt:
        return _Prompt(
            self.instructions, self._user(q), cache_system=False, retrieved=list(q.meetings)
        )

    def prompt_texts(self, q: AmiQuestion) -> tuple[str, str]:
        """(system, user) exactly as sent."""
        return self.instructions, self._user(q)


class ClosedBookReader(_Base):
    """The control: the question alone, ``closed_book.v1`` (answers allowed from knowledge)."""

    name = CLOSED
    prompt_ids = (CLOSED_BOOK_PROMPT,)

    def __init__(self, client: LLMClient, backend: ToolBackend | None = None) -> None:
        super().__init__(client, backend)
        self.instructions = load_prompt(CLOSED_BOOK_PROMPT)

    def _prompt(self, q: Any) -> _Prompt:
        return _Prompt(self.instructions, closed_book_user(q), cache_system=False, retrieved=[])

    def _turn_request(self, p: _Prompt) -> TurnRequest:
        base = super()._turn_request(p)
        return TurnRequest(**{**asdict(base), "prompt_id": CLOSED_BOOK_PROMPT})

    def prompt_texts(self, q: AmiQuestion) -> tuple[str, str]:
        return self.instructions, closed_book_user(q)


# -- running and scoring ---------------------------------------------------------------------------


def second_judge_sample(
    keys: Sequence[tuple[str, str]], seed: int, share: float = SECOND_JUDGE_SHARE
) -> set[tuple[str, str]]:
    """A seeded ``share`` of (condition, qid) answers for the second judge."""
    ordered = sorted(keys)
    k = round(share * len(ordered))
    return set(random.Random(f"{seed}:judge2").sample(ordered, k))


def run_compression(
    readers: Sequence[_Base],
    questions: Sequence[AmiQuestion],
    judge: LLMClient,
    *,
    layers: Layers,
    materials: Mapping[str, Material],
    seed: int,
    budget_usd: float,
    out_dir: Path,
    spent: Callable[[], float],
    judge2: LLMClient | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Answer and judge every (reader, question), then second-judge a seeded 30% sample.
    Stops when ``spent()`` passes ``budget_usd``. Writes ``<stamp>.compression.{jsonl,json}``."""
    from concentrate.evals.runner import _git

    rows: list[dict[str, Any]] = []
    stopped = None
    for q in questions:
        for r in readers:
            if spent() > budget_usd:
                stopped = f"spent ${spent():.2f} > budget ${budget_usd:.2f}"
                break
            res = r.run(q)
            a = res.answer
            v = judge.structured(
                judge_request_for(q.question, q.gold_answer, False, a.answer, a.abstain),
                JudgeOutput,
            )
            flagged = a.abstain or any(text_flags(a.answer).values())
            cond = r.name
            rows.append(
                {
                    "system": cond,
                    "qid": q.id,
                    "type": q.type,
                    "category": q.type,
                    "split": q.split,
                    "question": q.question,
                    "gold": q.gold_answer,
                    "answer": a.model_dump(),
                    "abstained": a.abstain,
                    "verdict": v.verdict,
                    "judge_reason": v.reason,
                    "confident_error": confident_error(v.verdict, a.answer, a.abstain),
                    "bucket": bucket(v.verdict, flagged, False),
                    "kept_pct": None
                    if cond == CLOSED
                    else sum(layers[(cond, m)].kept_tokens for m in q.meetings)
                    / sum(layers[(cond, m)].transcript_tokens for m in q.meetings),
                    "evidence_recall": None
                    if cond == CLOSED
                    else evidence_recall(q, layers, cond, materials),
                    "input_tokens": res.input_tokens,
                    "output_tokens": res.output_tokens,
                    "cost_usd": res.cost_usd,
                }
            )
        if stopped:
            break
    if judge2 is not None:
        sample = second_judge_sample([(x["system"], x["qid"]) for x in rows], seed)
        by_id = {q.id: q for q in questions}
        for x in rows:
            if (x["system"], x["qid"]) in sample and spent() <= budget_usd:
                q = by_id[x["qid"]]
                req = judge_request_for(
                    q.question, q.gold_answer, False, x["answer"]["answer"], x["abstained"]
                )
                x["judge2_verdict"] = judge2.structured(req, JudgeOutput).verdict
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report = {
        "meta": {
            "date": stamp,
            "git": _git(),
            "reader_model": readers[0].client.model if readers else None,
            "judge_model": judge.model,
            "judge2_model": judge2.model if judge2 else None,
            "prompts": {
                "answer": "answer.v1",
                "closed_book": CLOSED_BOOK_PROMPT,
                "judge": JUDGE_PROMPT,
            },
            "seed": seed,
            "budget_usd": budget_usd,
            "spent_usd": spent(),
            "stopped_early": stopped,
            "notes_sources": sorted({m.notes_source for m in materials.values()}),
            **(meta or {}),
        },
        "sizes": sizes(layers, questions, materials),
        "summary": summarize(rows, seed),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stamp}.compression.jsonl").write_text(
        "".join(json.dumps(x, default=str) + "\n" for x in rows)
    )
    (out_dir / f"{stamp}.compression.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def summarize(rows: Sequence[dict[str, Any]], seed: int) -> dict[str, Any]:
    """Per condition and split: accuracy (bootstrap CI), confident errors, the four answer types,
    by question type. Paired vs the ceiling (mean difference and 95% CI: H1 reads the lower
    bound) and vs the floor. ``headline`` repeats it on questions closed-book got wrong."""
    known = {x["qid"] for x in rows if x["system"] == CLOSED and x["verdict"] == "correct"}
    out: dict[str, Any] = {}
    for split in sorted({x["split"] for x in rows}):
        rs = [x for x in rows if x["split"] == split]
        out[split] = {
            "all": _block(rs, seed),
            "headline": _block([x for x in rs if x["qid"] not in known], seed),
            "closed_book_correct": sorted(q for q in known if any(x["qid"] == q for x in rs)),
        }
    return out


def _block(rows: Sequence[dict[str, Any]], seed: int) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = {}
    for x in rows:
        by.setdefault(x["system"], []).append(x)
    conds: dict[str, Any] = {}
    for c, rs in by.items():
        types: dict[str, list[dict[str, Any]]] = {}
        for x in rs:
            types.setdefault(x["type"], []).append(x)
        conds[c] = {
            **_rates(rs, seed),
            "buckets": {
                b: sum(x["bucket"] == b for x in rs) for b in sorted({x["bucket"] for x in rs})
            },
            "by_type": {t: _rates(v, seed) for t, v in sorted(types.items())},
        }
    ok = {c: {x["qid"]: x["verdict"] == "correct" for x in rs} for c, rs in by.items()}
    paired: dict[str, Any] = {}
    for ref in (CEILING, FLOOR):
        if ref not in ok:
            continue
        for c in ok:
            if c in (ref, CLOSED):
                continue
            qs = sorted(set(ok[c]) & set(ok[ref]))
            _, lo, hi = paired_bootstrap([ok[c][q] for q in qs], [ok[ref][q] for q in qs], seed)
            paired[f"{c} vs {ref}"] = {**_paired(ok[c], ok[ref], qs), "diff_ci": [lo, hi]}
    return {"conditions": conds, "paired": paired}


# -- the cost quote --------------------------------------------------------------------------------

LLM_METHODS = ("notes_anchored", "salience", "enhanced")  # §2's ingest-time compressors
FACTS = "facts"
Counter = Callable[[list[str]], list[int]]


def planned_conditions() -> list[str]:
    """Every answer condition in the plan (§2), closed-book excluded."""
    llm = [f"{m}_{round(b * 100):02d}" for m in LLM_METHODS for b in BUDGETS]
    return [*ZERO_COST_CONDITIONS, *llm, FACTS]


def proxy_for(condition: str) -> str:
    """The built condition whose prompt stands in for ``condition`` when pricing it: an LLM
    method at budget b is sized like random lines at b (it is given that target length); the
    facts layer is sized like 10% (ASSUMED: its size on AMI is unknown until built)."""
    if condition in ZERO_COST_CONDITIONS:
        return condition
    if condition == FACTS:
        return random_name(0.10)
    return f"random_{condition.rsplit('_', 1)[1]}"


@dataclass(frozen=True)
class QuoteAssumptions:
    """Everything the quote can't count. ``ASSUMED`` values are labelled in the output."""

    reader: str = "claude-haiku-5-5"
    judge: str = "claude-haiku-5-5"
    judge2: str = "claude-sonnet-5-5"
    judge2_share: float = SECOND_JUDGE_SHARE
    # Measured on cached deletion-sim calls (docs/benchmark/DELETION_SIM.md hand-over): the
    # structured-output schema and framing that count_tokens on the text doesn't see.
    answer_overhead: int = 255
    judge_overhead: int = 246
    answer_out: tuple[float, float] = (261.0, 456.0)  # mean, p90; measured on Opus answers
    judge_out: tuple[float, float] = (72.0, 90.0)  # replaced by the cached Haiku judge calls
    answer_text_tokens: int = 80  # ASSUMED: what the judge reads of an answer
    ingest_overhead: int = 600  # ASSUMED: an ingest prompt's instructions
    anchored_out: int = 1500  # ASSUMED: grounding-checker output per meeting
    salience_out_per_line: int = 8  # ASSUMED: a line id and a score
    real_questions: int = 62  # 47 DEV (seen) + ~15 TEST (plan §3)
    real_meetings: int = 4
    real_transcript_tokens: int = 10_000  # ASSUMED per real meeting: notes ~1k x "roughly 10x"
    real_notes_tokens: int = 1_000  # DELETION_SIM: ~4k tokens of notes over 4 meetings
    real_lines: int = 400  # ASSUMED per real meeting
    sonnet_reader: str = "claude-sonnet-5-5"


@dataclass
class QuoteRow:
    part: str
    model: str
    calls: int
    input_tokens: int
    output_expected: float
    output_high: float
    basis: str
    batch: bool = False

    def usd(self, high: bool = False) -> float:
        from concentrate.llm.client import cost_usd

        out = self.output_high if high else self.output_expected
        return cost_usd(self.model, self.input_tokens, round(out), batch=self.batch)


def _p90(xs: Sequence[float]) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, round(0.9 * (len(s) - 1)))]


def judge_output_calibration(
    pairs: Sequence[tuple[str, int, int]], fallback: tuple[float, float]
) -> tuple[float, float]:
    """(mean, p90) output tokens of recorded judge calls, or ``fallback`` when there are none."""
    outs = [float(o) for _, _, o in pairs]
    return (sum(outs) / len(outs), _p90(outs)) if outs else fallback


def quote(
    questions: Sequence[AmiQuestion],
    materials: Mapping[str, Material],
    layers: Layers,
    counter: Counter,
    a: QuoteAssumptions | None = None,
    conditions: Sequence[str] | None = None,
    optional: bool = True,
) -> list[QuoteRow]:
    """Price the full plan (§5) without spending: AMI answers counted from the exact prompts
    (LLM methods sized by :func:`proxy_for`), closed-book, the real-meeting questions from
    :class:`QuoteAssumptions`, ingest, the Haiku judge on every answer, a Sonnet re-judge of 30%,
    and the optional batched Sonnet reader. Counting is per prompt part (instructions, each
    meeting block, each question), so a few framing tokens are counted more than once.
    ``conditions`` prices a subset (ingest rows only for LLM methods in it); set
    ``a.real_questions = 0`` to price AMI alone."""
    a = a or QuoteAssumptions()
    conds = list(conditions or planned_conditions())
    instr = load_prompt("answer.v1")
    closed = load_prompt(CLOSED_BOOK_PROMPT)
    mids = sorted({m for q in questions for m in q.meetings})
    built = sorted({proxy_for(c) for c in conds})
    keys = [(c, m) for c in built for m in mids]
    blocks = [meeting_block(materials[m], layers[(c, m)]) for c, m in keys]
    qparts = [_user_prompt(q) for q in questions]
    cb = [closed_book_user(q) for q in questions]
    judge_texts = [
        (lambda r: f"{r.system}\n\n{r.user}")(
            judge_request_for(q.question, q.gold_answer, False, "", False)
        )
        for q in questions
    ]
    trans = [materials[m].transcript_text() + "\n" + materials[m].notes for m in sorted(materials)]
    counts = counter([instr, closed, *blocks, *qparts, *cb, *judge_texts, *trans])
    it = iter(counts)
    n_instr, n_closed = next(it), next(it)
    block_tok = {k: next(it) for k in keys}
    q_tok = [next(it) for _ in qparts]
    cb_tok = [next(it) for _ in cb]
    j_tok = [next(it) for _ in judge_texts]
    t_tok = dict(zip(sorted(materials), (next(it) for _ in trans), strict=True))

    rows: list[QuoteRow] = []
    nq = len(questions)
    ami_in = 0
    for c in conds:
        p = proxy_for(c)
        ami_in += sum(
            n_instr + a.answer_overhead + qt + sum(block_tok[(p, m)] for m in q.meetings)
            for q, qt in zip(questions, q_tok, strict=True)
        )
    n_ans = nq * len(conds)
    rows.append(
        QuoteRow(
            "answers: AMI",
            a.reader,
            n_ans,
            ami_in,
            a.answer_out[0] * n_ans,
            a.answer_out[1] * n_ans,
            f"{nq} q x {len(conds)} conditions; inputs counted (LLM methods sized as random lines "
            "at their budget, facts as 10%); output = deletion-sim calibration (Opus)",
        )
    )
    cb_in = sum(n_closed + a.answer_overhead + t for t in cb_tok)
    rows.append(
        QuoteRow(
            "answers: AMI closed-book",
            a.reader,
            nq,
            cb_in,
            a.answer_out[0] * nq,
            a.answer_out[1] * nq,
            "counted",
        )
    )
    rq = a.real_questions
    ctx: dict[str, float] = {
        c: a.real_meetings * (a.real_notes_tokens + _budget_of(c) * a.real_transcript_tokens)
        for c in conds
    }
    real_in = sum(rq * (n_instr + a.answer_overhead + 30 + ctx[c]) for c in conds)
    n_real = rq * len(conds)
    if rq:
        rows.append(_real_row(a, n_real, real_in))
    ami_meetings = sorted(materials)
    real_tt = [a.real_transcript_tokens + a.real_notes_tokens] * (a.real_meetings if rq else 0)
    all_tt = [t_tok[m] for m in ami_meetings] + real_tt
    lines = [len(materials[m].utterances) for m in ami_meetings] + [a.real_lines] * len(real_tt)
    n_m = len(all_tt)
    methods = {c.rsplit("_", 1)[0] for c in conds if c.rsplit("_", 1)[0] in LLM_METHODS}
    if "notes_anchored" in methods:
        rows.append(
            QuoteRow(
                "ingest: notes-anchored",
                a.reader,
                n_m,
                sum(all_tt) + a.ingest_overhead * n_m,
                a.anchored_out * n_m,
                a.anchored_out * n_m * 1.5,
                f"1 call per meeting ({len(ami_meetings)} AMI counted + {len(real_tt)} real "
                "ASSUMED); output ASSUMED",
            )
        )
    if "salience" in methods:
        sal_out = a.salience_out_per_line * sum(lines)
        rows.append(
            QuoteRow(
                "ingest: salience",
                a.reader,
                n_m,
                sum(all_tt) + a.ingest_overhead * n_m,
                sal_out,
                sal_out * 1.5,
                "1 call per meeting, ranks every line; output ASSUMED per line",
            )
        )
    if "enhanced" in methods:
        enh_out = sum(b * t for b in BUDGETS for t in all_tt)
        rows.append(
            QuoteRow(
                "ingest: enhanced",
                a.reader,
                n_m * len(BUDGETS),
                (sum(all_tt) + a.ingest_overhead * n_m) * len(BUDGETS),
                enh_out,
                enh_out * 1.25,
                "1 call per meeting per budget; output = budget x transcript",
            )
        )
    n_judged = n_ans + nq + n_real
    j_mean = sum(j_tok) / len(j_tok) + a.answer_text_tokens + a.judge_overhead
    rows.append(
        QuoteRow(
            "judge (every answer)",
            a.judge,
            n_judged,
            round(j_mean * n_judged),
            a.judge_out[0] * n_judged,
            a.judge_out[1] * n_judged,
            "AMI judge prompts counted, mean applied to real; output from cached Haiku judge calls",
        )
    )
    n2 = round(a.judge2_share * n_judged)
    rows.append(
        QuoteRow(
            f"second judge ({a.judge2_share:.0%})",
            a.judge2,
            n2,
            round(j_mean * n2),
            a.judge_out[0] * n2,
            a.judge_out[1] * n2,
            "same prompt, seeded sample",
        )
    )
    if optional:
        rows.append(_sonnet_row(a, questions, conds, q_tok, block_tok, n_instr, ctx))
    return rows


def _real_row(a: QuoteAssumptions, n: int, tokens: float) -> QuoteRow:
    return QuoteRow(
        "answers: real meetings",
        a.reader,
        n,
        round(tokens),
        a.answer_out[0] * n,
        a.answer_out[1] * n,
        f"ASSUMED: {a.real_questions} q, each reads all {a.real_meetings} meetings at "
        f"{a.real_transcript_tokens:,} transcript + {a.real_notes_tokens:,} notes tokens "
        "(real data not read in this quote)",
    )


def _sonnet_row(
    a: QuoteAssumptions,
    questions: Sequence[AmiQuestion],
    conds: Sequence[str],
    q_tok: Sequence[int],
    block_tok: Mapping[tuple[str, str], int],
    n_instr: int,
    ctx: Mapping[str, float],
) -> QuoteRow:
    """The optional batched Sonnet reader on the 20% layers, the floor and the ceiling."""
    son = [c for c in conds if c in (FLOOR, CEILING) or c.endswith("_20")]
    s_in = sum(
        n_instr + a.answer_overhead + qt + sum(block_tok[(proxy_for(c), m)] for m in q.meetings)
        for c in son
        for q, qt in zip(questions, q_tok, strict=True)
    ) + sum(a.real_questions * (n_instr + a.answer_overhead + 30 + ctx[c]) for c in son)
    n = (len(questions) + a.real_questions) * len(son)
    return QuoteRow(
        "OPTIONAL: Sonnet reader (20% + floor + ceiling), batched",
        a.sonnet_reader,
        n,
        round(s_in),
        a.answer_out[0] * n,
        a.answer_out[1] * n,
        f"{len(son)} conditions; judge not included",
        batch=True,
    )


def _budget_of(condition: str) -> float:
    if condition == FLOOR:
        return 0.0
    if condition == CEILING:
        return 1.0
    return int(proxy_for(condition).rsplit("_", 1)[1]) / 100


def quote_markdown(rows: Sequence[QuoteRow], counting: str) -> str:
    """The quote as a table, with required and optional totals."""
    out = [
        f"Token counting: {counting}. Expected = mean output; high = p90 output.",
        "",
        "| part | model | calls | input tokens | output (exp) | expected | high | basis |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| {r.part} | `{r.model}` | {r.calls:,} | {r.input_tokens:,} | "
            f"{round(r.output_expected):,} | ${r.usd():.2f} | ${r.usd(True):.2f} | {r.basis} |"
        )
    req = [r for r in rows if not r.part.startswith("OPTIONAL")]
    opt = [r for r in rows if r.part.startswith("OPTIONAL")]
    e, h = sum(r.usd() for r in req), sum(r.usd(True) for r in req)
    out.append(
        f"| **total (Haiku reader)** | | {sum(r.calls for r in req):,} | | | "
        f"**${e:.2f}** | **${h:.2f}** | |"
    )
    if opt:
        out.append(
            f"| total incl. optional | | | | | ${e + sum(r.usd() for r in opt):.2f} | "
            f"${h + sum(r.usd(True) for r in opt):.2f} | |"
        )
    return "\n".join(out) + "\n"
