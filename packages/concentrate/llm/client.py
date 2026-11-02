"""The single gateway for every LLM call in Concentrate.

Responsibilities:

* **Structured output.** Every call returns JSON validated against a Pydantic model, requested
  via ``output_config.format`` (forced tool use is rejected by current Claude models).
* **Reproducibility.** A disk cache keyed by a hash of (model, prompt id, system, user, schema,
  max_tokens, effort). Re-running an eval or the synthetic generator with a warm cache makes no
  API calls and returns byte-identical outputs.
* **Accounting.** Every call (cached or not) is recorded in a :class:`Ledger` with tokens, cost
  and latency, aggregated per pipeline stage.
* **Robustness.** SDK retries for 429/5xx; server-side refusal fallback; refusals and truncation
  raise typed errors instead of returning partial JSON.

The backend is pluggable so tests run with a fake and never touch the network.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

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


# Prompt caching: reads cost $0.20/MTok on the current Opus and Sonnet (model table); writes
# cost 1.25x the input price (5-minute TTL).
CACHE_READ_PER_MTOK: dict[str, float] = {"claude-opus-5-5": 0.20, "claude-sonnet-5-5": 0.20}
CACHE_WRITE_MULTIPLIER = 1.25


def cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Price a call. ``input_tokens`` excludes cached tokens, as the API reports it.

    Unknown models raise so we never silently report $0.
    """
    if model not in PRICING:
        raise KeyError(f"no pricing for model {model!r}; add it to concentrate.llm.client.PRICING")
    p_in, p_out = PRICING[model]
    p_read = CACHE_READ_PER_MTOK.get(model, p_in * 0.1)
    return (
        input_tokens * p_in
        + output_tokens * p_out
        + cache_read_tokens * p_read
        + cache_write_tokens * p_in * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000


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


@dataclass(frozen=True)
class Completion:
    """Raw backend output."""

    text: str
    model: str  # model that actually served the request (may differ after a fallback)
    input_tokens: int
    output_tokens: int
    stop_reason: str


class Backend(Protocol):
    def complete(self, request: LLMRequest, model: str) -> Completion: ...


class AnthropicBackend:
    """Calls the Claude Messages API with structured output and refusal fallback."""

    def __init__(self, max_retries: int = 4) -> None:
        import anthropic  # local import: tests and offline runs never need the SDK configured

        self._client = anthropic.Anthropic(max_retries=max_retries)

    def complete(self, request: LLMRequest, model: str) -> Completion:
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "output_config": {
                "effort": request.effort,
                "format": {"type": "json_schema", "schema": request.schema},
            },
        }
        if model in _FALLBACK_MODELS:
            kwargs["betas"] = ["server-side-fallback-2026-07-01"]
            kwargs["fallbacks"] = "default"
        response = self._client.beta.messages.create(**kwargs)
        text = next((b.text for b in response.content if b.type == "text"), "")
        return Completion(
            text=text,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            stop_reason=str(response.stop_reason),
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
    ) -> None:
        self.model = model or os.environ.get("CONCENTRATE_MODEL") or DEFAULT_MODEL
        self._backend = backend
        self._cache_dir = cache_dir or Path(os.environ.get("CONCENTRATE_CACHE", "data/cache/llm"))
        self.ledger = ledger or Ledger()
        self.offline = offline  # cache-only: raise instead of calling the API on a miss

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
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        return hashlib.sha256(blob).hexdigest()

    def is_cached(self, req: LLMRequest) -> bool:
        return (self._cache_dir / f"{self._key(req)}.json").exists()

    def structured(self, req: LLMRequest, out_type: type[M]) -> M:
        """Run ``req`` and validate the JSON reply as ``out_type``."""
        key = self._key(req)
        path = self._cache_dir / f"{key}.json"
        start = time.perf_counter()
        if path.exists():
            entry = json.loads(path.read_text())
            completion = Completion(**entry["completion"])
            cached = True
        else:
            if self.offline:
                raise LLMError(f"cache miss in offline mode for {req.prompt_id} ({key[:12]})")
            if self._backend is None:
                self._backend = AnthropicBackend()
            completion = self._backend.complete(req, self.model)
            if completion.stop_reason == "refusal":
                raise LLMRefusal(f"{req.prompt_id}: model declined the request")
            if completion.stop_reason == "max_tokens":
                raise LLMTruncated(f"{req.prompt_id}: output truncated at {req.max_tokens}")
            path.parent.mkdir(parents=True, exist_ok=True)
            entry = {"request": asdict(req), "model": self.model, "completion": asdict(completion)}
            path.write_text(json.dumps(entry, ensure_ascii=False, indent=1))
            cached = False
        self.ledger.add(
            CallRecord(
                stage=req.stage,
                prompt_id=req.prompt_id,
                model=completion.model,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
                cost_usd=cost_usd(
                    completion.model, completion.input_tokens, completion.output_tokens
                ),
                cached=cached,
                latency_s=time.perf_counter() - start,
                key=key,
            )
        )
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
    latency_s: float = 0.0  # of the original (uncached) call

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


class ToolBackend(Protocol):
    def turn(self, request: TurnRequest, model: str) -> TurnCompletion: ...


def _anthropic_turn(client: Any, request: TurnRequest, model: str) -> TurnCompletion:
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
    if model in _FALLBACK_MODELS:
        kwargs["betas"] = ["server-side-fallback-2026-07-01"]
        kwargs["fallbacks"] = "default"
    start = time.perf_counter()
    response = client.beta.messages.create(**kwargs)
    latency = time.perf_counter() - start
    usage = response.usage
    content = [b.model_dump(mode="json", exclude_none=True) for b in response.content]
    return TurnCompletion(
        content=content,
        text="".join(b.get("text", "") for b in content if b.get("type") == "text"),
        stop_reason=str(response.stop_reason),
        model=response.model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        latency_s=latency,
    )


def _turn_key(model: str, request: TurnRequest) -> str:
    payload = {"model": model, **asdict(request)}
    payload.pop("stage")  # accounting label only; must not split the cache
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(blob).hexdigest()


def run_turn(
    client: LLMClient, request: TurnRequest, backend: ToolBackend | None = None
) -> TurnCompletion:
    """Run one cached, accounted Messages call (with optional tools) through ``client``."""
    key = _turn_key(client.model, request)
    path = client.cache_dir / "turns" / f"{key}.json"
    if path.exists():
        completion = TurnCompletion(**json.loads(path.read_text())["completion"])
        cached = True
    else:
        if client.offline:
            raise LLMError(f"cache miss in offline mode for {request.prompt_id} ({key[:12]})")
        if backend is None:
            import anthropic

            backend = _SdkToolBackend(anthropic.Anthropic(max_retries=4))
        completion = backend.turn(request, client.model)
        if completion.stop_reason == "refusal":
            raise LLMRefusal(f"{request.prompt_id}: model declined the request")
        if completion.stop_reason == "max_tokens":
            raise LLMTruncated(f"{request.prompt_id}: output truncated at {request.max_tokens}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"completion": asdict(completion)}, ensure_ascii=False))
        cached = False
    client.ledger.add(
        CallRecord(
            stage=request.stage,
            prompt_id=request.prompt_id,
            model=completion.model,
            input_tokens=completion.total_input_tokens,
            output_tokens=completion.output_tokens,
            cost_usd=cost_usd(
                completion.model,
                completion.input_tokens,
                completion.output_tokens,
                completion.cache_read_tokens,
                completion.cache_write_tokens,
            ),
            cached=cached,
            latency_s=completion.latency_s,
            key=key,
        )
    )
    return completion


class _SdkToolBackend:
    def __init__(self, sdk_client: Any) -> None:
        self._client = sdk_client

    def turn(self, request: TurnRequest, model: str) -> TurnCompletion:
        return _anthropic_turn(self._client, request, model)


def count_tokens(texts: list[str], model: str, cache_dir: Path) -> list[int]:
    """Exact input-token counts via the free ``messages.count_tokens`` endpoint (cached).

    Counts include a few tokens of message framing per text, which is what a call would pay.
    """
    import anthropic

    out: list[int] = []
    sdk: Any = None
    for text in texts:
        key = hashlib.sha256(f"{model}\n{text}".encode()).hexdigest()
        path = cache_dir / "tokens" / f"{key}.txt"
        if path.exists():
            out.append(int(path.read_text()))
            continue
        if sdk is None:
            sdk = anthropic.Anthropic(max_retries=6)
        result = sdk.messages.count_tokens(
            model=model, messages=[{"role": "user", "content": text}]
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(result.input_tokens))
        out.append(result.input_tokens)
    return out
