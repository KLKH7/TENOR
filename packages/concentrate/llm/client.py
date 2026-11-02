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


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Price a call. Unknown models raise so we never silently report $0."""
    if model not in PRICING:
        raise KeyError(f"no pricing for model {model!r}; add it to concentrate.llm.client.PRICING")
    p_in, p_out = PRICING[model]
    return (input_tokens * p_in + output_tokens * p_out) / 1_000_000


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
