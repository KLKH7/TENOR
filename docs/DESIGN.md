# Design notes

A running log of decisions and trade-offs, newest phase last.

## Phase 1: models, source layer, sync

### Package layout
The brief asked for `packages/core`, `packages/sources`, and so on. Hatchling can't do an editable install that maps `packages/` → `concentrate/` (see pfmoore/editables#20). Generic top-level names like `core` would also collide with other installed packages. So modules live at `packages/concentrate/<module>` and are imported as `concentrate.<module>`. Each module still has its own directory, as the brief laid out.

### Two model layers
- `concentrate.core.granola` holds **wire models** that mirror the OpenAPI schemas field for field, including camelCase `hasMore` and British `organiser`. They use `extra="ignore"` so additive API changes (six minor versions in 2026) can't break sync.
- `concentrate.core.models` holds **normalized models** (`MeetingNote`, `Utterance`, `Channel`). Everything downstream uses only these, so it has no idea whether data came from the API or from fixtures.
- The store keeps the **raw wire JSON** of every note. Normalization can change without a re-sync, and nothing Granola gave us is lost.

### Utterance identity
The API has no utterance IDs, but citations and gold evidence need stable ones. We use `{note_id}:{index:04d}` in API order. This is only stable if Granola returns transcript items in the same order every time (ASSUMED, see SCHEMA_NOTES). If a transcript were ever re-processed server-side, IDs could shift. A content-hash ID would survive reordering but not text edits. Index is simpler and readable, and the trade-off is recorded here.

### Channel attribution
`attribution` (`me`/`them`) wins when present; otherwise `source` decides: `microphone` → Me, `speaker` → Them. We don't infer speaker names. That is the job of the enhancement layer (Phase 4) and the whole point of the "which Sam?" problem. In-person meetings put everyone on the mic, so they come out as all "Me". The sample fixture includes one of these on purpose.

### Rate limiting
Granola documents "burst 25 / 5 s window" and "sustained 5 req/s". A token bucket alone (capacity 25, 5/s) allows ~50 requests in the first 5 s, which a strict window would reject. We enforce **both** a token bucket and a 25-per-5-s sliding-window log. The window is the binding constraint, and it already implies 5 req/s long-run. Time is injected (`Clock`), and the tests run on a `FakeClock` against a fake server that enforces its own limit. The test asserts **zero** 429s across 62 requests.

On 429 we honour `Retry-After` if sent (undocumented), else use full-jitter exponential backoff. We also drain the local bucket so the next requests don't stampede.

Bug found and fixed while testing: a residual wait smaller than the clock's float resolution made `acquire` spin forever. Time comparisons now use an epsilon.

### Transcript fetching
We try `GET /v1/notes/{id}?include=transcript` first: one request per note, which matters at 5 req/s. On `413 TRANSCRIPT_TOO_LARGE` we refetch the note without the transcript and page `GET /v1/notes/{id}/transcript` at 100 per page. Always using the paginated endpoint would cost at least 2 requests per note.

### Sync
- **Watermark = max server `updated_at` observed**, never our wall clock, so clock skew can't skip notes.
- **10-minute overlap re-scan.** This absorbs same-timestamp updates and the undocumented inclusive/exclusive semantics of `updated_after`. Notes whose `updated_at` matches what we store are skipped without a Get Note call.
- **Pending queue.** A note that 404s after retries is recorded in `pending_notes` and retried on every later sync, independent of the watermark. That covers the List→Get race window.
- **Soft deletes.** If `deleted_at` is set we keep the row (for audit) and drop the transcript. That fits Granola's direction of not hoarding verbatim content.
- **Open risk:** if `updated_at` doesn't move when a note finishes processing, a note whose processing outlasts the overlap window could be missed. `sync --full` is the backstop.

### Why we still poll even though webhooks exist
The brief said there are no webhooks, but the docs show they exist (CORRECTED in SCHEMA_NOTES). We still poll in Phase 1. Webhooks need a public HTTPS endpoint and a Business/Enterprise workspace, and their payloads carry no content (`{event_id, event_type, note_id, occurred_at}`), so a webhook would only trigger the same Get Note call. A `webhooks` receiver that verifies Standard Webhooks signatures and enqueues `note_id`s into the same `fetch()` path is a small, well-defined follow-up. The poller stays as the reconciliation loop either way.

### Secrets
The key is read from `GRANOLA_API_KEY`, placed only in the httpx client's default headers, and never stored on any attribute that `repr` shows. Errors carry status and path only. httpx's own INFO logging is silenced by the CLI. A test captures DEBUG logs during 429/retry flows and asserts the key never appears.

### Testing approach
There is no network in tests. `tests/fake_granola.py` is a small in-memory Granola that covers List/Get/Transcript/Folders, cursor pagination, page-size validation, auth, server-side rate limiting and injectable failures. The strongest test is `test_api_and_fixture_sources_produce_identical_stores`: syncing the same data through `GranolaAPISource` (over the fake HTTP server) and through `FixtureSource` produces byte-identical normalized notes.

## Phase 2: synthetic company and benchmark questions

### Ground truth first, text second
`synth.scenario.build_world(seed)` builds the world *before* any text exists. A meeting is a list of **beats** (one clean speaker turn each). Every fact and commitment is registered against the exact beats that state it, so gold evidence comes from construction rather than from string-searching the output afterwards. Each beat's `protect` spans hold the words that carry a fact (e.g. "twenty five dollars"). The builder refuses to register a fact unless its spans are protected in the beats it cites.

### What is authored and what is seeded
The **plot** is hand-authored, so every difficulty is deliberate:
- the price changes from $20 to $25, with a reason;
- the launch slips twice (Oct 14 → Oct 28 → Nov 11), each time with a reason;
- Falcon is handed from Sam Patel to Tomás;
- both Sams appear in one meeting;
- the internal project Beacon sits alongside the customer Beacon Freight;
- some customer requests are resolved, some are open, and one is never answered;
- private health, family and compensation content appears in 1:1s and one customer call.

The **seed** controls everything else: which engineers commit to which tasks in standups, what happens to each commitment (kept 45%, slipped 20%, dropped 20%, reassigned 15%, resolved in later standups), small talk, filler placement, start-time jitter, noise and the dev/test split. A fully procedural plot was rejected. It would make the planted traps accidental and much harder to explain to a reader.

### Padding
Without padding, scripted beats average about 7 turns per meeting, which is unrealistically clean. `synth.pad` inserts fact-free filler to reach about 40–70 turns per meeting depending on the meeting kind. Filler is never inserted inside an evidence group, so a question like "Megan, what's on your plate?" stays directly before its answer, and all evidence indices are remapped afterwards.

**Honest limitation:** the template-only corpus is about 20k words over 40 meetings (~500 words each). A real 30-minute meeting is closer to 4–5k words. A smaller haystack makes retrieval *easier* for the baselines, so any Concentrate advantage measured here is conservative. `TARGET_TURNS` is the knob for a larger run.

### Hybrid rendering (LLM polish)
There is one LLM call per meeting (prompt `prompts/synth_polish.v1.md`, effort `low`). It rewrites every turn, and `polish.check_turn` accepts or rejects each rewritten turn independently:
- **Fact turns:** every protected span must survive verbatim. No new numbers (compared by value via ITN), names or dates.
- **Filler turns:** no numbers, dates, person names or commitment/decision language, and no entities other than those the meeting's fact turns mention.
- A rejected turn falls back to its template text.
- Acceptance and rejection counts by reason are written to `manifest.json`, so the dataset documents how much of it is LLM-written.

The test suite exercises this end to end with a fake LLM that deliberately breaks turns. Bad rewrites are rejected, validation still passes, and a second run served from cache reproduces the files byte for byte.

### Granola realism
- **Channels:** the owner's turns are `microphone`, everyone else on a call is `speaker`, and everyone in an in-person meeting is `microphone`. The in-person 1:1s are therefore all "Me", which is deliberately hard.
- **Diarization and names:** one in-person 1:1 is diarized (iOS). Investor meetings carry identified speaker names (ASSUMED, see SCHEMA_NOTES).
- **Crosstalk:** backchannels from the other channel overlap long turns.
- **Noise on unprotected text:** fillers, repetitions, false starts, name mishearing (Megan → Meghan), homophones and dropped short words. Rates are recorded in the manifest.
- **Summaries and typed notes:** `summary_markdown` is a lossy AI summary with 20% of points dropped, so the summaries-only baseline is honestly handicapped. Typed notes are sparse and terse.
- **Calendar:** one ad-hoc in-person meeting has no calendar event and no attendees.

### Questions (230 for seed 42)
All gold answers are computed from the world. The validator (`synth.questions.validate`) is mechanical: each answerable question's `evidence_keys` must occur in the text of its `gold_utterance_ids`, unanswerable questions must have no evidence, and each unanswerable question is guarded against the world so that a later plot change can't silently make it answerable.

This proves the answer is *stated* in the evidence, but not that a reader could *infer* it. For example, "who is 'Sam' here" still requires using the context cues. That inference gap is exactly what the benchmark measures. An LLM-judged entailment pass could be added; it is not used today, to keep validation free and deterministic.

Distribution (seed 42): single-hop 63, temporal 46, entity disambiguation 25, unanswerable 25 (11%), commitment status 22, latest state 17, multi-hop 12, decision change 10, aggregation 10. Private facts are excluded and reserved for the Phase 5 redaction eval. Split: 71 dev / 159 test, stratified by category. Only dev may be used for tuning.

### Determinism
The same seed gives byte-identical files (tested). A test also asserts that the committed `data/fixtures/northwind` equals a fresh `concentrate synth --seed 42`, so fixtures can't drift from the generator. With `--polish`, the LLM cache is the source of truth, and `--offline` re-renders from cache with zero API calls.

### LLM client (`concentrate.llm`)
- **Calls:** structured output via `output_config.format`, because the default model (`claude-opus-5-5`) rejects forced tool use.
- **Refusals:** server-side refusal fallback (`fallbacks: "default"`), with typed errors for refusal and truncation. Failed calls are never cached.
- **Cache:** a disk cache keyed by (model, prompt id, system, user, schema, max_tokens, effort).
- **Accounting:** a per-call ledger with per-stage totals. Unknown model pricing raises instead of silently reporting $0.
- **Cost estimate:** the pre-run estimate adds a 30% thinking-token allowance, because current models think adaptively and bill thinking as output. The real ratio will be measured on the first paid run.

### Fix found while building the UI prototype (2026-10-29)
The brief requires ASR name misspellings (Megan → Meghan), but the first fixtures contained **none**. Names almost only appeared in protected cue lines ("Megan, what's on your plate?"), and protected text is exempt from noise. Those cue names were never evidence keys (commitment evidence is the task phrase), so they are now unprotected. Fixtures were regenerated and validation still passes. The transcripts now contain realistic mishearings such as "Inez", "Thomas", "Lina" and "Coffee" (Kofi). The last one is outside edit distance 1, so the prototype linker misses it. That's a concrete case for Phase 4's phonetic matching.

## Note-view prototype (built ahead of Phase 6)
`web/` is Next.js 15 + TypeScript + Tailwind v4. It reads only Granola notes plus `directory.json`, never `world.json` (ground truth). Entity linking in the UI uses `web/lib/linker.ts`, a deliberately simple stand-in with these rules:
- full names resolve directly;
- a first name resolves when exactly one attendee has it, falling back to a unique directory match;
- edit-distance-1 matching catches ASR misspellings;
- glossary aliases and spoken ticket codes ("PAY two twelve" → PAY-212) expand to the project;
- the longest match wins, so "Beacon Freight" (customer) beats "Beacon" (project).

It uses no context, so speaker attribution ("which Them said this?") is untouched. Phase 4 replaces it with the Python enhancer, and the UI will render that output instead.

## Real data via Granola's MCP (2026-11-02)
- **Why:** the public REST API requires Business/Enterprise; the author's account is on Plus. The MCP works on every plan, so `GranolaMCPSource` is the realistic route to running Concentrate on real notes. The live probe (`docs/PROBE_2026-11-02.md`) informed every mapping.
- **Same protocol, different wire variant.** The MCP returns UUIDs, not `not_` IDs. `McpNote`/`McpNoteSummary`/`McpFolder` subclass the API models and override only ID constraints, so normalization and the store are shared. The store records `raw_format` (`api`|`mcp`) so raw JSON reloads with the right model. Existing local `.db` files predate the column; delete them and re-sync.
- **Parsing agent-oriented text.** `list_meetings` and `get_meetings` return XML-like text meant for LLMs, not strict XML. The parser uses lenient regexes plus HTML unescaping, and is tested against fixtures copied from the live format with invented content.
- **Transcripts by chunk, not by character page.** Chunk reads return structured passages (speaker label, start time). Full reads return paged text. We read 10 chunks per call, pin the `transcript_version`, and derive `end_time` from the next passage's start.
- **Auth.** OAuth 2.0 with dynamic client registration via the official `mcp` SDK (v2.3): a browser sign-in once, a local callback on 127.0.0.1, and tokens in `~/.config/concentrate/granola-mcp.json` (mode 600, never logged). The live transport is a sync wrapper (anyio blocking portal) around one async session.
- **Read-only by construction.** The source calls only `get_account_info`, `list_meetings`, `get_meetings`, `get_meeting_transcript` and `list_meeting_folders`, even though the MCP now has write tools.

## Synthetic transcripts updated to the live format (2026-11-02)
The probe showed three differences between real transcripts and our fixtures, and the generator now models them:
1. **Partial speaker naming:** most remote meetings name some "Them" speakers, and named speakers are still unlabeled on some passages.
2. **Short, fragmentary passages:** median 6 words, with cuts never inside a fact phrase.
3. **Attendees with no display name.**

All three make attribution *easier* than our earlier Me/Them-only fixtures in some meetings, and harder in others (nameless attendees break name→email links, which is exactly the cross-meeting miss we saw live). Fixtures were regenerated, and all 230 gold answers still validate against the new utterance IDs.

### Recalibration on real data (2026-11-02)
Measured on the author's 4 MCP-synced meetings (aggregates only, recorded in `PROBE_2026-11-02.md` §6):
- naming raised to match the real 0.82 named share;
- remote turns are now often kept whole, with one-word lead-ins;
- in-person meetings merge into long single-microphone blocks.

The remote 38+ word monologue tail is a content limitation of scripted turns and is documented rather than faked.

## Phase 3: baselines and eval harness (2026-11-02)
- **Four baselines, all with the same structured answer** (`answer`, `abstain`, `citations`):
  - **B0 top-k:** the 20 most relevant meetings, with full notes and transcripts in context. Retrieval is hybrid over transcript chunks and summaries: BM25 plus `all-MiniLM-L6-v2` embeddings, fused with RRF and ranked at meeting level.
  - **B1 summaries-only:** the same retrieval, but AI summaries and typed notes only.
  - **B2 full-context:** every meeting in a **cached system prompt**. That's more context but the cheapest per question after the first call, so it's an honest ceiling.
  - **B3 agentic:** a tool-using agent whose four tools mirror the live MCP (`search_meetings` over titles, summaries and typed notes only; `get_meetings`; `search_transcripts` returning 240-character passages *without speakers*; `get_meeting_transcript` returning chunks with speakers and line IDs). It has a 12-turn budget, and exhausting it counts as a failed answer. This is the baseline that matters after the probe.
- **Deviation from the real MCP:** transcript chunks carry our line IDs so every system can cite at line level, which makes citation metrics comparable. Granola's MCP passages have timestamps instead.
- **Scoring:**
  - The primary signal is an LLM judge (`judge.v1`), counting "correct" only.
  - Shown alongside so judge bias is visible: exact match of gold normalized values (date, number and person aware), and citation precision/recall against gold line IDs.
  - Abstention precision/recall, tokens (cache reads included), cost, latency p50/p95, and tool calls.
  - Accuracy has 95% bootstrap CIs (seeded).
- **Reproducibility and cost:**
  - Every turn is disk-cached by its full request, and reruns report the *original* latency, not the cached ~0 s.
  - Reports (JSON, Markdown and per-question JSONL) record the git SHA and dirty flag, model, prompt versions, seed, split and spend.
  - `CONCENTRATE_BUDGET_USD` is a hard per-run cap, checked before each question, and partial reports say so.
  - Estimates are offline (heuristic tokens; B3 assumed), so a small pilot run should calibrate them before a full run.

### Batch mode (2026-11-02)
- `concentrate eval --batch` (default) sends the single-turn systems (B0, B1, B2, B2o) and the judge through the Message Batches API at 50% of the price. B3 is multi-turn and stays interactive.
- **No second code path.** Each batch result is written to the exact cache file the interactive call would write (the custom_id is the 64-char cache key), then the normal code replays it. Single-turn systems expose `turn_request(q)`, and a test pins the pre-batch cache keys so old caches still hit.
- **Honest accounting.** Batch completions are flagged `batch`: the cost is recorded once at the batch rate, and replays are cache hits. Refused or truncated items are billed but not cached. Errored, expired, refused and truncated items fall back to the interactive call during the run, and the report counts them.
- **Honest latency.** The first `--latency-sample` questions (default 10) run fully interactively at full price, and they are the only latency source for batched systems. The report says how many rows each p50/p95 comes from.
- **Budget.** Each batch is estimated before it is submitted and refused if it would exceed the remaining cap. The estimate uses exact token counts when an API key is set, otherwise the heuristic ×1.93.
- **Differences from interactive.** The Batches API rejects the server-side `fallbacks` parameter, so a refusal in a batch is retried interactively, with the fallback. Prompt caching inside a batch is best-effort, so B2's cache-read assumption is optimistic in batch mode.

## Framing: benchmark first (2026-11-02)

**Decision (with the author):** the headline deliverable is the open meeting-memory benchmark and the head-to-head comparison. Concentrate is one entry in it, not the pitch.

**Why:** Granola has publicly described prototyping agent-oriented "enhanced transcripts" (resolving which Sam, expanding project names), so Concentrate's techniques are unlikely to be new to them. What an outsider can add:
- an independent, reproducible measurement;
- a public benchmark on meetings (we found none; existing ones are chat logs);
- concrete live-probe findings;
- numbers on the transcript-retirement question they've said they're weighing.

**Trade-off:** the work hardly changes, but polish moves to the benchmark: a card ([`BENCHMARK.md`](BENCHMARK.md)), one command per system, and a documented way to add a system. Its biggest weakness, being synthetic, is stated up front.

## Hardening: timeouts, retries, stall watchdog, CI (2026-11-02)

**Trigger:** three ingest builds froze for 30+ minutes at 0% CPU. With no timeout set, the SDK
used its defaults (600 s read timeout), and our `max_retries=4` retried silently, so one dead
connection could block for up to 50 minutes. Full write-up: [`HARDENING.md`](HARDENING.md).

**Decisions:**
- **Own the retry loop** (`concentrate.llm.resilience`) and turn SDK retries off.
  - Why: one policy for sync and async, every SDK entry point, Mem0 and Graphiti; logged at
    WARNING; testable with fake sleeps and clocks; and SDK and outer retries can never multiply.
  - Trade-off: we re-implement `Retry-After` handling, which the SDK already had.
- **Read timeout scales with `max_tokens`** (120–300 s), calibrated on measured latencies
  (p99 12 s, max 32 s). Trade-off: a legitimately very long non-streaming reply (more than
  ~9k tokens at the slowest rate seen) would time out. Streaming is the follow-up if that matters.
- **Stall watchdog, not just timeouts.** `ingest` and `eval` exit 75 after 15 minutes without a
  heartbeat and dump thread stacks; restarting is the recovery, because builds resume from
  `progress.jsonl`. This catches hangs that timeouts can't (deadlocks, library bugs).
- **Torn progress lines are repaired**, not fatal. Corruption in the middle of the file still
  raises.
- **CI** runs ruff, format, mypy and pytest with empty API keys. `*.md` is excluded from
  `ruff format`, because docs code blocks are hand-aligned sketches.

**Not fixed:**
- abandoned attempts may still be billed, and the ledger can't see it;
- `batches.create` can duplicate a batch on a lost reply;
- Mem0 and Graphiti have a one-chunk double-write window on kill.

## Report v2: paired significance, power, Pareto, failure taxonomy (2026-11-02)

`concentrate analyze <report>` works post hoc on any saved report and costs nothing unless `--llm` is passed. Methods are in [`ANALYSIS.md`](ANALYSIS.md).
- **Paired, not overlapping CIs.** Every system answers the same questions, so pairs are compared with McNemar's exact test and a paired bootstrap CI of the gap. Holm–Bonferroni corrects across all pairs; each category is its own family.
- **Power from observed discordance.** Sample sizes use Connor's approximation with the observed share of questions on which two systems disagree. A Monte Carlo test confirms about 80% power. Trade-off: discordance from a small pilot is noisy, so the numbers are re-estimated on each report rather than fixed.
- **Ingest amortised at a stated volume.** The frontier is shown at several volumes, along with break-even volumes, because write-time vs query-time is a volume question.
- **Deterministic failure types first.** They come from ground truth: fact histories, the directory and gold lines. An optional Haiku classifier only refines the generic buckets, including a `judge_error` label. Trade-off: string matching misses paraphrases, so `stale_value` and `wrong_entity` are lower bounds.
- **Pilot finding (10 dev questions).** No pairwise gap is reliable after correction, and a typical pair would need about a 62-point gap at n = 10. At the pilot's discordance, 120 test questions detect gaps of about 11 points and 206 detect about 9 (re-check on the XL report).

## Concentrate as an MCP server (workstream D, 2026-11-02)

`concentrate mcp-serve [--xl]` serves a built memory over stdio with six read-only tools (`ask_memory`, `get_facts`, `get_commitments`, `resolve_entity`, `get_meeting`, `get_transcript_lines`) and one resource (`concentrate://memory`). Details, client config and a demo script are in [`MCP_SERVER.md`](MCP_SERVER.md).

- **Logic split from protocol.** `mcp_server/service.py` is plain Python over `MemoryStore`, `EntityDirectory` and `build_context`. `server.py` only registers tools (mcp 2.x `MCPServer`; FastMCP was renamed in v2). Tests drive the real protocol through the SDK's in-process client.
- **Read-only twice over.** The tools carry `readOnlyHint` annotations, and `MemoryStore(read_only=True)` opens SQLite with `mode=ro`, so a write fails at the database. The read-only connection is shared across the SDK's worker threads (`check_same_thread=False`), which is safe because the sqlite3 module serializes access.
- **Deterministic "now".** `as_of` defaults to the last meeting day in the memory, not the clock.
- **Ambiguity is an error, not a guess.** A name that matches several entities ("Sam") returns the candidates and asks the agent to choose. Unresolved commitment owners stay unresolved.
- **The agent answers by default.** `ask_memory` returns the context; Concentrate's own answer (`answer.v1`, the eval's answer model) costs one call and is opt-in via `answer: true`.
- **Build selection.** The server picks the newest *complete* build for the corpus hash; partial builds need `--allow-partial`.
- **Caveat:** Granola's MCP reads the user's real meetings, while this server serves synthetic Northwind, so "ask both" compares behaviour on a kind of question, not answers to the same fact. Serving a memory built from real notes is the open next step.

## Results explorer (workstream E, 2026-11-02)

`/results` in the web app reads eval reports straight from `evals/reports/` (no DB) and makes every number clickable down to the transcript line a system cited. Details: [`RESULTS_EXPLORER.md`](RESULTS_EXPLORER.md).

**Decision:** before colouring citations, the explorer recomputes each answer's citation precision/recall from the gold lines and only trusts a fixture version that reproduces the stored values. It reads fixtures at the report's git SHA when they're committed. **Why:** utterance ids are positional, and the pilot report shows the risk: against today's regenerated fixtures 18/36 answers don't reproduce; at the run's SHA all 36 do.

**Trade-off:** a `git show` per opened note (cheap, local), and gitignored fixtures (XL) can only be checked for drift, not pinned. **Found:** the runner's jsonl drops the judge's reason (the row's `verdict` string overwrites the judge's object); the explorer shows it once the runner keeps it.
