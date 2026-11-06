"""The real-data slice: hand-verified questions about the user's own meetings, answered by
Granola's real agent (``query_granola_meetings``) and by Concentrate and the baselines.

Privacy: everything derived from the user's meetings (questions, gold answers, Granola's
answers, LLM cache, memory store, per-question reports) lives under ``data/real/``, which is
gitignored. Only code, prompt templates and aggregate scores are committed.

Granola is reached through :class:`ReadOnlyCaller`, which refuses every MCP tool outside a small
read-only allowlist, so this module cannot create folders, edit notes, send feedback or sign out.

Flow:

1. ``draft_requests`` -> LLM-drafted candidate questions (status ``draft``), stratified by
   :data:`CATEGORIES`, each with a proposed gold answer and the evidence it came from.
2. The user reviews every draft (``tenor real-kit review``): accept, edit or reject.
   Only ``verified`` questions are ever scored.
3. ``run_slice`` answers each verified question with every system, judges all answers with the
   benchmark judge (``judge.v1``) against the user's gold answer, and writes a report whose
   ``.jsonl`` has the same shape as ``evals.runner`` reports, so ``tenor judge-check``
   re-judges it unchanged.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from tenor.agents import corpus as cv
from tenor.agents.baselines import System
from tenor.blackbox.base import AllowlistCaller, ToolNotAllowed
from tenor.blackbox.kit import LEGACY_DIRS, ProductSystem
from tenor.blackbox.products import _GRANOLA_CITATION as _CITATION
from tenor.blackbox.products import parse_granola_answer as parse_granola_answer
from tenor.core.models import MeetingNote
from tenor.evals.scoring import (
    JUDGE_PROMPT,
    JudgeOutput,
    Row,
    judge_request_for,
    summarize,
)
from tenor.llm.client import LLMClient, LLMRequest, load_prompt
from tenor.sources.granola_mcp import McpCaller

REAL_DIR = Path("data/real")
QUESTIONS_FILE = REAL_DIR / "questions.jsonl"  # the dev split (the questions already seen)
TEST_QUESTIONS_FILE = REAL_DIR / "questions.test.jsonl"  # hand-written, never tuned on
Split = Literal["dev", "test"]
DRAFT_PROMPT = "real_draft.v1"

Category = Literal[
    "who_said",
    "decision",
    "change",
    "commitment_status",
    "disambiguation",
    "temporal",
    "unanswerable",
]
CATEGORIES: tuple[str, ...] = (
    "who_said",
    "decision",
    "change",
    "commitment_status",
    "disambiguation",
    "temporal",
    "unanswerable",
)
# Target mix for ~30 questions (the stratification the kit aims for).
TARGET: dict[str, int] = {
    "who_said": 5,
    "decision": 5,
    "change": 4,
    "commitment_status": 5,
    "disambiguation": 3,
    "temporal": 4,
    "unanswerable": 4,
}
CATEGORY_HELP: dict[str, str] = {
    "who_said": "who said or proposed X (speaker attribution)",
    "decision": "what was decided about X",
    "change": "what changed about X between meetings, or its current value",
    "commitment_status": "who committed to what, and whether it was later done or still open",
    "disambiguation": "which person (or project) is meant, when names/handles are ambiguous",
    "temporal": "when something happened or is due, or what was true as of a date",
    "unanswerable": "plausible question the meetings do NOT answer (gold: UNANSWERABLE)",
}

# The only Granola MCP tools this project may call (read-only). Everything else is refused.
READ_ONLY_TOOLS = frozenset(
    {
        "query_granola_meetings",
        "search_meetings",
        "get_meetings",
        "get_meeting_transcript",
        "search_transcripts",
        "list_meetings",
        "get_account_info",
    }
)


class RealQuestion(BaseModel):
    """One question about the user's real meetings. Only ``status == "verified"`` is scored."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    question: str
    gold_answer: str  # "UNANSWERABLE" for unanswerable questions
    key_terms: list[str] = Field(default_factory=list)  # exact-match signal (case-insensitive)
    evidence_note_ids: list[str] = Field(default_factory=list)  # meetings holding the answer
    evidence: str = ""  # quote / line IDs the draft relied on (for the reviewer)
    as_of: date
    status: Literal["draft", "verified", "rejected"] = "draft"
    origin: Literal["llm_draft", "user"] = "llm_draft"
    reviewer_note: str = ""
    split: Split = "dev"  # dev: seen while building/tuning; test: hand-written, held out

    @property
    def unanswerable(self) -> bool:
        return self.category == "unanswerable" or self.gold_answer.strip() == "UNANSWERABLE"


def questions_file(split: Split) -> Path:
    """Each split is its own file, so test questions can't leak into dev tooling by accident."""
    return TEST_QUESTIONS_FILE if split == "test" else QUESTIONS_FILE


def load_questions(path: Path = QUESTIONS_FILE) -> list[RealQuestion]:
    if not path.exists():
        return []
    return [RealQuestion.model_validate_json(x) for x in path.read_text().splitlines() if x]


def load_split(split: Split, root: Path = REAL_DIR) -> list[RealQuestion]:
    """The questions of one split; refuses a file holding the other split's questions."""
    qs = load_questions(root / questions_file(split).name)
    wrong = [q.id for q in qs if q.split != split]
    if wrong:
        raise ValueError(f"{split} file holds questions of another split: {wrong[:5]}")
    return qs


def save_questions(qs: Iterable[RealQuestion], path: Path = QUESTIONS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(q.model_dump_json() + "\n" for q in qs))
    tmp.replace(path)


def coverage(qs: list[RealQuestion]) -> dict[str, dict[str, int]]:
    """Per category: drafts, verified, rejected, and the target."""
    out = {c: {"draft": 0, "verified": 0, "rejected": 0, "target": TARGET[c]} for c in CATEGORIES}
    for q in qs:
        out[q.category][q.status] += 1
    return out


# -- read-only Granola access -------------------------------------------------------------------


class ReadOnlyCaller(AllowlistCaller):
    """Wraps an ``McpCaller`` and refuses any tool outside :data:`READ_ONLY_TOOLS`."""

    def __init__(self, inner: McpCaller) -> None:
        super().__init__(inner, READ_ONLY_TOOLS, "Granola")


class GranolaAgentSystem(ProductSystem):
    """Granola's own agent (``query_granola_meetings``) as the ``granola_agent`` system: the
    black-box :class:`~tenor.blackbox.products.GranolaProduct`, recorded once under
    ``record_dir`` and replayed afterwards (see ``blackbox.kit.ProductSystem``)."""

    def __init__(
        self,
        caller: McpCaller | None,
        record_dir: Path = LEGACY_DIRS["granola"],
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        from tenor.blackbox.products import GranolaProduct

        live = GranolaProduct(caller) if caller is not None else None
        super().__init__(
            "granola", live, record_dir, system_name="granola_agent", legacy_dirs=[], clock=clock
        )
        self.prompt_ids = ("granola:query_granola_meetings",)


# -- drafting -----------------------------------------------------------------------------------

DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "question": {"type": "string"},
                    "gold_answer": {"type": "string"},
                    "key_terms": {"type": "array", "items": {"type": "string"}},
                    "evidence_note_ids": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "string"},
                },
                "required": [
                    "category",
                    "question",
                    "gold_answer",
                    "key_terms",
                    "evidence_note_ids",
                    "evidence",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


class DraftItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Category
    question: str
    gold_answer: str
    key_terms: list[str]
    evidence_note_ids: list[str]
    evidence: str


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    questions: list[DraftItem]


def draft_request(notes: list[MeetingNote], wanted: dict[str, int], today: date) -> LLMRequest:
    """One drafting call over the whole (small) corpus, asking for ``wanted`` per category."""
    corpus = "\n\n".join(cv.full_view(m) for m in cv.by_date(notes))
    asks = "\n".join(f"- {c} ({CATEGORY_HELP[c]}): {n}" for c, n in wanted.items() if n > 0)
    return LLMRequest(
        stage="real.draft",
        prompt_id=DRAFT_PROMPT,
        system=load_prompt(DRAFT_PROMPT),
        user=f"Today's date: {today.isoformat()}\n\nQuestions wanted per category:\n{asks}\n\n"
        f"# Meetings\n\n{corpus}",
        schema=DRAFT_SCHEMA,
        max_tokens=16000,
    )


def drafts_to_questions(
    out: DraftOutput, existing: list[RealQuestion], today: date
) -> list[RealQuestion]:
    """New draft questions, numbered after ``existing`` and skipping duplicates of it."""
    seen = {q.question.strip().lower() for q in existing}
    n = len(existing)
    new: list[RealQuestion] = []
    for d in out.questions:
        if d.question.strip().lower() in seen:
            continue
        n += 1
        seen.add(d.question.strip().lower())
        gold = "UNANSWERABLE" if d.category == "unanswerable" else d.gold_answer
        new.append(
            RealQuestion(
                id=f"real-{n:03d}",
                category=d.category,
                question=d.question,
                gold_answer=gold,
                key_terms=[] if d.category == "unanswerable" else d.key_terms,
                evidence_note_ids=d.evidence_note_ids,
                evidence=d.evidence,
                as_of=today,
            )
        )
    return new


def real_directory(notes: list[MeetingNote]) -> dict[str, Any]:
    """An Enhance directory for real meetings built only from attendee metadata (no Northwind
    people). The user may hand-edit names/roles in ``data/real/directory.json`` afterwards."""
    people: dict[str, dict[str, str]] = {}
    for m in notes:
        for p in (m.owner, *m.attendees):
            email = p.email.lower()
            if email in people:
                if p.name and people[email]["name"] == email.split("@")[0]:
                    people[email]["name"] = p.name
                continue
            pid = re.sub(r"[^a-z0-9]+", "_", email.split("@")[0]).strip("_")
            people[email] = {
                "id": pid,
                "name": p.name or email.split("@")[0],
                "email": email,
                "role": "",
                "team": "",
                "org": email.split("@")[-1],
            }
    return {"people": sorted(people.values(), key=lambda x: x["id"]), "orgs": [], "projects": []}


# -- scoring ------------------------------------------------------------------------------------


def _cited_meetings(citations: list[str]) -> set[str]:
    """Meeting IDs from citations: ``[<note>:<line>]``, ``[<note>]`` or Granola URLs/IDs."""
    out = set()
    for c in citations:
        c = c.strip("[] ")
        m = _CITATION.search(c)
        out.add(m.group(1) if m else c.split(":")[0])
    return out


def meeting_citation_scores(
    q: RealQuestion, citations: list[str]
) -> tuple[float | None, float | None]:
    """(precision, recall) of cited *meetings* vs the gold evidence meetings."""
    gold = set(q.evidence_note_ids)
    if not gold or q.unanswerable:
        return None, None
    cited = _cited_meetings(citations)
    precision = len(cited & gold) / len(cited) if cited else None
    return precision, len(cited & gold) / len(gold)


def key_term_match(q: RealQuestion, answer: str, abstained: bool) -> float:
    """Fraction of the gold key terms present in the answer (unanswerable: did it abstain?)."""
    if q.unanswerable:
        return 1.0 if abstained else 0.0
    if abstained or not q.key_terms:
        return 0.0
    text = answer.lower()
    return sum(t.lower() in text for t in q.key_terms) / len(q.key_terms)


def judge_real(client: LLMClient, q: RealQuestion, answer: str, abstained: bool) -> JudgeOutput:
    """The benchmark judge (``judge.v1``), unchanged, against the user's gold answer."""
    req = judge_request_for(q.question, q.gold_answer, q.unanswerable, answer, abstained)
    return client.structured(req, JudgeOutput)


def interleave(questions: list[RealQuestion]) -> list[RealQuestion]:
    """Round-robin by category, so a run stopped by its budget drops questions evenly across
    categories instead of losing whole categories at the end of the file."""
    buckets: dict[str, list[RealQuestion]] = {}
    for q in questions:
        buckets.setdefault(q.category, []).append(q)
    out: list[RealQuestion] = []
    while any(buckets.values()):
        for b in buckets.values():
            if b:
                out.append(b.pop(0))
    return out


def run_slice(
    systems: list[System],
    questions: list[RealQuestion],
    client: LLMClient,
    *,
    seed: int,
    budget_usd: float,
    out_dir: Path,
    meta: dict[str, Any] | None = None,
    spent: Callable[[], float] | None = None,
    split: Split = "dev",
) -> dict[str, Any]:
    """Answer + judge every (system, verified question); write ``<stamp>.{json,jsonl,md}``."""
    from tenor.evals.runner import _git, to_markdown

    questions = interleave([q for q in questions if q.status == "verified"])
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    total = spent or client.ledger.spent_usd
    rows: list[Row] = []
    details: list[dict[str, Any]] = []
    stopped = None
    for q in questions:
        for s in systems:
            if total() > budget_usd:
                stopped = f"spent ${total():.2f} > budget ${budget_usd:.2f}"
                break
            res = s.run(q)
            verdict = judge_real(client, q, res.answer.answer, res.answer.abstain)
            precision, recall = meeting_citation_scores(q, res.answer.citations)
            row = Row(
                system=s.name,
                qid=q.id,
                category=q.category,
                correct=verdict.verdict == "correct",
                verdict=verdict.verdict,
                exact_match=key_term_match(q, res.answer.answer, res.answer.abstain),
                abstained=res.answer.abstain,
                unanswerable=q.unanswerable,
                cite_precision=precision,
                cite_recall=recall,
                input_tokens=res.input_tokens,
                output_tokens=res.output_tokens,
                cost_usd=res.cost_usd,
                latency_s=res.latency_s,
                tool_calls=res.tool_calls,
            )
            rows.append(row)
            details.append(
                {
                    "system": s.name,
                    "qid": q.id,
                    "question": q.question,
                    "gold": q.gold_answer,
                    "answer": res.answer.model_dump(),
                    "judge": verdict.model_dump(),
                    "retrieved": res.retrieved,
                    **res.extra,
                }
            )
        if stopped:
            break
    report: dict[str, Any] = {
        "meta": {
            "date": stamp,
            "git": _git(),
            "model": client.model,
            "seed": seed,
            "split": split,
            "track": "real",
            "questions": len(questions),
            "prompts": {s.name: list(s.prompt_ids) for s in systems} | {"judge": [JUDGE_PROMPT]},
            "budget_usd": budget_usd,
            "spent_usd": total(),
            "stopped_early": stopped,
            "citation_level": "meeting",
            **(meta or {}),
        },
        "metrics": summarize(rows, seed),
        "ledger_by_stage": client.ledger.by_stage(),
    }
    (out_dir / f"{stamp}.json").write_text(json.dumps(report, indent=2, default=str))
    (out_dir / f"{stamp}.jsonl").write_text(
        "".join(
            json.dumps({**d, **asdict(r)}, default=str) + "\n"
            for d, r in zip(details, rows, strict=True)
        )
    )
    (out_dir / f"{stamp}.md").write_text(to_markdown(report))
    report["paths"] = {k: str(out_dir / f"{stamp}.{k}") for k in ("json", "jsonl", "md")}
    return report


def aggregate_only(report: dict[str, Any]) -> dict[str, Any]:
    """The part of a report that is safe to commit: metrics and run metadata, no content."""
    keep = (
        "date",
        "git",
        "model",
        "seed",
        "split",
        "questions",
        "prompts",
        "spent_usd",
        "stopped_early",
    )
    meta = {k: report["meta"].get(k) for k in keep} | {
        k: report["meta"][k]
        for k in ("meetings", "k", "embedder", "category_counts", "ingest")
        if k in report["meta"]
    }
    return {"meta": meta, "metrics": report["metrics"]}


# -- live probe ---------------------------------------------------------------------------------


def run_probes(
    caller: McpCaller,
    probes: list[dict[str, Any]],
    out: Path,
    clock: Callable[[], float] = time.perf_counter,
) -> list[dict[str, Any]]:
    """Run ``[{"id", "theme", "tool", "args", "repeat"?}]`` through :class:`ReadOnlyCaller`,
    timing each call; append raw results to ``out`` (JSONL, private). Errors are recorded,
    not raised, so one failing probe doesn't stop the rest."""
    safe = ReadOnlyCaller(caller)
    results = []
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a") as f:
        for p in probes:
            for rep in range(int(p.get("repeat", 1))):
                start = clock()
                try:
                    raw, error = safe.call(p["tool"], p["args"]), None
                except ToolNotAllowed:
                    raise
                except Exception as exc:
                    raw, error = "", f"{type(exc).__name__}: {exc}"
                rec = {
                    "id": p["id"],
                    "theme": p.get("theme", ""),
                    "tool": p["tool"],
                    "args": p["args"],
                    "rep": rep,
                    "latency_s": clock() - start,
                    "chars": len(raw),
                    "raw": raw,
                    "error": error,
                    "at": datetime.now(UTC).isoformat(),
                }
                f.write(json.dumps(rec) + "\n")
                f.flush()
                results.append(rec)
    return results


# -- serving the real memory over MCP (WS-D's server) -------------------------------------------


def find_real_store(notes: list[MeetingNote], cache_root: Path | None = None) -> Path:
    """The newest complete Concentrate build for ``notes`` under ``data/real/cache/concentrate``."""
    from tenor.mcp_server.service import find_store

    return find_store(cache_root or REAL_DIR / "cache" / "concentrate", notes)


def open_real_memory(
    notes: list[MeetingNote],
    *,
    store_dir: Path | None = None,
    directory_path: Path = REAL_DIR / "directory.json",
    embedder: Any = None,
    answerer: Any = None,
) -> Any:
    """A read-only :class:`~tenor.mcp_server.service.MemoryService` over the real memory,
    with the same corpus and directory the build used (so citations and entities line up)."""
    import json as _json

    from tenor.agents.concentrate_system import COMPLETE_MARKER, DB_FILE
    from tenor.mcp_server.service import MemoryService
    from tenor.memory.entities import load_directory
    from tenor.memory.store import MemoryStore

    store_dir = store_dir or find_real_store(notes)
    marker = store_dir / COMPLETE_MARKER
    built = _json.loads(marker.read_text()) if marker.exists() else None
    info = {
        "fixtures": "real (data/real.db)",
        "store": str(store_dir),
        "build_complete": built is not None,
        "build_config": (built or {}).get("detail", {}).get("config"),
    }
    return MemoryService(
        MemoryStore(store_dir / DB_FILE, read_only=True),
        load_directory(directory_path, notes),
        notes,
        embedder=embedder,
        answerer=answerer,
        info=info,
    )


def spotcheck_markdown(rows: list[dict[str, Any]], questions: list[RealQuestion]) -> str:
    """Per question: each system's verdict vs the gold, listing first the questions where any
    system disagrees with the gold (the ones worth re-checking: the gold may be what's wrong).
    ``rows`` are report ``.jsonl`` lines. PRIVATE: contains questions and answers."""
    by_q: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        by_q.setdefault(r["qid"], {})[r["system"]] = r
    systems = sorted({r["system"] for r in rows})
    qmap = {q.id: q for q in questions}

    def flagged(qid: str) -> bool:
        return any(r["verdict"] != "correct" for r in by_q[qid].values())

    order = sorted(by_q, key=lambda q: (not flagged(q), q))
    lines = [
        "# Spot-check: where systems disagree with the gold (PRIVATE, do not commit)",
        "",
        f"{sum(map(flagged, by_q))} of {len(by_q)} questions have at least one non-correct "
        "verdict. For each, decide: is the system wrong, or is the gold wrong?",
        "",
        "| qid | category | " + " | ".join(systems) + " |",
        "|---|---|" + "---|" * len(systems),
    ]
    short = {"correct": "✓", "partially_correct": "~", "incorrect": "✗"}
    for qid in order:
        cells = [short.get(by_q[qid].get(s, {}).get("verdict", ""), "-") for s in systems]
        lines.append(
            f"| {qid} | {by_q[qid][next(iter(by_q[qid]))]['category']} | "
            + " | ".join(cells)
            + " |"
        )
    for qid in [q for q in order if flagged(q)]:
        q = qmap.get(qid)
        lines += [
            "",
            f"## {qid}: {q.question if q else ''}",
            f"**Gold:** {q.gold_answer if q else ''}",
        ]
        for s in systems:
            hit = by_q[qid].get(s)
            if hit:
                ans = hit["answer"]["answer"].replace("\n", " ")
                lines.append(f"- **{s}** ({hit['verdict']}): {ans[:600]}")
                lines.append(f"  - judge: {hit.get('judge', {}).get('reason', '')}")
    return "\n".join(lines) + "\n"
