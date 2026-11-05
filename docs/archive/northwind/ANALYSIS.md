# Analysing a report: `concentrate analyze`

`concentrate eval` writes `evals/reports/<stamp>.{json,jsonl,md}`: per-system accuracy with a bootstrap CI, cost and latency. That's enough to rank systems, but not to trust the ranking. `concentrate analyze <report>` reads a saved report after the fact (no re-run, no API calls) and writes:

| File | Contents |
|---|---|
| `<stamp>.analysis.md` | plain-language summary, then the four sections below |
| `<stamp>.analysis.json` | the same numbers, machine-readable (every pair, every category) |
| `<stamp>.failures.jsonl` | one line per wrong answer: primary failure type and all signals |

```bash
uv run concentrate analyze evals/reports/20261102T100403Z
```

Options:
- `--fixtures`: where the ground truth lives (defaults to the report's `meta.fixtures`, else `data/fixtures/northwind`);
- `--volume`: questions per corpus for amortising ingest (default 1,000);
- `--seed`: bootstrap seed (defaults to the report's seed);
- `--llm [--estimate]`: the optional failure classifier.

Same report, fixtures and seed give byte-identical output. Code lives in `concentrate.evals.{stats,pareto,failures,analyze}`.

## 1. Paired significance

Every system answers the same questions, so each comparison is **paired**. For systems A and B, each question lands in one cell:

| | B right | B wrong |
|---|---|---|
| **A right** | both | A only |
| **A wrong** | B only | neither |

- **McNemar's exact test.** Questions both systems got right, or both got wrong, say nothing about which is better; only the *A only* and *B only* counts do. If A and B were equally good, each disagreement would be a fair coin flip, so the p-value is an exact binomial tail: `2 · P(X ≤ min(A only, B only))`, with X ~ Binomial(A only + B only, ½). We use the exact test, not the χ² approximation, because disagreement counts are small.
- **Paired bootstrap CI.** We resample questions with replacement 2,000 times. Each resample keeps both systems' answers to a question together, and we take the 2.5th and 97.5th percentiles of accuracy(A) − accuracy(B).
- **Holm–Bonferroni.** With 7 systems there are 21 pairs. Testing 21 pairs at α = 0.05 finds about one "significant" gap by luck even if all systems are equal. Holm's correction:
  - sort the p-values from smallest to largest;
  - multiply the i-th smallest (1-based) by (m − i + 1);
  - take a running maximum so the adjusted values stay in order.

  This bounds the chance of *any* false positive across the family at α, and it is never less powerful than plain Bonferroni. Overall pairs form one family; each category's pairs form their own family.
- **Reporting.** A pair is reliable (✅) when its Holm-adjusted p < 0.05. The summary phrases it as "A beats B by X points [CI], p=… (Holm)".

**Why not compare the two systems' CIs?** Overlapping per-system CIs are often read as "no difference", and that reading is wrong in two ways:
- Each per-system CI includes question-difficulty noise: which questions happened to be sampled. That noise is shared by both systems and cancels in a paired difference.
- Two 95% intervals can overlap even when the difference is significant.

The paired test only looks at the questions where the systems disagree, which is where the information is.

## 2. Power and sample size

Before believing "no reliable difference", ask whether the test could have found one. For a paired test, the quantity that matters is the **discordance** ψ: the share of questions where exactly one of the two systems is right. For a gap δ, Connor's (1987) normal approximation gives the number of questions needed at α = 0.05 and 80% power:

n = (z₀.₉₇₅ · √ψ + z₀.₈ · √(ψ − δ²))² / δ²

A gap of δ needs ψ ≥ δ, so smaller ψ values are raised to δ. The report shows, per pair:
- the observed ψ;
- n for 5- and 10-point gaps;
- the **minimum detectable effect** (MDE) with the current n, and with 120 or 206 questions.

A "typical case" table compares four designs:
- paired, at the median observed ψ;
- the same with Bonferroni over all pairs (conservative);
- paired, if errors were independent;
- an unpaired two-proportion test.

A Monte Carlo test (`tests/test_analysis.py`) checks that the formula delivers about 80% power with the exact test.

**Caveat.** ψ estimated from a small pilot is itself noisy. Re-run `analyze` on the full report before quoting sample sizes.

## 3. Cost–accuracy Pareto frontier

The cost of one question: `answer $/q + ingest $ ÷ volume`.
- **Answer $/q** comes from the report rows. It is the system's own calls; the judge is excluded.
- **Ingest** is the one-off memory build recorded in `meta.ingest`. Write-time systems pay it once per corpus, so their cost per question falls as the corpus answers more questions.

A system is **dominated** if another system is:
- at least as accurate;
- at least as cheap (and, in the latency column, at least as fast by p50);
- strictly better on at least one of these.

The undominated systems form the frontier: the only sensible choices, whatever trade-off you prefer. A system whose latency wasn't measured (batched rows) is not compared on latency.

The report also shows frontier membership at 100, 1,000, 10,000 and 100,000 questions. For each write-time vs query-time pair, it gives the **break-even volume**: `ingest ÷ (query-time $/q − write-time $/q)`.

Frontier accuracy is a point estimate. Before calling one system's domination of another real, check the pair in §1.

## 4. Failure taxonomy

Every answer whose verdict is not *correct* (so including *partially correct*) gets tags from deterministic signals. The signals use the report row plus the benchmark's ground truth:
- `questions.jsonl`: gold values, gold lines and sources;
- `world.json`: fact histories with `valid_from`/`valid_to`, and commitments;
- `directory.json`: people, orgs and projects.

The **primary** type is the first tag that applies, in this order:

| Type | Signal |
|---|---|
| `abstained_wrongly` | abstained, but the question has an answer |
| `should_have_abstained` | answered an unanswerable question |
| `stale_value` | the answer states another value from the same fact's history: same `(subject, predicate)` in `world.json` (e.g. the old owner), or a reassigned commitment's original owner or due date, and doesn't contain all gold values. The detail says whether that value is older or newer than the gold one; "newer" means the system answered an "as of" question with a later value |
| `wrong_entity` | names a person, org or project that shares the gold entity's first name or first word (the other Sam; Beacon Freight vs the Beacon project) and doesn't name the gold entity. Gold entities come from the question's sources, its gold values, and names in the question or gold answer. A short name never matches inside a longer one ("Beacon" inside "Beacon Freight") |
| `partial_list` | a multi-value gold answer with some but not all values found (exact-match fraction strictly between 0 and 1), or judged partially correct |
| `retrieval_miss` | cites no gold evidence. If the system cites lines, none of them is a gold line; if it cites only meetings (B1 summaries, Mem0), none is a gold meeting. The detail says `no citations`, `wrong meeting` or `right meeting, no gold line`, and adds `gold meeting not among retrieved` when the row's `retrieved` list shows retrieval itself failed |
| `reasoning_or_reading` | cited gold evidence and still got it wrong |
| `unclassified` | no ground truth could be matched |

If a report question's text doesn't match the fixture question with the same ID, that row's ground truth is ignored, and the analysis says how many questions matched. This protects against analysing an XL report with the base fixtures.

The table shows counts per system and type, with up to three example question IDs per cell (lowest IDs first, so the output is stable). A collapsible list underneath gives every wrong answer with all its signals.

**Known limits.**
- Value matching reuses the exact-match normaliser, which can miss paraphrases.
- `stale_value` and `wrong_entity` only fire when the old value or the look-alike name actually appears in the answer.
- Citations are what the system *claims* to rely on. A system that reads the gold line but cites something else counts as a retrieval miss.

### Optional LLM refinement

`--llm` sends only the answers left in generic buckets (`retrieval_miss`, `reasoning_or_reading`, `unclassified`) to a cheap classifier:
- model: `claude-haiku-5-5`, low effort;
- prompt: `prompts/failure_classify.v1.md`, which asks for the reason first, then the label;
- input: the deterministic signals as evidence.

Labels: retrieval miss, misread evidence, temporal confusion, entity confusion, stale value, partial list, counting or aggregation, hallucination, over-abstention, **judge error** (the answer looks right and the verdict looks wrong), other.

It refines the deterministic types and never overrides them. The command prints an estimate first, asks before spending more than $2, and `--estimate` only prints the estimate. On the pilot it classified 2 answers for $0.0003. An XL report with ~7 systems and ~200 test questions should cost a few cents.
