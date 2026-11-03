"""Timeouts, retries, the stall watchdog and crash-safe resume (docs/HARDENING.md). No network:
the real anthropic SDK is driven through ``httpx2.MockTransport``."""

from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest
from pydantic import BaseModel

from concentrate import watchdog
from concentrate.agents.mem0_system import MeteredAnthropic, UsageMeter
from concentrate.agents.progress import read_progress
from concentrate.llm import resilience
from concentrate.llm.client import (
    AnthropicBackend,
    Completion,
    LLMClient,
    LLMRequest,
    TurnRequest,
    _SdkToolBackend,
    run_turn,
)
from concentrate.llm.resilience import (
    RetryPolicy,
    acall_with_retries,
    anthropic_client,
    call_with_retries,
    is_retryable,
    timeout_for,
)

MODEL = "claude-opus-5-5"


@pytest.fixture(autouse=True)
def _clean_watchdog_state() -> Iterator[None]:
    yield
    watchdog.reset_stop()
    watchdog.on_forced_exit("t", None)


FAST = RetryPolicy(max_attempts=4, base_s=0.0, cap_s=0.0, deadline_s=60.0)


class Out(BaseModel):
    x: int


def _message(text: str = '{"x": 1}', model: str = MODEL) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


class Script:
    """A MockTransport handler that plays failures, then succeeds; records every request."""

    def __init__(self, *failures: str) -> None:
        self.failures = list(failures)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.failures:
            kind = self.failures.pop(0)
            if kind == "read_timeout":
                raise httpx2.ReadTimeout("silent socket", request=request)
            if kind == "connect":
                raise httpx2.ConnectError("connection reset", request=request)
            status = int(kind)
            body = {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}
            return httpx2.Response(status, json=body, headers={"retry-after-ms": "0"})
        return httpx2.Response(200, json=_message())


def _sdk(script: Script) -> Any:
    return anthropic_client(
        api_key="test-key", http_client=httpx2.Client(transport=httpx2.MockTransport(script))
    )


def _request(max_tokens: int = 16000) -> LLMRequest:
    return LLMRequest("test.stage", "test.v1", "sys", "user", {"type": "object"}, max_tokens)


# -- timeouts --------------------------------------------------------------------------------


def test_timeout_scales_with_max_tokens_and_is_clamped() -> None:
    small, mid, big = timeout_for(500), timeout_for(8000), timeout_for(64000)
    assert small.read == resilience.MIN_READ_TIMEOUT_S
    assert resilience.MIN_READ_TIMEOUT_S < (mid.read or 0) < resilience.MAX_READ_TIMEOUT_S
    assert big.read == resilience.MAX_READ_TIMEOUT_S
    assert small.connect == resilience.CONNECT_TIMEOUT_S
    # The old behaviour we are replacing: SDK default read timeout of 600 s, times 5 attempts.
    assert (big.read or 0) < 600


def test_sdk_client_has_explicit_timeout_and_no_hidden_retries() -> None:
    client = anthropic_client(api_key="test-key")
    assert client.max_retries == 0  # retries are ours, logged, and never multiply with the SDK's
    assert client.timeout.read <= resilience.MAX_READ_TIMEOUT_S


def test_each_call_sends_a_timeout_scaled_to_its_max_tokens() -> None:
    script = Script()
    AnthropicBackend(FAST, sdk_client=_sdk(script), sleep=lambda s: None).complete(
        _request(max_tokens=2000), MODEL
    )
    sent = script.requests[0].extensions["timeout"]
    assert sent["read"] == timeout_for(2000).read
    assert sent["connect"] == resilience.CONNECT_TIMEOUT_S


# -- retries through the real SDK ------------------------------------------------------------


@pytest.mark.parametrize(
    "failures", [("read_timeout",), ("connect",), ("529", "read_timeout", "500"), ("429",)]
)
def test_backend_retries_transient_failures_then_succeeds(failures: tuple[str, ...]) -> None:
    script, slept = Script(*failures), []
    backend = AnthropicBackend(FAST, sdk_client=_sdk(script), sleep=slept.append)
    completion = backend.complete(_request(), MODEL)
    assert completion.text == '{"x": 1}'
    assert len(script.requests) == len(failures) + 1
    assert len(slept) == len(failures)


def test_backend_gives_up_after_max_attempts_with_the_timeout_error() -> None:
    script = Script(*["read_timeout"] * 10)
    backend = AnthropicBackend(FAST, sdk_client=_sdk(script), sleep=lambda s: None)
    with pytest.raises(anthropic.APITimeoutError):
        backend.complete(_request(), MODEL)
    assert len(script.requests) == FAST.max_attempts


def test_backend_does_not_retry_a_bad_request() -> None:
    script = Script("400")
    with pytest.raises(anthropic.BadRequestError):
        AnthropicBackend(FAST, sdk_client=_sdk(script), sleep=lambda s: None).complete(
            _request(), MODEL
        )
    assert len(script.requests) == 1


def test_tool_turns_retry_and_account_once(tmp_path: Path) -> None:
    script = Script("read_timeout", "connect")
    client = LLMClient(model=MODEL, cache_dir=tmp_path)
    req = TurnRequest("agent.turn", "agent.v1", "sys", ({"role": "user", "content": "hi"},))
    backend = _SdkToolBackend(_sdk(script), FAST, sleep=lambda s: None)
    out = run_turn(client, req, backend)
    assert out.text == '{"x": 1}' and len(script.requests) == 3
    assert len(client.ledger.records) == 1  # failed attempts are never accounted
    assert len(list((tmp_path / "turns").glob("*.json"))) == 1


# -- cache and ledger integrity --------------------------------------------------------------


class Flaky:
    """A backend that times out ``fails`` times (as the retry layer would finally raise)."""

    def __init__(self, fails: int) -> None:
        self.fails, self.calls = fails, 0

    def complete(self, request: LLMRequest, model: str) -> Completion:
        self.calls += 1
        if self.calls <= self.fails:
            raise anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x"))
        return Completion('{"x": 7}', model, 100, 20, "end_turn")


def test_timed_out_call_writes_no_cache_and_no_cost(tmp_path: Path) -> None:
    client = LLMClient(Flaky(fails=1), model=MODEL, cache_dir=tmp_path)
    with pytest.raises(anthropic.APITimeoutError):
        client.structured(_request(), Out)
    assert not client.is_cached(_request())
    assert client.ledger.records == [] and client.ledger.spent_usd() == 0.0
    # The rerun (as after a restart) makes exactly one paid call and caches it.
    assert client.structured(_request(), Out).x == 7
    assert client.is_cached(_request()) and len(client.ledger.records) == 1
    assert client.structured(_request(), Out).x == 7  # now a free cache hit
    assert [r.cached for r in client.ledger.records] == [False, True]


def test_retried_call_through_llmclient_is_counted_once(tmp_path: Path) -> None:
    script = Script("read_timeout", "529")
    backend = AnthropicBackend(FAST, sdk_client=_sdk(script), sleep=lambda s: None)
    client = LLMClient(backend, model=MODEL, cache_dir=tmp_path)
    assert client.structured(_request(), Out).x == 1
    assert len(client.ledger.records) == 1
    assert client.ledger.records[0].input_tokens == 100


# -- the retry loop itself -------------------------------------------------------------------


def _timeout() -> Exception:
    return anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x"))


def test_backoff_is_exponential_with_full_jitter() -> None:
    policy = RetryPolicy(max_attempts=6, base_s=1.0, cap_s=10.0, deadline_s=1e9)
    rng = random.Random(0)
    for retry, ceiling in enumerate([1, 2, 4, 8, 10, 10]):
        delays = [policy.backoff(retry, rng) for _ in range(200)]
        assert min(delays) >= 0 and max(delays) <= ceiling
        assert max(delays) > ceiling * 0.8  # spread over the whole window, not fixed


def test_retry_after_header_is_honoured() -> None:
    response = httpx2.Response(
        429, headers={"retry-after": "3"}, request=httpx2.Request("POST", "https://x")
    )
    err = anthropic.RateLimitError("slow down", response=response, body=None)
    calls, slept = iter([err, "ok"]), []

    def fn() -> str:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    policy = RetryPolicy(base_s=100.0, cap_s=100.0)
    assert call_with_retries(fn, what="t", policy=policy, sleep=slept.append) == "ok"
    assert slept == [3.0]


def test_deadline_stops_retrying() -> None:
    now = [0.0]

    def fn() -> None:
        now[0] += 400.0  # each attempt burns a 400 s read timeout
        raise _timeout()

    policy = RetryPolicy(max_attempts=100, base_s=1.0, cap_s=1.0, deadline_s=1000.0)
    with pytest.raises(anthropic.APITimeoutError):
        call_with_retries(fn, what="t", policy=policy, sleep=lambda s: None, clock=lambda: now[0])
    assert now[0] == 1200.0  # 3 attempts: no retry is started past the deadline


def test_retryable_classification() -> None:
    req = httpx2.Request("POST", "https://x")
    assert is_retryable(_timeout())
    assert is_retryable(anthropic.APIConnectionError(request=req))
    assert is_retryable(httpx2.ReadTimeout("x"))
    for status, retry in [(400, False), (401, False), (404, False), (429, True), (529, True)]:
        err = anthropic.APIStatusError(
            "e", response=httpx2.Response(status, request=req), body=None
        )
        assert is_retryable(err) is retry
    assert not is_retryable(ValueError("bad json"))


def test_retries_are_logged_loudly(caplog: pytest.LogCaptureFixture) -> None:
    calls = iter([_timeout(), "ok"])

    def fn() -> str:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    with caplog.at_level("WARNING", logger="concentrate.llm"):
        call_with_retries(fn, what="extract (extract.v2)", policy=FAST, sleep=lambda s: None)
    assert "extract (extract.v2) failed (APITimeoutError); retry 1/3" in caplog.text


def test_async_retries() -> None:
    attempts = []

    async def fn() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise _timeout()
        return "ok"

    async def no_sleep(_: float) -> None:
        return None

    assert asyncio.run(acall_with_retries(fn, what="g", policy=FAST, sleep=no_sleep)) == "ok"
    assert len(attempts) == 3


def test_mem0_client_retries_and_meters_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resilience.time, "sleep", lambda s: None)
    sent: list[dict[str, Any]] = []
    usage = SimpleNamespace(input_tokens=900, output_tokens=300)

    def create(**kw: Any) -> Any:
        sent.append(kw)
        if len(sent) == 1:
            raise _timeout()
        return SimpleNamespace(usage=usage, model=kw["model"], stop_reason="end_turn")

    api = SimpleNamespace(create=create)
    meter, sdk = UsageMeter(), SimpleNamespace(messages=api, beta=SimpleNamespace(messages=api))
    client = MeteredAnthropic(sdk, meter)
    client.messages.create(model="claude-sonnet-5-5", max_tokens=2000, system="s", messages=[])
    assert len(sent) == 2 and len(meter.records) == 1
    assert sent[-1]["timeout"].read == timeout_for(16000).read  # Mem0 raises max_tokens to 16k


# -- watchdog --------------------------------------------------------------------------------


def test_watchdog_asks_first_then_forces_after_the_grace(tmp_path: Path) -> None:
    fired: list[int] = []
    offset = [0.0]
    marker = tmp_path / "FORCED_EXIT"
    watchdog.on_forced_exit("t", lambda: marker.write_text("x"))
    dog = watchdog.Watchdog(
        600,
        what="ingest b5",
        grace_s=300,
        clock=lambda: time.monotonic() + offset[0],
        on_stall=fired.append,
    )
    watchdog.beat()
    offset[0] = 599
    assert not dog.check() and not watchdog.stop_requested()
    offset[0] = 601  # stalled: ask for a clean stop, don't kill yet
    assert not dog.check() and watchdog.stop_requested() and not fired
    with pytest.raises(watchdog.StopRequested, match="stalled"):
        watchdog.raise_if_stopping()
    offset[0] = 899
    assert not dog.check() and not marker.exists()
    offset[0] = 902  # still stuck after the grace: hooks, then the hard exit
    assert dog.check() and fired == [watchdog.STALL_EXIT_CODE]
    assert marker.exists()
    assert not dog.check()  # fires once


def test_watchdog_beats_keep_it_quiet_and_silence_trips_it() -> None:
    tripped = threading.Event()
    dog = watchdog.Watchdog(0.3, grace_s=0.1, check_s=0.02, on_stall=lambda code: tripped.set())
    with dog:
        for _ in range(10):  # 0.5 s of steady progress: no stall
            watchdog.beat()
            time.sleep(0.05)
        assert not tripped.is_set() and not watchdog.stop_requested()
        assert tripped.wait(timeout=3)  # then silence: it asks, waits the grace, then fires
    assert watchdog.stop_requested()


def test_retries_stop_once_a_stop_is_requested() -> None:
    attempts = []

    def fn() -> str:
        attempts.append(1)
        watchdog.request_stop("SIGTERM")  # arrives while the first attempt is in flight
        raise _timeout()

    with pytest.raises(watchdog.StopRequested):
        call_with_retries(fn, what="t", policy=FAST, sleep=lambda s: None)
    assert len(attempts) == 1


def test_first_sigterm_asks_second_interrupts() -> None:
    with watchdog.graceful_signals():
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)  # the handler runs in this (main) thread between bytecodes
        assert watchdog.stop_requested()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGTERM)
            time.sleep(1)
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL  # handler restored


def test_cli_turns_a_clean_stop_into_exit_75() -> None:
    import typer

    from concentrate.cli import _stall_guard

    with pytest.raises(typer.Exit) as exc, _stall_guard(0, "ingest b5"):
        watchdog.request_stop("SIGTERM")
        watchdog.raise_if_stopping()
    assert exc.value.exit_code == watchdog.STALL_EXIT_CODE


def test_llm_calls_and_retries_beat(tmp_path: Path) -> None:
    _, before = watchdog.last_beat()
    client = LLMClient(Flaky(fails=0), model=MODEL, cache_dir=tmp_path)
    client.structured(_request(), Out)
    client.structured(_request(), Out)  # cache hits are progress too
    calls = iter([_timeout(), "ok"])

    def fn() -> str:
        item = next(calls)
        if isinstance(item, Exception):
            raise item
        return item

    call_with_retries(fn, what="t", policy=FAST, sleep=lambda s: None)
    assert watchdog.last_beat()[1] - before == 4  # 2 replies + 1 retry + 1 success


# -- crash-safe progress logs ----------------------------------------------------------------


def test_read_progress_drops_a_torn_tail_and_keeps_appends_clean(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    path.write_text('{"key": "a"}\n{"key": "b"}\n{"key": "c", "cost')  # killed mid-write
    assert [r["key"] for r in read_progress(path)] == ["a", "b"]
    with path.open("a") as fh:
        fh.write(json.dumps({"key": "c"}) + "\n")
    assert [r["key"] for r in read_progress(path)] == ["a", "b", "c"]


def test_read_progress_completes_a_row_missing_only_its_newline(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    path.write_text('{"key": "a"}\n{"key": "b"}')
    assert [r["key"] for r in read_progress(path)] == ["a", "b"]
    assert path.read_text().endswith("}\n")


def test_read_progress_refuses_corruption_in_the_middle(tmp_path: Path) -> None:
    path = tmp_path / "progress.jsonl"
    path.write_text('{"key": "a"}\n{"ke\n{"key": "c"}\n')
    with pytest.raises(ValueError, match="corrupt row 2"):
        read_progress(path)
    assert read_progress(tmp_path / "missing.jsonl") == []


# -- Kuzu: probe in a child process, checkpoint on close ------------------------------------


def _kuzu_with_wal(tmp_path: Path) -> Path:
    """A real Kuzu store whose writer died without checkpointing (a WAL is left behind)."""
    import subprocess
    import sys

    db = tmp_path / "graph.kuzu"
    code = (
        "import os, sys, kuzu\n"
        "db = kuzu.Database(sys.argv[1]); c = kuzu.Connection(db)\n"
        "c.execute('CREATE NODE TABLE Episodic(uuid STRING, PRIMARY KEY(uuid))')\n"
        "c.execute('CHECKPOINT')\n"
        "for i in range(20): c.execute(f\"CREATE (:Episodic {{uuid: 'u{i}'}})\")\n"
        "os._exit(0)\n"
    )
    subprocess.run([sys.executable, "-c", code, str(db)], check=True)
    return db


def test_probe_replays_a_healthy_wal_in_a_child(tmp_path: Path) -> None:
    from concentrate.agents.kuzu_safety import needs_probe, probe_kuzu, wal_path

    db = _kuzu_with_wal(tmp_path)
    assert wal_path(db).stat().st_size > 0 and needs_probe(db)
    assert probe_kuzu(db) is None


def test_probe_reports_a_native_crash_instead_of_dying(tmp_path: Path) -> None:
    from concentrate.agents.kuzu_safety import probe_kuzu

    # Any fatal signal exercises the same path. SIGKILL is used instead of a real SIGBUS
    # because macOS shows a "Python quit unexpectedly" dialog for SIGBUS/SIGSEGV crashes.
    killed = "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n"
    problem = probe_kuzu(tmp_path / "graph.kuzu", code=killed)
    assert problem is not None and "SIGKILL" in problem
    assert "exit 3" in (probe_kuzu(tmp_path, code="raise SystemExit(3)") or "")


def test_close_kuzu_checkpoints_away_the_wal(tmp_path: Path) -> None:
    import kuzu

    from concentrate.agents.kuzu_safety import close_kuzu, needs_probe, wal_path

    db_path = _kuzu_with_wal(tmp_path)
    db = kuzu.Database(str(db_path))
    driver = SimpleNamespace(db=db, client=kuzu.AsyncConnection(db))
    asyncio.run(driver.client.execute("CREATE (:Episodic {uuid: 'late'})"))
    close_kuzu(driver)
    assert not wal_path(db_path).exists() and not needs_probe(db_path)
    conn = kuzu.Connection(kuzu.Database(str(db_path)))
    assert conn.execute("MATCH (n:Episodic) RETURN count(n)").get_next() == [21]
