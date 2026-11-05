# Deletion simulation: what auto-deleting transcripts costs, measured from outside

Status: **DEV run done** (47 seen questions, 2026-11-04, $6.42, approved cap $9). Results below. The test split has not run: no test questions exist yet.
Code: [`evals/deletion_sim.py`](../../packages/concentrate/evals/deletion_sim.py), CLI `concentrate deletion-sim`.
Tests: [`tests/test_deletion_sim.py`](../../tests/test_deletion_sim.py) (fakes only, no network).
AMI replication: **planned**, quoted, not run ([below](#ami-replication-planned)).

## The question

Granola lets users turn on transcript auto-deletion. Its own help text says chat will then be limited to whatever is in the notes, and chat quality will be degraded.

How much worse does it get, on which kinds of question? And when it is wrong, does it say "I don't know", or does it answer wrong with confidence? What would recover the loss?

We can't switch deletion on inside Granola's agent and watch. So we rebuild the four situations from the outside. Each uses the same real questions, the same answer model and prompt, and the same judges.

## The four conditions

Each condition after A adds one layer to the one before it.

| | Condition | What the answer model sees | What it approximates |
|---|---|---|---|
| A | `granola` | nothing: Granola's own agent answers (`query_granola_meetings`, black box) | Granola today, transcripts kept |
| B | `notes_only` | Granola's notes (AI summary + typed notes) for **every** meeting held by the as-of date | Granola chat **after** deletion |
| C | `notes_facts` | B + Concentrate's verified facts, commitments and digests, every transcript deleted | deletion with a fact layer extracted first |
| D | `notes_facts_fallback` | C + transcript passages, only when C's answer abstains or says the material doesn't cover it | the hot transcript deleted, a cold copy read on demand |

B, C and D all use `answer.v1` on the same answer model as every other system. A is Granola's own agent and model.

### Where the notes come from

The notes are Granola's own AI notes, as the public MCP returns them (`get_meetings`). We use the copy already synced into `data/real.db` by `concentrate sync --source granola-mcp`. That copy came from the same MCP, so no live call is needed. Only reads were ever made (the MCP source is read-only by construction).

### What each condition can and can't tell us

- **A is the only real Granola.** Everything else is our reconstruction. We can't see Granola's internals: its retrieval, its prompts, or how its agent actually behaves once transcripts are gone.
- **B approximates Granola's degraded chat. It is not Granola's actual post-deletion behaviour.**
  - B sees *all* the notes, not a retrieved subset. With 4 real meetings (about 4k tokens of notes) there is nothing to retrieve, so B is an **upper bound** for a notes-only agent: it can't miss a note by searching badly.
  - B uses our answer model, not Granola's. So A − B mixes "lost the transcripts" with "different model and prompt". C − B and D − C don't have this problem, because B, C and D share the model and prompt.
  - Granola's real degraded chat could be better than B (a better prompt) or worse (retrieval misses at scale).
- **C keeps what the fact layer kept.** Facts were extracted from the transcripts *before* deletion: that is the point of a fact layer. Their line IDs stay as provenance pointers, but no transcript line is shown.
  - Deletion uses `retire_after_days = -1`, so even a meeting held on the as-of day loses its transcript. `0` would keep same-day transcripts.
- **D is for reference: it needs the transcripts to exist.** D's first call is exactly C's request, so it costs nothing extra after C has run.
  - The fallback fires only when C hedges or abstains. So D **can't recover a confident wrong answer**, by design. If C's confident-error rate is high, that is the fallback's blind spot, and the run will show it.

## What is measured, per answer

- **Primary verdict.** `judge.v1` on the answer model, against the user's gold answer, exactly as in the [real slice](../results/REAL_SLICE.md).
- **Second judge.** The same `judge.v1` request on a different model (default `claude-sonnet-5-5`). Reported: second-judge accuracy and the agreement rate.
- **Confident error.** Judged `incorrect` *and* neither abstained nor said the material doesn't cover it.
  - "Doesn't cover it" uses `says_not_covered`, the same detector the fallback uses. It also reads Granola's prose, since Granola has no abstain flag.
  - A hedged "the notes don't say", judged incorrect, counts as a **hedged miss**, not a confident error.
- **Tags.** From P2-T's tag file (`--tags`; JSON `{qid: [tags]}` or JSONL `{"qid", "tags"}`). If there is no file, every answer is `(untagged)`.
- **Summary.** Per condition, and per category and tag: accuracy and confident-error rate (bootstrap 95% CIs), partial share, hedged-miss share, and how often the fallback fired.
  - Paired wins and losses along the ladder: B vs A, C vs B, D vs C, on the same questions.

Files: per-answer rows go to `data/real/reports/<stamp>.deletion.jsonl` (private). The aggregate goes to `<stamp>.deletion.aggregate.json`, checked by `write_committable` (no question, answer or meeting ID can leak).

## Splits

- `--split dev`: the 47 verified questions already seen while building. Granola's answers to 39 of them are recorded; 8 would be asked live (hosted, $0 to us).
- `--split test`: the user's hand-written held-out questions, imported with `concentrate real-kit import-test data/real/test_questions.md`. None are imported yet.
  - Until they are, the quote prices 30 test questions (`--test-n`) at the dev per-question rate.

## The exact cost quote

Run with `concentrate deletion-sim --quote --split dev|test`. It makes no paid calls.

How it is counted:
- **Input tokens are counted exactly.** Every request the system would send is built and counted with the free `count_tokens` endpoint.
  - Add the structured-output overhead that counting misses: answer +255, judge +246 tokens per call. That is the median of recorded minus counted tokens over 36 cached real-slice answer calls and 146 cached judge calls.
  - The last estimate was about 2× low because it used chars/3.5 and fixed output guesses.
- **Cached requests are $0.** That covers Granola's 39 recorded dev answers and their judge verdicts. An identical request (D's first call = C's) is paid once.
- **Output tokens are not knowable in advance.** "Expected" uses the recorded mean (answer 261, judge 65, thinking included). "High" uses the 90th percentile (456 / 84).
- **Fallback.** Expected: it fires on 47% of first answers, Concentrate's measured rate. High: on every answer.
- **Judge inputs** are exact when the answer is recorded (Granola's). Otherwise they are counted with an empty answer plus the mean recorded answer length.
- **Prices.** Answer and judge model `claude-opus-5-5` ($4 / $20 per M), second judge `claude-sonnet-5-5`, interactive (no batch discount).

| Condition | dev (47 q) expected | dev high | test (30 q, scaled) expected | test high |
|---|---|---|---|---|
| A: Granola full (judges only) | $0.05 | $0.06 | $0.20 | $0.22 |
| B: notes only | $1.56 | $1.77 | $0.99 | $1.13 |
| C: notes + fact layer | $2.73 | $2.94 | $1.74 | $1.87 |
| D: + transcript fallback | $1.93 | $3.95 | $1.23 | $2.52 |
| **Total** | **$6.27** | **$8.71** | **$4.17** | **$5.74** |

- Per condition, the second judge is about $0.10 on dev.
- C's answer calls are the largest item: about 11.5k input tokens per question, the notes plus the memory context.

## DEV results (47 seen questions, real meetings)

**DEV: these questions were seen while building Concentrate, and their gold answers were bulk-approved.** Treat the numbers as where to look, not as claims.

- Run `20261104T012414Z` at git `6dc2cb6`, seed 42. Answer model and primary judge `claude-opus-5-5`; second judge `claude-sonnet-5-5` (188 of 188 answers re-judged).
- Tags come from P2-T's portable rule (`notes_vs_transcript.real_tags` at `9acda8c`), recomputed privately: 23 notes-sufficient, 18 transcript-needed, 6 unanswerable.
- Aggregate: [`evals/reports/real-deletion-sim-dev.aggregate.json`](../../evals/reports/real-deletion-sim-dev.aggregate.json). Per-question detail stays in `data/real/` only.
- CIs: 95% bootstrap. Sign test: exact two-sided, on discordant pairs.

### Per condition

| Condition | n | Accuracy [95% CI] | Confident error [95% CI] | Hedged miss | Fallback fired | 2nd-judge accuracy | Judge agreement |
|---|---|---|---|---|---|---|---|
| A: Granola full | 47 | 89% [79–98] | 6% [0–15] | 4% | n/a | 85% | 96% |
| B: notes only | 47 | 57% [43–70] | 0% | 43% | n/a | 60% | 96% |
| C: notes + fact layer | 47 | 66% [51–79] | 0% | 34% | n/a | 64% | 98% |
| D: + transcript fallback | 47 | 87% [77–96] | 0% | 13% | 60% (28/47) | 87% | 100% |

Partial answers (neither correct nor counted as a miss above) were 4% / 9% / 4% / 6%.

Granola's 6% confident error is the same under P2-T's flag-only definition (answered and judged incorrect).

### By tag (accuracy; confident error)

| Tag | n | A Granola | B notes only | C + facts | D + fallback |
|---|---|---|---|---|---|
| notes-sufficient | 23 | 91%; 0% | 57%; 0% | 65%; 0% | 83%; 0% |
| transcript-needed | 18 | 94%; 6% | 44%; 0% | 56%; 0% | 89%; 0% |
| unanswerable | 6 | 67%; 33% | 100%; 0% | 100%; 0% | 100%; 0% |

### Paired comparisons (same questions)

| Comparison | Slice | wins / losses | Δ accuracy | sign test p |
|---|---|---|---|---|
| **B vs A: what deletion costs** | all (47) | 2 / 17 | −32 pts | 0.001 |
| | notes-sufficient (23) | 0 / 8 | −35 pts | 0.008 |
| | transcript-needed (18) | 0 / 9 | −50 pts | 0.004 |
| | unanswerable (6) | 2 / 0 | +33 pts | 0.50 |
| **C vs B: what facts recover** | all (47) | 4 / 0 | +9 pts | 0.13 |
| | notes-sufficient / transcript-needed | 2 / 0 each | +9 / +11 pts | 0.50 each |
| **D vs C: what the fallback adds** | all (47) | 10 / 0 | +21 pts | 0.002 |
| | notes-sufficient (23) | 4 / 0 | +17 pts | 0.13 |
| | transcript-needed (18) | 6 / 0 | +33 pts | 0.031 |

### Reading (DEV, n = 47; read with the caveats above)

- **Deletion costs a lot, and more where the transcript is needed.** On transcript-needed questions, notes-only drops 50 points against Granola. The loss is significant even overall (17 losses to 2).
  - Part of the A − B gap is our model and prompt, not deletion (see "What each condition can and can't tell us").
- **Without transcripts, our answer model hedges; it doesn't bluff.** None of B's or C's incorrect answers was confident: each one abstained or said the material doesn't cover it.
  - Granola, with transcripts, produced the only confident errors: 2 of its 6 unanswerable questions got a confident answer.
  - So on these questions deletion turns right answers into *refusals*, not into confident errors. That is a property of `answer.v1` on this model. Granola's own degraded chat may behave differently, and we can't observe it.
- **The fact layer alone recovers little.** C vs B: +9 points, 4–0, not significant at n = 47.
- **The fallback recovers most of the loss.** D reaches 87% against Granola's 89%. Because C never bluffed, the fallback's blind spot (it can't fire on a confident error) didn't come up here.
  - But D reads transcripts, so it is "keep a cold copy", not deletion.
- **Bad for the tag rule:** notes-only scores only 57% on *notes-sufficient* questions.
  - Either the string-matching rule over-calls notes-sufficient, or the answer model under-uses the notes. This run can't tell which.
- **Unanswerable has n = 6.** Don't read anything into its row.

### Spend vs the quote

- **Actual: $6.42.** That is $6.13 for answers and the primary judge, plus $0.29 for the second judge. The quote was $6.27 expected, $8.71 high: 2% over expected.
- **Input tokens matched the quote exactly** for B (248,707) and C (541,338).
- The overshoot came from the fallback. It fired on 60% of answers, not the 47% assumed, and outputs ran a little long.

## AMI replication (planned)

The result above rests on the author's 4 private meetings, so nobody else can check it. This replicates it on the public [AMI corpus](AMI_DESIGN.md): AMI meetings played into Granola and Wispr Flow. It can be released, and n grows as more AMI meetings are recorded.

Status: **built and quoted; nothing paid has run.** Code: [`evals/deletion_ami.py`](../../packages/concentrate/evals/deletion_ami.py), CLI `concentrate ami deletion-sim`. Tests: [`tests/test_deletion_ami.py`](../../tests/test_deletion_ami.py) (fakes only).

### Conditions

Same ladder, one product comparison added.

| | Condition | What is answered from | Answered by |
|---|---|---|---|
| A | `granola_chat` | Granola's own Chat, transcripts kept (answers recorded once, replayed at $0) | Granola |
| B | `notes_only` | Granola's notes for every recorded meeting | our model, `answer.v1` |
| B′ | `notes_only_wispr` | the same, Wispr Flow's notes | our model, `answer.v1` |
| C | `notes_facts` | B + Concentrate's fact layer, extracted from Granola's transcript, then every transcript deleted | our model, `answer.v1` |
| D | `notes_facts_fallback` | C + Granola's transcript, read only when C hedges or abstains | our model, `answer.v1` |

- **Same code as the original.** B, C and D are the original classes; only the inputs change.
- **Same answer model:** read from the original's meta (`claude-opus-5-5`).
- **One judge for all five,** A included: stage 3's `judge.v1` on `claude-sonnet-5-5`, gold + AMI speaker roster, product boilerplate stripped ([WRONG_FACTS.md](WRONG_FACTS.md)). A's judge requests are stage 3's, byte for byte, so they replay from cache.
- **Paired:** B vs A, B′ vs B, C vs B, D vs C. Exact sign tests, bootstrap and Wilson 95% CIs, by AMI question type and by tag (DEV/TEST, one- vs two-meeting).

### What grows with the data

- **Meetings:** every AMI recording the synced DBs match (`match_recordings` + `recording_ids.txt`). No meeting list in the code.
- **Questions:** any AMI question whose meetings both apps recorded and that Granola Chat answered. Today: 68 questions on 5 meetings (13 ES2006a questions wait for Chat answers).
- **Figures:** `ami_retention_steps` (with a Granola vs Wispr notes-only panel), `ami_answer_types`, `ami_question_heatmap`. They read n from the aggregate (`evals/reports/ami/deletion-sim.aggregate.json`) and appear once it exists.

### Quote (2026-11-05, $0 to compute)

`concentrate ami deletion-sim --quote`. Inputs counted with the free `count_tokens`; output and fallback rate from the real DEV run.

| Run | Expected | High |
|---|---|---|
| Pilot: 5 DEV questions × 5 conditions, fact layer over 1 meeting | $0.97 | $1.23 |
| Full: 68 questions × 5 conditions, fact layer over 7 recordings | $13.08 | $16.50 |

- **Largest items:** C ($4.25) and D ($3.17 expected, $5.48 if the fallback always fires). B and B′ are $2.56 each: every question reads all 7 meetings' notes (~7.5k tokens).
- **ASSUMED until built:** C's memory context is priced at 6,226 tokens/question (C − B on the real run). The fact-layer build ($0.53, high $0.79) is a calibrated model estimate, not a count. After the pilot or build, re-run `--quote`: C and D become exact.
- **A costs $0:** answers are recorded and all 68 judge verdicts are cached from stage 3.
- **No second judge by default** (`--judge2 claude-opus-5-5` adds one).

### Commands

```bash
uv run concentrate ami deletion-sim --quote
uv run concentrate ami deletion-sim --pilot              # hard cap $1.50, asks first
uv run concentrate ami deletion-sim --max-usd 17 --commit-to evals/reports/ami/deletion-sim.aggregate.json
```

Every client (answers, judge, fact-layer build) shares one ledger and one hard cap (`LLMClient(max_usd=...)`). A run refuses to start if the quote's high estimate exceeds the cap. Per-answer rows stay in `data/ami/results/deletion/` (gitignored); the aggregate is checked by `check_committable` (no question, gold or answer text).

### Threats to validity

- **Silent replay: no typed notes.** AMI is played in with nobody typing, so "notes" are AI notes only. Real users' typed notes could make B stronger.
- **Mostly role-play.** IS1008 is a scenario series (actors designing a remote control); IB4003 is the only non-scenario meeting with questions. Real meetings have more context and less structure.
- **AMI gold is summary-level.** Gold answers are annotators' summary sentences, not facts checked against the audio; lists must be complete to score "correct", which favours partial verdicts.
- **One human verifier so far.** The calibrated judge was checked by one person (21 rows, `human_verification_v3`).
- **Small n.** 68 questions, 13 DEV; most of the ladder steps will not reach p < 0.05.
- **A − B still mixes model and deletion,** as in the original. B′ − B is clean: same model, same prompt, only the app's notes differ.
- **B sees every meeting's notes** (an upper bound, as in the original). As meetings grow it becomes a long-context test; a retrieval variant may be needed past ~30 meetings.
- **The fact layer has no AMI people directory:** only attendee metadata (the account owner), the same method as the original.

## Why outside-in

- Granola's engineers can measure deletion from the inside. An outside benchmark measures what a *user* (or an agent over the public MCP) actually gets.
- It uses only Granola's public surface: the public MCP, read-only, and its own notes. So anyone with a Granola account can re-run it on their own meetings.
- It makes the trade-off concrete, per question type: where do notes suffice, and where does deletion turn a right answer into a confident wrong one? It also tests whether a fact layer extracted before deletion recovers the loss.

## Commands

```bash
uv run concentrate deletion-sim --quote --split dev
```

```bash
uv run concentrate deletion-sim --split dev --tags data/real/tags.json --budget 9
```

The run asks for confirmation unless `--yes` is passed, and it stops at `--budget`.
