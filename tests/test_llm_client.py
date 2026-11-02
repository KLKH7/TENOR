"""LLM client: caching, accounting and error handling, with a fake backend (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from concentrate.llm.client import (
    Completion,
    LLMClient,
    LLMError,
    LLMRefusal,
    LLMRequest,
    LLMTruncated,
    cost_usd,
)


class Out(BaseModel):
    answer: str


class FakeBackend:
    def __init__(self, text: str = '{"answer": "hi"}', stop: str = "end_turn") -> None:
        self.text, self.stop, self.calls = text, stop, 0

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.calls += 1
        return Completion(self.text, model, 1000, 500, self.stop)


def _req(user: str = "hello") -> LLMRequest:
    return LLMRequest(
        stage="test", prompt_id="t.v1", system="sys", user=user, schema={"type": "object"}
    )


def test_cache_hit_skips_backend_and_is_free(tmp_path: Path) -> None:
    backend = FakeBackend()
    client = LLMClient(backend, model="claude-opus-5-5", cache_dir=tmp_path)
    assert client.structured(_req(), Out).answer == "hi"
    assert client.structured(_req(), Out).answer == "hi"
    assert backend.calls == 1
    assert [r.cached for r in client.ledger.records] == [False, True]
    assert client.ledger.spent_usd() == pytest.approx(cost_usd("claude-opus-5-5", 1000, 500))
    stage = client.ledger.by_stage()["test"]
    assert stage["calls"] == 2 and stage["cached"] == 1


def test_cache_key_depends_on_model_and_prompt(tmp_path: Path) -> None:
    backend = FakeBackend()
    LLMClient(backend, model="claude-opus-5-5", cache_dir=tmp_path).structured(_req(), Out)
    LLMClient(backend, model="claude-sonnet-5-5", cache_dir=tmp_path).structured(_req(), Out)
    LLMClient(backend, model="claude-opus-5-5", cache_dir=tmp_path).structured(_req("x"), Out)
    assert backend.calls == 3


def test_offline_mode_raises_on_miss(tmp_path: Path) -> None:
    client = LLMClient(FakeBackend(), cache_dir=tmp_path, offline=True)
    with pytest.raises(LLMError, match="offline"):
        client.structured(_req(), Out)


@pytest.mark.parametrize(("stop", "exc"), [("refusal", LLMRefusal), ("max_tokens", LLMTruncated)])
def test_bad_stop_reasons_raise_and_are_not_cached(
    tmp_path: Path, stop: str, exc: type[Exception]
) -> None:
    client = LLMClient(FakeBackend(stop=stop), cache_dir=tmp_path)
    with pytest.raises(exc):
        client.structured(_req(), Out)
    assert not list(tmp_path.iterdir())


def test_unknown_model_pricing_is_an_error() -> None:
    with pytest.raises(KeyError):
        cost_usd("some-unknown-model", 1, 1)


def test_default_model_comes_from_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CONCENTRATE_MODEL", "claude-sonnet-5-5")
    assert LLMClient(FakeBackend(), cache_dir=tmp_path).model == "claude-sonnet-5-5"
