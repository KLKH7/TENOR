# Hardening: timeouts, retries, stall detection, resumable builds, CI

Workstream I, 2026-11-02. Code: `packages/concentrate/llm/resilience.py`,
`packages/concentrate/watchdog.py`, `packages/concentrate/agents/progress.py`,
`packages/concentrate/agents/kuzu_safety.py`,
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
| **Watchdog** | `ingest` and `eval` run under a heartbeat watchdog. Every API reply, retry, cache hit and batch poll is a "beat". After 15 minutes with no beat, it logs CRITICAL and *asks* the run to stop cleanly (§2a). If the run is still stuck 6 minutes later, it dumps all thread stacks, writes a recovery marker and force-exits. Either way the exit code is **75**. | `watchdog.Watchdog`, `--stall-minutes` |
| **Safe stop** | SIGTERM, SIGINT (Ctrl-C) or a stall sets a stop flag. Loops stop between items, retries stop, and the Graphiti store is checkpointed and closed. A second signal interrupts at once, and cleanup still runs. | `watchdog.graceful_signals`, `agents/kuzu_safety.py` |
| **Crash-safe resume** | `progress.jsonl` readers tolerate a torn last line (from a kill mid-write) and trim it, so the next append starts on a clean line. | `agents/progress.read_progress` |
| **CI** | ruff check, ruff format --check, mypy, pytest on every push and PR, with no secrets. | `.github/workflows/ci.yml` |

All of it covers every place we talk to Anthropic:
- `AnthropicBackend` (structured calls) and tool turns (`run_turn`);
- `count_tokens`;
- the Batches backend (create / retrieve / results);
- Mem0's `MeteredAnthropic`;
- Graphiti's `AccountedAnthropicClient` (async).

The Granola sources already had a 30 s timeout and their own retries, so we left them alone.

### 2a. Stopping without corrupting the graph store (follow-up, 2026-11-02)

**What happened next.** The hung Graphiti build was stopped with SIGTERM while it was writing.
Python's default SIGTERM action kills the process on the spot. That left Kuzu's write-ahead log
(`graph.kuzu.wal`) half-written, and every later start died with
`Fatal Python error: Bus error` while replaying it. That's a native crash: no `try` can catch it.
Recovery was manual: set the WAL aside, then trim `progress.jsonl` to the 408 episodes the main
file still held. About $10.66 of work was lost.

**How Kuzu stores writes.** Kuzu appends every change to a write-ahead log (WAL) first. At a
*checkpoint* it folds the log into the main file and deletes the log. A clean close checkpoints.
A kill doesn't, so the next open must *replay* the log. If the log was torn mid-write, replay
can crash.

**Now, in order of preference:**

1. **Stop between items.** The first SIGTERM/SIGINT, or a detected stall, only sets a flag
   (`watchdog.request_stop`). Every ingest loop (Mem0, Graphiti, Concentrate) checks it before
   starting the next item, and the retry layer starts no new attempt. The item in flight
   finishes, `StopRequested` unwinds the stack, and Graphiti's `finally` runs `close_kuzu`. That
   waits for queries already running, runs `CHECKPOINT` and closes, so no WAL is left behind.
   The CLI prints "stopped cleanly" and exits 75.

   A crash, or a second Ctrl-C, takes the same cleanup path. Python delivers signals between
   bytecodes, never inside a Kuzu call, so the interruption always lands between two statements.
2. **Force, but leave a note.** If the main thread is stuck in native code and never reaches
   the next item, the watchdog waits `GRACE_S` = 6 minutes. That's the 300 s maximum read
   timeout plus 60 s, so a thread stuck in an API read gets to come back. Then it dumps stacks,
   writes `FORCED_EXIT` next to the open graph ("its WAL may need recovery") and calls
   `os._exit(75)`. It never closes the database from the watchdog thread, because the stuck
   thread might be inside it.
3. **Open defensively.** Before opening an existing graph, we check whether it was closed
   cleanly: a non-empty WAL or a `FORCED_EXIT` marker means it wasn't. In that case we first
   open it (replaying the WAL) in a **child process**. If the child crashes (e.g. SIGBUS) or
   fails, the build stops with `GraphStoreError` naming the recovery steps below; it doesn't
   bus-error on every start. A Python exception from the in-process open is wrapped the same way.
   A successful test-open removes the marker. A finished build also checkpoints, closes and
   reopens its graph, so a later unclean exit, e.g. during an eval, has nothing to replay.
4. **Reconcile on resume.** On every resume we read the Episodic uuids actually stored in Kuzu.
   Any `progress.jsonl` row whose episode isn't there is moved to `progress.orphaned.jsonl` and
   rebuilt. The progress file is rewritten atomically (temp file + `os.replace`). That spend was
   real, so it's reported separately as `orphaned_cost_usd` and kept out of the system's ingest
   cost. Episodes stored in the graph without a progress row are counted and logged.

### Recovering a Graphiti store

Follow these steps if a build fails with `GraphStoreError ... cannot be opened`:

1. Make sure no build is running on that store (`pgrep -fl "concentrate ingest"`).
2. Set the log aside; don't delete it: `mv graph.kuzu.wal graph.kuzu.wal.broken`.
3. Rerun the same command. It test-opens the main file, reconciles `progress.jsonl` against the
   episodes it holds (step 4 above, which replaces the manual trim we did), and rebuilds only
   what was lost. The lost spend appears as `orphaned_rows` / `orphaned_cost_usd` in the stats.

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

Exit 75 means the run was stopped: a stall, or a SIGTERM/Ctrl-C. It normally stopped cleanly;
if it says "forcing exit", the next open runs the test-open first. Exit 1 means an API error that outlived its retries, e.g. the network was
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

- **Stops are clean, and forced exits are marked.** A requested stop finishes the current
  episode, closes the store and exits 75 (`test_a_requested_stop_finishes_the_episode_and_closes_the_store`,
  `test_cli_turns_a_clean_stop_into_exit_75`, `test_first_sigterm_asks_second_interrupts`).
  The watchdog asks before it forces (`test_watchdog_asks_first_then_forces_after_the_grace`),
  and a forced exit leaves the marker (`test_a_forced_exit_mid_build_leaves_a_recovery_marker`).
- **A broken store fails clearly, and the lost work is redone.** A crashing replay raises
  `GraphStoreError` with the recovery steps, without opening in-process
  (`test_an_unopenable_store_fails_clearly_with_recovery_steps`). A real Kuzu WAL replays in the
  child (`test_probe_replays_a_healthy_wal_in_a_child`), and a child SIGBUS is reported, not
  fatal (`test_probe_reports_a_native_crash_instead_of_dying`). `CHECKPOINT` on close removes the
  WAL (`test_close_kuzu_checkpoints_away_the_wal`). Progress rows for lost episodes are dropped
  and rebuilt (`test_resume_drops_progress_rows_whose_episode_was_lost`).

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
- **A forced exit can still tear the WAL.** We can't make `os._exit` during a native write
  safe; we can only mark it, test-open it in a child and explain the recovery. Kuzu 0.11.3
  replays a simply truncated WAL fine in our tests, and we couldn't reproduce the bus error on
  demand. So the "crash" path is tested with a child that kills itself with SIGBUS, not with
  the real corrupt file.
- **Stopping mid-episode can leave a partial episode.** If a stop arrives while an episode's
  API call is failing, the retry layer stops and the episode is abandoned halfway. Graphiti
  writes an episode in several statements, so some of its nodes may stay in the graph. That
  episode has no progress row and will be added again. Clean stops between episodes don't
  have this problem.
- **Mem0's local Qdrant store isn't explicitly closed.** Mem0 now stops between chunks too, but
  we don't flush Qdrant ourselves. It writes through on each `add`, and no corruption has been
  seen.
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

**Write-ahead log and checkpoint.** A database first appends each change to a log (fast,
sequential), and only later folds the log into its main file at a *checkpoint*. After a crash,
replaying the log restores the committed changes. That only works if the log itself is intact,
so the safest shutdown is a checkpoint, which leaves no log at all.

**Graceful vs forced shutdown.** Ask first ("finish what you're doing, then stop"); force only
when asking fails. Forcing is sometimes unavoidable, so it should leave evidence (a marker, a
stack dump) and the next start should check before trusting the data. We do that check in a
throwaway child process, so if it crashes, only the child dies.

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
