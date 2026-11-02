# Hardening: timeouts, retries, stall detection, resumable builds, CI

Workstream I, 2026-11-02. Code: `packages/concentrate/llm/resilience.py`,
`packages/concentrate/watchdog.py`, `packages/concentrate/agents/progress.py`,
`.github/workflows/ci.yml`. Tests: `tests/test_hardening.py` plus kill-and-resume tests in
`tests/test_mem0_system.py` and `tests/test_graphiti_system.py`.

## 1. What went wrong

On 2026-11-02 all three long memory builds (`concentrate ingest` for Mem0, Graphiti and
Concentrate) froze at the same moment and stayed frozen for over 30 minutes. Each process sat at
0% CPU, holding a TCP connection to the Anthropic API in `ESTABLISHED` (or half-`CLOSED`) state.
Nothing was printed.

**Diagnosis.** All three froze at once, so the cause was shared, most likely a network blip. Our
code made it worse in two ways:

1. **The timeout was effectively 10 minutes.** We never set one, so the SDK default applied:
   600 s read timeout. A Messages call without streaming sends no bytes until the whole reply is
   ready, so the client simply waits. If the connection dies silently (a router drops it, and no
   FIN or RST packet ever arrives), the socket looks open and the read blocks for the full 600 s.
2. **The retries were silent and multiplied the wait.** We passed `max_retries=4`, so the SDK
   retried a timed-out call up to 4 times: up to 5 × 600 s = **50 minutes** for one call. It logs
   retries only at INFO, and our CLI shows WARNING and above, so nothing appeared on screen.

The builds didn't crash. They resumed fine once restarted by hand. The problem was that nothing
ever *noticed* they had stopped.

What we can't prove: we didn't capture a stack trace at the time, so "blocked in a socket read
inside the SDK" is inferred from the symptoms (0% CPU, an open connection, the SDK defaults). The
watchdog below now dumps every thread's stack on a stall, so next time we'll know for certain.

## 2. The fix, in layers

| Layer | What it does | Where |
|---|---|---|
| **Timeouts** | connect 10 s; read 120–300 s, scaled to `max_tokens` (was 600 s); write 60 s; pool 30 s. Sent with every call. | `resilience.timeout_for` |
| **Retries** | Our own loop, with SDK retries off (`max_retries=0`) so the two layers never multiply. Up to 5 attempts, exponential backoff with full jitter (base 2 s, cap 60 s), honours `Retry-After`, and starts no retry after 20 minutes. **Every retry is logged at WARNING.** | `resilience.call_with_retries` (+ async twin) |
| **Watchdog** | `ingest` and `eval` run under a heartbeat watchdog. Every API reply, retry, cache hit and batch poll is a "beat". After 15 minutes with no beat, it logs CRITICAL, dumps all thread stacks and exits with code **75**. | `watchdog.Watchdog`, `--stall-minutes` |
| **Crash-safe resume** | `progress.jsonl` readers tolerate a torn last line (from a kill mid-write) and trim it, so the next append starts on a clean line. | `agents/progress.read_progress` |
| **CI** | ruff check, ruff format --check, mypy, pytest on every push and PR, with no secrets. | `.github/workflows/ci.yml` |

All of it covers every place we talk to Anthropic:
- `AnthropicBackend` (structured calls) and tool turns (`run_turn`);
- `count_tokens`;
- the Batches backend (create / retrieve / results);
- Mem0's `MeteredAnthropic`;
- Graphiti's `AccountedAnthropicClient` (async).

The Granola sources already had a 30 s timeout and their own retries, so we left them alone.

### Choosing the numbers (measured, not guessed)

We measured from our own LLM cache (461 timed calls, aggregate only):
- latency: p50 3.6 s, p99 12 s, max 32 s;
- largest reply: 2.4k output tokens;
- slowest throughput seen: about 39 tokens/s.

Read timeout = 60 s + `max_tokens`/60 s, clamped to [120, 300]. So a 16k-token call gets 300 s,
enough for about 9k tokens at the slowest rate we've seen: 4× our largest real reply, and 10×
our slowest real call.

The longest silent gap one call can now produce is about 300 s (read) + 10 s (connect) + 60 s
(backoff), roughly 6 minutes. The watchdog's 15 minutes sits well above that, so it only fires
on something genuinely stuck.

### How to run a long build now

```bash
until uv run concentrate ingest --xl --systems b4; do
  code=$?; [ "$code" -eq 75 ] || [ "$code" -eq 1 ] || exit "$code"
  echo "ingest stopped ($code); resuming in 30s"; sleep 30
done
```

Exit 75 means a stall. Exit 1 means an API error that outlived its retries, e.g. the network was
down for 20 minutes. In both cases a rerun resumes from `progress.jsonl`, and every finished call
is a free cache hit. `--stall-minutes 0` turns the watchdog off.

## 3. Guarantees (each pinned by a test)

- **A timed-out call never writes to the cache or the ledger.** Nothing is cached or charged
  until a complete reply arrives (`test_timed_out_call_writes_no_cache_and_no_cost`).
- **A retried call is counted once** (`test_retried_call_through_llmclient_is_counted_once`,
  `test_tool_turns_retry_and_account_once`, `test_mem0_client_retries_and_meters_once`).
- **Bad requests are not retried:** 400, 401, 404 fail at once (`test_backend_does_not_retry_a_bad_request`).
- **The real SDK sees our timeout and our retries.** The tests drive `anthropic.Anthropic`
  through `httpx2.MockTransport`, simulating:
  - read timeouts,
  - connection resets,
  - 429s, 500s and 529s.

  (The SDK 2.x is built on `httpx2`, a fork of httpx. A plain `httpx.Timeout` is a different
  type there, which mypy caught.)
- **A kill mid-build resumes with nothing lost and nothing duplicated,** even if the kill tore
  the last progress line (`test_ingest_resumes_after_a_kill_with_a_torn_progress_line`,
  `test_resume_survives_a_kill_mid_progress_write`).

## 4. What this does *not* fix (honest limits)

- **Abandoned attempts may still be billed.** When we give up on a call after 300 s, the server
  may have finished generating it and charged us. The client never sees that usage, so the
  ledger can't record it. The WARNING lines count such attempts. With these timeouts they should
  be rare: 300 s is 10× our slowest real call.
- **`batches.create` is not idempotent.** If the request reached the server but the reply was
  lost, a retry submits a second batch, and both are billed. We chose a possible duplicate over
  a hang. A fix would list recent batches before retrying.
- **Mem0 and Graphiti have a tiny double-write window.** A kill *after* `add()` succeeds but
  *before* the progress line is flushed re-adds that one chunk on resume. Mem0 deduplicates by
  hash; Graphiti would add one duplicate episode. Concentrate has no such window, because each
  meeting commits in one SQLite transaction.
- **Streaming would be the deeper fix.** With streaming, bytes arrive every few hundred
  milliseconds, so a 30–60 s read timeout catches a dead connection quickly, however long the
  reply. We didn't switch: it changes response handling in five places, and it can't be checked
  against the real API without spending credit. It's the natural next step if stalls recur.
- **CI hasn't run on GitHub yet.** The branch isn't pushed. Every step passes locally:
  - `uv sync --locked`
  - `ruff check`
  - `ruff format --check`
  - mypy
  - 238 tests, with no API keys and `HF_HUB_OFFLINE=1`.

**Formatting markdown.** `ruff format --check` was failing on a Python sketch in
`docs/concentrate/enhance.md`: valid code, but with hand-aligned comments. We now exclude `*.md`
from the formatter (`[tool.ruff.format] exclude`). Docs code blocks are illustrations, not source
files, and rewriting someone's alignment is more surprising than leaving it.

## 5. The CS concepts, in plain language

**Timeout.** How long you're willing to wait before deciding something went wrong. A network
can't tell "slow" apart from "dead": a dropped connection usually looks exactly like a quiet one.
So *you* must pick a point where you stop waiting. With no timeout, a program can wait forever.
There are separate timeouts for:
- connecting (should be fast);
- reading the reply (depends on how much work the server is doing for you).

**Retry.** Trying again after a failure, because many failures are temporary: a blip, an
overloaded server (529), a rate limit (429). Only retry what might succeed next time. A 400 "bad
request" will fail identically forever.

**Idempotency.** An operation is *idempotent* if doing it twice has the same effect as doing it
once. For example, "set the price to $20" is idempotent; "add $20" is not. Retries are only safe
for idempotent operations, or ones you've made safe. Our LLM calls are safe for *our* data,
because we save a result only after receiving it whole. But they're not free: the server may
bill the attempt we abandoned. Submitting a batch is the non-idempotent case: a lost reply plus
a retry makes two batches.

**Exponential backoff with jitter.** After each failure, wait longer before the next try (2 s,
4 s, 8 s ... up to a cap), so a struggling server gets room to recover. *Jitter* picks a random
wait within that window. Without it, every client that failed at the same moment retries at the
same moment, and they knock the server over again (the "thundering herd"). All three of our
builds failing together is exactly that situation in miniature.

**Deadline vs timeout.** A timeout limits one attempt; a deadline limits the whole effort,
retries included. Without a deadline, "5 attempts × 10 minutes" quietly becomes 50 minutes.

**Heartbeat and watchdog.** A heartbeat is a regular "I'm still making progress" signal. A
watchdog is a separate timer that acts when the heartbeats stop. It's a safety net that doesn't
need to know *why* something hung:
- a socket,
- a deadlock,
- a library bug.

It runs on its own thread, because the main thread is the one that's stuck. And it exits hard
(`os._exit`), because a stuck thread never sees a polite `sys.exit`.

**Checkpointing and crash-only design.** Each finished unit of work is appended to
`progress.jsonl`, so a restart skips everything already done. If restarting is cheap and safe,
"crash and restart" becomes the recovery plan for *every* failure. That's simpler and more
reliable than trying to handle each failure in place. The one subtlety: a crash can tear the
last line of the log, so the reader has to treat a half-written final line as "not done".
