# Concentrate memory engine (stages 2–5): as built

Status: **built and smoke-tested on 4 meetings (2026-11-02).** Not yet run on the benchmark; wiring
into `evals/runner.py` is the coordinator's job. The Enhance stage (stage 1) is a **stub** here
(raw `Me` / `Them (Name)` labels, no mention links); the real one plugs in through `Enhancer`.

Code: `packages/concentrate/memory/` and `packages/concentrate/agents/concentrate_system.py`.
Prompts: `prompts/extract.v1.md`, `prompts/entail.v1.md` (answering uses the shared `answer.v1`).
Tests: `tests/test_memory.py`, `tests/test_concentrate_system.py` (no network, scripted fake LLM).

## Pipeline as built

```
note ─► Enhance (stub) ─► Extract (Sonnet 5.5, extract.v1) ─► Verify (gate) ─► Consolidate (SQLite)
                                                                                  │
question ─► parse (entities, intent, as-of) ─► structured lookup + hybrid search ─┴► answer.v1 (Opus 5.5)
```

| Module | Role |
|---|---|
| `memory/entities.py` | Directory (people / orgs / projects from `directory.json`, plus unknown attendees); longest-match name and alias finder. |
| `memory/enhance_stub.py` | The agreed Enhance contract (`SpeakerResolution`, `MentionLink`, `EnhancedNote`) and a no-inference stub. `Enhancer(name, enhance, line, load_directory)` is the swap point. |
| `memory/schema.py` | Extraction models and the JSON schema enforced through `output_config.format`. |
| `memory/extract.py` | Prompt building, local line IDs (`[0037]`) and their mapping back to `not_x:0037`, and windowing. |
| `memory/verify.py` | The grounding gate and the `gate_eval` bad-fact injection harness. |
| `memory/store.py` | Bitemporal SQLite store: facts, evidence, contradictions, commitments and events, rejections, L1 lines, meetings (digest, tier sizes, stats). |
| `memory/pipeline.py` | `MemoryBuilder.ingest(note)`: runs stages 2–4 for one meeting in one transaction. |
| `memory/query.py` | Question parsing, structured lookup, hybrid retrieval and context rendering, including retirement. |
| `agents/concentrate_system.py` | `ConcentrateSystem`: a `WriteTimeSystem` with caching and resume, `run`, `turn_request`, and the estimates. |

### Extract
- **Ingestion call.** One structured-output call per meeting on `CONCENTRATE_INGEST_MODEL` (default `claude-sonnet-5-5`, effort `medium`).
  - It goes through `run_turn`, so it gets the shared disk cache, ledger and refusal fallback.
  - The system prompt (`extract.v1` + the directory) is a prompt-cache breakpoint: it is written once, then read at $0.20/MTok.
- **What the model receives:**
  - the meeting header and attendees;
  - **existing memory keys** (current `subject.predicate = value`): first the entities this meeting mentions, then the most recently changed keys, capped at 80 rows. This lets a later meeting reuse the same key, so it *supersedes* the earlier value instead of creating a parallel one;
  - **open commitments** with ids (`c12`), so updates can point to the commitment they change;
  - Granola's summary, for orientation only;
  - the transcript with 4-digit local line IDs.
- **What the model returns:**
  - facts: `subject` (directory id or a new snake_case id), typed `predicate` and `kind`, `value`, `value_norm`, `valid_from` resolved against the meeting date, `source_lines`, a verbatim `quote`, `reason`, `confidence` and a `privacy` class;
  - commitments: owner, task, due date;
  - commitment updates: done / slipped / reassigned / dropped, with evidence lines;
  - a 3–6 sentence digest that cites line IDs (L3).
- **Chunking (open question 2): whole meetings.** Measured on the same 4 meetings:

  | | Whole meeting | 40-line windows (6 overlap) |
  |---|---|---|
  | Calls | 4 | 11 |
  | Cost | $0.055 | $0.066 (+19%) |
  | Input tokens | 24.8k | 51.3k |
  | Facts kept | 12 | 11 |
  | Commitments | 1 | 2 |

  Recall was about the same. Windows re-send the cached prefix and the meeting context for every window, so they cost more, and they cut apart statements and their reasons, which are often dozens of lines apart. XL meetings average ~7k tokens, far inside the context window. `BuildConfig(window_lines=N)` stays available, and a whole-meeting call that hits `max_tokens` is automatically retried in 60-line windows.

### Verify: the grounding gate
Every extracted item (fact, commitment, update) passes three layers, cheapest first:
1. **Deterministic checks.**
   - Every cited line ID exists *in this note*.
   - `valid_from` and due dates are ISO dates.
   - Every number in `value_norm` is said in the cited lines. Spoken numbers are parsed with `core.spoken.spoken_numbers`: "twenty five dollars" gives 25, "PAY two twelve" gives 212, and "312k" gives 312000.
   - For an ISO date, the month name and the day must both be said ("October eighth").
2. **Quote check.** The extractor's quote must be a normalised substring of the cited lines; `...` may join pieces from several cited lines.
3. **Entailment (optional, cheap).** One `entail.v1` call per meeting on `claude-haiku-5-5` at effort `low`. It runs only on items that passed 1–2 but have confidence < 0.75, or whose date is only said relatively ("by Thursday"), which layer 1 can't check.

Rejections go to the `rejections` table with a machine-readable reason (`unknown_line`, `quote_mismatch`, `value_not_in_lines`, `malformed`, `not_entailed`, `update_unmatched`, …) and a detail string.

`verify.gate_eval` takes facts that passed the gate and injects known-bad copies of them:
- `fake_line`: a line ID that doesn't exist;
- `wrong_line`: a real line that doesn't say it;
- `invented_quote`;
- `wrong_number`: the date or number shifted by 7;
- `wrong_subject`: right lines, wrong entity.

It then reports precision and recall, overall and per corruption type.

### Consolidate: the bitemporal store
- **Keys and time columns.** Facts are keyed by `(subject, predicate)` and carry `valid_from` / `valid_to` (valid time) and `recorded_at` / `valid_to_recorded_at` (transaction time: when the meeting that told us happened).
- **Supersession.** A new value closes the open one (`valid_to = new.valid_from`, `superseded_by`). Nothing is deleted. An out-of-order fact is slotted into the timeline.
- **Restatement.** The same value restated (compared on alphanumerics, so "$25" equals "25") adds an `evidence` row and raises confidence (noisy-OR).
- **Contradiction.** A different value on the *same* valid date is flagged in `contradictions`, not resolved.
- **Commitments.** A commitment is a row plus an append-only list of events. `Commitment.status_at(day)` replays the events: `open → slipped → done`, or `reassigned` / `dropped`. With no event, the status is **`no_follow_up`** once the meeting day has passed, which is not the same as done.
- **Atomicity.** Each meeting commits in one transaction together with its `meetings` row (digest, tier sizes, stats) and its L1 lines. The store is therefore its own progress log, and `progress.jsonl` is an append-only copy for humans.

### Answer
1. **Parse the question.** Directory names and aliases give the entities (longest match first, so "Beacon Freight" beats "Beacon"; an ambiguous first name keeps all its candidates). Regex flags give the intent (as_of / latest / history / status / list / open). The date is `q.as_of`.
2. **Structured lookup.** For each resolved entity, show every version of every key, each marked `CURRENT` or `superseded` *as of the question date*. Only facts the memory knew by then are included (transaction time ≤ as-of), so a later meeting can't leak into an "as of" answer.
3. **Commitments.** Include those owned by a resolved person or matching the question's words, with their status on the question date and their event history.
4. **Hybrid search.** BM25 plus embeddings (RRF, the same `HybridIndex` as B0) over fact texts and meeting digests, for open-ended questions and keys the parser missed.
5. **Source lines.** Add the L1 transcript lines that the shown facts cite, grouped by meeting.
6. **Answer.** One `run_turn` with the **same `answer.v1` prompt, `ANSWER_SCHEMA` and answer model** as every baseline, so the model cites **line IDs**. `turn_request(q)` returns that exact request, so batch prefetch works.

**Retirement mode** (`retire_after_days=N`) drops the L0/L1 lines of meetings more than N days before the question date. Facts still show their line IDs as provenance pointers, and the context says which transcripts are retired.

## Concepts, in plain terms

**Bitemporal data.** A fact has two kinds of time.
- *Valid time* is when it was true in the world: "the price was $20 from Sep 8 until Sep 29".
- *Transaction time* is when *we learnt* it: "we only found out on Sep 29 that the $20 price had ended".

Keeping both answers two different questions. "What was the price on Sep 15?" uses valid time. "On Sep 20, what did we think the price would be in October?" needs transaction time: back then nobody knew it would change. Banks and auditors use this so that history is never rewritten, only appended to. Here, the meeting's date is the transaction time, and `Fact.live_at(day, known_at)` implements both questions.

**Supersession.** When a new value arrives for the same key, the old row isn't overwritten. It is closed: its `valid_to` is set to the new value's start. The current value is "the version with no end"; history is "all versions in order"; and "as of D" is "the version whose interval contains D". Overwriting would make the last two questions unanswerable.

**Grounding gate.** A language model asked to extract facts can produce plausible facts that the transcript never states. The gate checks each fact against its sources *before* storing it:
- does the cited line exist?
- is the quoted text really in it?
- is the number actually said?

Then the memory contains only what the transcripts say. That property is what would make it safe to delete a transcript and keep its memory. The cheap checks are programs, not models, so they are exact and free. The model-based check only runs where the programs can't decide.

**Structured lookup vs. retrieval.** Retrieval (search) finds text that *looks like* the question and hopes the answer is inside it. It struggles with "latest" and "as of" questions, because the old price and the new price look equally similar to "what is the price?". Structured lookup goes straight to the key `(Northwind Teams, price_per_seat)` and reads the versions with their dates, so "current" and "as of Oct 9" are exact operations, not guesses. Search is still used for open questions that don't map to a key.

## Smoke test (real API, 2026-11-02)
Meetings: base Northwind `w2-pricing` (not_TifZvvCb7IQQHS), `w4-launch-sync` (not_2No74qJmT2XI4v), `w5-pricing` (not_i0kpeW6NCQ4Jpe) and `w7-launch-sync` (not_MX1VBL2uoUhUwx). Ingest model: Sonnet 5.5 (effort medium). Entailment: Haiku 5.5 (effort low). Answers: Opus 5.5. Enhancer: stub.

**Ingestion** (whole meetings):
- **Calls and tokens:** 5 calls (4 extractions + 1 entailment); 24,808 input tokens (11,622 cache-read, 3,874 cache-write) and 2,928 output tokens.
- **Cost and time:** **$0.055**; 29 s wall (13 s for the first meeting, then 4–7 s each).
- **Yield:**

  | | Extracted | Kept |
  |---|---|---|
  | Facts | 12 | 12 (10 inserted, 2 supersessions) |
  | Commitments | 2 | 1 |
  | Commitment updates | 0 | 0 |

  There were 0 contradictions.
- **Rejections:** 1 `not_entailed`. A commitment ("I'll update the pricing page copy…") was attributed to Priya, but the line is just "Me". Haiku correctly said the cited line doesn't identify the speaker. This is exactly the case speaker attribution (Enhance) has to fix.
- **Supersessions found:**
  - Teams price $20 → $25 on 09-29, with reason "Corvid and Beacon Freight both need SSO and audit logs";
  - Teams launch date 10-28 → 11-11 on 10-14.

  Acmeway's grandfathered $20 was stored as its own key (`acmeway.price_per_seat`), not as a change to the Teams price.

**Tier sizes per meeting** (estimated tokens; L0 = raw transcript, L1 = enhanced transcript, which equals L0 with the stub, L2 = kept facts and commitments, L3 = digest):

| Meeting | L0 | L1 | L2 | L3 |
|---|---|---|---|---|
| Pricing review (09-08) | 1,785 | 1,785 | 102 | 132 |
| Teams launch sync (09-22) | 1,238 | 1,238 | 100 | 100 |
| Pricing review v2 (09-29) | 1,788 | 1,788 | 183 | 135 |
| Teams launch sync (10-14) | 1,311 | 1,311 | 68 | 100 |
| **Total** | 6,122 | 6,122 | 453 | 467 |

L2 + L3 is **~15% of L0**: what survives retirement is about 1/7 of the transcript.

**Answers** (7 dev questions whose evidence is in these meetings: q020, q090, q121, q131, q138, q140, q204):
- **Exact match 7/7.** Mean citation recall 0.86; mean citation precision 0.59. The model also cites lines that support context it mentions, such as the old price or the Acmeway exception.
- **Cost:** $0.128 in total (~$0.018 each). Context was 1.4–2.5k tokens per question.
- **Retirement:** with N = 14 the same contexts shrink by 6–21% (the lines go; the facts and IDs stay). The retirement-curve accuracy run is not done yet.

**Gate evaluation** (`gate_eval` on the 12 kept facts plus 56 injected bad ones):

| Mode | Precision | Recall | Caught | Missed |
|---|---|---|---|---|
| Deterministic + quote | 1.00 | 0.79 | every `fake_line`, `wrong_line`, `invented_quote` and `wrong_number` (44/44) | all 12 `wrong_subject` |
| + Haiku entailment on every item | 1.00 | 0.86 | adds 4/12 `wrong_subject` | 8/12 `wrong_subject` |

No good fact was rejected. The entailment eval cost $0.001.

**Known gap:** a right line attached to the wrong entity gets through. The cheap checks can't see it, and Haiku at low effort mostly misses it too. Real cases will be subtler than the synthetic `zz_unrelated_project`. Options: a stricter entailment prompt that asks "is the claim about *this* entity?"; Sonnet for entailment; or requiring the subject's name or alias to appear in the cited lines or their neighbours (the Enhance mention links make this deterministic).

**Total smoke spend:** ≈ **$0.25** (ingest $0.055 + windowed comparison $0.066 + answers $0.128 + gate eval $0.001).

## Cost model and XL projection
`estimate_ingest` / `estimate_ingest_usd` (cache-aware) use constants calibrated on the smoke run. On the smoke meetings the estimate is $0.062 (25.4k in / 2.9k out); the actual was $0.055 (24.8k in / 2.9k out). Calibration:
- **Cached prefix:** count_tokens ÷ estimate = 1.336 for `extract.v1` + directory, plus 1,334 tokens of structured-output schema overhead (the measured prefix is 3,874 tokens).
- **Transcript:** measured ÷ estimate = 1.65 for summary + transcript with local IDs. Full IDs cost more: `full_view` measures 1.94× the estimate.
- **Context:** grows by 100 tokens per meeting from 400, capped at 3,400 (keys and open-commitment caps). This is **assumed**, not yet measured at XL scale.
- **Output:** 40 + 9.5 tokens per transcript line.

**XL** (184 meetings, 32,832 lines; 1,351,148 tokens via `corpus.full_view`), Sonnet 5.5:

| Item | Projection |
|---|---|
| Input | 1.40M uncached + 4.3k cached prefix × 184 (2.19M total) |
| Output | 0.32M |
| **Interactive cost** | **≈ $6.2** (≈ $7.6 without prompt caching; Opus 5.5 would be ≈ $12.2) |
| Message Batches | ≈ $3.1. But each meeting's prompt includes keys from earlier meetings, so batching needs a two-pass design: extract without keys, then reconcile predicates. |
| Time, sequential | ≈ 45–60 min, from ~0.1 s per transcript line measured on the smoke run |

Output is about half the cost, and the per-line output rate was fitted on dense base meetings. XL meetings are padded with filler, so the output estimate is probably high. This is above the $2 approval threshold, so it hasn't been run.

## Open risks
- **Predicate drift.** Supersession needs the same `(subject, predicate)` across meetings. The keys-in-prompt mechanism worked on the smoke run: `price_per_seat` and `launch_date` were reused. But with a 4-meeting sample the extractor still invented near-duplicates (`included_features` / `includes_sso_audit_logs` across runs). At XL scale, synonyms will split histories. Mitigations:
  - a canonicalisation pass over predicates (embedding plus LLM merge);
  - a seeded predicate vocabulary per entity kind.
- **Keys block at scale.** 80 rows plus 40 commitments is ~3k tokens per call. Selection relies on mentions, which the Enhance stage will improve.
- **Commitments on "Me" lines.** In in-person meetings, and whenever the owner is unnamed, commitments are rejected or misattributed until speaker attribution lands. Commitment yield was low in the smoke run (1 kept).
- **The entity-only question parser.** Questions that name no directory entity rely on search alone.
- **The gate's wrong-entity blind spot** (above).
- **Batch ingestion is not supported.** Ingestion is sequential by design (each meeting sees the memory so far).

## What the real Enhance stage must provide (contract)
- `enhance(note, directory, history) -> EnhancedNote` with `attendee_ids`: directory ids, or raw emails when unknown. These are used for the attendee list and to prioritise keys and commitments.
- `enhanced_line(u, e) -> str` starting with `[<utterance_id>] ` followed by the resolved speaker, e.g. `Sam Patel (Backend Engineer)`. The prefix is rewritten to a local ID for the prompt; anything else in the line is kept verbatim, and it is what the quote check matches against, so **don't paraphrase the utterance text**.
- A directory loader for `Enhancer.load_directory(path)`. Our own `EntityDirectory` is still used for fact subjects, so the **ids must be the same** as `directory.json`'s.
- Next, `MentionLink`s with `entity_id` should be used for:
  - the mentioned-entity set, replacing our string matching;
  - a deterministic "subject is mentioned near the cited lines" gate check, which closes the wrong-subject gap.
- Integration: `ConcentrateSystem(client, enhancer=Enhancer("enhance-v1", enhance.enhance, enhance.enhanced_line, <loader>))`. The enhancer name is part of the store's config hash, so the store rebuilds automatically.
