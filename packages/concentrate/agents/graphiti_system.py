"""B5: Graphiti (Zep's open-source temporal knowledge graph) as a write-time baseline.

Graphiti matters most among the competitors because it already keeps *bitemporal* facts: every
edge carries ``valid_at`` / ``invalid_at`` (world time) and ``expired_at`` (when the graph learnt
it was superseded). Ingestion turns each meeting into episodes; Graphiti's LLM pipeline extracts
entities and facts, dedupes them against the graph and invalidates contradicted facts using each
episode's ``reference_time`` (the meeting's start). Questions are answered from a hybrid
(BM25 + embedding, RRF) search over facts and entity summaries, through the same ``answer.v1``
path as every other baseline.

Everything runs locally except LLM calls:

* graph store: embedded Kuzu (``graphiti_core.driver.kuzu_driver``), no server;
* embeddings: local ``sentence-transformers/all-MiniLM-L6-v2`` via :class:`LocalEmbedder`;
* reranking: RRF only (no cross-encoder), so :class:`NoRerank` is never called;
* LLM: Anthropic through :class:`AccountedAnthropicClient`, a subclass of Graphiti's
  ``AnthropicClient`` whose requests are valid for current models and which records the usage of
  every call so ingestion cost is measured, not guessed.

See ``docs/baselines/graphiti.md`` for every deviation from Graphiti's defaults.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import time
import typing
import warnings
from collections.abc import Coroutine, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

os.environ.setdefault("GRAPHITI_TELEMETRY_ENABLED", "false")  # no PostHog calls from a benchmark

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.driver.driver import GraphProvider
from graphiti_core.driver.kuzu_driver import KuzuDriver
from graphiti_core.edges import EntityEdge
from graphiti_core.embedder.client import EmbedderClient
from graphiti_core.graph_queries import get_fulltext_indices
from graphiti_core.llm_client.anthropic_client import AnthropicClient
from graphiti_core.llm_client.config import LLMConfig, ModelSize
from graphiti_core.llm_client.errors import RefusalError
from graphiti_core.nodes import EntityNode, EpisodeType
from graphiti_core.prompts.models import Message
from graphiti_core.search.search_config import (
    EdgeSearchConfig,
    EdgeSearchMethod,
    NodeSearchConfig,
    NodeSearchMethod,
    SearchConfig,
)
from pydantic import BaseModel

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
from concentrate.agents.retrieval import Embedder
from concentrate.core.models import MeetingNote
from concentrate.llm.client import (
    PRICING,
    LLMClient,
    ToolBackend,
    TurnRequest,
    cost_usd,
    estimate_tokens,
    load_prompt,
    run_turn,
)
from concentrate.llm.resilience import acall_with_retries, async_anthropic_client, timeout_for

DEFAULT_INGEST_MODEL = "claude-sonnet-5-5"
DEFAULT_CACHE_ROOT = Path("data/cache/graphiti")
EPISODE_UTTERANCES = 40  # utterances per episode (see docs/baselines/graphiti.md for the cost case)
MIN_TAIL_UTTERANCES = 10  # a shorter trailing chunk is merged into the previous episode
K_EDGES, K_NODES = 30, 10
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"}
_FORMAT_VERSION = 1  # bump when the episode text or ingestion config changes meaningfully

# Ingestion cost model, calibrated on the 2-meeting smoke test (docs/baselines/graphiti.md).
# Billed input for episode i ~= INGEST_INPUT_PER_TOKEN * tokens(i)
#   + PREV_CONTEXT_CALLS * tokens(previous <= PREVIOUS_EPISODES episodes),
# because Graphiti re-sends the last 10 episodes in ~3 prompts per episode (extract nodes,
# extract edges, summaries). Token counts are ``estimate_tokens`` of the episode text. Planning
# only: measured on 4 episodes, so the steady state (10 previous episodes) is extrapolated.
INGEST_INPUT_PER_TOKEN = 28.7  # (65,993 - 3 * 3,704) / 1,911 over the 3 non-first episodes
INGEST_OUTPUT_PER_TOKEN = 1.5  # 3,770 / 2,522
PREV_CONTEXT_CALLS, PREVIOUS_EPISODES = 3, 10
FACT_TOKENS_EST, NODE_TOKENS_EST = 60, 120  # context line sizes for the per-question estimate
OUTPUT_TOKENS_EST = 900


# -- accounting ---------------------------------------------------------------------------------


@dataclass
class IngestCall:
    """Usage of one Anthropic call made by Graphiti during ingestion."""

    model: str
    input_tokens: int  # uncached input, as the API reports it
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    latency_s: float
    prompt: str = ""  # Graphiti prompt name when known (response model name otherwise)


@dataclass
class IngestLedger:
    calls: list[IngestCall] = field(default_factory=list)

    def add(self, call: IngestCall) -> None:
        self.calls.append(call)

    def totals(self, since: int = 0) -> dict[str, float]:
        cs = self.calls[since:]
        return {
            "llm_calls": len(cs),
            "input_tokens": sum(
                c.input_tokens + c.cache_read_tokens + c.cache_write_tokens for c in cs
            ),
            "output_tokens": sum(c.output_tokens for c in cs),
            "cache_read_tokens": sum(c.cache_read_tokens for c in cs),
            "cache_write_tokens": sum(c.cache_write_tokens for c in cs),
            "cost_usd": sum(c.cost_usd for c in cs),
        }

    def by_prompt(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for c in self.calls:
            s = out.setdefault(
                c.prompt, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
            )
            s["calls"] += 1
            s["input_tokens"] += c.input_tokens + c.cache_read_tokens + c.cache_write_tokens
            s["output_tokens"] += c.output_tokens
            s["usd"] += c.cost_usd
        return out


def price_call(model: str, served_by: str, usage: Any) -> tuple[int, int, int, int, float]:
    """Read an Anthropic ``usage`` object and price it (by the model that actually served it)."""
    inp = int(getattr(usage, "input_tokens", 0) or 0)
    out = int(getattr(usage, "output_tokens", 0) or 0)
    read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    priced_as = served_by if served_by in PRICING else model
    return inp, out, read, write, cost_usd(priced_as, inp, out, read, write)


class AccountedAnthropicClient(AnthropicClient):
    """Graphiti's Anthropic client, made valid for current models and fully accounted.

    Graphiti 0.31's ``AnthropicClient`` forces a tool call (``tool_choice: {type: "tool"}``) and
    sends ``temperature``; current models reject forced tool choice, and Sonnet 5.5 rejects
    non-default sampling. This subclass instead requests structured output through
    ``output_config.format`` with the response model's (strict-transformed) JSON schema, sends no
    sampling parameters, turns thinking off for these short extraction calls
    (``thinking: between_tools``, unless ``thinking="adaptive"``), and opts into the server-side
    refusal fallback. Every response's usage is recorded in ``ledger`` *before* parsing, so
    retried or failed attempts are still paid for in the accounts.
    """

    def __init__(
        self,
        model: str,
        sdk_client: Any,
        ledger: IngestLedger,
        *,
        effort: str = "medium",
        thinking: str = "off",
        max_tokens: int = 16000,
    ) -> None:
        config = LLMConfig(api_key="unused", model=model, max_tokens=max_tokens)
        super().__init__(config=config, client=sdk_client, max_tokens=max_tokens)
        self.ledger, self.effort, self.thinking = ledger, effort, thinking

    def request_kwargs(
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None,
        max_tokens: int,
        model: str,
    ) -> dict[str, Any]:
        """The exact Messages API arguments for one Graphiti prompt."""
        from anthropic.lib._parse._transform import transform_schema

        output_config: dict[str, Any] = {"effort": self.effort}
        if response_model is not None:
            schema = transform_schema(response_model.model_json_schema())
            output_config["format"] = {"type": "json_schema", "schema": schema}
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": messages[0].content,
            "messages": [{"role": m.role, "content": m.content} for m in messages[1:]],
            "output_config": output_config,
        }
        if self.thinking == "off":
            kwargs["thinking"] = {"type": "between_tools"}
        if model in _FALLBACK_MODELS:
            kwargs["betas"] = [_FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    # Graphiti's own AnthropicClient returns (data, in, out) here, unlike its base class.
    async def _generate_response(  # type: ignore[override]
        self,
        messages: list[Message],
        response_model: type[BaseModel] | None = None,
        max_tokens: int | None = None,
        model_size: ModelSize = ModelSize.medium,
        *,
        model: str | None = None,
    ) -> tuple[dict[str, Any], int, int]:
        effective_model = model or self.model
        kwargs = self.request_kwargs(
            messages,
            response_model,
            self._resolve_max_tokens(max_tokens, effective_model),
            effective_model,
        )
        start = time.perf_counter()
        kwargs["timeout"] = timeout_for(int(kwargs["max_tokens"]))
        result = await acall_with_retries(
            lambda: self.client.beta.messages.create(**kwargs),
            what=f"graphiti {kwargs['model']}",
        )
        inp, out, read, write, usd = price_call(effective_model, str(result.model), result.usage)
        self.ledger.add(
            IngestCall(
                model=str(result.model),
                input_tokens=inp,
                output_tokens=out,
                cache_read_tokens=read,
                cache_write_tokens=write,
                cost_usd=usd,
                latency_s=time.perf_counter() - start,
                prompt=response_model.__name__ if response_model is not None else "json",
            )
        )
        if result.stop_reason == "refusal":
            raise RefusalError(f"model refused a Graphiti prompt ({kwargs['model']})")
        if result.stop_reason == "max_tokens":
            raise ValueError(f"Graphiti prompt truncated at max_tokens={kwargs['max_tokens']}")
        text = "".join(getattr(b, "text", "") for b in result.content if b.type == "text")
        data = (
            json.loads(text) if response_model is not None else self._extract_json_from_text(text)
        )
        return typing.cast(dict[str, Any], data), inp + read + write, out


# -- local embedder and reranker ----------------------------------------------------------------


class LocalEmbedder(EmbedderClient):
    """Adapts a Concentrate :class:`Embedder` (default: local MiniLM) to Graphiti's interface."""

    def __init__(self, embedder: Embedder | None = None) -> None:
        self._embedder = embedder

    @property
    def embedder(self) -> Embedder:
        if self._embedder is None:
            from concentrate.agents.retrieval import SentenceTransformerEmbedder

            self._embedder = SentenceTransformerEmbedder()
        return self._embedder

    async def create(
        self, input_data: str | list[str] | Iterable[int] | Iterable[Iterable[int]]
    ) -> list[float]:
        text = input_data if isinstance(input_data, str) else " ".join(map(str, input_data))
        return [float(x) for x in self.embedder.embed([text])[0]]

    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []
        return [[float(x) for x in row] for row in self.embedder.embed(input_data_list)]


class NoRerank(CrossEncoderClient):
    """Graphiti requires a cross-encoder object; our RRF search configs never call it."""

    async def rank(self, query: str, passages: list[str]) -> list[tuple[str, float]]:
        return [(p, 0.0) for p in passages]


# -- episodes -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Episode:
    key: str  # "<note_id>#<part>"
    note_id: str
    part: int
    name: str
    body: str
    reference_time: datetime
    title: str


def meeting_time(m: MeetingNote) -> datetime:
    return m.scheduled_start or m.created_at


def episodes_for(m: MeetingNote, size: int = EPISODE_UTTERANCES) -> list[Episode]:
    """Split a meeting into episodes of ~``size`` speaker-labelled lines, each with the header.

    Line IDs are left out (Graphiti would treat them as content); citations are traced back to
    the meeting through the episode manifest instead.
    """
    when = meeting_time(m)
    people = ", ".join(p.name or p.email for p in m.attendees) or "unknown"
    title = m.title or "(untitled meeting)"
    head = f"Meeting: {title}\nDate: {when.strftime('%a %Y-%m-%d %H:%M UTC')}\nAttendees: {people}"
    groups: list[list[str]] = []
    lines = [f"{cv.speaker_label(u)}: {u.text}" for u in m.utterances]
    for i in range(0, len(lines), size):
        chunk = lines[i : i + size]
        if groups and len(chunk) < MIN_TAIL_UTTERANCES:
            groups[-1].extend(chunk)
        else:
            groups.append(chunk)
    if not groups:  # no transcript: fall back to the AI summary so the meeting isn't lost
        groups = [[f"Summary:\n{cv.summary_view(m)}"]]
    n = len(groups)
    return [
        Episode(
            key=f"{m.note_id}#{i}",
            note_id=m.note_id,
            part=i,
            name=f"{title} [{m.note_id}] part {i + 1}/{n}",
            body=f"{head}\nTranscript part {i + 1} of {n}:\n" + "\n".join(g),
            reference_time=when,
            title=title,
        )
        for i, g in enumerate(groups)
    ]


# -- graph backend (real: Graphiti over Kuzu; tests inject a fake) ------------------------------


@dataclass(frozen=True)
class Fact:
    uuid: str
    fact: str
    name: str
    valid_at: datetime | None
    invalid_at: datetime | None
    expired_at: datetime | None
    episodes: tuple[str, ...]  # Graphiti episode UUIDs


@dataclass(frozen=True)
class Entity:
    name: str
    summary: str


class GraphBackend(Protocol):
    async def add_episode(self, ep: Episode) -> str:
        """Add one episode; return its Graphiti UUID."""
        ...

    async def search(
        self, query: str, k_edges: int, k_nodes: int
    ) -> tuple[list[Fact], list[Entity]]: ...

    async def counts(self) -> dict[str, int]: ...


def _fact(e: EntityEdge) -> Fact:
    return Fact(e.uuid, e.fact, e.name, e.valid_at, e.invalid_at, e.expired_at, tuple(e.episodes))


class KuzuGraphiti:
    """Graphiti over an embedded Kuzu database at ``path``."""

    def __init__(
        self,
        path: Path,
        llm: AccountedAnthropicClient,
        embedder: EmbedderClient,
        *,
        fresh: bool,
    ) -> None:
        with warnings.catch_warnings():  # Kuzu is deprecated upstream; see the doc's risks
            warnings.simplefilter("ignore", DeprecationWarning)
            self.driver = KuzuDriver(str(path))
        self.graphiti = Graphiti(
            graph_driver=self.driver, llm_client=llm, embedder=embedder, cross_encoder=NoRerank()
        )
        self._fresh = fresh

    async def setup(self) -> None:
        # KuzuDriver.build_indices_and_constraints is a no-op in graphiti-core 0.31, so its
        # full-text indexes (needed by BM25 search) must be created explicitly, once.
        if self._fresh:
            for q in get_fulltext_indices(GraphProvider.KUZU):
                await self.driver.execute_query(q)
            self._fresh = False

    async def add_episode(self, ep: Episode) -> str:
        res = await self.graphiti.add_episode(
            name=ep.name,
            episode_body=ep.body,
            source_description=f"Granola meeting transcript {ep.note_id}",
            reference_time=ep.reference_time,
            source=EpisodeType.message,
        )
        return str(res.episode.uuid)

    async def search(
        self, query: str, k_edges: int, k_nodes: int
    ) -> tuple[list[Fact], list[Entity]]:
        hybrid_e = [EdgeSearchMethod.bm25, EdgeSearchMethod.cosine_similarity]
        hybrid_n = [NodeSearchMethod.bm25, NodeSearchMethod.cosine_similarity]
        edges = await self.graphiti.search_(
            query,
            config=SearchConfig(
                edge_config=EdgeSearchConfig(search_methods=hybrid_e), limit=k_edges
            ),
        )
        nodes = await self.graphiti.search_(
            query,
            config=SearchConfig(
                node_config=NodeSearchConfig(search_methods=hybrid_n), limit=k_nodes
            ),
        )
        ents = [Entity(n.name, n.summary) for n in nodes.nodes if isinstance(n, EntityNode)]
        return [_fact(e) for e in edges.edges], ents

    async def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for key, q in (
            ("episodes", "MATCH (n:Episodic) RETURN count(n) AS c"),
            ("entities", "MATCH (n:Entity) RETURN count(n) AS c"),
            ("facts", "MATCH (n:RelatesToNode_) RETURN count(n) AS c"),
            (
                "facts_invalidated",
                "MATCH (n:RelatesToNode_) WHERE n.invalid_at IS NOT NULL RETURN count(n) AS c",
            ),
            (
                "facts_expired",
                "MATCH (n:RelatesToNode_) WHERE n.expired_at IS NOT NULL RETURN count(n) AS c",
            ),
        ):
            rows, _, _ = await self.driver.execute_query(q)
            out[key] = int(typing.cast(list[dict[str, Any]], rows)[0]["c"]) if rows else 0
        return out


# -- context ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeRef:
    note_id: str
    title: str
    when: str  # ISO date of the meeting


def _day(d: datetime | None) -> str:
    return d.date().isoformat() if d else "unknown"


def build_context(
    facts: Sequence[Fact], entities: Sequence[Entity], manifest: dict[str, EpisodeRef]
) -> tuple[str, list[str]]:
    """Render facts (with validity intervals and source meetings) and entity summaries.

    Facts are listed chronologically by ``valid_at`` so "latest state" reads top to bottom.
    Returns the context and the meeting IDs it cites (in order of first appearance).
    """
    cited: list[str] = []
    lines = []
    ordered = sorted(facts, key=lambda f: (f.valid_at is None, f.valid_at or datetime.min, f.uuid))
    for f in ordered:
        refs = [manifest[u] for u in f.episodes if u in manifest]
        ids = list(dict.fromkeys(r.note_id for r in refs))
        cited += [i for i in ids if i not in cited]
        until = _day(f.invalid_at) if f.invalid_at else "present"
        # expired_at is ingestion wall-clock time, not world time: show the flag, not the date.
        status = "; superseded by a later meeting" if f.expired_at else ""
        src = (
            "; ".join(f"[{r.note_id}] {r.title} ({r.when})" for r in dict.fromkeys(refs))
            or "unknown"
        )
        lines.append(f"- {f.fact}\n  valid: {_day(f.valid_at)} -> {until}{status}\n  source: {src}")
    ents = [f"- {e.name}: {' '.join(e.summary.split())}" for e in entities if e.summary.strip()]
    body = (
        "<graph_facts>\nFacts from a temporal knowledge graph built from the meetings. "
        "'valid' is when the fact held in the world (-> present = still true as far as the graph "
        "knows); 'superseded' means a later meeting contradicted it. Cite the meeting IDs in the "
        "source lines.\n"
        + ("\n".join(lines) or "(no facts found)")
        + "\n</graph_facts>\n\n<graph_entities>\n"
        + ("\n".join(ents) or "(no entities found)")
        + "\n</graph_entities>"
    )
    return body, cited


# -- the system ---------------------------------------------------------------------------------


def corpus_hash(notes: Sequence[MeetingNote], config: dict[str, Any]) -> str:
    """Stable hash of (note_id, updated_at) pairs plus the ingestion config shaping the graph."""
    items = sorted((m.note_id, m.updated_at.isoformat()) for m in notes)
    blob = json.dumps({"notes": items, "config": config}, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class GraphitiSystem:
    """B5: Graphiti temporal knowledge graph (write-time memory), answered with ``answer.v1``."""

    name = "b5_graphiti"
    prompt_ids: tuple[str, ...] = (ANSWER_PROMPT,)

    def __init__(
        self,
        client: LLMClient,
        *,
        ingest_model: str | None = None,
        ingest_effort: str | None = None,
        ingest_thinking: str = "off",
        embedder: Embedder | None = None,
        anthropic_client: Any | None = None,
        graph_factory: typing.Callable[
            [Path, AccountedAnthropicClient, EmbedderClient, bool], GraphBackend
        ]
        | None = None,
        cache_root: Path = DEFAULT_CACHE_ROOT,
        episode_utterances: int = EPISODE_UTTERANCES,
        k_edges: int = K_EDGES,
        k_nodes: int = K_NODES,
        backend: ToolBackend | None = None,
    ) -> None:
        self.client, self.backend = client, backend
        self.ingest_model = (
            ingest_model or os.environ.get("CONCENTRATE_INGEST_MODEL") or DEFAULT_INGEST_MODEL
        )
        self.ingest_effort = (
            ingest_effort or os.environ.get("CONCENTRATE_INGEST_EFFORT") or "medium"
        )
        self.ingest_thinking = ingest_thinking
        self.embedder = LocalEmbedder(embedder)
        self._sdk = anthropic_client
        self._graph_factory = graph_factory
        self.cache_root = cache_root
        self.episode_utterances, self.k_edges, self.k_nodes = episode_utterances, k_edges, k_nodes
        self.instructions = load_prompt(ANSWER_PROMPT)
        self.ledger = IngestLedger()
        self.graph: GraphBackend | None = None
        self.manifest: dict[str, EpisodeRef] = {}
        self.cache_dir: Path | None = None
        self._loop = asyncio.new_event_loop()

    # sync wrappers over Graphiti's async API: one private loop for the system's lifetime, so the
    # async SDK client and Kuzu connection are always used from the same loop.
    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        return self._loop.run_until_complete(coro)

    def close(self) -> None:
        if not self._loop.is_closed():
            self._loop.close()

    def config(self) -> dict[str, Any]:
        import graphiti_core

        return {
            "format": _FORMAT_VERSION,
            "graphiti": getattr(graphiti_core, "__version__", "0.31"),
            "model": self.ingest_model,
            "effort": self.ingest_effort,
            "thinking": self.ingest_thinking,
            "episode_utterances": self.episode_utterances,
            "embedder": getattr(
                self.embedder._embedder, "name", "sentence-transformers/all-MiniLM-L6-v2"
            ),
        }

    def _open(self, path: Path, fresh: bool) -> GraphBackend:
        sdk = self._sdk
        if sdk is None:
            sdk = async_anthropic_client()
        llm = AccountedAnthropicClient(
            self.ingest_model,
            sdk,
            self.ledger,
            effort=self.ingest_effort,
            thinking=self.ingest_thinking,
        )
        if self._graph_factory is not None:
            return self._graph_factory(path, llm, self.embedder, fresh)
        graph = KuzuGraphiti(path, llm, self.embedder, fresh=fresh)
        self._run(graph.setup())
        return graph

    def episodes(self, notes: Sequence[MeetingNote]) -> list[Episode]:
        """All episodes, oldest meeting first (Graphiti needs chronological, sequential adds)."""
        return [
            e for m in cv.by_date(list(notes)) for e in episodes_for(m, self.episode_utterances)
        ]

    def ingest(self, notes: list[MeetingNote]) -> IngestStats:
        """Build (or reuse, or resume) the graph for ``notes`` under ``cache_root/<hash>/``."""
        self.cache_dir = d = self.cache_root / corpus_hash(notes, self.config())
        done_path, progress_path, db = d / "ingest.json", d / "progress.jsonl", d / "graph.kuzu"
        if done_path.exists():
            done = json.loads(done_path.read_text())
            self.manifest = {u: EpisodeRef(**r) for u, r in done["manifest"].items()}
            self.graph = self._open(db, fresh=False)
            s = done["stats"]
            return IngestStats(
                llm_calls=s["llm_calls"],
                input_tokens=s["input_tokens"],
                output_tokens=s["output_tokens"],
                cost_usd=s["cost_usd"],
                seconds=s["seconds"],
                cached=True,
                detail=done["detail"],
            )
        progress = read_progress(progress_path)  # tolerates a kill mid-write
        if not progress and d.exists():  # a crash before the first episode finished: start clean
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
        self.graph = self._open(db, fresh=not progress)
        done_keys = {p["key"] for p in progress}
        self.manifest = {p["uuid"]: EpisodeRef(**p["ref"]) for p in progress}
        start = time.perf_counter()
        with progress_path.open("a") as fh:
            for ep in self.episodes(notes):
                if ep.key in done_keys:
                    continue
                before, t0 = len(self.ledger.calls), time.perf_counter()
                uuid = self._run(self.graph.add_episode(ep))
                ref = EpisodeRef(ep.note_id, ep.title, ep.reference_time.date().isoformat())
                self.manifest[uuid] = ref
                row = {
                    "key": ep.key,
                    "uuid": uuid,
                    "ref": asdict(ref),
                    "corpus_tokens": estimate_tokens(ep.body),
                    "seconds": time.perf_counter() - t0,
                    **self.ledger.totals(before),
                }
                fh.write(json.dumps(row) + "\n")
                fh.flush()
                progress.append(row)
        counts = self._run(self.graph.counts())
        total = {
            k: sum(p[k] for p in progress)
            for k in (
                "llm_calls",
                "input_tokens",
                "output_tokens",
                "cost_usd",
                "seconds",
                "corpus_tokens",
            )
        }
        detail: dict[str, Any] = {
            **counts,
            "meetings": len(notes),
            "episode_text_tokens_est": total["corpus_tokens"],
            "cache_read_tokens": sum(p.get("cache_read_tokens", 0) for p in progress),
            "cache_write_tokens": sum(p.get("cache_write_tokens", 0) for p in progress),
            "by_prompt": self.ledger.by_prompt(),
            "resumed": len(done_keys) > 0,
            "this_run_seconds": time.perf_counter() - start,
            "config": self.config(),
        }
        stats = IngestStats(
            llm_calls=int(total["llm_calls"]),
            input_tokens=int(total["input_tokens"]),
            output_tokens=int(total["output_tokens"]),
            cost_usd=float(total["cost_usd"]),
            seconds=float(total["seconds"]),
            detail=detail,
        )
        done_path.write_text(
            json.dumps(
                {
                    "stats": {
                        k: v for k, v in asdict(stats).items() if k not in ("detail", "cached")
                    },
                    "detail": detail,
                    "manifest": {u: asdict(r) for u, r in self.manifest.items()},
                },
                indent=1,
                default=str,
            )
        )
        return stats

    def estimate_ingest(self, notes: list[MeetingNote]) -> tuple[int, int]:
        """Offline (input, output) token estimate, calibrated on the smoke test's ratios."""
        sizes = [estimate_tokens(e.body) for e in self.episodes(notes)]
        inp = sum(
            INGEST_INPUT_PER_TOKEN * s
            + PREV_CONTEXT_CALLS * sum(sizes[max(0, i - PREVIOUS_EPISODES) : i])
            for i, s in enumerate(sizes)
        )
        return round(inp), round(INGEST_OUTPUT_PER_TOKEN * sum(sizes))

    def _context(self, q: QuestionLike) -> tuple[str, list[str]]:
        if self.graph is None:
            raise RuntimeError("GraphitiSystem.run called before ingest()")
        facts, entities = self._run(self.graph.search(q.question, self.k_edges, self.k_nodes))
        body, cited = build_context(facts, entities, self.manifest)
        return f"{body}\n\n{_user_prompt(q)}", cited

    def run(self, q: QuestionLike) -> SystemResult:
        user, retrieved = self._context(q)
        before = len(self.client.ledger.records)
        start = time.perf_counter()
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
        context = self.k_edges * FACT_TOKENS_EST + self.k_nodes * NODE_TOKENS_EST
        return estimate_tokens(self.instructions) + context + estimate_tokens(
            _user_prompt(q)
        ), OUTPUT_TOKENS_EST
