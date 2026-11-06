# Concepts glossary

Every CS and stats idea the project uses. Each entry has four parts:
- **plain words**: what it is;
- **here**: why this repo uses it, with the file;
- **trade-off**: what it costs or gets wrong;
- **example**: a tiny worked case.

Where an example has numbers, they come from running the repo's own functions (`uv run python -c …`),
not from memory. The sections follow the order of a run: getting data → retrieving → calling LLMs →
measuring → resolving names → storing facts → surviving long runs → judging the benchmark itself.

Paths are relative to `packages/concentrate/`.

---

## 1. Getting data from Granola

### Token bucket
- **Plain words.** A bucket holds up to *N* tokens and refills at *r* tokens per second. Each
  request spends one token; if the bucket is empty, you wait. This allows short bursts while
  enforcing a long-run average rate.
- **Here.** Granola documents "burst 25 / 5 s, sustained 5 req/s". `RateLimiter`
  ([`sources/ratelimit.py:69`](../../../../packages/tenor/sources/ratelimit.py#L69)) uses capacity 25, refill 5/s.
- **Trade-off.** A bucket alone is too generous here. It starts full (25), then refills 25 more in
  5 s, so it lets through ~50 requests in the first 5 seconds, which Granola's window would reject.
- **Example.** At t=0 you can send 25 requests instantly (`tests/test_ratelimit.py:14`). Request 26
  waits 0.2 s (1 token ÷ 5/s).

### Sliding window (log)
- **Plain words.** Remember the timestamp of every recent request. Before sending, count how many
  happened in the last *W* seconds; if that's already the limit, wait until the oldest one ages out.
- **Here.** The same `RateLimiter` keeps a `deque` of send times (`_sent`) and enforces ≤25 per 5 s
  alongside the bucket. Whichever wait is longer wins (`_wait_time`, line 95). The test asserts
  **zero** 429s from a fake server that enforces its own limit (`tests/test_granola_api.py:50`).
- **Trade-off.** Memory grows with the limit (25 timestamps, trivial here), and it's stricter than
  necessary at window edges. A "fixed window" counter is cheaper but allows 2× bursts at the boundary.
- **Example.** 25 requests at t=0. Request 26 can't go until t=5.0, when the first one leaves the
  window, even though the bucket refilled a token at t=0.2.

### Watermark + overlap sync
- **Plain words.** To fetch only what changed, remember the newest `updated_at` you've seen (the
  *watermark*). Next time, ask for "updated after watermark − overlap", re-scanning a small window
  in case you missed something at the boundary.
- **Here.** [`sources/poller.py:38`](../../../../packages/tenor/sources/poller.py#L38) `sync`:
  - the watermark is the **server's** max `updated_at`, never our wall clock, so clock skew can't
    skip notes;
  - the overlap is 10 minutes;
  - notes whose `updated_at` matches the store are skipped without a fetch;
  - 404s go to a pending queue retried every sync.
- **Trade-off.** The overlap re-lists a few notes each time (cheap, because unchanged ones are
  skipped). Open risk: if a note's `updated_at` doesn't move when it finishes processing, and
  processing takes longer than the overlap, it could be missed. `sync --full` is the backstop.
- **Example.** Last sync saw a max of 10:00. The next one asks `updated_after=09:50`. A note stamped
  09:58 that appeared late (same-second race) is still caught. A note still at 10:00 with the same
  `updated_at` is skipped.

### Exponential backoff (with jitter)
- **Plain words.** After a failure, wait, then wait twice as long after the next one, up to a cap.
  *Jitter* randomises each wait so many clients that failed together don't retry together (the
  "thundering herd").
- **Here.** Two places:
  - LLM calls: [`llm/resilience.py:79`](../../../../packages/tenor/llm/resilience.py#L79) `RetryPolicy.backoff`
    = `uniform(0, min(60, 2·2ⁿ))`. That's true "full jitter", under a 20-minute deadline, with
    `Retry-After` honoured.
  - Granola HTTP: [`sources/granola_api.py:99`](../../../../packages/tenor/sources/granola_api.py#L99) `_backoff`
    = `uniform(ceiling/2, ceiling)`. Its docstring says "full jitter", but that formula is what's
    usually called *equal jitter*. It's a small naming inaccuracy, worth knowing if asked.
- **Trade-off.** It's slower to recover from a one-off blip than retrying immediately, but much
  kinder to an overloaded server. Retrying is only safe if the operation is idempotent (below).
- **Example.** Retries 0, 1, 2, 3 with base 2 s draw waits from [0,2], [0,4], [0,8], [0,16] s.

### Idempotency
- **Plain words.** Doing something twice has the same effect as doing it once. "Set price = $20"
  is idempotent; "add $20" isn't.
- **Here.**
  - A retried Messages call is safe *for our state*: nothing is cached or accounted until a whole
    reply arrives ([`llm/resilience.py`](../../../../packages/tenor/llm/resilience.py) docstring).
  - Ingest is idempotent per meeting: `MemoryBuilder.ingest` returns early if `has_meeting`
    ([`memory/pipeline.py:196`](../../../../packages/tenor/memory/pipeline.py#L196)).
  - `batches.create` is **not**: a lost reply plus a retry makes two batches (documented in
    [`docs/engineering/HARDENING.md`](../../../engineering/HARDENING.md), not fixed).
- **Trade-off.** Making things idempotent usually means storing a key (here, the meeting ID or the
  request hash) and checking it first.
- **Example.** Kill an ingest halfway and rerun it. Meetings already committed are skipped, and the
  one in flight is redone from scratch because its transaction never committed.

---

## 2. Retrieval

### BM25
- **Plain words.** A keyword-ranking formula. A document scores higher when it contains the
  query's words, especially **rare** words (inverse document frequency). Repeating a word helps
  less and less (saturation), and long documents are penalised a little.
- **Here.** `HybridIndex` ([`agents/retrieval.py:59`](../../../../packages/tenor/agents/retrieval.py#L59))
  uses `rank_bm25.BM25Okapi` over 15-line transcript chunks and summaries (b0/b1), and over fact
  texts and digests (Concentrate).
- **Trade-off.** It's exact-word only: "cost" won't match "price", and ASR spelling errors ("Meghan")
  break it. It's fast, free and explainable.
- **Example.** Query "Teams price per seat". A chunk saying "price per seat" beats one that just
  says "Teams" many times, because "seat" is rarer than "Teams".

### Embeddings
- **Plain words.** A model maps text to a vector so that texts with similar *meaning* land near
  each other. You rank by cosine similarity (a dot product, when vectors are normalised).
- **Here.** `SentenceTransformerEmbedder` ([`agents/retrieval.py:34`](../../../../packages/tenor/agents/retrieval.py#L34)),
  `all-MiniLM-L6-v2`, runs locally on CPU (free, cached).
- **Trade-off.** It catches paraphrase but blurs exact details. "$20" and "$25" embed almost the
  same, so it's weak exactly where meeting memory needs precision. It also needs a model download.
- **Example.** "How much does the Teams plan cost?" can retrieve "we're moving to twenty five
  dollars a seat" with zero shared keywords.

### Reciprocal rank fusion (RRF)
- **Plain words.** A way to merge several ranked lists without comparing their raw scores (BM25 and
  cosine scores are on different scales). Each document gets Σ 1/(k + rank), with k = 60.
- **Here.** [`agents/retrieval.py:50`](../../../../packages/tenor/agents/retrieval.py#L50) `rrf`, fusing the BM25 and dense rankings.
- **Trade-off.** It throws away *how much* better the top hit was, using only rank. k = 60 is a
  convention (Cormack et al. 2009), not something tuned here.
- **Example** (run on the real function): BM25 ranks docs `[0,1,2]`, dense ranks `[2,0,3]`.
  - doc0 = 1/61 + 1/62 ≈ .0325
  - doc2 = 1/63 + 1/61 ≈ .0323
  - doc1 = 1/62 ≈ .0161
  - doc3 = 1/63 ≈ .0159

  `rrf` returns `[0, 2, 1, 3]`.

---

## 3. Calling LLMs cheaply

### Prompt caching
- **Plain words.** If many requests start with the same long prefix, the provider can keep that
  prefix's processed state. Later requests pay a small "cache read" price for it instead of the
  full input price.
- **Here.** `TurnRequest.cache_system` puts a `cache_control` breakpoint on the system prompt
  ([`llm/client.py:392`](../../../../packages/tenor/llm/client.py#L392)). b2 puts *every meeting* in that
  system prompt ([`agents/baselines.py:292`](../../../../packages/tenor/agents/baselines.py#L292)); Concentrate's extractor and Mem0 use it too.
  Prices are in [`llm/client.py:81`](../../../../packages/tenor/llm/client.py#L81): reads $0.20/MTok, writes 1.25× input.
- **Trade-off.** The first call costs 25% *more* (the write). The cache expires after ~5 minutes,
  and inside a batch it's only best-effort, so b2's batch cost is optimistic.
- **Example.** b2 on the base set sends about 151,636 input tokens per question (from `eval --estimate`).
  - Uncached on Opus 5.5 ($4/MTok): ≈ $0.61.
  - First call (write, 1.25×): ≈ $0.76.
  - Every later call (read, $0.20/MTok): ≈ $0.03.

  That's why the biggest context is one of the cheapest systems per question.

### Batch API
- **Plain words.** Submit many requests at once and get the results later (within 24 h) at 50% of
  the price.
- **Here.** `concentrate eval --batch` (the default) prefetches the single-turn systems and the judge
  ([`llm/client.py:765`](../../../../packages/tenor/llm/client.py#L765) `prefetch_turns`). Each result is written to
  **the same cache file** an interactive call would write (`custom_id` = the 64-char cache key), so
  the normal code path replays it. That means no second code path.
- **Trade-off.** No latency information, because you don't know how long one answer took. So the
  first 10 questions run interactively at full price and are the only latency source. b3 (multi-turn)
  can't be batched. Refusals inside a batch are retried interactively.
- **Example.** In the estimate above, b0 ≈ $12.89 and b1 ≈ $1.92 *including* the batch discount and judge.

---

## 4. Measuring (the eval harness)

### LLM-as-judge
- **Plain words.** Use a model to grade free-text answers against a gold answer, because exact
  string matching is too brittle ("Oct 28" vs "October 28th").
- **Here.** `judge.v1` ([`evals/scoring.py:110`](../../../../packages/tenor/evals/scoring.py#L110)) returns
  `correct | partially_correct | incorrect` plus a reason, and only `correct` counts. Exact match
  ([`:50`](../../../../packages/tenor/evals/scoring.py#L50)) and citation P/R are shown next to it, so judge bias is visible.
- **Trade-off.** The judge is the same model as the answerer (Opus 5.5), which risks
  self-preference. Mitigations:
  - `judge-check` re-grades with Sonnet 5.5 and reports Cohen's kappa
    ([`evals/judge_check.py:82`](../../../../packages/tenor/evals/judge_check.py#L82));
  - `hand-check` exports a blind sample for a person to label.
- **Example.** On the 40 pilot answers, Opus and Sonnet agreed on 97.5% (κ = 0.89), and the ranking
  didn't change ([`docs/archive/northwind/DESIGN.md`](../../northwind/DESIGN.md)). κ corrects for chance agreement:
  - two judges calling 8/10 and 7/10 "correct", agreeing on 9, give κ ≈ 0.74;
  - if they agreed on all 10, κ would be 1.

### Bootstrap confidence interval
- **Plain words.** To see how much a score could wobble, resample your questions *with replacement*
  thousands of times, recompute the score each time, and take the middle 95% of those scores.
- **Here.** [`evals/scoring.py:141`](../../../../packages/tenor/evals/scoring.py#L141) `bootstrap_ci`: 2,000 resamples, seeded.
- **Trade-off.** It assumes the questions are a fair sample of the kind of question you care about.
  With tiny n, the interval is coarse and can sit hard against 100%.
- **Example** (real function, seed 42): 9/10 correct gives CI [70%, 100%]; 7/10 gives [40%, 90%].
  Those overlap, which is why the pilot's ranking proves nothing.

### McNemar's test (paired)
- **Plain words.** When two systems answer the *same* questions, only the questions where exactly
  one was right (the *discordant* pairs) tell you anything. Under "no difference", each discordant
  question is a fair coin flip, so the p-value is a binomial tail.
- **Here.** [`evals/stats.py:70`](../../../../packages/tenor/evals/stats.py#L70) `mcnemar_exact`, plus a *paired* bootstrap of
  the gap ([`:76`](../../../../packages/tenor/evals/stats.py#L76)).
- **Trade-off.** It ignores questions both got right or both got wrong, which is correct but means
  you need many disagreements. Pairing is far more sensitive than checking whether two separate CIs overlap.
- **Example.** Pilot, b2 vs b1: b2-only 3, b1-only 0. P(3 heads in 3 flips) × 2 sides = 0.25. Not significant.

### Holm–Bonferroni correction
- **Plain words.** Testing many pairs makes a false "win" likely somewhere. Holm sorts the p-values,
  multiplies the smallest by m, the next by m−1, and so on, keeping them non-decreasing.
- **Here.** [`evals/stats.py:95`](../../../../packages/tenor/evals/stats.py#L95) `holm`, over all system pairs (and separately within each category).
- **Trade-off.** It controls "any false positive" at α, so it's conservative: real but small gaps
  can be missed. It's always at least as powerful as plain Bonferroni.
- **Example.** p = [0.01, 0.04, 0.03] → adjusted [0.03, 0.06, 0.06]. Only the first survives at 0.05.

### Statistical power
- **Plain words.** The chance a test detects a real gap of a given size. Planning means asking "how
  many questions do I need to have an 80% chance of seeing a 10-point gap?"
- **Here.** [`evals/stats.py:115`](../../../../packages/tenor/evals/stats.py#L115) `mcnemar_sample_size` (Connor 1987) and its
  inverse `mcnemar_mde`. They're driven by the observed **discordance** (how often two systems disagree).
- **Trade-off.** Discordance estimated from a 10-question pilot is noisy, so the plan is
  re-estimated on every report rather than fixed.
- **Example.** At 30% discordance, a 10-point gap needs 234 questions. With the 206 XL test
  questions, the smallest reliably detectable gap is ≈ 10.6 points.

### Pareto frontier
- **Plain words.** A system is *dominated* if another is at least as good on every axis (accuracy,
  cost, optionally latency) and strictly better on one. The undominated ones form the frontier,
  the only rational choices.
- **Here.** [`evals/pareto.py:52`](../../../../packages/tenor/evals/pareto.py#L52) `frontier`. Write-time
  ingest is amortised: `$ per q = answer $ + ingest $ / volume`, and `breakeven_volume` finds where
  write-time memory becomes cheaper.
- **Trade-off.** The answer depends on the volume you assume. That's why the analysis shows several.
- **Example** (real function): Concentrate-like 90% at $0.01/q answering plus $40 ingest; b0-like 80% at $0.05/q.
  - Break-even is 1,000 questions.
  - At 100 questions, Concentrate costs $0.41/q, and both are on the frontier.
  - At 2,000 questions, it costs $0.03/q and dominates b0.

### Precision / recall for lists
- **Plain words.** When the answer is a *set* of items:
  - **precision** = share of what you returned that's right;
  - **recall** = share of the right items you returned;
  - **F1** = their harmonic mean.
- **Here.**
  - **Citations:** [`evals/scoring.py:59`](../../../../packages/tenor/evals/scoring.py#L59) `citation_scores` compares cited
    line IDs with gold IDs.
  - **Exact match** scores the *fraction* of gold values found.
  - **Failure taxonomy** has a `partial_list` type.
  - **Brief eval** matches shown items to gold items one-to-one (greedy, best score first:
    [`evals/brief.py:133`](../../../../packages/tenor/evals/brief.py#L133) `match`, then `SectionScore.metrics`).
- **Trade-off.** Matching free-text items needs a rule (here, word overlap or a cited line within a
  few lines of gold), and that rule shapes the score. Citation precision is undefined if nothing is cited.
- **Example.** Gold lines {a, b, c, d}; a system cites {a, b, x}. Precision 2/3, recall 2/4. Brief
  result: memory recall 91% vs 30% on open commitments, but memory precision 43% vs 90%.

---

## 5. Who is who (Enhance)

### Jaro–Winkler similarity
- **Plain words.** A 0–1 string similarity for short strings like names. It counts characters that
  match within a small window, penalises transpositions, then boosts strings that share a prefix (up to 4 chars).
- **Here.** [`enhance/strings.py:46`](../../../../packages/tenor/enhance/strings.py#L46), combined in `name_similarity` ([`:188`](../../../../packages/tenor/enhance/strings.py#L188)).
- **Trade-off.** It's spelling-based, so sound-alikes with different letters score low, and short
  names score high against each other.
- **Example** (real function): "meghan"/"megan" 0.961; "coffee"/"kofi" only 0.611.

### Double Metaphone
- **Plain words.** Turns a word into a short code for how it *sounds* (with an alternate code for
  ambiguous spellings). Words that sound alike get the same code.
- **Here.** [`enhance/strings.py:60`](../../../../packages/tenor/enhance/strings.py#L60), used in `sounds_alike`. If two names
  sound alike, `name_similarity` lifts the score to `0.55 + 0.45·JW`.
- **Trade-off.** Codes are coarse. "dana"/"dan" both code `TN` and score 0.974, even though the
  `name_similarity` docstring says look-alikes like that "stay lower". Context (attendees, directory)
  has to break such ties.
- **Example.** "coffee" → `KF`, "kofi" → `KF`, so the score is 0.55 + 0.45·0.611 = 0.825. That's
  enough to link the ASR "Coffee" to Kofi.

### Label propagation
- **Plain words.** When you know the labels of a few nodes, spread them to neighbours that are
  likely to share a label, and repeat until nothing changes.
- **Here.** [`enhance/speakers.py:437`](../../../../packages/tenor/enhance/speakers.py#L437) `_diarized`: in in-person meetings with iOS
  diarization ("Speaker A/B"), whole clusters are assigned to distinct people jointly. Every
  permutation is scored, the lines are re-scored with the new labels, and this repeats up to 4 rounds.
  One confident line can name the whole cluster.
- **Trade-off.** Errors propagate too: one wrong anchor mislabels a whole cluster. Trying every
  permutation only works because clusters and attendees are few.
- **Honest result.** On the base fixtures, the 22 diarized in-person lines currently get **0%
  coverage** (all fall below the 0.6 threshold), and in-person accuracy overall is 21%, *below* the
  "always the owner" baseline of 64% (`python -m concentrate.enhance.evaluate data/fixtures/northwind`).

### Forward–backward
- **Plain words.** For a *chain* of uncertain labels where neighbours influence each other (a
  hidden Markov model), you can compute each position's exact probability:
  - a **forward** pass sums over everything to the left;
  - a **backward** pass sums over everything to the right;
  - you multiply the two.
- **Here.** [`enhance/speakers.py:253`](../../../../packages/tenor/enhance/speakers.py#L253) `_remote_chain`. Consecutive
  unnamed "Them" lines form a chain. Adjacent lines prefer the same speaker, strongly if the sentence
  continues (odds 6.5:1) or after a one-word lead-in (25:1). Named lines act as evidence.
- **Trade-off.** It's exact and doesn't feed a line's own guess back to itself (naive iterative
  relabelling does). But it only couples *neighbours*, and the transition weights were set on the base set.
- **Example.** Lines: [Them "Sara here, quick update"], [Them "so the export…"],
  [Them "…ships Friday"]. Line 1's self-reference cue ("Sara here", +4 log-odds) makes it almost
  certainly Sara, and the forward pass carries that forward; the continuation weight makes
  lines 2–3 very likely Sara too. On base, remote unnamed lines reach 91% precision on the 63% they resolve.

---

## 6. Storing facts you can trust

### Grounding / entailment
- **Plain words.** *Grounded* = every claim points to its source. *Entailed* = the source actually
  supports the claim (not just mentions the words).
- **Here.** The gate ([`memory/verify.py`](../../../../packages/tenor/memory/verify.py)):
  1. cited lines must exist in this meeting;
  2. the quote must be a substring of them;
  3. numbers and dates in the value must be *said* in them;
  4. only uncertain items go to an LLM entailment check (`entail.v2`), which fails closed.
- **Trade-off.** Deterministic checks are free but literal: they can't verify "the CFO" means Ruth.
  The entailment model is another LLM that can be wrong. The gate trades recall (dropping true facts)
  for precision.
- **Example** (real function): `check_value("25", "we moved to twenty five dollars a seat")` passes.
  `check_value("30", …)` fails with *"number 30 not said in cited lines"*.

### Bitemporal data
- **Plain words.** Two clocks per fact:
  - **valid time:** when it was true in the world;
  - **transaction time:** when we learnt it.

  That lets you ask both "what was true on D?" and "what did we *believe* on D?".
- **Here.** `facts.valid_from / valid_to` and `recorded_at / valid_to_recorded_at`
  ([`memory/store.py`](../../../../packages/tenor/memory/store.py)). The query is `Fact.live_at(day, known_at)` ([`:92`](../../../../packages/tenor/memory/store.py#L92)),
  and the answer path filters on `recorded_at ≤ as_of` so a later meeting can't leak into an "as of" answer.
- **Trade-off.** It adds more columns and harder queries. Dates are day-granular strings.
- **Example** (run it in [EXERCISES.md](EXERCISES.md) #4): price $20 from Sept 1, $25 said on Sept 29.
  - "As of Oct 1" → $25.
  - "As of Oct 1, but known on Sept 20" → $20 (we hadn't heard yet).

### Supersession
- **Plain words.** A new value doesn't overwrite the old one; it *closes* it (`valid_to` = new start)
  and links `superseded_by`. History stays queryable.
- **Here.** [`memory/store.py:246`](../../../../packages/tenor/memory/store.py#L246) `add_fact`:
  - same value → **restated** (evidence added; confidence combined as 1 − (1−a)(1−b));
  - new value later → **superseded**;
  - different value the same day → **contradiction** (both flagged, nothing resolved).
- **Trade-off.** "Same value" is a normalisation rule (`norm_value`: lowercase alphanumerics, so "$25" = "25").
  A value phrased differently ("25 per seat" vs "25") would create a false supersession.
- **Example.** $20 (0.8) → $25 (0.8) → "25 dollars" (0.8): inserted, superseded, restated. $25
  ends with confidence 0.96 and two evidence lines.

### Compression tiers (L0–L3)
- **Plain words.** Keep the same meeting at several levels of detail:
  - **L0:** raw transcript;
  - **L1:** enhanced transcript;
  - **L2:** verified facts;
  - **L3:** a digest.

  Then measure what you lose by keeping only the small ones.
- **Here.** Each meeting stores token counts per tier ([`memory/pipeline.py:329`](../../../../packages/tenor/memory/pipeline.py#L329)).
  `build_context(retire_after_days=N)` drops L0/L1 for older meetings at answer time
  ([`memory/query.py:149`](../../../../packages/tenor/memory/query.py#L149)). Facts keep their line IDs as dangling provenance.
- **Trade-off.** Anything not extracted into L2/L3 (exact wording, tone, an offhand remark) is gone for good.
- **Example.** The retirement workstream's hand-over note (branch `claude/ws-c-retirement-…`, not
  merged here) reports L2+L3 ≈ 31k tokens vs 624k raw on XL (~5%), and Concentrate 89% → 88% on XL dev
  as N goes from ∞ to 0. Its caveat: there's no question category that needs verbatim quotes.

---

## 7. Surviving long runs

### Watchdog (and heartbeat)
- **Plain words.**
  - A *heartbeat* is a regular "still making progress" signal.
  - A *watchdog* is a separate timer thread that acts when heartbeats stop, without needing to know why.
- **Here.** [`watchdog.py`](../../../../packages/tenor/watchdog.py): `beat()` is called after every API reply, retry or cache
  hit. With 15 minutes of silence, the process dumps every thread's stack and calls `os._exit(75)`
  (`EX_TEMPFAIL`), and a wrapper loop restarts it.
- **Trade-off.** A legitimately slow step longer than the threshold gets killed. `os._exit` skips
  cleanup on purpose, because the stuck main thread would never see a polite `sys.exit`.
- **Example.** On 2026-11-02, three builds froze for 30+ minutes at 0% CPU. With no timeout, a dead
  socket could block for up to 5 × 600 s = 50 minutes. Now: a 120–300 s read timeout, a 20-minute
  deadline, and a 15-minute watchdog.

### WAL / crash recovery
- **Plain words.** A *write-ahead log* records each change durably *before* it counts as done. After a
  crash, you replay the log and discard any half-written tail. (SQLite has a real WAL mode; this
  project doesn't turn it on.)
- **Here.** Two mechanisms play that role:
  - `progress.jsonl`, an append-only log of finished items. Mem0 and Graphiti resume from it;
    Concentrate also writes one, but only as a log.
    [`agents/progress.py:21`](../../../../packages/tenor/agents/progress.py#L21) `read_progress` drops a **torn last line** and
    truncates the file back to the last complete row; a corrupt *middle* line raises.
  - Concentrate commits each meeting in **one SQLite transaction** (default rollback journal), so
    the store itself is the progress record.
- **Trade-off.** Recovery only works at item granularity. HARDENING.md lists the gaps:
  - Mem0/Graphiti have a one-chunk double-write window on kill;
  - abandoned API attempts may still be billed, and the ledger can't see that.
- **Example.** Killed mid-append, the file ends in `{"id": "m7", "co`. The next read logs "dropping a
  torn final row", truncates those bytes, and m7 is redone.

---

## 8. Judging the benchmark itself

### Type/token ratio (TTR)
- **Plain words.** Distinct words ÷ total words, measured in fixed-size windows (TTR depends on length).
  Low TTR means repetitive text.
- **Here.** [`synth/fidelity.py:71`](../../../../packages/tenor/synth/fidelity.py#L71) `ttr`, at 2k and 5k tokens; `concentrate fidelity`.
- **Trade-off.** It's a crude proxy for "sounds like real people". Real speech is repetitive too.
- **Example.** XL v1 TTR@2k was .11; v2 is .21 ([`docs/archive/northwind/FIDELITY_V2.md`](../../northwind/FIDELITY_V2.md)). On the base set, the `remote` section prints `ttr_2k` 0.224.

### N-gram duplication
- **Plain words.** How often passages repeat word-for-word (exact duplicates), or share the same
  first six words (a template signature).
- **Here.** `dup6_exact` and `dup6_first6` in `corpus_stats` ([`synth/fidelity.py:136`](../../../../packages/tenor/synth/fidelity.py#L136)).
- **Trade-off.** It catches templating but not subtler sameness. The thresholds (6 words) are a judgement call.
- **Example.**
  - v1: 78% of long XL passages were exact copies of 69 filler sentences, so BM25 could cheat.
  - v2: exact duplicates fell to 4.9%, but 54% still share their first six words, against 0.6% in real meetings.

### Distractor realism
- **Plain words.** A retrieval test is only hard if the wrong documents look plausible: same topics,
  similar names, nearby numbers.
- **Here.**
  - v2 filler names the real projects and customers ([`synth/filler.py`](../../../../packages/tenor/synth/filler.py) docstring).
  - XL adds near-miss customers and a third "Sam" ([`synth/roster.py:119`](../../../../packages/tenor/synth/roster.py#L119)).
  - Topic names in filler are protected from ASR noise so "Beacon Freight" isn't split.
- **Trade-off.** Distractors must stay **fact-free**, or they'd change the gold answers. So they're
  plausible in topic but empty in content, which a strong reader may notice.
- **Example.** "Beacon" (internal project) vs "Beacon Freight" (customer): a question about one
  must not be answered from the other.

### Dev/test split and pre-registration
- **Plain words.** Tune only on *dev* questions and run *test* once, so the score isn't fitted to the
  exam. *Pre-registration* means writing down what you'll measure and how before you see results.
- **Here.**
  - Questions are stratified by category into dev/test: base 71/159, XL 90/206.
  - CLAUDE.md forbids tuning on test.
  - The enhancer's weights were set on base only, with XL as the held-out check.
  - Every report records git SHA, prompts, seed and split.
  - The README commits to publishing XL test numbers "good or bad".
- **Trade-off.** It's slower: you can't iterate on the exam you report. And there's no formal,
  timestamped pre-registration document. The closest things are PHASE4_DESIGN.md (written before
  Concentrate existed) and the README's status note.
- **Example.** The 10-question pilot was **dev**, and `analyze` labels it "exploratory, not the headline".

---

## 9. Protocols

### MCP: tools vs resources, stdio
- **Plain words.** The Model Context Protocol lets an AI client use external servers:
  - **tools** are functions the model *calls* with arguments ("get_facts(subject=…)");
  - **resources** are read-only documents the client can *fetch* by URI;
  - **stdio** transport means the client launches the server as a subprocess and they talk JSON-RPC
    over stdin/stdout.
- **Here.**
  - *As a client:* `GranolaMCPSource` calls only five read tools of Granola's MCP (OAuth, browser
    sign-in once) and never write tools ([`sources/granola_mcp.py`](../../../../packages/tenor/sources/granola_mcp.py)).
  - *As a server:* `concentrate mcp-serve` exposes six read-only tools and one resource,
    `concentrate://memory` ([`mcp_server/server.py:59`](../../../../packages/tenor/mcp_server/server.py#L59)). The store
    is opened with SQLite `mode=ro`.
- **Trade-off.** On stdio, stdout *is* the protocol, so nothing may print there (logs go to
  stderr). Tool descriptions are prompt text, so their wording changes agent behaviour.
- **Example.** `resolve_entity("Sam")` returns three candidates (Patel, Okafor, Rivera) and asks
  the agent to choose, instead of guessing.
