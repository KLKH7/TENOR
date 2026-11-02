# Unit economics and the scale curve (workstream F)

Two questions a startup asks before adopting any of this:

1. **What does each approach cost per user per month, and when does paying up front
   (write-time memory) beat paying per question (query-time retrieval)?**
2. **Does query-time retrieval get worse as a user's meeting history grows?**

Both are answered from *measured* runs. Everything that isn't measured is a named CLI flag, and
the guesses are labelled as guesses.

```bash
uv run concentrate economics                      # free: re-prices measured tokens
uv run concentrate economics --meetings 80 --questions 100 --ingest-model claude-haiku-5-5
uv run concentrate scale-curve --estimate          # free (token counting); prints the cost
uv run concentrate scale-curve --model claude-haiku-5-5 --n 0
```

Outputs: `evals/economics/<report>.{md,json,breakeven.svg}` and
`evals/scale/<stamp>/{scale.md,scale.json,scale_accuracy.svg,scale_cost.svg}` plus one normal eval
report per corpus size.

## 1. The economics model

### Inputs (measured)

| input | where it comes from |
|---|---|
| ingest tokens per meeting, per write-time system | every build's `progress.jsonl` under `data/cache/{mem0,graphiti,concentrate}/` (one row per meeting or chunk, written as the build runs) |
| per-question answer tokens, latency, accuracy | the per-question rows of any eval report (`evals/reports/*.jsonl`, or a scale-curve size report) |
| prices | `concentrate.llm.client.PRICING` (+ cache-read prices, Batch API discount) |

Two reconciliation details worth knowing, because they would silently skew the numbers:

- **Progress rows don't all count tokens the same way.** Mem0's and Concentrate's rows count
  `input_tokens` as the *whole prompt* (cache reads and writes included); Graphiti's count
  uncached input only. Re-pricing each convention reproduces the recorded cost to the cent
  (Mem0, Graphiti) or within 3% (Concentrate, whose rows mix a little Haiku entailment into a
  Sonnet build). The ingest model of each build is *inferred* this way, not assumed.
- **Report rows don't give the cache split.** B2 (full context) caches the corpus prefix, so its
  real cost is ~5x below its token count at list price. `split_cache` recovers the cached share
  from the recorded cost.

### The model

Per user per month, with `M` meetings, `Q` questions, ingest cost `I` per meeting and answer cost
`a` per question:

```
write-time  = M·I + Q·a_w          query-time = Q·a_r
break-even  Q* = M·I / (a_r − a_w)       →   Q*/M = I / (a_r − a_w)
```

`Q*/M` (questions per meeting) does not depend on how many meetings a user has: it is how many
times, on average, each meeting must later be asked about before having studied it pays off.

Sliders (CLI flags): `--meetings`, `--questions`, `--ingest-model`, `--answer-model`,
`--batch-ingest/--no-batch-ingest` (default on: ingest is asynchronous), `--batch-queries`
(default off: users wait for answers), `--batch-discount` (0.5), `--cache-hit` (default: as
measured). A sensitivity grid (ingest model × answer model) is always printed.

### Results

Two runs, both free (`evals/economics/`):

- **XL** (`evals/economics/xl/`): answer tokens measured on Northwind-XL (184 meetings) in the
  scale curve's 100% size, re-priced from Haiku to Opus 5.5. This is the more realistic one: b0
  reads **~160k tokens per question** on XL (k=20 whole meetings), 2.3x the 68k it read on the
  40-meeting pilot corpus.
- **Pilot** (`evals/economics/20261102T100403Z.*`): the 10-question Opus pilot on base Northwind.

XL, Sonnet ingest (Batch API), Opus answers, 40 meetings + 30 questions per user per month:

| system | kind | $/meeting ingest | $/question | **$/user/month** | ingest share |
|---|---|---:|---:|---:|---:|
| Concentrate | write-time | $0.0139 | $0.019 | **$1.14** | 49% |
| Mem0 | write-time | $0.046 | $0.025 *(est.)* | **$2.60** | 71% |
| B3 agent (Granola-style) | query-time | — | $0.108 | **$3.23** | — |
| Graphiti | write-time | $0.163 *(partial build)* | $0.032 *(est.)* | **$7.49** | 87% |
| B0 top-k | query-time | — | $0.307 | **$9.21** | — |

Break-even (questions per user per month above which write-time is cheaper, at 40
meetings/month; in brackets, questions per meeting):

| write-time \ vs | B3 agent | B0 top-k |
|---|---:|---:|
| Concentrate | 6.3 (0.16/mtg) | 1.9 (0.05/mtg) |
| Mem0 | 22 (0.56/mtg) | 6.6 (0.16/mtg) |
| Graphiti | 86 (2.15/mtg) | 24 (0.59/mtg) |

What this says:

- **Ingest is cheap if the extractor is lean.** Concentrate makes ~1.2 Sonnet calls per meeting
  (10.6k uncached + 4.7k cached input, ~540 out): **1.4¢ per meeting** with the Batch API. Mem0
  makes ~6 calls (3.3x), Graphiti ~17 calls and 150k input tokens per meeting (12x). How many
  LLM calls a pipeline makes per meeting matters more than which pipeline it is.
- **Break-even arrives almost immediately against Opus-priced retrieval.** Against Granola-style
  agentic retrieval, Concentrate pays for itself once a user asks about **1 in 6 meetings**.
  Against top-k, it's 1 in 20.
- **The answer model dominates query-time cost; the ingest model dominates write-time cost.**
  The sensitivity grid in `evals/economics/xl/*.md` shows it:
  - With **Haiku answers**, query-time is nearly free (B3 $0.08/user/month) and write-time
    *loses* on cost (Concentrate $0.57 with Sonnet ingest).
  - With **Haiku ingest**, every write-time system drops sharply (Graphiti $7.49 → $1.29).
  So the decision is really "which model must answer to be accurate enough?". If the answer
  needs a frontier model, studying ahead is cheaper. If a small model can answer from raw
  retrieval, it isn't.
- **Accuracy is not settled here.** The XL accuracies in the table (B0 80%, B3 82%,
  Concentrate 73%, n=45) come from *Haiku* answers in the scale curve, and the CIs overlap. The
  final Opus comparison (workstream A) replaces them; re-run
  `concentrate economics --report <that report>`.

Caveats specific to these numbers:

- Re-pricing B3's Haiku tokens to Opus assumes the same number of turns and output tokens.
  Opus may take fewer turns, or think longer.
- Mem0 and Graphiti answer costs are planning estimates: neither appears in a report yet.
- Graphiti's ingest comes from its in-progress build (139/184 meetings, and the build was
  restarted).
- All of this is synthetic. Real meetings are longer (`FIDELITY.md`), which raises ingest and
  B0 roughly in proportion and leaves the break-even ratio about the same.

### Assumptions and guesses

| kind | assumption |
|---|---|
| measured | ingest tokens/meeting: mean over completed meetings of each build (Northwind-XL is synthetic; real meetings may be longer, see `FIDELITY.md`) |
| measured | per-question tokens, latency, accuracy: the report passed in |
| assumed | write-time systems with no report rows use their own planning estimate for the answer call (`PLANNING_ANSWER_TOKENS`), flagged "ASSUMED estimate" in the table |
| assumed | query-time systems need no LLM at ingest; embeddings/indexing cost ~0 for everyone (local embedder) |
| assumed | ingest always gets the Batch API price; answers don't (`--batch-queries` to change) |
| assumed | a `--cache-hit` override applies one hit rate to all prompt tokens and ignores cache writes |
| **guess** | **40 meetings and 30 questions per user per month.** We have no usage data. Set them. |
| ignored | storage, vector DB hosting, re-ingest on note edits, retries, the judge (an eval cost, not a product cost), Granola's own summary generation (paid either way) |

## 2. The scale curve

Run: `evals/scale/20261102T223053Z/` (`scale.md`, `scale_accuracy.svg`, `scale_cost.svg`, one
report per size). Answer and judge model **Haiku 5.5** (see below), k=20, Batch API, seed 42.
**Spend: $1.19.**

**Design.** The XL meetings are sorted by date and cut into nested prefixes: 25% (46
meetings), 50% (92) and 100% (184). The question set is the 45 dev questions whose gold evidence
lies inside the 50% prefix. Each size runs the subset answerable there: 21 questions at 25%
(8 of them unanswerable), all 45 at 50% and 100%. So every comparison asks the *same* question
against a bigger haystack. Concentrate isn't rebuilt per size. Its bitemporal store is rolled
back in transaction time (`rewind_store`), which a test proves equal to building on the prefix.

| system | 25% (n=21) | 50% (n=45) | 100% (n=45) | 50→100%: right→wrong / wrong→right | in-tok/q 25→100% | cite recall 25→100% |
|---|---:|---:|---:|---:|---:|---:|
| B0 top-k | 86% | 91% | **80%** | 7 / 2 | 157k → 160k | 96% → 87% |
| B3 agent | 95% | 91% | **82%** | 5 / 1 | 15.6k → 21.5k | 73% → 55% |
| Concentrate | 71% | 76% | **73%** | 4 / 3 | 6.2k → 8.4k | 65% → 83% |

What this says, honestly:

- **Both query-time systems lose accuracy when the haystack doubles from 92 to 184 meetings**
  (B0 −11 points, B3 −9). Concentrate is flat (−3, with 4 lost and 3 gained). The direction
  matches the hypothesis, but **it is not statistically significant at this n**. On paired
  flips, a two-sided sign test gives p≈0.18 (B0, 7 vs 2) and p≈0.22 (B3, 5 vs 1). Call it
  suggestive, not proven.
- **The degradation shows up in citations before it shows up in answers.** B3's citation recall
  falls steadily, 73% → 65% → 55%. It still answers, but finds less of the gold evidence. It also
  works harder: input tokens per question rise 38% (15.6k → 21.5k), so query-time cost *grows*
  with corpus size. B0's cost is flat by construction (always 20 meetings).
- **Accuracy on the 21 questions common to all sizes barely moves** for B0 and B3. The 25%
  questions are easy (8 of 21 unanswerable, mostly single-hop and temporal). The drop comes from
  the 24 harder questions added at 50%.
- **Concentrate's absolute accuracy is lowest at every size** (71–76%). Its weakness is extraction
  recall, not the haystack: the haystack doesn't hurt it, but it starts lower. Its citation
  recall *rises* with size (65% → 83%) as later meetings add corroborating evidence to facts.
  This is with Haiku answering. The Opus comparison is workstream A's.

**Why Haiku.** For the full design, Opus would have cost about $47 and Sonnet about $24
(`scale-curve --estimate`, free), against a $12 budget. The curve measures a *trend* with the
model held fixed. Haiku is probably more sensitive to distractors than Opus, so read these
numbers as an upper bound on degradation, not as headline accuracy.

**Why retrieval precision falls as the haystack grows.** Top-k always returns k meetings. With
46 meetings, k=20 covers 43% of the corpus, so the gold meeting almost always makes the cut.
With 184 meetings it covers 11%. Every extra meeting is another near-miss competing for those
20 slots: the same people, the same project names, an older value of the same price. The chance
that one of them outranks the gold meeting grows with the corpus. An agent can search again,
but it pays in tokens and still stops early. A write-time store answers from a per-entity
lookup ("Teams → price → current value"), whose size grows with the number of *facts about the
entity*, not the number of meetings.

Next steps (not done, would need spend):

- Repeat the 50% → 100% comparison on Sonnet (≈$20 for 45 questions × 2 sizes × 3 systems; ≈$7
  for B3 and Concentrate only).
- Grow the question set: the test split would roughly triple n, but test is reserved for the
  final comparison.
- Add a 200%+ point by concatenating a second synthetic company, to see whether the trend
  continues.
