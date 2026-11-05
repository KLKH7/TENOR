# B4: Mem0 baseline

`concentrate.agents.mem0_system.Mem0System` implements `WriteTimeSystem` using the open-source
`mem0ai` package (v2.2.1, pinned in `uv.lock`). It builds a memory once, then answers each
question from the top-k memories through the same answer path as B0-B2.

## Construction

```python
from concentrate.agents.mem0_system import Mem0System

system = Mem0System(
    client,  # shared LLMClient (answer model = CONCENTRATE_MODEL)
    backend=None,  # optional ToolBackend for the answer call (tests)
    memory_factory=None,  # default: real mem0.Memory (tests inject a fake)
    ingest_model=None,  # default: $CONCENTRATE_INGEST_MODEL or claude-sonnet-5-5
    k=25,  # memories retrieved per question
    chunk_utterances=40,
    effort="medium",  # output_config.effort for Mem0's extraction calls
    cache_system=True,  # prompt-cache Mem0's static system prompt
    cache_root=Path("data/cache/mem0"),
)
stats = system.ingest(notes)  # IngestStats; cached=True and $0 if the store exists
result = system.run(question)  # SystemResult; call ingest first (it opens the store)
system.estimate_ingest(notes)  # (input, output) tokens, offline
system.estimate_ingest_usd(notes)  # $ with the cached system prefix priced correctly
```

## What happens

**Ingestion.** Meetings are processed in chronological order. Each meeting becomes:

1. a *summary chunk*: title, `[note_id]`, date/time, attendees, Granola's AI summary and the typed
   notes;
2. *transcript chunks* of 40 utterances, each prefixed with the same header and with speaker
   labels (`Me (name)` / `Them (name)`, from `corpus.speaker_label`). Utterance IDs are not
   included: Mem0 rewrites everything into free-text memories and would drop them anyway.

Each chunk is one `Memory.add([{"role": "user", "content": ...}], user_id="concentrate-corpus",
metadata={note_id, chunk_id, meeting_date, title}, prompt=...)` call. In mem0 2.x that is Mem0's
"additive" pipeline: one LLM call (its ~12.5k-token `ADDITIVE_EXTRACTION_PROMPT` system prompt +
the last ≤10 messages truncated to 300 chars + ≤10 similar existing memories + the new chunk),
then local embedding, hash dedup, spaCy entity linking and fastembed BM25 indexing. Memories
inherit the chunk's metadata, so every retrieved memory traces back to its meeting.

**Storage.** Local, on disk: Qdrant (embedded mode) + Mem0's SQLite history DB under
`data/cache/mem0/<corpus_hash>/<config_hash>/`. `corpus_hash` = sha256 of the sorted
`(note_id, updated_at)` pairs; `config_hash` covers the mem0 version, ingest model, effort, chunk
size, embedder and user ID, so changing any of them builds a fresh store instead of silently
reusing one built differently. A `complete.json` marker means the store is complete (reused,
`IngestStats.cached=True`, zero cost; the original build's stats are in `detail["built"]`).
`progress.jsonl` logs every finished chunk with its usage, so an interrupted build resumes where
it stopped and the final stats still cover the whole build.

**Answering.** `Memory.search(question, top_k=25, filters={"user_id": ...})` (Mem0's hybrid
ranking: semantic + BM25 + entity boost). Memories are grouped under their source meeting
(`## <title> [not_x]` + `When:`), meetings in chronological order, memories in rank order within a
meeting, and sent with `answer.v1` / `ANSWER_SCHEMA` via `run_turn` on the shared `LLMClient`.
`SystemResult` comes from the ledger exactly like `baselines._result`; `retrieved` lists the
meeting IDs that contributed memories.

**Cost accounting.** Mem0 calls Anthropic itself, so after building the `Memory` we replace its
Anthropic provider's SDK client (`memory.llm.client`) with `MeteredAnthropic`, which forwards to a
real `anthropic.Anthropic` and records every response's `usage` (input, output, cache read, cache
write, stop reason) in a `UsageMeter`, priced with `concentrate.llm.client.cost_usd`. If a future
mem0 release renames that attribute, construction fails loudly rather than under-reporting cost.
Ingestion calls do not go through the `LLMClient` disk cache (Mem0 owns the loop); the persisted
store plays that role.

## Configuration and every deviation from library defaults

| Setting | Mem0 default | Ours | Why |
|---|---|---|---|
| LLM provider / model | OpenAI `gpt-5-mini` (Anthropic provider default `claude-sonnet-4-6`) | Anthropic `claude-sonnet-5-5` (`CONCENTRATE_INGEST_MODEL`) | Benchmark rule: ingestion model is configurable, default Sonnet 5.5 |
| Embedder | OpenAI `text-embedding-3-small` (HF provider default `multi-qa-MiniLM-L6-cos-v1`) | HF `sentence-transformers/all-MiniLM-L6-v2`, local CPU | Same embedder as B0; fully local |
| Vector store | Qdrant at `/tmp/qdrant` | Qdrant embedded, `on_disk`, under `data/cache/mem0/...` | Persistent, per-corpus, reusable |
| History DB / `MEM0_DIR` | `~/.mem0/` | inside `data/cache/mem0/` | Keep all state in the repo cache |
| Telemetry | PostHog on, remote notices | `MEM0_TELEMETRY=False` | Fully local except the LLM |
| `max_tokens` | 2000 | 16000 | Sonnet 5.5 thinks adaptively by default; 2000 risks truncated JSON, which Mem0 silently treats as "no memories" |
| Effort | not sent (API default `high` on Sonnet 5.5) | `medium` | Same default effort as `concentrate.llm.LLMRequest`; cost |
| Sampling (`temperature` 0.1, `top_k` 1) | sent for older models | always stripped | Sonnet 5.5 / Opus 5.5 reject non-default sampling params (mem0 2.2.1 already omits them for these model names; we enforce it) |
| Output format | free text, parsed with a JSON fallback | `output_config.format` = JSON schema of Mem0's own documented `{"memory": [{id, text, attributed_to, linked_memory_ids}]}` | A malformed reply would silently drop the chunk's memories; content is unchanged |
| System prompt caching | none | `cache_control: ephemeral` on Mem0's static system prompt | Pure cost saving: the 12.5k-token prompt is identical on every call (cuts ingestion cost roughly 3x); set `cache_system=False` for list-price behaviour |
| Refusal fallback | none | `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) for models that support it | Same policy as every other LLM call in Concentrate |
| Custom instructions (`prompt=`) | none | one sentence per meeting: meeting title and date, "treat the date as the Observation Date", `Me` = note owner, `Them` = others | OSS Mem0 sets the observation date to *today* and rejects `timestamp=` (platform-only), so without this every relative date would resolve against the ingestion day |
| Optional deps | `mem0ai[nlp]` and fastembed are extras | installed (`spacy` + `en_core_web_sm` 3.8.0, `fastembed`) | Mem0's search ranks with BM25 (fastembed) and entity boosts (spaCy); without them it silently degrades to semantic-only |
| `search` | `top_k=20`, `threshold=0.1`, no rerank | `top_k=25`, rest default | Slightly more recall for multi-hop questions |

Unchanged: extraction prompt, the ADD-only pipeline, dedup, entity linking, scoring, thresholds.

## Smoke test (2026-11-02, real API)

Two small Northwind meetings (`not_ry2VP5Uj6VVtCe` "Priya / Alex 1:1", 65 utterances;
`not_ld8TQZiz2bxSeu` "Priya / Sam 1:1", 59 utterances), **5,021 corpus tokens** (exact
`count_tokens` of `corpus.full_view`, identical for Opus 5.5 and Sonnet 5.5), and two ad-hoc
questions written for the smoke test (not taken from `questions.jsonl`).

| | Value |
|---|---|
| Chunks / LLM calls | 6 / 6 (2 summary + 4 transcript) |
| Input tokens (total) | 83,450 = 8,186 uncached + 62,720 cache read + 12,544 cache write |
| Output tokens | 2,348 (incl. adaptive thinking) |
| Ingestion cost | **$0.0838** (Sonnet 5.5) |
| Ingestion wall clock | 26.7 s (≈4.5 s/call, sequential) |
| Memories created | 20 |
| Truncated / refused calls | 0 / 0 |
| Answer calls (Opus 5.5) | 2 calls, 3,938 in / 195 out, $0.0197, ~2 s each |
| Rerun | `cached=True`, $0, store reopened; answers served from the turn cache |
| **Total smoke spend** | **$0.10** |

Both answers were correct and cited the right meeting ("Alex Kim wanted Tomás to lead the
on-call rotation starting in October 2026..."; "...Sam will own the design system starting in
November 2026"). Memories are dated, attributed sentences, e.g. *"In the October 22, 2026 1:1 with
Sam Okafor, the user (Priya Raman) shared the good news that she and Lina agreed Sam will own the
design system from November 2026."* Mem0 also stores a fair amount of filler ("the other
participant said it had been a busy couple of weeks").

### Ingestion cost per 1k corpus tokens and projections (Sonnet 5.5)

* **Measured:** $0.0838 / 5.021k = **$0.0167 per 1k corpus tokens** (includes one $0.031 cache
  write that a larger build amortises).
* **Per-call model** (`estimate_ingest_usd`, calibrated on the same 6 calls; reproduces them at
  $0.0838): per call 12,544 cached system tokens + ~109 instruction tokens + chunk + Mem0's context
  (last messages + similar memories, measured growing ~280 tokens/call from ~80, capped at 1,900),
  and ~0.85 output tokens per chunk token. The cap is an extrapolation: the smoke test only reached
  5 prior messages.

| Corpus | Meetings | Corpus tokens | Calls | Per-call model | Flat measured rate | Wall clock (sequential) |
|---|---|---|---|---|---|---|
| Northwind base | 40 | 127,972 | 134 | **$1.74** | $2.13 | ~10 min |
| Northwind-XL | 184 | 1,351,148 | 1,089 | **$15.6** | $22.5 | ~80 min |

Plan for **$16-23** for XL and **$1.7-2.1** for base. Without prompt caching (`cache_system=False`)
the system prompt alone would add ≈$0.025/call (XL: +$27).

## Limitations

* **No line-level provenance.** Memories are rewritten sentences; citations are meeting IDs
  (`not_...`), so citation precision/recall against gold utterance IDs will score low by
  construction. Compare B4 on answer correctness, and on meeting-level citation if the scorer
  supports it.
* **ADD-only memory.** mem0 2.x extracts additively; it links but does not update or delete
  superseded facts, so "latest state" questions depend on the answer model reconciling dated
  memories. The meeting date in each memory (from our per-meeting instruction) is what makes
  that possible.
* **Retrieval is the whole context.** 25 short memories (~1.5k tokens) is much less than B0's
  20 full meetings; questions needing many facts or exact wording may abstain.
* **Mem0's context window crosses meetings.** "Last 10 messages" is per user, and we use one
  user, so the tail of the previous meeting is shown when extracting the next one. That is
  Mem0's design for a single user; we kept it.
* **Ingestion is sequential and not LLM-cached.** A failed run resumes from `progress.jsonl`, but
  a fresh store with the same config costs the full amount again. Embedded Qdrant takes a file
  lock: one process per store.
* **Model downloads on first use** (not in tests): the MiniLM embedder (shared with B0), fastembed's
  `Qdrant/bm25`, and `en_core_web_sm` (installed as a wheel from the spaCy GitHub release).
* **Estimates** are calibrated on 6 calls from two 1:1s; output length varies a lot with chunk
  content (96 to 1,187 tokens per call). Treat projections as ±30%.
