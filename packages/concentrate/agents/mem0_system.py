"""B4: Mem0 (open-source ``mem0ai``) as a write-time memory baseline.

Ingestion feeds every meeting, chronologically, into one Mem0 memory (one ``user_id`` for the
whole corpus): first the header + AI summary + typed notes, then the transcript in chunks of
``CHUNK_UTTERANCES`` utterances with speaker labels. Mem0's own pipeline (v2.x "additive"
extraction: one LLM call per ``add``, then local embedding, hash dedup, spaCy entity linking and
fastembed BM25) turns each chunk into memories. Everything is local except the LLM calls:

* LLM: Anthropic, ``CONCENTRATE_INGEST_MODEL`` (default ``claude-sonnet-5-5``), via Mem0's own
  Anthropic provider whose SDK client is wrapped by :class:`MeteredAnthropic` to (a) make
  requests valid for current models and (b) record every call's usage for cost accounting.
* Embedder: local ``sentence-transformers/all-MiniLM-L6-v2`` (same model as B0).
* Vector store: local on-disk Qdrant under ``data/cache/mem0/<corpus hash>/<config hash>/``.

At question time the top-``k`` memories are retrieved with ``Memory.search`` and answered on the
same path as B0-B2 (``answer.v1``, ``ANSWER_SCHEMA``, shared ``LLMClient``). Memories carry the
source meeting's ID in metadata, so citations are meeting IDs: line-level provenance is lost.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from concentrate import watchdog
from concentrate.agents import corpus as cv
from concentrate.agents.baselines import (
    ANSWER_PROMPT,
    ANSWER_SCHEMA,
    Answer,
    IngestStats,
    QuestionLike,
    SystemResult,
    _result,
    _user_prompt,
)
from concentrate.agents.progress import read_progress
from concentrate.agents.retrieval import DEFAULT_EMBEDDING_MODEL
from concentrate.core.models import MeetingNote
from concentrate.llm.client import (
    _FALLBACK_MODELS,
    PRICING,
    LLMClient,
    ToolBackend,
    TurnRequest,
    cost_usd,
    estimate_tokens,
    load_prompt,
    run_turn,
)
from concentrate.llm.resilience import anthropic_client, call_with_retries, timeout_for

logger = logging.getLogger(__name__)

INGEST_MODEL_ENV = "CONCENTRATE_INGEST_MODEL"
DEFAULT_INGEST_MODEL = "claude-sonnet-5-5"
USER_ID = "concentrate-corpus"  # one Mem0 user for the whole corpus
CHUNK_UTTERANCES = 40
DEFAULT_TOP_K = 25
INGEST_EFFORT = "medium"  # same default effort as concentrate.llm.LLMRequest
INGEST_MAX_TOKENS = 16000  # Mem0's default (2000) truncates once adaptive thinking is on
EMBEDDING_DIMS = 384  # all-MiniLM-L6-v2
DEFAULT_CACHE_ROOT = Path("data/cache/mem0")
COMPLETE_MARKER = "complete.json"
PROGRESS_FILE = "progress.jsonl"

# Per-call cost model for estimate_ingest, calibrated on the 2026-11-02 smoke test (2 Northwind
# meetings, 6 calls, Sonnet 5.5; see docs/systems/baselines/mem0.md). One Mem0 `add` = one LLM call:
#   input  = Mem0 system prompt (cached prefix) + our per-meeting prompt + chunk
#            + context Mem0 adds (last <=10 messages truncated to 300 chars, <=10 existing
#              memories), which grew ~280 tokens/call from ~80 and is capped at ~1,900;
#   output = ~0.85 tokens per chunk token + ~20 (JSON memories incl. adaptive thinking).
SYSTEM_PROMPT_TOKENS = 12_544  # ADDITIVE_EXTRACTION_PROMPT, measured (cache write/read size)
MEETING_PROMPT_TOKENS = 109
CONTEXT_TOKENS_BASE, CONTEXT_TOKENS_PER_CALL, CONTEXT_TOKENS_CAP = 80, 280, 1_900
OUTPUT_PER_CHUNK_TOKEN, OUTPUT_PER_CALL = 0.85, 20
EXACT_PER_ESTIMATED_TOKEN = 1.60  # chunk text: count_tokens / estimate_tokens, measured
ANSWER_OUTPUT_TOKENS_EST = 900

# Output schema for Mem0's additive-extraction prompt (its documented "OUTPUT FORMAT"). Enforced
# with structured output so a malformed reply can't silently drop a chunk's memories.
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "memory": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": "string"},
                    "attributed_to": {"type": "string"},
                    "linked_memory_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "text", "attributed_to", "linked_memory_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["memory"],
    "additionalProperties": False,
}
_SAMPLING_PARAMS = ("temperature", "top_p", "top_k")


# -- usage metering ------------------------------------------------------------------------------


@dataclass(frozen=True)
class UsageRecord:
    model: str
    input_tokens: int  # uncached input, as the API reports it
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    stop_reason: str
    cost_usd: float


@dataclass
class UsageMeter:
    """Collects the usage of every LLM call Mem0 makes."""

    records: list[UsageRecord] = field(default_factory=list)

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        stop_reason: str = "end_turn",
    ) -> UsageRecord:
        rec = UsageRecord(
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            stop_reason=stop_reason,
            cost_usd=cost_usd(
                model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens
            ),
        )
        self.records.append(rec)
        return rec

    def since(self, start: int) -> dict[str, Any]:
        """Totals over ``records[start:]`` (total input counts cached reads and writes too)."""
        recs = self.records[start:]
        return {
            "llm_calls": len(recs),
            "input_tokens": sum(
                r.input_tokens + r.cache_read_tokens + r.cache_write_tokens for r in recs
            ),
            "output_tokens": sum(r.output_tokens for r in recs),
            "cache_read_tokens": sum(r.cache_read_tokens for r in recs),
            "cache_write_tokens": sum(r.cache_write_tokens for r in recs),
            "cost_usd": sum(r.cost_usd for r in recs),
            "truncated": sum(r.stop_reason == "max_tokens" for r in recs),
            "refused": sum(r.stop_reason == "refusal" for r in recs),
        }


class _MeteredMessages:
    def __init__(self, owner: MeteredAnthropic) -> None:
        self._owner = owner

    def create(self, **kwargs: Any) -> Any:
        return self._owner.create(**kwargs)


class MeteredAnthropic:
    """Stands in for the ``anthropic.Anthropic`` client inside Mem0's Anthropic provider.

    Mem0 only calls ``client.messages.create(...)``. Each call is rewritten so it is valid and
    robust on current models, sent, and its usage recorded in ``meter``:

    * sampling parameters (``temperature``/``top_p``/``top_k``) are dropped: Sonnet 5.5 and
      Opus 5.5 reject non-default values (Mem0 already omits them for these model names);
    * ``max_tokens`` is raised to ``max_tokens`` and ``output_config.effort`` set explicitly;
    * the reply is constrained to Mem0's extraction JSON (``EXTRACTION_SCHEMA``) when no tools
      are requested;
    * the (static, ~8k-token) system prompt is marked as a prompt-cache breakpoint;
    * models that support it get the server-side refusal fallback, as in ``concentrate.llm``.
    """

    def __init__(
        self,
        sdk: Any,
        meter: UsageMeter,
        *,
        effort: str = INGEST_EFFORT,
        max_tokens: int = INGEST_MAX_TOKENS,
        cache_system: bool = True,
        schema: dict[str, Any] | None = EXTRACTION_SCHEMA,
    ) -> None:
        self._sdk, self.meter = sdk, meter
        self.effort, self.max_tokens = effort, max_tokens
        self.cache_system, self.schema = cache_system, schema
        self.messages = _MeteredMessages(self)

    def prepare(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """The request actually sent for Mem0's ``kwargs`` (pure; exposed for tests)."""
        out = {k: v for k, v in kwargs.items() if k not in _SAMPLING_PARAMS}
        out["max_tokens"] = max(int(out.get("max_tokens") or 0), self.max_tokens)
        output_config: dict[str, Any] = {"effort": self.effort}
        if self.schema is not None and not out.get("tools"):
            output_config["format"] = {"type": "json_schema", "schema": self.schema}
        out["output_config"] = output_config
        system = out.get("system")
        if isinstance(system, str):
            if system:
                block: dict[str, Any] = {"type": "text", "text": system}
                if self.cache_system:
                    block["cache_control"] = {"type": "ephemeral"}
                out["system"] = [block]
            else:
                out.pop("system")
        if out.get("model") in _FALLBACK_MODELS:
            out["betas"] = ["server-side-fallback-2026-07-01"]
            out["fallbacks"] = "default"
        return out

    def create(self, **kwargs: Any) -> Any:
        request = self.prepare(kwargs)
        api = self._sdk.beta.messages if "betas" in request else self._sdk.messages
        request["timeout"] = timeout_for(int(request["max_tokens"]))
        response = call_with_retries(lambda: api.create(**request), what="mem0 extraction")
        usage = response.usage
        served = str(response.model)
        rec = self.meter.record(
            served if served in PRICING else str(request["model"]),
            int(usage.input_tokens),
            int(usage.output_tokens),
            int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            str(response.stop_reason),
        )
        if rec.stop_reason in ("max_tokens", "refusal"):
            logger.warning("mem0 extraction call ended with stop_reason=%s", rec.stop_reason)
        return response


# -- memory backend ------------------------------------------------------------------------------


class MemoryBackend(Protocol):
    """The slice of ``mem0.Memory`` this system uses (tests inject a fake)."""

    def add(
        self,
        messages: list[dict[str, str]],
        *,
        user_id: str,
        metadata: dict[str, Any],
        prompt: str | None = None,
    ) -> dict[str, Any]: ...

    def search(self, query: str, *, top_k: int, filters: dict[str, Any]) -> dict[str, Any]: ...


MemoryFactory = Callable[[Path, UsageMeter], MemoryBackend]


def mem0_factory(
    model: str,
    *,
    effort: str = INGEST_EFFORT,
    cache_system: bool = True,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> MemoryFactory:
    """Build real ``mem0.Memory`` instances (local Qdrant + HF embedder + metered Anthropic)."""

    def build(store: Path, meter: UsageMeter) -> MemoryBackend:
        # Keep Mem0 fully local: no PostHog telemetry / remote notices, no ~/.mem0 state.
        os.environ["MEM0_TELEMETRY"] = "False"
        os.environ.setdefault("MEM0_DIR", str(store.parent.parent / "_mem0_home"))
        from mem0 import Memory

        store.mkdir(parents=True, exist_ok=True)
        config = {
            "vector_store": {
                "provider": "qdrant",
                "config": {
                    "collection_name": "concentrate",
                    "path": str(store / "qdrant"),
                    "embedding_model_dims": EMBEDDING_DIMS,
                    "on_disk": True,
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {
                    "model": embedding_model,
                    "embedding_dims": EMBEDDING_DIMS,
                    "model_kwargs": {"device": "cpu"},
                },
            },
            "llm": {
                "provider": "anthropic",
                "config": {"model": model, "max_tokens": INGEST_MAX_TOKENS},
            },
            "history_db_path": str(store / "history.db"),
        }
        memory = Memory.from_config(config)
        if not hasattr(memory.llm, "client"):
            raise RuntimeError("mem0's Anthropic provider has no .client; update MeteredAnthropic")
        memory.llm.client = MeteredAnthropic(
            anthropic_client(INGEST_MAX_TOKENS), meter, effort=effort, cache_system=cache_system
        )
        backend: MemoryBackend = memory
        return backend

    return build


# -- corpus → Mem0 messages ----------------------------------------------------------------------


@dataclass(frozen=True)
class IngestChunk:
    key: str  # stable, e.g. "not_x:summary" or "not_x:t002"
    note_id: str
    meeting_date: str
    title: str
    content: str
    prompt: str


def _when(m: MeetingNote) -> str:
    return (m.scheduled_start or m.created_at).strftime("%a %Y-%m-%d %H:%M UTC")


def _meeting_prompt(m: MeetingNote) -> str:
    day = (m.scheduled_start or m.created_at).date().isoformat()
    return (
        f"The new messages are an excerpt from a recorded work meeting, '{m.title}', held on "
        f"{day}. Treat {day} as the Observation Date (resolve relative dates against it). "
        "'Me' is the note owner (the user); 'Them' is anyone else on the call."
    )


def ingest_chunks(m: MeetingNote, chunk_utterances: int = CHUNK_UTTERANCES) -> list[IngestChunk]:
    """One meeting as Mem0 inputs: header + summary + notes, then transcript chunks."""
    day = (m.scheduled_start or m.created_at).date().isoformat()
    title = m.title or "untitled"
    people = ", ".join(p.name or p.email for p in m.attendees) or "unknown"
    head = f"Meeting: {m.title} [{m.note_id}]\nDate: {_when(m)}\nAttendees: {people}"
    prompt = _meeting_prompt(m)
    summary = [head, "", "AI summary:", cv.summary_view(m)]
    if m.private_notes_markdown:
        summary += ["", "My typed notes:", m.private_notes_markdown]
    out = [IngestChunk(f"{m.note_id}:summary", m.note_id, day, title, "\n".join(summary), prompt)]
    u = m.utterances
    parts = range(0, len(u), chunk_utterances)
    for n, i in enumerate(parts):
        lines = "\n".join(f"{cv.speaker_label(x)}: {x.text}" for x in u[i : i + chunk_utterances])
        content = f"{head}\nTranscript part {n + 1} of {len(parts)}:\n{lines}"
        out.append(IngestChunk(f"{m.note_id}:t{n:03d}", m.note_id, day, title, content, prompt))
    return out


def corpus_hash(notes: list[MeetingNote]) -> str:
    """Stable hash of the corpus identity: sorted (note_id, updated_at) pairs."""
    pairs = sorted((m.note_id, m.updated_at.isoformat()) for m in notes)
    return hashlib.sha256(json.dumps(pairs).encode()).hexdigest()[:16]


# -- the system ----------------------------------------------------------------------------------


class Mem0System:
    """B4: Mem0 memory built at write time, top-k memories answered with ``answer.v1``."""

    name = "b4_mem0"
    prompt_ids: tuple[str, ...] = (ANSWER_PROMPT,)

    def __init__(
        self,
        client: LLMClient,
        *,
        backend: ToolBackend | None = None,
        memory_factory: MemoryFactory | None = None,
        ingest_model: str | None = None,
        k: int = DEFAULT_TOP_K,
        chunk_utterances: int = CHUNK_UTTERANCES,
        effort: str = INGEST_EFFORT,
        cache_system: bool = True,
        cache_root: Path = DEFAULT_CACHE_ROOT,
    ) -> None:
        self.client, self.backend, self.k = client, backend, k
        self.ingest_model = ingest_model or os.environ.get(INGEST_MODEL_ENV) or DEFAULT_INGEST_MODEL
        self.chunk_utterances, self.effort, self.cache_system = (
            chunk_utterances,
            effort,
            cache_system,
        )
        self.cache_root = cache_root
        self.factory = memory_factory or mem0_factory(
            self.ingest_model, effort=effort, cache_system=cache_system
        )
        self.meter = UsageMeter()
        self.instructions = load_prompt(ANSWER_PROMPT)
        self._memory: MemoryBackend | None = None
        self._store: Path | None = None
        self._notes: dict[str, MeetingNote] = {}

    # -- ingestion --

    def config(self) -> dict[str, Any]:
        """Everything that changes what the memory contains (part of the store path)."""
        import importlib.metadata as md

        try:
            mem0_version = md.version("mem0ai")
        except md.PackageNotFoundError:  # pragma: no cover - fake backends in tests
            mem0_version = "none"
        return {
            "mem0ai": mem0_version,
            "ingest_model": self.ingest_model,
            "effort": self.effort,
            "chunk_utterances": self.chunk_utterances,
            "embedder": DEFAULT_EMBEDDING_MODEL,
            "user_id": USER_ID,
            "format": 1,
        }

    def store_dir(self, notes: list[MeetingNote]) -> Path:
        cfg = hashlib.sha256(json.dumps(self.config(), sort_keys=True).encode()).hexdigest()[:8]
        return self.cache_root / corpus_hash(notes) / cfg

    def _open(self, store: Path) -> MemoryBackend:
        if self._memory is None or self._store != store:
            self._memory, self._store = self.factory(store, self.meter), store
        return self._memory

    def ingest(self, notes: list[MeetingNote]) -> IngestStats:
        """Build (or reuse) the Mem0 memory for ``notes``. Resumes an interrupted build."""
        self._notes = {m.note_id: m for m in notes}
        store = self.store_dir(notes)
        marker = store / COMPLETE_MARKER
        if marker.exists():
            self._open(store)
            built = json.loads(marker.read_text())
            return IngestStats(cached=True, detail={"store": str(store), "built": built})

        progress_path = store / PROGRESS_FILE
        done: dict[str, dict[str, Any]] = {}
        if progress_path.exists():
            for row in read_progress(progress_path):  # tolerates a kill mid-write
                done[row["key"]] = row
        elif store.exists():  # leftovers without progress log: start clean
            shutil.rmtree(store)
        memory = self._open(store)
        store.mkdir(parents=True, exist_ok=True)

        start = time.perf_counter()
        chunks = [c for m in cv.by_date(notes) for c in ingest_chunks(m, self.chunk_utterances)]
        with progress_path.open("a") as log:
            for c in chunks:
                if c.key in done:
                    continue
                watchdog.raise_if_stopping()  # SIGTERM / stall: stop between chunks
                before = len(self.meter.records)
                t0 = time.perf_counter()
                result = memory.add(
                    [{"role": "user", "content": c.content}],
                    user_id=USER_ID,
                    metadata={
                        "note_id": c.note_id,
                        "chunk_id": c.key,
                        "meeting_date": c.meeting_date,
                        "title": c.title,
                    },
                    prompt=c.prompt,
                )
                added = sum(1 for r in result.get("results", []) if r.get("event") == "ADD")
                row = {
                    "key": c.key,
                    "memories_added": added,
                    "seconds": time.perf_counter() - t0,
                    **self.meter.since(before),
                }
                done[c.key] = row
                log.write(json.dumps(row) + "\n")
                log.flush()

        rows = list(done.values())

        def total(field_name: str) -> Any:
            return sum(r[field_name] for r in rows)

        stats = IngestStats(
            llm_calls=total("llm_calls"),
            input_tokens=total("input_tokens"),
            output_tokens=total("output_tokens"),
            cost_usd=total("cost_usd"),
            # Build time across all runs that contributed (a resumed build counts earlier runs).
            seconds=total("seconds"),
            detail={
                "store": str(store),
                "meetings": len(notes),
                "chunks": len(chunks),
                "memories_created": total("memories_added"),
                "cache_read_tokens": total("cache_read_tokens"),
                "cache_write_tokens": total("cache_write_tokens"),
                "truncated_calls": total("truncated"),
                "refused_calls": total("refused"),
                "wall_seconds_this_run": time.perf_counter() - start,
                "config": self.config(),
            },
        )
        marker.write_text(json.dumps(asdict(stats), indent=1))
        return stats

    def _ingest_plan(self, notes: list[MeetingNote]) -> dict[str, int]:
        """Offline per-call token model (constants calibrated on the smoke test)."""
        chunks = [c for m in cv.by_date(notes) for c in ingest_chunks(m, self.chunk_utterances)]
        uncached = output = 0
        for i, c in enumerate(chunks):
            chunk = round(estimate_tokens(c.content) * EXACT_PER_ESTIMATED_TOKEN)
            context = min(CONTEXT_TOKENS_BASE + CONTEXT_TOKENS_PER_CALL * i, CONTEXT_TOKENS_CAP)
            uncached += MEETING_PROMPT_TOKENS + chunk + context
            output += round(OUTPUT_PER_CHUNK_TOKEN * chunk) + OUTPUT_PER_CALL
        calls = len(chunks)
        system = SYSTEM_PROMPT_TOKENS * calls
        return {
            "calls": calls,
            "uncached_input": uncached,
            "system_input": system,
            "cache_write": SYSTEM_PROMPT_TOKENS if calls and self.cache_system else 0,
            "output": output,
        }

    def estimate_ingest(self, notes: list[MeetingNote]) -> tuple[int, int]:
        """(input, output) tokens to ingest ``notes`` without calling any model.

        Input counts the cached system prefix too, so pricing it all at the input rate
        overstates cost; :meth:`estimate_ingest_usd` prices the cache correctly.
        """
        plan = self._ingest_plan(notes)
        return plan["uncached_input"] + plan["system_input"], plan["output"]

    def estimate_ingest_usd(self, notes: list[MeetingNote]) -> float:
        """Estimated ingestion spend with the system prefix priced as cache reads/one write."""
        plan = self._ingest_plan(notes)
        if not self.cache_system:
            return cost_usd(
                self.ingest_model, plan["uncached_input"] + plan["system_input"], plan["output"]
            )
        return cost_usd(
            self.ingest_model,
            plan["uncached_input"],
            plan["output"],
            cache_read_tokens=plan["system_input"] - plan["cache_write"],
            cache_write_tokens=plan["cache_write"],
        )

    # -- answering --

    def retrieve(self, question: str) -> list[dict[str, Any]]:
        if self._memory is None:
            raise RuntimeError("Mem0System.run() before ingest(): call ingest(notes) first")
        found = self._memory.search(question, top_k=self.k, filters={"user_id": USER_ID})
        results: list[dict[str, Any]] = found.get("results", [])
        return results

    def context(self, memories: list[dict[str, Any]]) -> tuple[str, list[str]]:
        """Group memories by source meeting (chronological), keeping rank order within one."""
        groups: dict[str, list[str]] = {}
        meta: dict[str, tuple[str, str, str]] = {}  # note_id -> (sort key, title, when)
        for mem in memories:
            md = mem.get("metadata") or {}
            nid = str(md.get("note_id") or "unknown")
            groups.setdefault(nid, []).append(str(mem.get("memory", "")).strip())
            note = self._notes.get(nid)
            if note is not None:
                key = (note.scheduled_start or note.created_at).isoformat()
                meta[nid] = (key, note.title or "untitled", _when(note))
            else:
                day = str(md.get("meeting_date") or "")
                meta[nid] = (day, str(md.get("title") or "untitled"), day or "unknown date")
        order = sorted(groups, key=lambda n: (meta[n][0], n))
        blocks = []
        for nid in order:
            _, title, when = meta[nid]
            bullets = "\n".join(f"- {t}" for t in groups[nid])
            blocks.append(f"## {title} [{nid}]\nWhen: {when}\n{bullets}")
        body = "\n\n".join(blocks) if blocks else "(no memories found)"
        preamble = (
            "Memories extracted from the meetings by a memory system. Each is grouped under "
            "its source meeting; cite the meeting ID in brackets."
        )
        return f"<memories>\n{preamble}\n\n{body}\n</memories>", [
            n for n in order if n != "unknown"
        ]

    def _user(self, q: QuestionLike) -> tuple[str, list[str]]:
        ctx, retrieved = self.context(self.retrieve(q.question))
        return f"{ctx}\n\n{_user_prompt(q)}", retrieved

    def run(self, q: QuestionLike) -> SystemResult:
        before = len(self.client.ledger.records)
        start = time.perf_counter()
        user, retrieved = self._user(q)
        completion = run_turn(
            self.client,
            TurnRequest(
                stage=f"eval.{self.name}",
                prompt_id=ANSWER_PROMPT,
                system=self.instructions,
                messages=({"role": "user", "content": user},),
                schema=ANSWER_SCHEMA,
                max_tokens=8000,
            ),
            self.backend,
        )
        return _result(
            self.client,
            before,
            start,
            Answer.model_validate_json(completion.text),
            retrieved,
            context=estimate_tokens(self.instructions) + estimate_tokens(user),
        )

    def estimate(self, q: QuestionLike) -> tuple[int, int]:
        """Answer-call tokens. Uses live retrieval when ingested; else assumes k ~40-token lines."""
        if self._memory is not None:
            user, _ = self._user(q)
        else:
            user = "x" * round(3.5 * (self.k * 40 + 300))
        return estimate_tokens(self.instructions) + estimate_tokens(user), ANSWER_OUTPUT_TOKENS_EST
