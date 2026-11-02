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
