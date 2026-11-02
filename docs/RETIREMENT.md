# The transcript-retirement curve (workstream C)

Granola has said it is weighing **deleting old transcripts while keeping a working memory**.
This experiment measures what that costs: we retire transcripts older than N days and see which
questions can still be answered.

## Setup

- **Corpus and questions:** Northwind-XL (184 meetings, 2026-08-31 → 10-23), **dev split only**
  (90 questions, 9 categories, as-of dates 09-04 → 10-24). Nothing was tuned on test, and test
  was not run.
- **Retirement rule:** for a question asked on `as_of`, a meeting is *retired* when its day is
  more than N days before `as_of` (`day < as_of − N`). N = ∞ means nothing is retired and N = 0
  retires every earlier meeting. The rule is the same for every system:
  `concentrate.evals.retirement.is_retired`, which mirrors `memory.query.build_context`.
- **Systems** (same answer model `claude-opus-5-5`, same `answer.v1` prompt, same judge):
  - `concentrate@N`: Concentrate's v2 build (store `78a4767dabf454ae/6b9c4de7`; enhancer
    `enhance-v1`, `extract.v2`, `entail.v2`, Sonnet 5.5 ingest). Retired meetings keep **L2**
    (verified facts with line IDs) and **L3** (digests). Their **L0/L1** transcript lines are no
    longer shown, but facts keep their line IDs as pointers.
  - `b1_summaries`: top-20 meetings, AI summary + typed notes only, i.e. roughly what Granola
    keeps anyway. This is also the N = 0 end of b0 (see below).
  - `b0_topk@N` (`RetiredTopKSystem`): top-20 retrieval where a retired meeting's transcript is
    **deleted**. Its chunks can no longer be retrieved; if the meeting still ranks through its
    summary or notes, it is shown summary-only. **Not run for accuracy** (see Budget). We report
    what its contexts would contain, which costs nothing to compute.
- **Measures:**
  - accuracy (LLM judge), overall and by category, with 95% bootstrap CIs (seed 42);
  - **gold line text in context:** the share of the question's gold transcript lines whose text
    is in the answer context;
  - **gold line IDs referenced:** the same, but a bare ID pointer also counts, as Concentrate's
    facts keep after retirement;
  - **tokens kept:** taken from the build's per-meeting tier sizes, as a mean over questions,
    counting only meetings held as of each question.

Reproduce:

```bash
uv run concentrate retire-curve --b0-settings "" --latency-sample 0
```

```bash
uv run concentrate retire-curve --evidence-only --b0-settings "inf,30,14,7,0" --offline
```

The first run is ~$8 on a cold cache and $0 when cached. The second is always $0.

## Budget

The total budget was about $10. Running b0 at k = 20 means ~158k input tokens per question at N = ∞
(≈ $29 for 90 dev questions in batch mode), ≈ $16 at N = 14 and ≈ $12 at N = 7. The user chose
to run only Concentrate × 5 settings plus b1 (≈ $8), and to describe b0 by its contexts instead.

## Results (XL dev, 90 questions)

Paid run `evals/reports/retirement/20261102T222859Z` ($7.04). The final curve
`20261102T224523Z.curve.md` is an offline re-render of the same cached answers ($0), with the
corrected evidence metric. Chart: [`retirement_curve.svg`](retirement_curve.svg). The green line
is Concentrate and the dashed line is b1.

| System | N (days) | Accuracy [95% CI] | Gold line **text** in context | Gold line **IDs** referenced | Meetings retired | Raw transcript tok kept | Store tok kept | Input tok/q |
|---|---|---|---|---|---|---|---|---|
| concentrate@∞ | ∞ | 89% [82%, 96%] | 91% | 93% | 0% | 623,826 | 100% | 8,983 |
| concentrate@30 | 30 | 88% [80%, 94%] | 57% | 93% | 41% | 352,751 | 60% | 6,703 |
| concentrate@14 | 14 | 88% [80%, 94%] | 30% | 93% | 70% | 159,925 | 31% | 5,718 |
| concentrate@7 | 7 | 88% [80%, 94%] | 22% | 93% | 83% | 80,681 | 18% | 5,356 |
| concentrate@0 | 0 | 88% [81%, 94%] | 1% | 93% | 99% | 2,875 | 3% | 4,989 |
| b1_summaries | (all) | 64% [54%, 74%] | 0% | 0% | (all) | 0 | — | 5,938 |

"Store tok kept" counts L0 + L1 + L2 + L3 against no retirement. The memory alone (L2 + L3) is
31k tokens, **≈ 5% of the raw transcript** (624k tokens, mean as of each question).

Accuracy by category (n is small, so CIs are wide; full CIs in the curve report):

| Category | n | ∞ | 30 | 14 | 7 | 0 | b1 |
|---|---|---|---|---|---|---|---|
| latest_state | 11 | 100% | 100% | 100% | 100% | 100% | 73% |
| commitment_status | 9 | 89% | 89% | 89% | 89% | 89% | 67% |
| multi_hop | 7 | 100% | 100% | 100% | 100% | 100% | 71% |
| decision_change | 8 | 100% | 100% | 88% | 100% | 100% | 62% |
| single_hop | 11 | 91% | 91% | 91% | 91% | 91% | 73% |
| temporal | 18 | 94% | 94% | 94% | 94% | 94% | 83% |
| entity_disambiguation | 10 | 70% | 70% | 70% | 60% | 60% | 30% |
| aggregation | 8 | 50% | 38% | 50% | 50% | 50% | 0% |
| unanswerable | 8 | 100% | 100% | 100% | 100% | 100% | 100% |

b0 with deleted transcripts, contexts only (no answers were run; this is
`--evidence-only`, $0). It shows the share of gold line text the top-20 context still contains:

| | b0@∞ | b0@30 | b0@14 | b0@7 | b0@0 |
|---|---|---|---|---|---|
| all answerable | 96% | 60% | 32% | 23% | 1% |
| temporal | 100% | 100% | 83% | 72% | 6% |
| entity_disambiguation | 99% | 22% | 2% | 0% | 0% |
| decision_change | 94% | 40% | 15% | 0% | 0% |

## Honest read

1. **On this benchmark, retiring transcripts costs Concentrate almost nothing.** Accuracy goes
   from 89% to 88% between keeping everything and keeping only facts and digests (N = 0), and the
   CIs overlap almost completely. Only 4 of 90 questions ever change verdict across the five
   settings, and 3 of those changes are not monotonic in N (q657 is wrong at 14 but right at 7
   and 0; q805 is wrong only at 30; q692 is wrong only at 7). That is answer and judge noise,
   not information loss. The one plausible real loss is **q245** (entity_disambiguation): "who
   said X *and what is their role*". At N = 0 the answer names Sam Patel correctly but drops the
   role, which the transcript line's speaker label carried and the fact did not.
2. **What survives:** `latest_state`, `commitment_status`, `multi_hop`, `single_hop` and
   `temporal` are flat. This was expected for the first two, since they are exactly what the
   fact store and the commitment ledger model. **The expected loss on temporal detail did not
   appear.** On this benchmark, temporal questions ask "what was X as of date D", and the facts'
   valid-time intervals answer that without any transcript.
3. **What is fragile:** `entity_disambiguation` (70% → 60%, one question) is the category where
   the transcript adds something the facts don't: who exactly said a line, and their role.
   `aggregation` is poor at every N (≈ 50%). That is an extraction-recall problem, not a
   retirement problem.
4. **What the memory keeps is what matters.** b1 (summaries only) is what you'd have if you
   deleted transcripts and kept Granola's notes. It scores **64%, 24 points below
   concentrate@0**, at about the same context size (~5–6k tokens/question), and the CIs don't
   overlap. b0 with deleted transcripts slides toward b1. At N = 14 its top-20 contexts hold
   only 32% of the gold line text, and 2% for entity questions. At N = 0 it is essentially b1.
   (b0's retired *accuracy* was not measured; see Budget.)
5. **Pointers survive retirement and text doesn't.** Concentrate's facts still reference 93% of
   gold lines at every N, so a retired answer can say *where* the evidence was even though it
   can't quote it. If Granola kept the transcripts in cold storage, those pointers could fetch
   lines on demand.

### Caveats (why these numbers flatter retirement)
- **There's no verbatim-quote category.** The brief expected exact quotes to suffer, and they
  would: a retired meeting has no quotable text. But no Northwind question asks for exact
  wording, so the benchmark can't show that loss. This is the most important gap.
- **The questions are fact-shaped.** Gold answers come from the same world facts and commitments
  that the extractor is built to capture (the synthetic generator writes facts first, then
  dialogue). Real meetings have nuance, tone and side-threads that no extractor anticipates, and
  questions about those would lose more. `FIDELITY.md` reports that 96% of the wording is
  templated filler, which makes extraction easier than on real speech.
- **Small n.** There are 7–18 questions per category, so a one-question change is 6–14 points.
- **Dev split only** and one seed. Test was not run (it would need the user's sign-off and
  budget). Answers are single samples; the noise in point 1 shows that matters.
- **Short horizon.** The corpus spans 7.5 weeks, so N = 30 already retires 41% of meetings. A
  real account has years of history, where facts from old meetings stop mattering *or* become
  the only record.
- **The token counts in the answer context are small anyway.** Concentrate shows at most
  `MAX_SOURCE_LINES` cited lines. Retirement shrinks its context by 45% (9.0k → 5.0k
  tokens/question), but the answer model never saw whole transcripts, even at N = ∞.

## What this means for Granola's storage decision

- If the working memory is **structured, cited facts with validity dates plus a commitment
  ledger**, deleting old transcripts loses almost nothing on state, commitment and history
  questions. You keep ~5% of the tokens: on this corpus, 31k of memory against 624k of raw
  transcript.
- If what you keep is **summaries**, which is roughly today's notes, you lose about a quarter of
  answerable questions. That is the gap Granola should weigh.
- What you *do* lose is untested here, but it is predictable: **verbatim quotes, who-said-what
  with speaker detail, and anything the extractor didn't anticipate.** A middle path is to tier
  storage rather than delete. Keep L2/L3 hot, and move L0 to cold storage, fetched by the line-ID
  pointers that the facts already carry (93% of gold evidence stays addressable).
- The next experiment that would settle it: add a **verbatim/attribution question category**,
  and run the same sweep on the real-data slice (workstream B) or the v2 corpus (workstream G).
