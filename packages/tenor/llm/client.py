"""The single gateway for every LLM call in Concentrate.

Responsibilities:

* **Structured output.** Every call returns JSON validated against a Pydantic model, requested
  via ``output_config.format`` (forced tool use is rejected by current Claude models).
* **Reproducibility.** A disk cache keyed by a hash of (model, prompt id, system, user, schema,
  max_tokens, effort). Re-running an eval or the synthetic generator with a warm cache makes no
  API calls and returns byte-identical outputs.
* **Accounting.** Every call (cached or not) is recorded in a :class:`Ledger` with tokens, cost
  and latency, aggregated per pipeline stage.
* **Robustness.** Explicit timeouts and our own logged retries with exponential backoff
  (:mod:`tenor.llm.resilience`); server-side refusal fallback; refusals and truncation
  raise typed errors instead of returning partial JSON. A call that fails or times out writes
  nothing to the cache and nothing to the ledger. Every reply beats the stall watchdog.
* **Batch prefetch.** :func:`prefetch_turns` / :func:`prefetch_structured` send many requests
  through the Message Batches API (50% price) and write each result into the *same* cache file
  the interactive path would, so the normal code then replays it with no divergence. Batch
  completions are flagged (``batch=True``): priced at the batch rate, never used for latency.

The backend is pluggable so tests run with a fake and never touch the network.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx2
from pydantic import BaseModel

from tenor.llm.resilience import (
    DEFAULT_POLICY,
    MIN_READ_TIMEOUT_S,
    RetryPolicy,
    anthropic_client,
    call_with_retries,
    timeout_for,
)
from tenor.watchdog import beat

DEFAULT_MODEL = "claude-opus-5-5"

# USD per 1M tokens (input, output). Source: Anthropic model table, cached 2026-10-06.
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
_FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}

M = TypeVar("M", bound=BaseModel)


class LLMError(Exception):
    """Base error for LLM calls."""


class LLMRefusal(LLMError):
    """The model (and its fallback) declined the request."""


class LLMTruncated(LLMError):
    """Output hit ``max_tokens``; the JSON is incomplete."""


class BudgetExceeded(LLMError):
    """A live call would take this run past its approved spend cap (``max_usd``)."""


# Prompt caching: reads cost $0.20/MTok on the current Opus and Sonnet (model table); writes
# cost 1.25x the input price (5-minute TTL).
CACHE_READ_PER_MTOK: dict[str, float] = {"claude-opus-5-5": 0.20, "claude-sonnet-5-5": 0.20}
CACHE_WRITE_MULTIPLIER = 1.25
# Message Batches API: "All usage is charged at 50 percent of the standard API prices"
# (platform.claude.com/docs/en/build-with-claude/batch-processing, checked 2026-11-02).
BATCH_DISCOUNT = 0.5


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    *,
    batch: bool = False,
) -> float:
    """Price a call. ``input_tokens`` excludes cached tokens, as the API reports it.

    ``batch`` applies the Message Batches discount to every token class. Unknown models raise
    so we never silently report $0.
    """
    if model not in PRICING:
        raise KeyError(f"no pricing for model {model!r}; add it to tenor.llm.client.PRICING")
    p_in, p_out = PRICING[model]
    p_read = CACHE_READ_PER_MTOK.get(model, p_in * 0.1)
    full = (
        input_tokens * p_in
        + output_tokens * p_out
        + cache_read_tokens * p_read
        + cache_write_tokens * p_in * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000
    return full * BATCH_DISCOUNT if batch else full


def estimate_tokens(text: str) -> int:
    """Cheap offline token estimate (~3.5 chars/token for English prose). Planning only."""
    return max(1, round(len(text) / 3.5))


@dataclass(frozen=True)
class LLMRequest:
    stage: str  # pipeline stage for accounting, e.g. "synth.polish"
    prompt_id: str  # versioned prompt file stem, e.g. "synth_polish.v1"
    system: str
    user: str
    schema: dict[str, Any]
    max_tokens: int = 16000
    effort: str = "medium"
    # Mark ``system`` as a prompt-cache breakpoint (put the long, shared context there).
    cache_system: bool = False


@dataclass(frozen=True)
class Completion:
    """Raw backend output."""

    text: str
    model: str  # model that actually served the request (may differ after a fallback)
    input_tokens: int
    output_tokens: int
    stop_reason: str
    batch: bool = False  # produced by the Message Batches API (batch price, no latency)
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


class Backend(Protocol):
    def complete(self, request: LLMRequest, model: str) -> Completion: ...


def structured_params(request: LLMRequest, model: str) -> dict[str, Any]:
    """Messages API params for a single-turn structured call (shared by interactive and batch)."""
    system: Any = request.system
    if request.cache_system:
        system = [{"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}]
    return {
        "model": model,
        "max_tokens": request.max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": request.user}],
        "output_config": {
            "effort": request.effort,
            "format": {"type": "json_schema", "schema": request.schema},
        },
    }


class AnthropicBackend:
    """Calls the Claude Messages API with structured output and refusal fallback.

    Each call gets a timeout scaled to its ``max_tokens`` and is retried under ``policy``
    (SDK retries are off; see :mod:`tenor.llm.resilience`).
    """

    def __init__(
        self,
        policy: RetryPolicy = DEFAULT_POLICY,
        *,
        sdk_client: Any | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        # The SDK is imported lazily: tests and offline runs never need it configured.
        self._client = sdk_client if sdk_client is not None else anthropic_client()
        self._policy, self._sleep = policy, sleep

    def complete(self, request: LLMRequest, model: str) -> Completion:
        kwargs = structured_params(request, model)
        if model in _FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        kwargs["timeout"] = timeout_for(request.max_tokens)
        response = call_with_retries(
            lambda: self._client.beta.messages.create(**kwargs),
            what=f"{request.stage} ({request.prompt_id})",
            policy=self._policy,
            sleep=self._sleep,
        )
        text = next((b.text for b in response.content if b.type == "text"), "")
        return Completion(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=str(response.stop_reason),
            cache_read_tokens=int(getattr(response.usage, "cache_read_input_tokens", 0) or 0),
            cache_write_tokens=int(getattr(response.usage, "cache_creation_input_tokens", 0) or 0),
        )


@dataclass
class CallRecord:
    stage: str
    prompt_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float  # what the call cost (or originally cost, if served from cache)
    cached: bool
    latency_s: float
    key: str
    batch: bool = False  # completion came from the Message Batches API: latency is not real


@dataclass
class Ledger:
    """Accumulates per-call records; summarises tokens and spend by stage."""

    records: list[CallRecord] = field(default_factory=list)

    def add(self, record: CallRecord) -> None:
        self.records.append(record)

    def spent_usd(self) -> float:
        """Money actually spent in this process (cache hits are free)."""
        return sum(r.cost_usd for r in self.records if not r.cached)

    def by_stage(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = defaultdict(
            lambda: {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0}
        )
        for r in self.records:
            s = out[r.stage]
            s["calls"] += 1
            s["cached"] += int(r.cached)
            s["input_tokens"] += r.input_tokens
            s["output_tokens"] += r.output_tokens
            s["usd"] += 0.0 if r.cached else r.cost_usd
        return dict(out)

    def to_json(self) -> list[dict[str, Any]]:
        return [asdict(r) for r in self.records]


class LLMClient:
    """Structured, cached, accounted LLM calls. Construct once per run."""

    def __init__(
        self,
        backend: Backend | None = None,
        *,
        model: str | None = None,
        cache_dir: Path | None = None,
        ledger: Ledger | None = None,
        offline: bool = False,
        max_usd: float | None = None,
    ) -> None:
        self.model = model or os.environ.get("CONCENTRATE_MODEL") or DEFAULT_MODEL
        self._backend = backend
        self._cache_dir = cache_dir or Path(os.environ.get("CONCENTRATE_CACHE", "data/cache/llm"))
        self.ledger = ledger or Ledger()
        self.offline = offline  # cache-only: raise instead of calling the API on a miss
        # Hard spend cap for this process: set it to the amount Kian approved. A live call is
        # refused once spend reaches it, so a run can overshoot by at most one call; batches are
        # checked against a worst-case estimate before they are submitted.
        env_cap = os.environ.get("CONCENTRATE_MAX_USD")
        self.max_usd = max_usd if max_usd is not None else (float(env_cap) if env_cap else None)

    def check_budget(self, next_usd: float = 0.0) -> None:
        """Raise :class:`BudgetExceeded` if spending ``next_usd`` more would pass ``max_usd``."""
        if self.max_usd is None:
            return
        spent = self.ledger.spent_usd()
        if spent >= self.max_usd or spent + next_usd > self.max_usd:
            raise BudgetExceeded(
                f"spend cap ${self.max_usd:.2f} reached: spent ${spent:.4f}, "
                f"next would add ~${next_usd:.4f}"
            )

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def _key(self, req: LLMRequest) -> str:
        payload = {
            "model": self.model,
            "prompt_id": req.prompt_id,
            "system": req.system,
            "user": req.user,
            "schema": req.schema,
            "max_tokens": req.max_tokens,
            "effort": req.effort,
        }
        if req.cache_system:  # only when set, so existing cache keys are unchanged
            payload["cache_system"] = True
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()

    def cache_path(self, req: LLMRequest) -> Path:
        return self._cache_dir / f"{self._key(req)}.json"

    def is_cached(self, req: LLMRequest) -> bool:
        return self.cache_path(req).exists()

    def write_cache(self, req: LLMRequest, completion: Completion) -> None:
        """Persist ``completion`` exactly as :meth:`structured` does after a live call."""
        path = self.cache_path(req)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"request": asdict(req), "model": self.model, "completion": asdict(completion)}
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=1))

    def structured(self, req: LLMRequest, out_type: type[M]) -> M:
        """Run ``req`` and validate the JSON reply as ``out_type``."""
        key = self._key(req)
        path = self.cache_path(req)
        start = time.perf_counter()
        if path.exists():
            entry = json.loads(path.read_text())
            completion = Completion(**entry["completion"])
            cached = True
        else:
            if self.offline:
                raise LLMError(f"cache miss in offline mode for {req.prompt_id} ({key[:12]})")
            self.check_budget()
            if self._backend is None:
                self._backend = AnthropicBackend()
            completion = self._backend.complete(req, self.model)
            if completion.stop_reason == "refusal":
                raise LLMRefusal(f"{req.prompt_id}: model declined the request")
            if completion.stop_reason == "max_tokens":
                raise LLMTruncated(f"{req.prompt_id}: output truncated at {req.max_tokens}")
            self.write_cache(req, completion)
            cached = False
        self.ledger.add(
            CallRecord(
                stage=req.stage,
                prompt_id=req.prompt_id,
                model=completion.model,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=cost_usd(
                    completion.model,
                    completion.input_tokens,
                    completion.output_tokens,
                    completion.cache_read_tokens,
                    completion.cache_write_tokens,
                    batch=completion.batch,
                ),
                cached=cached,
                latency_s=0.0 if completion.batch else time.perf_counter() - start,
                key=key,
                batch=completion.batch,
            )
        )
        beat()
        return out_type.model_validate_json(completion.text)


def load_prompt(prompt_id: str, root: Path | None = None) -> str:
    """Read ``prompts/<prompt_id>.md`` (e.g. ``synth_polish.v1``)."""
    base = root or Path(__file__).resolve().parents[3] / "prompts"
    return (base / f"{prompt_id}.md").read_text()


# -- multi-turn tool use (agentic baselines) ----------------------------------------------------


@dataclass(frozen=True)
class TurnRequest:
    """One Messages API call in a (possibly multi-turn, tool-using) conversation."""

    stage: str
    prompt_id: str
    system: str
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...] = ()
    schema: dict[str, Any] | None = None  # JSON schema for the final text answer
    max_tokens: int = 16000
    effort: str = "medium"
    cache_system: bool = False  # mark the system prompt as a cache breakpoint (shared corpus)


@dataclass(frozen=True)
class TurnCompletion:
    content: list[dict[str, Any]]  # assistant content blocks, replayable verbatim
    text: str
    stop_reason: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_s: float = 0.0  # of the original (uncached) call; 0 for batch results
    batch: bool = False  # produced by the Message Batches API (batch price, no latency)

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


class ToolBackend(Protocol):
    def turn(self, request: TurnRequest, model: str) -> TurnCompletion: ...


def turn_params(request: TurnRequest, model: str) -> dict[str, Any]:
    """Messages API params for one turn (shared by the interactive and batch paths)."""
    system: list[dict[str, Any]] = [{"type": "text", "text": request.system}]
    if request.cache_system:
        system[0]["cache_control"] = {"type": "ephemeral"}
    output_config: dict[str, Any] = {"effort": request.effort}
    if request.schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": request.schema}
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": request.max_tokens,
        "system": system,
        "messages": list(request.messages),
        "output_config": output_config,
    }
    if request.tools:
        kwargs["tools"] = list(request.tools)
    return kwargs


def turn_completion(
    message: dict[str, Any], *, latency_s: float = 0.0, batch: bool = False
) -> TurnCompletion:
    """Build a :class:`TurnCompletion` from a Messages API ``Message`` serialised to JSON."""
    usage = message.get("usage") or {}
    content: list[dict[str, Any]] = list(message.get("content") or [])
    return TurnCompletion(
        content=content,
        text="".join(b.get("text", "") for b in content if b.get("type") == "text"),
        stop_reason=str(message.get("stop_reason")),
        model=str(message["model"]),
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
        cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        latency_s=latency_s,
        batch=batch,
    )


def _anthropic_turn(
    client: Any,
    request: TurnRequest,
    model: str,
    policy: RetryPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], None] | None = None,
) -> TurnCompletion:
    kwargs = turn_params(request, model)
    if model in _FALLBACK_MODELS:
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    kwargs["timeout"] = timeout_for(request.max_tokens)
    holder: dict[str, float] = {}

    def attempt() -> Any:
        holder["start"] = time.perf_counter()  # latency of the attempt that succeeded
        return client.beta.messages.create(**kwargs)

    what = f"{request.stage} ({request.prompt_id})"
    response = call_with_retries(attempt, what=what, policy=policy, sleep=sleep)
    latency = time.perf_counter() - holder["start"]
    return turn_completion(response.model_dump(mode="json", exclude_none=True), latency_s=latency)


def _turn_key(model: str, request: TurnRequest) -> str:
    payload = {"model": model, **asdict(request)}
    payload.pop("stage")  # accounting label only; must not split the cache
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def turn_cache_path(client: LLMClient, request: TurnRequest) -> Path:
    """Where :func:`run_turn` caches ``request`` for ``client``'s model."""
    return client.cache_dir / "turns" / f"{_turn_key(client.model, request)}.json"


def write_turn_cache(client: LLMClient, request: TurnRequest, completion: TurnCompletion) -> None:
    """Persist ``completion`` exactly as :func:`run_turn` does after a live call."""
    path = turn_cache_path(client, request)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"completion": asdict(completion)}, ensure_ascii=False))


def run_turn(
    client: LLMClient, request: TurnRequest, backend: ToolBackend | None = None
) -> TurnCompletion:
    """Run one cached, accounted Messages call (with optional tools) through ``client``."""
    key = _turn_key(client.model, request)
    path = turn_cache_path(client, request)
    if path.exists():
        completion = TurnCompletion(**json.loads(path.read_text())["completion"])
        cached = True
    else:
        if client.offline:
            raise LLMError(f"cache miss in offline mode for {request.prompt_id} ({key[:12]})")
        client.check_budget()
        if backend is None:
            backend = _SdkToolBackend(anthropic_client())
        completion = backend.turn(request, client.model)
        if completion.stop_reason == "refusal":
            raise LLMRefusal(f"{request.prompt_id}: model declined the request")
        if completion.stop_reason == "max_tokens":
            raise LLMTruncated(f"{request.prompt_id}: output truncated at {request.max_tokens}")
        write_turn_cache(client, request, completion)
        cached = False
    client.ledger.add(
        CallRecord(
            stage=request.stage,
            prompt_id=request.prompt_id,
            model=completion.model,
            input_tokens=completion.total_input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=_turn_cost(completion),
            cached=cached,
            latency_s=completion.latency_s,
            key=key,
            batch=completion.batch,
        )
    )
    beat()
    return completion


def _turn_cost(c: TurnCompletion) -> float:
    return cost_usd(
        c.model,
        c.input_tokens,
        c.output_tokens,
        c.cache_read_tokens,
        c.cache_write_tokens,
        batch=c.batch,
    )


class _SdkToolBackend:
    def __init__(
        self,
        sdk_client: Any,
        policy: RetryPolicy = DEFAULT_POLICY,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._client, self._policy, self._sleep = sdk_client, policy, sleep

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        return _anthropic_turn(self._client, request, model, self._policy, self._sleep)


def count_tokens(texts: list[str], model: str, cache_dir: Path) -> list[int]:
    """Exact input-token counts via the free ``messages.count_tokens`` endpoint (cached).

    Counts include a few tokens of message framing per text, which is what a call would pay.
    """
    out: list[int] = []
    sdk: Any = None
    for text in texts:
        key = hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()
        path = cache_dir / "tokens" / f"{key}.txt"
        if path.exists():
            out.append(int(path.read_text()))
            continue
        if sdk is None:
            sdk = anthropic_client(max_tokens=0)
        messages: list[dict[str, Any]] = [{"role": "user", "content": text}]
        result = call_with_retries(
            functools.partial(sdk.messages.count_tokens, model=model, messages=messages),
            what="count_tokens",
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(result.input_tokens))
        out.append(result.input_tokens)
    return out


# -- Message Batches prefetch -------------------------------------------------------------------

# API limits (platform.claude.com/docs/en/api/messages/batches/create, checked 2026-11-02):
# custom_id must match ^[a-zA-Z0-9_-]{1,64}$; a batch holds <= 100,000 requests and <= 256 MB.
# The cache keys are 64-char sha256 hex digests, so they are valid custom_ids as-is.
BATCH_MAX_REQUESTS = 100_000
BATCH_MAX_BYTES = 200 * 1024 * 1024  # stay well under the 256 MB cap (JSON framing overhead)
_CUSTOM_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


@dataclass(frozen=True)
class BatchItemResult:
    """One line of a batch's results, SDK-independent."""

    custom_id: str
    type: str  # succeeded | errored | canceled | expired
    message: dict[str, Any] | None = None  # the Messages API Message (JSON) when succeeded
    error: str = ""


class BatchBackend(Protocol):
    """The three Message Batches operations prefetch needs (fakeable in tests)."""

    def create(self, requests: list[dict[str, Any]]) -> str:
        """Submit ``[{custom_id, params}]``; return the batch id."""
        ...

    def retrieve(self, batch_id: str) -> str:
        """Return the batch's ``processing_status`` (``in_progress``/``canceling``/``ended``)."""
        ...

    def results(self, batch_id: str) -> Iterable[BatchItemResult]:
        """Stream an ended batch's results (any order)."""
        ...


class AnthropicBatchBackend:
    """Message Batches over the official SDK (``client.messages.batches``, no beta header).

    The server-side ``fallbacks`` parameter is rejected by the Batches API, so batch params
    never carry it; a refused item is simply not cached and the run falls back to the
    interactive call (which does use ``fallbacks``).
    """

    def __init__(self, policy: RetryPolicy = DEFAULT_POLICY, *, sdk_client: Any = None) -> None:
        # Batch bodies can be ~200 MB: allow a long write; replies are small, so reads stay short.
        self._client = sdk_client if sdk_client is not None else anthropic_client(max_tokens=0)
        self._policy = policy
        self._upload_timeout = httpx2.Timeout(MIN_READ_TIMEOUT_S, connect=10.0, write=600.0)

    def create(self, requests: list[dict[str, Any]]) -> str:
        # Not idempotent: a create whose reply is lost may have made a batch server-side. Retrying
        # can then submit it twice (both billed); we accept that over hanging, and log it.
        batch = call_with_retries(
            lambda: self._client.messages.batches.create(
                requests=requests,
                timeout=self._upload_timeout,
            ),
            what=f"batches.create ({len(requests)} requests)",
            policy=self._policy,
        )
        return str(batch.id)

    def retrieve(self, batch_id: str) -> str:
        batch = call_with_retries(
            lambda: self._client.messages.batches.retrieve(batch_id),
            what=f"batches.retrieve {batch_id}",
            policy=self._policy,
        )
        return str(batch.processing_status)

    def results(self, batch_id: str) -> Iterable[BatchItemResult]:
        # Opening the stream is retried; a drop mid-stream raises (rerunning the command resumes:
        # items already written to the cache are skipped).
        stream = call_with_retries(
            lambda: self._client.messages.batches.results(batch_id),
            what=f"batches.results {batch_id}",
            policy=self._policy,
        )
        for item in stream:
            r = item.result
            if r.type == "succeeded":
                message = r.message.model_dump(mode="json", exclude_none=True)
                yield BatchItemResult(item.custom_id, "succeeded", message)
            elif r.type == "errored":
                err = r.error.model_dump(mode="json")
                detail = err.get("error", err)
                yield BatchItemResult(
                    item.custom_id,
                    "errored",
                    error=f"{detail.get('type', '')}: {detail.get('message', '')}",
                )
            else:
                yield BatchItemResult(item.custom_id, str(r.type))


@dataclass
class BatchReport:
    """What a prefetch did. ``fallback`` items were not cached: the run calls them live."""

    requested: int = 0  # distinct requests asked for
    already_cached: int = 0  # skipped: the cache already had them
    submitted: int = 0
    succeeded: int = 0  # written to the cache
    fallback: dict[str, int] = field(default_factory=dict)  # reason -> count (not cached)
    batch_ids: list[str] = field(default_factory=list)
    seconds: float = 0.0  # submit -> all results written
    cost_usd: float = 0.0  # billed (all succeeded items, incl. refused/truncated), batch price


@dataclass(frozen=True)
class _Item:
    key: str
    params: dict[str, Any]
    stage: str
    prompt_id: str
    write: Callable[[dict[str, Any]], None]  # write the cache entry from a usable Message


def _worst_case_usd(model: str, params: dict[str, Any]) -> float:
    """Upper-bound price of one batch item: estimated input, full ``max_tokens`` output."""
    text = json.dumps(
        {k: params.get(k) for k in ("system", "messages", "tools")}, ensure_ascii=False
    )
    return cost_usd(model, estimate_tokens(text), int(params.get("max_tokens", 0)), batch=True)


def _chunks(items: list[_Item]) -> list[list[_Item]]:
    out: list[list[_Item]] = [[]]
    size = 0
    for it in items:
        n = len(json.dumps({"custom_id": it.key, "params": it.params}, ensure_ascii=False))
        if out[-1] and (len(out[-1]) >= BATCH_MAX_REQUESTS or size + n > BATCH_MAX_BYTES):
            out.append([])
            size = 0
        out[-1].append(it)
        size += n
    return [c for c in out if c]


def _run_batch(
    client: LLMClient,
    items: list[_Item],
    already_cached: int,
    backend: BatchBackend | None,
    sleep: Callable[[float], None],
    poll_s: float,
    max_poll_s: float,
) -> BatchReport:
    report = BatchReport(requested=len(items) + already_cached, already_cached=already_cached)
    if not items or client.offline:
        return report
    for it in items:
        if not _CUSTOM_ID.match(it.key):
            raise ValueError(f"cache key {it.key!r} is not a valid batch custom_id")
    client.check_budget(sum(_worst_case_usd(client.model, it.params) for it in items))
    backend = backend or AnthropicBatchBackend()
    by_id = {it.key: it for it in items}
    start = time.perf_counter()
    for chunk in _chunks(items):
        reqs = [{"custom_id": it.key, "params": it.params} for it in chunk]
        report.batch_ids.append(backend.create(reqs))
        report.submitted += len(chunk)
    pending = list(report.batch_ids)
    interval = poll_s
    while pending:
        pending = [b for b in pending if backend.retrieve(b) != "ended"]
        beat()  # a batch can legitimately take hours: polling is liveness
        if pending:
            sleep(interval)
            interval = min(max_poll_s, interval * 1.5)
    seen: set[str] = set()
    fallback: Counter[str] = Counter()
    for batch_id in report.batch_ids:
        for res in backend.results(batch_id):
            item = by_id.get(res.custom_id)
            if item is None or res.custom_id in seen:
                continue
            seen.add(res.custom_id)
            if res.type != "succeeded" or res.message is None:
                fallback[res.type] += 1  # errored/canceled/expired items are not billed
                continue
            # A succeeded item is billed even when we can't use it, so it is always recorded.
            c = turn_completion(res.message, batch=True)
            usd = _turn_cost(c)
            report.cost_usd += usd
            client.ledger.add(
                CallRecord(
                    stage=f"{item.stage}.batch",
                    prompt_id=item.prompt_id,
                    model=c.model,
                    input_tokens=c.total_input_tokens,
                    output_tokens=c.output_tokens,
                    cost_usd=usd,
                    cached=False,
                    latency_s=0.0,
                    key=item.key,
                    batch=True,
                )
            )
            if c.stop_reason in ("refusal", "max_tokens"):  # as the interactive path: no cache
                fallback[c.stop_reason] += 1
                continue
            item.write(res.message)
            report.succeeded += 1
    missing = len(by_id) - len(seen)
    if missing:
        fallback["missing"] += missing
    report.fallback = dict(fallback)
    report.seconds = time.perf_counter() - start
    return report


def prefetch_turns(
    client: LLMClient,
    requests: Sequence[TurnRequest],
    backend: BatchBackend | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 5.0,
    max_poll_s: float = 60.0,
) -> BatchReport:
    """Batch every uncached ``TurnRequest`` and write results where :func:`run_turn` reads them.

    Already-cached and duplicate requests are skipped. Errored, expired, canceled, refused or
    truncated items are left uncached, so :func:`run_turn` calls them interactively later.
    """
    items: dict[str, _Item] = {}
    cached = 0
    for req in requests:
        key = _turn_key(client.model, req)
        if key in items:
            continue
        if turn_cache_path(client, req).exists():
            cached += 1
            continue

        def write(message: dict[str, Any], req: TurnRequest = req) -> None:
            write_turn_cache(client, req, turn_completion(message, batch=True))

        items[key] = _Item(key, turn_params(req, client.model), req.stage, req.prompt_id, write)
    return _run_batch(client, list(items.values()), cached, backend, sleep, poll_s, max_poll_s)


def prefetch_structured(
    client: LLMClient,
    requests: Sequence[LLMRequest],
    backend: BatchBackend | None = None,
    *,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 5.0,
    max_poll_s: float = 60.0,
) -> BatchReport:
    """Batch every uncached ``LLMRequest`` and write results where
    :meth:`LLMClient.structured` reads them (same skip/fallback rules as :func:`prefetch_turns`).
    """
    items: dict[str, _Item] = {}
    cached = 0
    for req in requests:
        key = client._key(req)
        if key in items:
            continue
        if client.is_cached(req):
            cached += 1
            continue

        def write(message: dict[str, Any], req: LLMRequest = req) -> None:
            t = turn_completion(message, batch=True)
            # Same text rule as AnthropicBackend.complete: the first text block.
            text = next((b.get("text", "") for b in t.content if b.get("type") == "text"), "")
            c = Completion(text, t.model, t.input_tokens, t.output_tokens, t.stop_reason, True)
            client.write_cache(req, c)

        items[key] = _Item(
            key, structured_params(req, client.model), req.stage, req.prompt_id, write
        )
    return _run_batch(client, list(items.values()), cached, backend, sleep, poll_s, max_poll_s)
