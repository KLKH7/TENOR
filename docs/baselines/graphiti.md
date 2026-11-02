# B5: Graphiti (Zep's open-source temporal knowledge graph)

Code: `packages/concentrate/agents/graphiti_system.py` (`GraphitiSystem`, a `WriteTimeSystem`).
Tests: `tests/test_graphiti_system.py` (offline: fake Anthropic SDK, hashing embedder, real Kuzu).

Graphiti is the competitor closest to Concentrate's thesis: it is a write-time memory whose facts
(graph edges) are bitemporal. Each edge has `valid_at` / `invalid_at` (when the fact held in the
world) and `expired_at` (when the graph learnt it was superseded). When a new episode contradicts
an existing fact, Graphiti's edge-resolution prompt marks the old edge invalid, using the
episode's `reference_time`. If B5 does well on the temporal and latest-state questions, that
counts against "precomputed time-aware memory is new". It does not count against the claim that
it beats query-time retrieval.

## Configuration

| Piece | Choice | Notes |
|---|---|---|
| Library | `graphiti-core==0.31.2` (`[anthropic,kuzu]` extras) | The version `uv add` resolved on 2026-11-02; pinned in `uv.lock`. |
| Graph store | Embedded **Kuzu** (`KuzuDriver`), file `data/cache/graphiti/<hash>/graph.kuzu` | No server. Kuzu 0.11.3 ships full-text search built in, so nothing is downloaded at runtime. |
| Ingest LLM | `CONCENTRATE_INGEST_MODEL`, default `claude-sonnet-5-5`; effort `CONCENTRATE_INGEST_EFFORT`, default `medium`; thinking off (`between_tools`) | Goes through `AccountedAnthropicClient`, a subclass of Graphiti's `AnthropicClient`. |
| Embedder | Local `sentence-transformers/all-MiniLM-L6-v2` (384-d), via `LocalEmbedder` wrapping Concentrate's `Embedder` protocol | No OpenAI. Tests inject `HashEmbedder`. |
| Cross-encoder | `NoRerank` (never called) | Search uses RRF only (below). |
| Episodes | One episode per ~40 transcript utterances of a meeting (a tail under 10 lines is merged into the previous episode), `EpisodeType.message`, `reference_time` = meeting start (`scheduled_start`, else `created_at`) | Added sequentially, oldest meeting first, each awaited (Graphiti requires this for invalidation). |
| Episode text | `Meeting: <title>` / `Date: <start>` / `Attendees: <names>` / `Transcript part i of n:` then speaker-labelled lines (`Them (Marcus Chen): ...`, `Me: ...`) | No line IDs, no AI summary. A meeting with no transcript falls back to its summary as one episode. |
| Search | Two `search_` calls per question: edges (BM25 + cosine, RRF, k=30) and entity nodes (BM25 + cosine, RRF, k=10) | Communities and raw episodes are off (see deviations). |
| Answer | `run_turn` with `answer.v1`, `ANSWER_SCHEMA`, the shared `LLMClient` (`CONCENTRATE_MODEL`, default Opus 5.5), `max_tokens=8000` | Same answer path as B0 to B2. The answer cache, ledger and accounting all go through `_result`. |
| Persistence | `data/cache/graphiti/<hash>/` holding `graph.kuzu`, `progress.jsonl` (one row per finished episode with its usage) and `ingest.json` (written last: stats, node and edge counts, episode→meeting manifest) | `ingest.json` present means reuse (`IngestStats.cached=True`, with the original cost). If `progress.jsonl` exists without `ingest.json`, the run resumes and skips finished episodes. |

The context given to the answer model lists each fact with its validity interval and source:

```
- Marcus Chen announced the decision to price Teams at twenty dollars per seat per month.
  valid: 2026-09-08 -> 2026-09-29; superseded by a later meeting
  source: [not_TifZvvCb7IQQHS] Pricing review: Northwind Teams (2026-09-08)
```

Facts are sorted chronologically by `valid_at`, followed by entity summaries. Citations are
meeting IDs (`not_...`), traced from each edge's `episodes` UUIDs through the episode manifest.
Graphiti has no line-level provenance, so B5 can't cite utterances. This is a structural
disadvantage on line-citation metrics. It is not a bug.

## Deviations from Graphiti defaults, and why

1. **Request shape (required for current models).** Graphiti 0.31's `AnthropicClient` forces a
   tool call (`tool_choice: {"type": "tool"}`) and sends `temperature`. Claude Sonnet 5.5 and
   Opus 5.5 reject forced tool choice with a 400, and Sonnet 5.5 rejects non-default sampling.
   `AccountedAnthropicClient._generate_response` instead:
   - asks for structured output through `output_config.format` (`json_schema`), using the
     response model's schema made strict by the SDK's `transform_schema`. All 16 built-in Graphiti
     response schemas transform cleanly;
   - sends no `temperature` or `top_p`;
   - sends `thinking: {"type": "between_tools"}` (thinking off). Graphiti's prompts were written
     for non-reasoning extraction models (its default is Haiku 4.5). This keeps cost predictable.
     `ingest_thinking="adaptive"` re-enables it. A probe confirmed Sonnet 5.5 accepts both shapes
     with structured output;
   - opts into the server-side refusal fallback (`server-side-fallback-2026-07-01`,
     `fallbacks: "default"`), the same as `concentrate.llm`. Each call is priced by
     `response.model`, so a call served by a fallback model is charged at that model's price.

   Graphiti's own retry loop is kept: two retries on a parse or validation error, which append
   the error to the prompt. It is now rarely needed, because structured output guarantees valid
   JSON.
2. **Cost accounting.** Every Anthropic response's `usage` (input, output, cache read, cache
   write) is recorded in an `IngestLedger` *before* parsing, so failed or retried attempts are
   still counted. Costs come from `concentrate.llm.client.cost_usd`. Graphiti's `token_tracker` is
   ignored. Ingest calls bypass the project's disk cache: the Kuzu graph is the cache.
3. **Kuzu full-text indexes.** `KuzuDriver.build_indices_and_constraints` is a no-op in 0.31, so
   BM25 search fails with "Table RelatesToNode_ doesn't have an index with name
   edge_name_and_fact". `KuzuGraphiti.setup` creates the four `CREATE_FTS_INDEX` indexes once,
   on a fresh database.
4. **No OpenAI anywhere.** Graphiti defaults to an OpenAI LLM, embedder and reranker. We pass our
   own embedder, `NoRerank`, and RRF search configs that never call the cross-encoder.
   `openai` is still installed, because graphiti-core requires it, but it is never imported on
   our path.
5. **Search recipe.** Graphiti's default `search()` is edges-only with RRF, limit 10. We use
   k=30 edges plus k=10 entity summaries. A larger k gives the answer model a fair chance at
   multi-hop and latest-state questions, and a context of about 3 to 6k tokens is still far below
   B0's. We build our own `SearchConfig` objects because `Graphiti.search` mutates a module-level
   recipe's `limit`. Communities are off: building them needs an extra LLM pass, and they are
   rarely useful at this corpus size. Raw episode search is off, since that would make B5 a
   transcript retriever, not a graph memory.
6. **Telemetry off.** `GRAPHITI_TELEMETRY_ENABLED=false` is set (with `setdefault`) when the
   module is imported.
7. **Episode granularity: 40 utterances, not one per message.** Graphiti's per-episode pipeline
   makes a roughly fixed set of calls per episode, plus dedupe calls that scale with facts:
   extract nodes, extract edges, summaries, then node and edge dedupe. In the smoke test that came
   to 3 to 7 calls per episode. Per-utterance episodes would cost about 40x more calls. Graphiti
   also re-sends the last 10 episodes as context in about 3 of those prompts, so the
   previous-context bill is roughly 30x the corpus whatever the granularity. Finer episodes
   therefore mainly add fixed per-call overhead: each prompt has about 4k tokens of
   instructions. The corpus's 15-utterance `chunks()` would roughly triple the episode count and
   about double the bill. A whole meeting per episode (2k to 7k tokens on XL) would risk
   extraction recall and output truncation. 40 lines (~600 tokens) gives 2 episodes per base
   meeting. Change it with `episode_utterances=`.
8. **Cache key.** The directory hash covers the sorted `(note_id, updated_at)` pairs *plus* the
   ingestion config (format version, graphiti version, ingest model, effort, thinking, episode
   size, embedder name). Without the config, a graph built by one model would be silently reused
   for another.
9. **Async.** Graphiti is async. `GraphitiSystem` owns one private event loop for its lifetime
   and runs every coroutine on it. The `AsyncAnthropic` connection pool and the Kuzu async
   connection are therefore always used from the same loop. Repeated `asyncio.run` would break
   the SDK client. Call `close()` when done.

## Smoke test (real API, 2026-11-02)

Scratch script, not committed. It loads the two Teams pricing reviews (`w2-pricing` =
`not_TifZvvCb7IQQHS`, decides $20/seat; `w5-pricing` = `not_i0kpeW6NCQ4Jpe`, decides $25/seat)
from `data/fixtures/northwind` with `load_corpus`, runs `GraphitiSystem(LLMClient()).ingest(...)`,
then asks two questions.

**Ingestion (Sonnet 5.5, effort medium, thinking off):**

| | |
|---|---|
| Episodes | 4 (2 per meeting) |
| Episode text | 2,522 tokens by estimate, **3,849 exact** (`count_tokens`) |
| `full_view` of the two meetings | **7,575 exact tokens** |
| LLM calls | 20 (4 ExtractedEntities, 4 ExtractedEdges, 3 SummarizedEntities, 1 NodeResolutions, 8 EdgeDuplicate) |
| Tokens | 77,138 input (no cache reads or writes), 3,770 output |
| Cost | **$0.192** |
| Wall clock | 54 s (13.6 s per episode, sequential) |
| Graph | 4 episodes, 13 entities, 15 facts; 1 fact invalidated (`invalid_at` set), 1 expired |

Per-episode input grows as the graph fills: 11.1k, 21.7k, 18.9k and 25.4k tokens for the four
episodes. The growth comes from previous-episode context and from more dedupe candidates.

**Temporal invalidation worked.** After ingesting the w5 review, the w2 fact "Marcus Chen
announced the decision to price Teams at twenty dollars per seat per month" had `valid_at`
2026-09-08 and `invalid_at` 2026-09-29, and was expired. The $25 fact is valid from 2026-09-29
to present. Two things fell short. Graphiti phrased the $25 fact as a *proposal* ("proposed ...
raising the price to twenty five dollars"), and it stored that fact twice, once per endpoint
(SSO, audit logs). The Acmeway carve-out ("keeps twenty for the pilot year") was extracted as a
separate fact and was correctly *not* invalidated.

**Questions (Opus 5.5 answer model, `answer.v1`):**

| Question (as of 2026-10-24) | Answer | Correct? | Input / output tokens | Cost | Latency |
|---|---|---|---|---|---|
| What is the current price per seat for Northwind Teams? | $25/seat/month, replacing $20 from 2026-09-08 (notes the record calls it a proposal) | yes | 3,083 / 425 | $0.021 | 4.4 s |
| As of September 15, 2026, what was the price per seat for Northwind Teams? | $20/seat/month, in place until the 2026-09-29 review | yes | 3,092 / 160 | $0.016 | 1.9 s |

Both answers cited `not_i0kpeW6NCQ4Jpe` and `not_TifZvvCb7IQQHS`. The per-question estimate
(`estimate`, with entity lines now sized at 120 tokens) is about 3,475 input tokens, against
about 3,070 measured.

A second run reused the graph from disk (`cached=True`, no ingest calls; full-text search works
on the reopened Kuzu file). It re-asked both questions after one change to the context: the
"superseded" marker no longer shows `expired_at`. The answers were the same: $25 now, $20 on
Sep 15.

Total smoke spend was about **$0.27**: $0.192 ingest, 2 x $0.037 answers, and under $0.001 for
the request-shape probe. The budget was $1.00.

## Cost per corpus token and projections (Sonnet 5.5, $2 / $10 per MTok)

Two measures from the smoke test, per 1k **exact** tokens:

- per 1k tokens of `corpus.full_view` (summary, notes and transcript with line IDs, which is the
  benchmark's corpus unit): **$0.0253**, or 10.2 input and 0.50 output LLM tokens per corpus
  token;
- per 1k tokens of episode text (what Graphiti actually reads): **$0.0499**.

A linear projection of the smoke ratio understates large corpora. The smoke graph never had more
than 3 previous episodes, but at steady state Graphiti re-sends 10. `estimate_ingest` models
this:

    input_i = 28.7 * tokens(episode_i) + 3 * tokens(previous <= 10 episodes)

Both inputs are calibrated on the smoke run. The first coefficient comes from the three episodes
that had a non-empty graph. The output model is 1.5 * tokens. Token counts use `estimate_tokens`
on episode text. On the smoke corpus this model predicts 83.5k input tokens; 77.1k was measured.

| Corpus | Meetings | Episodes | Exact `full_view` tokens | Linear (smoke ratio) | Steady-state model (`estimate_ingest`) | Wall clock (13.6 s per episode, sequential) |
|---|---|---|---|---|---|---|
| Northwind base | 40 | 82 | 127,972 | $3.24 | **$5.64** (2.49M input, 66k output) | ~19 min |
| Northwind-XL | 184 | 860 | 1,351,148 | $34.2 | **$61.7** (27.3M input, 0.70M output) | ~3.3 h, likely more as dedupe grows |

Plan with the steady-state figures. Both models are extrapolated from 4 episodes, and the main
uncertainty is how far EdgeDuplicate and NodeResolutions calls grow on a dense graph. Graphiti
caps their candidates, so growth should be bounded. Prompt caching was not attempted.
The smoke run recorded no cache reads or writes.

## Limitations and open risks

- **Kuzu is deprecated upstream.** graphiti-core warns that the Kuzu backend "will be removed in a
  future release" because Kuzu is unmaintained. We pin `graphiti-core==0.31.2` and `kuzu==0.11.3`
  through `uv.lock`, and suppress the warning. If Kuzu is dropped, the lightest replacement is
  FalkorDB in Docker (`docker run -p 6379:6379 falkordb/falkordb`), via `FalkorDriver`. Only
  `KuzuGraphiti` would change. Kuzu also has fewer search optimisations than Neo4j in Graphiti's
  code: several queries take Kuzu-specific simpler paths.
- **Meeting-level citations only.** Graphiti keeps episode provenance, not line provenance.
- **Sequential ingestion.** `add_episode` must run in order for invalidation to work. That rules
  out the bulk path (`add_episode_bulk` skips invalidation), so XL ingestion takes hours.
- **Extraction quality is model-bound.** In the smoke test, a decision ("go to twenty five
  dollars a seat") became a "proposal", facts were duplicated per endpoint, and filler ("asked Me
  to share their screen") became facts. These are Graphiti behaviours; we did not tune its prompts.
- **Thinking off.** This is our choice, matching Graphiti's design point. With adaptive thinking
  the graph might be better but more expensive. That is untested.
- **The ingestion cost model is extrapolated** (above). The real base and XL bills should be
  recorded in `IngestStats.detail` (per-prompt breakdown in `by_prompt`) and compared.
- **`expired_at` is wall-clock time.** We show "superseded" without its date, so the answer model
  isn't confused by an ingestion date that falls after the question's as-of date.
- **The `as_of` date is not used as a search filter.** We give the answer model dated facts and
  let it reason, as Zep does. A `valid_at <= as_of` filter would help "as of" questions, but it is
  not Graphiti's default behaviour.
