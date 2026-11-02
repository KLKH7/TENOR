"""Concentrate: the precomputed meeting memory (Enhance -> Extract -> Verify -> Consolidate ->
Answer), as a :class:`~concentrate.agents.baselines.WriteTimeSystem`.

Ingestion walks the corpus chronologically; each meeting is enhanced (speaker/mention linking:
the stub until ``concentrate.enhance`` lands), extracted by ``extract.v1`` on the ingest model,
passed through the grounding gate and consolidated into a bitemporal SQLite store under
``data/cache/concentrate/<corpus hash>/<config hash>/``. The store doubles as the progress
record: every meeting commits atomically with its stats, so an interrupted build resumes where it
stopped, and a ``complete.json`` marker makes a finished build a free cache hit.

Questions are answered by the same ``answer.v1`` prompt, ``ANSWER_SCHEMA`` and answer model as
every baseline, in one call (so ``turn_request`` supports batch mode). The difference is the
context: a structured lookup by entity and date plus hybrid retrieval over facts and digests,
with the transcript lines each fact cites, so answers cite **line IDs**, not just meetings.

``retire_after_days`` drops L0/L1 (transcript lines) of meetings older than N days at answer
time; facts keep their line IDs as provenance pointers. This is the retirement-curve knob.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

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
from concentrate.agents.mem0_system import corpus_hash
from concentrate.agents.retrieval import Embedder
from concentrate.core.models import MeetingNote
from concentrate.llm.client import (
    CACHE_READ_PER_MTOK,
    Ledger,
    LLMClient,
    ToolBackend,
    TurnRequest,
    cost_usd,
    estimate_tokens,
    load_prompt,
    run_turn,
)
from concentrate.memory.enhance_stub import Enhancer, real_enhancer
from concentrate.memory.entities import EntityDirectory, load_directory
from concentrate.memory.extract import EXTRACT_PROMPT
from concentrate.memory.pipeline import BuildConfig, MemoryBuilder
from concentrate.memory.query import MemoryIndex, build_context, parse_question
from concentrate.memory.store import MemoryStore
from concentrate.memory.verify import ENTAIL_PROMPT

INGEST_MODEL_ENV = "CONCENTRATE_INGEST_MODEL"
DEFAULT_INGEST_MODEL = "claude-sonnet-5-5"
DEFAULT_ENTAIL_MODEL = "claude-haiku-5-5"
DEFAULT_CACHE_ROOT = Path("data/cache/concentrate")
DEFAULT_DIRECTORY = Path("data/fixtures/northwind/directory.json")
COMPLETE_MARKER = "complete.json"
PROGRESS_FILE = "progress.jsonl"
DB_FILE = "memory.sqlite"
STORE_FORMAT = 1

# Ingestion cost model, calibrated on the 2026-11-02 smoke run (4 base Northwind meetings,
# Sonnet 5.5, effort medium, whole meetings; docs/concentrate/memory.md). Per extraction call:
#   cached prefix = extract.v1 + directory (count_tokens/estimate = 1.336) + structured-output
#                   schema overhead (measured prefix 3,874 tokens - 2,540 counted = 1,334);
#   uncached      = summary + transcript with local line IDs (measured/estimate = 1.65)
#                   + context (header, existing keys, open commitments), which grows with the
#                   memory until the max_keys / max_open_commitments caps;
#   output        = OUTPUT_BASE + OUTPUT_PER_LINE * lines (JSON + adaptive thinking; measured
#                   2,928 tokens for 291 lines).
SYSTEM_EXACT_PER_ESTIMATED = 1.336
SCHEMA_OVERHEAD_TOKENS = 1_334
TRANSCRIPT_EXACT_PER_ESTIMATED = 1.65
CONTEXT_BASE, CONTEXT_PER_MEETING, CONTEXT_CAP = 400, 100, 3_400
OUTPUT_BASE, OUTPUT_PER_LINE = 40, 9.5
ENTAIL_USD_PER_MEETING = 0.0003  # Haiku 5.5 at low effort: measured $0.0010 for 4 calls
ANSWER_OUTPUT_TOKENS_EST = 400


class ConcentrateSystem:
    """Write-time memory with structured lookup; answers with ``answer.v1`` like B0-B5."""

    name = "concentrate"
    prompt_ids: tuple[str, ...] = (ANSWER_PROMPT, EXTRACT_PROMPT, ENTAIL_PROMPT)

    def __init__(
        self,
        client: LLMClient,
        *,
        backend: ToolBackend | None = None,
        ingest_backend: ToolBackend | None = None,
        ingest_model: str | None = None,
        entail_model: str | None = DEFAULT_ENTAIL_MODEL,
        enhancer: Enhancer | None = None,
        directory_path: Path | None = DEFAULT_DIRECTORY,
        embedder: Embedder | None = None,
        cache_root: Path = DEFAULT_CACHE_ROOT,
        build: BuildConfig | None = None,
        k_facts: int = 20,
        k_digests: int = 4,
        retire_after_days: int | None = None,
    ) -> None:
        self.client, self.backend = client, backend
        self.ingest_backend = ingest_backend
        self.ingest_model = ingest_model or os.environ.get(INGEST_MODEL_ENV) or DEFAULT_INGEST_MODEL
        self.entail_model = entail_model
        self.enhancer = enhancer if enhancer is not None else real_enhancer()
        self.directory_path = directory_path
        self.embedder, self.cache_root = embedder, cache_root
        self.build = build or BuildConfig()
        self.k_facts, self.k_digests = k_facts, k_digests
        self.retire_after_days = retire_after_days
        self.instructions = load_prompt(ANSWER_PROMPT)
        self.ingest_ledger = Ledger()
        self._store: MemoryStore | None = None
        self._index: MemoryIndex | None = None
        self._directory: EntityDirectory | None = None

    # -- ingestion --

    def config(self) -> dict[str, Any]:
        """Everything that changes what the memory contains (part of the store path)."""
        prompts = {
            p: hashlib.sha256(load_prompt(p).encode()).hexdigest()[:8]
            for p in (EXTRACT_PROMPT, ENTAIL_PROMPT)
        }
        directory = (
            hashlib.sha256(self.directory_path.read_bytes()).hexdigest()[:8]
            if self.directory_path is not None and self.directory_path.exists()
            else "none"
        )
        return {
            "ingest_model": self.ingest_model,
            "entail_model": self.entail_model if self.build.entailment else None,
            "enhancer": self.enhancer.name,
            "prompts": prompts,
            "directory": directory,
            "build": asdict(self.build),
            "format": STORE_FORMAT,
        }

    def store_dir(self, notes: list[MeetingNote]) -> Path:
        cfg = hashlib.sha256(json.dumps(self.config(), sort_keys=True).encode()).hexdigest()[:8]
        return self.cache_root / corpus_hash(notes) / cfg

    def _clients(self, cache_dir: Path) -> tuple[LLMClient, LLMClient | None]:
        extract = LLMClient(
            model=self.ingest_model,
            cache_dir=cache_dir,
            ledger=self.ingest_ledger,
            offline=self.client.offline,
        )
        entail = None
        if self.build.entailment and self.entail_model:
            entail = LLMClient(
                model=self.entail_model,
                cache_dir=cache_dir,
                ledger=self.ingest_ledger,
                offline=self.client.offline,
            )
        return extract, entail

    def _open(self, store_dir: Path, notes: list[MeetingNote]) -> MemoryStore:
        if self._store is not None:
            self._store.close()
        self._store = MemoryStore(store_dir / DB_FILE)
        self._directory = load_directory(self.directory_path, notes)
        return self._store

    def ingest(self, notes: list[MeetingNote]) -> IngestStats:
        """Build (or reuse, or resume) the memory for ``notes``."""
        store_dir = self.store_dir(notes)
        marker = store_dir / COMPLETE_MARKER
        if marker.exists():
            self._open(store_dir, notes)
            self._reindex()
            return IngestStats(
                cached=True,
                detail={"store": str(store_dir), "built": json.loads(marker.read_text())},
            )
        if store_dir.exists() and not (store_dir / DB_FILE).exists():
            shutil.rmtree(store_dir)  # leftovers without a store: start clean
        store = self._open(store_dir, notes)
        assert self._directory is not None
        extract, entail = self._clients(self.client.cache_dir)
        builder = MemoryBuilder(
            store,
            self._directory,
            extract,
            extract_backend=self.ingest_backend,
            entail_client=entail,
            entail_backend=self.ingest_backend,
            enhancer=self.enhancer,
            enhancer_directory=self.enhancer.load_directory(self.directory_path),
            config=self.build,
        )
        start = time.perf_counter()
        ordered = cv.by_date(notes)
        with (store_dir / PROGRESS_FILE).open("a") as log:
            for i, m in enumerate(ordered):
                if store.has_meeting(m.note_id):
                    continue
                row = builder.ingest(m, ordered[:i])
                log.write(json.dumps({"note_id": m.note_id, **row}) + "\n")
                log.flush()
        stats = self._stats(store, store_dir, time.perf_counter() - start)
        marker.write_text(json.dumps(asdict(stats), indent=1))
        self._reindex()
        return stats

    def _stats(self, store: MemoryStore, store_dir: Path, wall: float) -> IngestStats:
        rows = [m["stats"] for m in store.meetings()]

        def total(key: str) -> Any:
            return sum(r.get(key, 0) for r in rows)

        def merged(key: str) -> dict[str, int]:
            out: dict[str, int] = {}
            for r in rows:
                for k, v in r.get(key, {}).items():
                    out[k] = out.get(k, 0) + v
            return out

        tiers = {t: sum(m["tiers"][t] for m in store.meetings()) for t in ("L0", "L1", "L2", "L3")}
        facts = store.all_facts()
        return IngestStats(
            llm_calls=total("llm_calls"),
            input_tokens=total("input_tokens"),
            output_tokens=total("output_tokens"),
            cost_usd=total("cost_usd"),
            seconds=total("seconds"),
            detail={
                "store": str(store_dir),
                "meetings": len(rows),
                "facts_extracted": total("facts_extracted"),
                "commitments_extracted": total("commitments_extracted"),
                "updates_extracted": total("updates_extracted"),
                "entailment_checked": total("entailment_checked"),
                "kept": merged("kept"),
                "rejected": merged("rejected"),
                "fact_rows": len(facts),
                "current_facts": sum(f.valid_to is None for f in facts),
                "contradictions": len(store.contradictions()),
                "commitments": len(store.commitments()),
                "tiers_tokens": tiers,
                "cache_read_tokens": total("cache_read_tokens"),
                "cache_write_tokens": total("cache_write_tokens"),
                "wall_seconds_this_run": wall,
                "config": self.config(),
            },
        )

    def _reindex(self) -> None:
        assert self._store is not None and self._directory is not None
        self._index = MemoryIndex(self._store, self._directory, self.embedder)

    @property
    def store(self) -> MemoryStore:
        if self._store is None:
            raise RuntimeError("ConcentrateSystem used before ingest(): call ingest(notes) first")
        return self._store

    # -- ingestion estimates --

    def _ingest_plan(self, notes: list[MeetingNote]) -> dict[str, int]:
        directory = load_directory(self.directory_path, notes)
        prefix = estimate_tokens(load_prompt(EXTRACT_PROMPT) + directory.prompt_block())
        system = round(prefix * SYSTEM_EXACT_PER_ESTIMATED) + SCHEMA_OVERHEAD_TOKENS
        uncached = output = calls = 0
        for i, m in enumerate(cv.by_date(notes)):
            lines = "\n".join(
                f"[{u.index:04d}] {cv.speaker_label(u)}: {u.text}" for u in m.utterances
            )
            text = f"{cv.summary_view(m)}\n{lines}"
            context = min(CONTEXT_BASE + CONTEXT_PER_MEETING * i, CONTEXT_CAP)
            uncached += round(estimate_tokens(text) * TRANSCRIPT_EXACT_PER_ESTIMATED) + context
            output += round(OUTPUT_BASE + OUTPUT_PER_LINE * len(m.utterances))
            calls += 1
        return {"calls": calls, "system": system, "uncached_input": uncached, "output": output}

    def estimate_ingest(self, notes: list[MeetingNote]) -> tuple[int, int]:
        """(input, output) tokens to ingest ``notes`` (input includes the cached system prompt)."""
        p = self._ingest_plan(notes)
        return p["uncached_input"] + p["system"] * p["calls"], p["output"]

    def estimate_ingest_usd(self, notes: list[MeetingNote]) -> float:
        """Ingestion spend with the system prompt written to cache once and read thereafter."""
        p = self._ingest_plan(notes)
        if not p["calls"]:
            return 0.0
        reads = p["system"] * (p["calls"] - 1) if self.ingest_model in CACHE_READ_PER_MTOK else 0
        writes = p["system"] if reads else 0
        plain = p["system"] * p["calls"] - reads - writes
        usd = cost_usd(
            self.ingest_model,
            p["uncached_input"] + plain,
            p["output"],
            cache_read_tokens=reads,
            cache_write_tokens=writes,
        )
        if self.build.entailment and self.entail_model:
            usd += ENTAIL_USD_PER_MEETING * p["calls"]
        return usd

    # -- answering --

    def _user(self, q: QuestionLike) -> tuple[str, list[str]]:
        if self._index is None or self._directory is None:
            raise RuntimeError("ConcentrateSystem.run() before ingest(): call ingest(notes) first")
        pq = parse_question(q.question, q.as_of, self._directory)
        ctx = build_context(
            self._index,
            q.question,
            pq,
            k_facts=self.k_facts,
            k_digests=self.k_digests,
            retire_after_days=self.retire_after_days,
        )
        return f"{ctx.text}\n\n{_user_prompt(q)}", ctx.meetings

    def _request(self, user: str) -> TurnRequest:
        return TurnRequest(
            stage=f"eval.{self.name}",
            prompt_id=ANSWER_PROMPT,
            system=self.instructions,
            messages=({"role": "user", "content": user},),
            schema=ANSWER_SCHEMA,
            max_tokens=8000,
        )

    def turn_request(self, q: QuestionLike) -> TurnRequest:
        """The single Messages request ``run(q)`` sends (for batch prefetch)."""
        return self._request(self._user(q)[0])

    def run(self, q: QuestionLike) -> SystemResult:
        before = len(self.client.ledger.records)
        start = time.perf_counter()
        user, retrieved = self._user(q)
        completion = run_turn(self.client, self._request(user), self.backend)
        return _result(
            self.client,
            before,
            start,
            Answer.model_validate_json(completion.text),
            retrieved,
            context=estimate_tokens(self.instructions) + estimate_tokens(user),
        )

    def estimate(self, q: QuestionLike) -> tuple[int, int]:
        """Answer-call tokens: exact context when ingested, else a calibrated constant."""
        if self._index is not None:
            user, _ = self._user(q)
        else:
            user = "x" * round(3.5 * 4500)
        return estimate_tokens(self.instructions) + estimate_tokens(user), ANSWER_OUTPUT_TOKENS_EST
