# How Hindsight measures

This page defines every number Hindsight reports. For each metric you get:
- what it means in plain English;
- why it matters to someone choosing a meeting-memory approach;
- how it is computed (formula and code);
- a worked example from a real report row;
- its known weaknesses;
- its status.

The worked examples come from the 120-question synthetic **test** run [`20261103T002456Z`](../../evals/reports/20261103T002456Z.md), unless they say otherwise. Its analysis is [`20261103T002456Z.analysis.md`](../../evals/reports/20261103T002456Z.analysis.md). Real-slice numbers come from the aggregate-only analyses [`real-20261103T004019Z`](../../evals/reports/real-20261103T004019Z.analysis.md) (Granola's agent vs Concentrate, 39 questions) and [`real-20261103T013240Z`](../../evals/reports/real-20261103T013240Z.analysis.md) (top-k, 47 questions).

**Status key:** **computed** = reported by `eval` since Phase 0. **new** = added in Phase 1 (`concentrate analyze`, $0, from saved rows). **needs runs** = needs new answers or new LLM calls, so it costs money.

Every number on this page is regenerated, for free, by:

```bash
uv run concentrate analyze evals/reports/20261103T002456Z.jsonl
```

## Why there is no single score

One number hides the trade-offs, and the trade-offs are the decision. A team choosing a memory approach is asking things like:
- Is it right often enough?
- Can it show where an answer came from?
- Does it say "I don't know" instead of guessing?
- What does an answer cost, and how long does it take?
- Does it still work on *our* meetings?

A system can be cheap and wrong, or right and slow. Any weighting of the two is a product decision, not a measurement. So Hindsight reports six dimensions side by side, plus a cost/accuracy frontier, and leaves the weighting to the reader.

| | Dimension | Question it answers |
|---|---|---|
| A | [Correctness](#a-correctness) | Is the answer right? |
| B | [Judge reliability](#b-judge-reliability) | Can we trust the grader? |
| C | [Grounding](#c-grounding) | Can it show its work, and did it look in the right place? |
| D | [Knowing what it doesn't know](#d-knowing-what-it-doesnt-know) | Does it decline when it should, and only then? |
| E | [Efficiency](#e-efficiency) | What does an answer cost, and how fast is it? |
| F | [Robustness](#f-robustness) | Does it hold up when conditions change? |

The [statistics](#statistics-used-everywhere) section explains how gaps between systems are tested.

---

## A. Correctness

### A1. Accuracy, with a 95% bootstrap CI

- **Definition.** The share of questions whose answer an LLM judge marks *correct* against a known gold answer. *Partially correct* counts as wrong here.
- **Why it matters.** This is the headline question: how often will a user get the right answer?
- **Computation.** `accuracy = (# verdict == "correct") / n`. The CI resamples the questions with replacement 2,000 times, recomputes the mean each time, and takes the 2.5th and 97.5th percentiles.
  - Judge: `judge.v1` prompt, [`scoring.py`](../../packages/concentrate/evals/scoring.py) `judge`.
  - Summary and CI: `summarize` and `bootstrap_ci` in the same file.
  - Gold answers are planted by the generator (synthetic) or approved by the meeting owner (real).
- **Worked example.** Concentrate got 110 of 120 right: **91.7%, CI [85.8%, 95.8%]**. The agent (b3) got 108: 90.0% [84.2%, 95.0%]. The two CIs overlap almost completely. To compare two systems, use the paired test in [Statistics](#statistics-used-everywhere), not overlapping CIs.
- **Weaknesses.**
  - The judge is an LLM and can be wrong ([B](#b-judge-reliability)).
  - Synthetic gold answers come from the same planted facts an extraction system is built to find. That is a shared blind spot that favours extraction-based systems.
  - The real-slice gold answers were drafted by an LLM and approved in bulk ([`REAL_SLICE.md`](../results/REAL_SLICE.md)).
  - n = 120 is small: a CI is about ±6 points wide.
- **Status.** computed.

### A2. Partial credit

- **Definition.** Accuracy with half a point for *partially correct* answers.
- **Why it matters.** "Three of the four action items" is more useful than nothing. Strict accuracy can't tell an incomplete answer from a wrong one.
- **Computation.** `(# correct + 0.5 × # partially_correct) / n`. Code: [`metrics.py`](../../packages/concentrate/evals/metrics.py) `credit_stats`.
- **Worked example.** Concentrate had 6 partially correct answers, so its partial credit is (110 + 3) / 120 = **94%**. Mem0 had 14 partials and rises from 81% to 87%: it is more often "incomplete" than "wrong".
- **Weaknesses.**
  - The 0.5 weight is arbitrary.
  - Partial verdicts are where the two judges disagree most (52 of the 69 primary-judge partials were confirmed by the second judge).
- **Status.** new.

### A3. Exact-match cross-check

- **Definition.** The share of the gold answer's key values (names, dates, numbers, list items) that a deterministic string matcher finds in the answer. No LLM is involved.
- **Why it matters.** It gives a sanity check on the judge that costs nothing and can't be talked round.
- **Computation.** For each normalised gold value, check whether it appears in the answer (dates in several spellings, numbers as digits or words, people by full or last name). The score is the mean over the values. An unanswerable question scores 1 if the system abstained.
  - Code: [`scoring.py`](../../packages/concentrate/evals/scoring.py) `exact_match`.
  - On the real slice: `key_term_match` in [`real_slice.py`](../../packages/concentrate/evals/real_slice.py) (owner-listed key terms).
- **Worked example.** q002 asks for "the plan for Lighthouse". The gold value is "move onto the new warehouse and retire the old cron jobs". Top-k's answer contains it, so exact match = 1.0, matching the judge's *correct*.
- **Weaknesses.**
  - It misses paraphrases, so it reads low for wordy systems: b3 scores 77% exact match against 90% judged.
  - It cannot detect a wrong claim sitting next to the right value.
- **Status.** computed.

## B. Judge reliability

### B1. Second-judge agreement and Cohen's κ

- **Definition.** A different model (Sonnet 5.5) re-grades every answer with the same prompt. We report how often the two judges agree, and Cohen's κ, which is agreement corrected for chance.
- **Why it matters.** Every correctness number depends on the grader. If two graders disagree often, a 2-point gap between systems means nothing.
- **Computation.** `κ = (p_observed − p_chance) / (1 − p_chance)`. We report it on correct vs not-correct and on the 3-way verdict, plus whether the system ranking changes. Code: [`judge_check.py`](../../packages/concentrate/evals/judge_check.py) `cohen_kappa`; command `concentrate judge-check`.
- **Worked example.** On 840 answers, the judges agreed on correct vs not-correct **97.3% of the time, κ 0.90** (3-way: 96.2%, κ 0.87) ([report](../../evals/reports/20261103T002456Z.judgecheck.claude-sonnet-5-5.md)).
  - The ranking changed only at the bottom: Graphiti and summaries swap places, and they are 1 point apart.
  - The second judge never upgraded a primary *incorrect* to *correct*; 3 answers went the other way (primary *correct*, second *incorrect*).
- **Weaknesses.**
  - Two LLMs can share a bias, so agreement is not correctness.
  - The second judge costs money (about $1.9 here).
- **Status.** computed (synthetic test run and both real-slice reports, κ 0.93).

### B2. Human hand-check agreement

- **Definition.** A human grades a blind random sample of answers. We report agreement with the LLM judge.
- **Why it matters.** It is the only check that doesn't rely on another LLM.
- **Computation.** Planned: a stratified sample of ~50 answers, with the system name hidden, graded by hand, then κ against the primary judge.
- **Weaknesses.** One grader (Kian), small n.
- **Status.** needs runs (human time; $0 API).

## C. Grounding

### C1. Citation precision and recall (line level)

- **Definition.** For systems that cite transcript lines: of the lines cited, the share that are gold evidence lines (**precision**); of the gold lines, the share cited (**recall**).
- **Why it matters.** A user or an auditor can only trust an answer they can check. A citation to the exact line makes checking take seconds.
- **Computation.** `P = |cited ∩ gold| / |cited|`, `R = |cited ∩ gold| / |gold|`, averaged over the questions where each is defined. Code: [`scoring.py`](../../packages/concentrate/evals/scoring.py) `citation_scores`.
- **Worked example.** On q002, top-k cited three lines (`:0072`, `:0125`, `:0126`). The only gold line is `:0072`, so P = 1/3 and R = 1.
  - Means over the run: Concentrate P 0.68 / R 0.89; agent 0.70 / 0.84; top-k 0.64 / 0.89.
- **Weaknesses.**
  - The gold lines are the planted lines. A correct line elsewhere that says the same thing counts against precision.
  - **Summaries, Mem0 and Graphiti cite meetings, not lines** (Mem0 and Graphiti store extracted text, not line IDs). Their `cite_recall` of 0.0 in the base report means "doesn't expose lines", not "cites wrongly"; the analysis marks them `no (meetings only)`.
  - On the real slice there are no gold lines, so citations are scored at meeting level.
- **Status.** computed.

### C2. Faithfulness / hallucination rate

- **Definition.** The share of answers containing a claim that the cited lines don't support.
- **Why it matters.** A confidently wrong answer with a real-looking citation is the most damaging failure for a meeting tool.
- **Computation.** Planned: run the existing entailment checker ([`memory/verify.py`](../../packages/concentrate/memory/verify.py), Haiku) on each answer and its cited lines.
- **Weaknesses.**
  - It needs line citations, so it is n/a for Mem0, Graphiti and summaries.
  - The entailment checker is itself an LLM.
- **Status.** needs runs (experiment X2, about $1–2).

### C3. Source recall: did it pull the right meeting?

- **Definition.** Of the answerable questions, the share where at least one gold meeting was among the meetings the system pulled (**any**). Also reported: the share where every gold meeting was pulled (**all**).
- **Why it matters.** It separates two failures with different fixes:
  - "never looked in the right meeting" is a retrieval problem;
  - "looked there and still got it wrong" is a reading or reasoning problem.
- **Computation.**
  - Gold meetings: the note IDs of the gold line IDs (`not_X:0072` → `not_X`). On the real slice, the owner-approved `evidence_note_ids`.
  - Pulled meetings: each row's `retrieved` list, de-duplicated.
  - `recall_any = mean(gold ∩ pulled ≠ ∅)` and `recall_all = mean(gold ⊆ pulled)`, over answerable questions with known gold meetings.
  - Code: [`metrics.py`](../../packages/concentrate/evals/metrics.py) `source_stats`. The meaning of `retrieved` per system is in `RETRIEVED_MEANS`.
- **What `retrieved` means differs by system,** so the analysis prints it next to every number:

  | System | `retrieved` holds |
  |---|---|
  | top-k, summaries | the k meetings put in context |
  | oracle | the gold meetings, by construction (100%, not a measurement) |
  | agent (b3) | meetings it opened with a get-meeting/transcript tool call; search hits that it didn't open are excluded |
  | Mem0 / Graphiti | source meetings of the memories / facts retrieved |
  | Concentrate | source meetings of the facts (and fallback passages) in context |
  | Granola's agent (real) | the meetings it **cited**: what it read isn't visible from outside |
- **Worked example.** On q559 ("What is the current tables migrated for Atlas?") Graphiti pulled 29 meetings, none of them the gold one, and answered wrong. That is a retrieval miss.
  - Over the run: Concentrate 100% (any) / 95% (all), agent 99% / 88%, top-k 98% / 92%, Mem0 100% / 91%, Graphiti 93% / 80%.
- **What it showed, and its weaknesses.**
  - **It barely separates systems on synthetic data.** Every system pulls at least one gold meeting on ≥93% of questions, so most errors are *not* meeting-level retrieval misses. The red team found Mem0 misses where the right *memory* existed but wasn't retrieved. Meeting-level recall can't see that, because some memory from the right meeting was retrieved.
  - **Pulling more makes it easier.** Concentrate and Graphiti pull ~23 meetings per answer, top-k 10, the agent 3. Read source recall together with "sources pulled" (C4).
  - **On the real slice it is meaningless.** The corpus has 4 meetings, and Concentrate pulls all 4 every time (100% by construction). Granola's agent's 100% / 87% is measured on what it *cited*.
- **Status.** new.

### C4. Sources pulled per answer

- **Definition.** The number of distinct meetings a system put in front of the answering model (mean, median, max).
- **Why it matters.**
  - It is the price of high source recall: more meetings mean more tokens, and more chances to be distracted by an outdated or look-alike fact.
  - For agents, it shows how much they explore.
- **Computation.** `len(set(retrieved))` per row. Code: [`metrics.py`](../../packages/concentrate/evals/metrics.py) `source_stats`.
- **Worked example.** On q002 the agent made 5 tool calls and opened the same meeting 4 times, which counts as **1** source. Over the run the agent averaged 3.1 meetings (max 29), against top-k's fixed 10 and Concentrate's 23.6.
- **Weaknesses.**
  - A "meeting" is not a fixed amount of text. Top-k pulls whole transcripts (81k tokens per question); Concentrate pulls a few facts from each of 23 meetings (8.8k tokens).
  - Compare tokens ([E1](#e1-cost-per-answer-at-equal-prices)) for size, and use this metric for breadth.
- **Status.** new.

### C5. List completeness

- **Definition.** On answerable questions with more than one gold item (lists, aggregations, multi-part changes), the mean share of gold items found in the answer. Also reported: the share with *every* item.
- **Why it matters.** "What did Megan commit to?" is a typical meeting-memory question. A system that reliably finds 7 of 10 items is a different product from one that finds all 10.
- **Computation.** Per question, the [A3](#a3-exact-match-cross-check) exact-match fraction over the question's `answer_norm` items, averaged over questions with `len(answer_norm) > 1`. Code: [`metrics.py`](../../packages/concentrate/evals/metrics.py) `list_stats`.
- **Worked example.** q796, "List everything Megan Liu committed to across all meetings", has 14 gold items. Concentrate's answer contains 10, so completeness = **71%**, and the judge said *partially correct*.
  - Over 32 list questions: Concentrate 87% complete on average (75% fully complete), agent 81% (62%), top-k 80% (69%), Mem0 77% (62%).
- **Weaknesses.**
  - The string matcher misses paraphrased items, so it understates completeness for wordy answers. The agent is 62% fully complete by string match but 88% judged correct.
  - The real slice has no item lists, so this is synthetic only.
- **Status.** new.

## D. Knowing what it doesn't know

### D1. Abstention precision and recall

- **Definition.**
  - **Precision:** of the times a system declined, the share where the question really had no answer.
  - **Recall:** of the unanswerable questions, the share it declined.
  - Also: the **false-abstain rate**, the share of *answerable* questions it declined.
- **Why it matters.** For a memory tool, "I don't see that in your meetings" is far better than an invented answer. But a tool that declines too often is useless.
- **Computation.** `precision = TP / #abstained`, `recall = TP / #unanswerable`, `false_abstain = (#abstained − TP) / #answerable`, where TP = abstained on an unanswerable question. Code: [`metrics.py`](../../packages/concentrate/evals/metrics.py) `abstain_stats` (the base report's `abstain_precision` and `abstain_recall` come from `scoring.summarize`).
- **Worked example.** q843 asks for the launch date for Otter, which no meeting gives. Concentrate declined: a true positive.
  - Over the run: Concentrate 92% precision / 85% recall / 1% false abstain. Summaries 55% / 92% / 9%: they decline a lot and catch most unanswerables, but almost half their declines are on questions that had an answer, such as q814, a multi-hop question.
- **Weaknesses.**
  - Only 13 synthetic unanswerables, so recall moves in steps of 8 points.
  - Granola's agent returns prose with no abstain flag, so its abstention metrics are n/a, not zero.
  - The 39-question real report has **no** unanswerable questions, so precision and recall are undefined there; only the false-abstain rate means anything.
- **Status.** computed (precision, recall); new (false abstain).

### D2. Accuracy when it answered, vs coverage

- **Definition.**
  - **Coverage:** the share of questions the system answered at all, rather than declining.
  - **Accuracy when answered:** judged accuracy on those questions only.
- **Why it matters.** These are the two numbers a product team actually trades off: "how often does it give an answer" against "when it does, can I trust it". Overall accuracy mixes them together.
- **Computation.** `coverage = #not_abstained / n`, `acc_answered = #correct among not_abstained / #not_abstained`. Code: [`metrics.py`](../../packages/concentrate/evals/metrics.py) `abstain_stats`.
- **Worked example.**
  - **Synthetic:** Concentrate covers 90% and is 92% right when it answers. Summaries cover 82% and are 74% right when they answer: worse on both.
  - **Real slice:** this metric explains the headline loss. Concentrate scored 59% against Granola's agent's 92%, but **10 of its 16 wrong answers were declines** on answerable questions (26% false abstain; coverage 74%). When it did answer, it was right **79%** of the time.
  - So on real meetings Concentrate mostly *didn't find* the answer, rather than getting it wrong. This is the gap the transcript fallback (`on_abstain`) targets ([`fallback.md`](../archive/concentrate/fallback.md)).
- **Weaknesses.**
  - A system can raise accuracy-when-answered just by declining more, so always read it with coverage.
  - On unanswerable questions, answering counts as wrong, so this isn't pure "accuracy on answerable questions".
  - Real n = 39: the 79% rests on 29 answered questions.
- **Status.** new.

### D3. Calibration: reliability diagram, ECE, Brier, selective accuracy

- **Definition.** Each answer comes with a 0–1 confidence.
  - **Calibration** asks whether answers given at 80% confidence are right 80% of the time (reliability diagram, expected calibration error, Brier score).
  - **Selective accuracy** is the accuracy of the most confident x% of answers.
- **Why it matters.** It lets a product show "not sure" honestly, or route low-confidence questions to a fallback.
- **Computation.** Planned: `ECE = Σ_bins (n_b / n) · |acc_b − conf_b|`, `Brier = mean((conf − correct)²)`.
- **Weaknesses.**
  - Self-reported LLM confidence is often poorly calibrated, which is itself a finding.
  - It needs every system to emit a confidence, and external products may not.
- **Status.** needs runs (experiment X5, about $5–8: no current system reports a confidence).

### D4. Confident error, and abstained when the answer existed

- **Definition.**
  - **Confident error:** the share of all questions the system answered (did not abstain on) *and* the judge marked **incorrect**. Partially correct answers are not counted: they are incomplete, not wrong. Answering an unanswerable question counts.
  - **Abstained when the answer existed:** the count of answerable questions it declined (its rate is D1's false-abstain rate).
- **Why it matters.** Granola's own worst output is the answer that is "wrong, but confident". A wrong answer that looks right costs the user more than "I don't see that in your notes". Accuracy alone can't tell the two apart.
- **Computation.** `confident_error = #(not abstained and verdict == "incorrect") / n`. Code: [`metrics.py`](../../packages/concentrate/evals/metrics.py) `confident_error`, reported by `abstain_stats` and the D-section table of every `analyze` report. By tag: [`by_tag.py`](../../packages/concentrate/evals/by_tag.py) (bootstrap CI over questions).
- **Worked example.** Summaries (b1) answered 98 of 120 test questions and 10 of those were judged incorrect: **8%**. Concentrate: 3 of 120, **2%**. On the 17 transcript-needed questions ([`NOTES_VS_TRANSCRIPT.md`](../archive/northwind/NOTES_VS_TRANSCRIPT.md)), summaries' confident error rises to **41%** (7 of 17), against 0–6% for systems that read transcripts.
- **Weaknesses.**
  - It inherits the judge's errors, and the partial/incorrect boundary is where judges disagree most (B1).
  - Granola's agent has no abstain flag: a polite "couldn't find it" that the judge marks incorrect counts too, so its value is an upper bound (shown as ≤).
- **Status.** new (Phase 2, $0).

## E. Efficiency

### E1. Cost per answer at equal prices

- **Definition.** Dollars per answer, with **every** answer priced from its token counts at **one** price list. The default is the interactive Opus 5.5 price: no Batch API and no prompt-cache credit, since a user is waiting for the answer.
- **Why it matters.** It is the running cost of a deployment. It also has to be comparable: a system can't look cheaper just because its answers happened to go through a discounted channel.
- **Computation.**
  - `$/answer = (input_tokens × p_in + output_tokens × p_out) / 10⁶`, averaged per system. Opus 5.5: p_in = $4/Mtok, p_out = $20/Mtok.
  - Code: [`metrics.py`](../../packages/concentrate/evals/metrics.py) `reprice` and `cost_stats`; option `--price-model`.
  - Reported separately, never mixed in:
    - **Batch saving**: the same tokens through the Batch API, 50% off.
    - **Recorded** $/q: what the run actually paid.
- **The bug this fixes** (red team R1, [`RED_TEAM.md`](../archive/northwind/RED_TEAM.md)).
  - The harness batches every system that can be batched: top-k, summaries, oracle and Concentrate.
  - Their 110 non-sample answers were billed at 50%, while the agent, Mem0 and Graphiti paid full price.
  - `analyze` and `economics` used the recorded `cost_usd`, which mixed the two bases.
  - `economics` made it worse: its cache-split step read the batch discount as ~50% prompt-cache hits.
  - Both now re-price from tokens. `economics` first converts each batched row's recorded cost back to the interactive basis.
- **Worked example.** Concentrate's q012 used 7,783 input and 93 output tokens. At the interactive price that is 7,783 × 4 + 93 × 20 = **$0.0330**. The run recorded $0.0165 because that answer was batched.
  - Per system: Concentrate **$0.040/q** (recorded $0.022), agent $0.137 (recorded $0.137, never batched), top-k $0.328 (recorded $0.177).
  - So at equal prices Concentrate is about **3.5× cheaper per answer than the agent, not 6×.**
- **Weaknesses.**
  - Rows don't record a cache split, so caching isn't credited to anyone. The agent re-sends its growing conversation every turn; with message-level caching it would cost about $0.07–0.08 (an estimate, R1).
  - Token counts depend on the answer model, so re-pricing on another model is a what-if, not a measurement.
  - Granola's agent's tokens aren't visible: marked "not observable", and kept out of the cost frontier.
- **Status.** computed (tokens); new (equal-price re-pricing, batch saving).

### E2. Ingest cost, amortised, and the break-even question count

- **Definition.**
  - **Ingest:** a write-time system (Concentrate, Mem0, Graphiti) pays once to build its memory.
  - **Break-even N:** the number of questions per corpus above which it is cheaper in total than a query-time system.
- **Why it matters.** "Memory is cheaper" is true only past some usage level. N is the number a buyer needs; a frontier drawn at one assumed volume hides it.
- **Computation.**
  - `N* = ingest / (a_query − a_write)`, with both answer costs at the E1 price. It is "never" if the write-time system's answers cost at least as much.
  - Ingest is taken **as recorded when the store was built** (interactive, each system's own ingest model).
    - A run that reused a cached build records $0 for itself; the original build cost under `detail.built` is used instead.
    - Before this fix, Concentrate and Mem0 were treated as free to ingest, and missing from the break-even table.
  - The oracle is excluded as a rival: it isn't deployable.
  - Code: [`metrics.py`](../../packages/concentrate/evals/metrics.py) `ingest_usd`; [`pareto.py`](../../packages/concentrate/evals/pareto.py) `breakeven_volume`.
  - `concentrate economics` gives the per-user-per-month view, with **batch-priced** ingest by default, since ingest can run asynchronously. That halves N. The scenario line says which basis is used.
- **Worked example.** Concentrate's 184-meeting build cost $4.96. Against the agent: 4.96 / (0.1372 − 0.0395) ≈ **51 questions per corpus**.
  - Mem0 ($17.02) needs 142, Graphiti ($59.58) 550.
  - Against summaries, no write-time system ever pays back: summaries answers cost $0.017.
- **Weaknesses.**
  - It ignores storage, re-ingest when notes are edited, and embedding cost.
  - Ingest ran once per system, so there's no variance estimate.
- **Status.** new (break-even in `analyze`, ingest fix).

### E3. Tokens in/out, latency p50/p95, tool calls

- **Definition.**
  - Mean input and output tokens per answer.
  - Median (p50) and 95th-percentile (p95) seconds to answer.
  - Mean tool calls per answer.
- **Why it matters.** Tokens drive cost, and latency is what a user feels. Tool calls show how much an agent explores, and each one is a round trip.
- **Computation.** From each row's `input_tokens`, `output_tokens`, `latency_s` and `tool_calls`. Code: [`scoring.py`](../../packages/concentrate/evals/scoring.py) `summarize`.
- **Worked example.** The agent's q002: 18,732 tokens in, 693 out, 5 tool calls, 20.2 s. Over the run the agent's p50 is 13.2 s (n = 120); Concentrate's is 3.8 s.
- **Weaknesses.**
  - **For batched systems, latency is measured on only 10 interactive sample questions:** Concentrate, top-k, oracle and summaries all have `latency_n` = 10. Their p95 is the 10th-largest of 10 values and shouldn't be quoted (red team R7).
  - Latency depends on API load at the time of the run.
- **Status.** computed.

## F. Robustness

The same metrics, sliced by condition. No new formulas: the question is whether a conclusion survives a change in the data.

- **By question type.** Per-category accuracy, with paired tests inside each category (Holm-corrected per category).
  - Example: on aggregation questions, top-k and Mem0 score 42% against Concentrate's and the agent's 83%. A gap that disappears overall can be large inside a category.
  - Weakness: about 13 questions per category, so few per-category gaps are statistically reliable.
  - Status: computed.
- **By corpus size (scale curve).** Accuracy on nested prefixes of the corpus (25/50/100%). Status: computed for b0, b3 and Concentrate ([`ECONOMICS.md`](../archive/northwind/ECONOMICS.md) §2).
- **By transcript realism (v1 → v2 → v2.1).** The same systems on templated vs realistic text and hard extras. Status: needs runs (X6, X7).
- **By retention (transcript-deletion curve).** What is lost if transcripts are deleted after extraction. Status: computed for the v1 corpus ([`RETIREMENT.md`](../archive/northwind/RETIREMENT.md)); the extras need runs (X6).
- **Synthetic vs real gap.** The same system's accuracy on synthetic and on real meetings.
  - Example: Concentrate goes from **92%** synthetic to **59%** on 39 real questions, while Granola's agent scores 92% on the real ones. This is the most important robustness result so far.
  - Weaknesses: the real slice is one person, 4 meetings and LLM-drafted gold. D2 shows most of the drop is declining, not wrong answers.
  - Status: computed (dev-quality; a fresh hand-written test is X1).

---

## Statistics used everywhere

All tests are **paired**: every system answers the same questions, so they are compared question by question. Code: [`stats.py`](../../packages/concentrate/evals/stats.py) and [`analyze.py`](../../packages/concentrate/evals/analyze.py).

- **Bootstrap CI** (`bootstrap_ci`, `paired_bootstrap`).
  - Resample the questions with replacement 2,000 times and recompute the statistic each time; the middle 95% of the results is the CI.
  - For a gap between two systems, each resample keeps both systems' answers to a question together, which is what makes it paired.
  - Example: Concentrate − agent = **+1.7 points, CI [−3.3, +6.7]**. The interval includes zero, so it is a tie.
- **McNemar's exact test** (`mcnemar_exact`).
  - Only questions where exactly one of the two systems is right carry information.
  - If the systems were equally good, those questions would split 50/50, so the p-value is a binomial test on the split.
  - Example: Concentrate vs the agent split **6 vs 4**: p = 0.75. Concentrate vs Mem0 split 15 vs 2: p = 0.002.
- **Holm correction** (`holm`).
  - Testing all 21 pairs gives 21 chances of a fluke. Holm multiplies the smallest p by 21, the next by 20, and so on (keeping the sequence non-decreasing), which controls the chance of *any* false win at 5%.
  - A gap is reported as reliable only if its Holm-adjusted p is under 0.05.
  - Example: Concentrate vs Mem0 adjusts to p = 0.028, still reliable; 10 of the 21 pairs survive.
- **Power and minimum detectable gap** (`mcnemar_sample_size`, `mcnemar_mde`).
  - Power is the chance a real gap of a given size would be detected. It depends on n and on the **discordance**: the share of questions where the two systems disagree.
  - Example: Concentrate and the agent disagree on 8% of questions. At n = 120 the smallest gap detectable with 80% power is **about 7 points**; seeing a 5-point gap needs about 260 questions.
  - So "tied" here means "within about 7 points", not "equal".
  - Every analysis prints this table, so a tie is never reported as a win, and a non-result is never mistaken for proof of equality.

**What the statistics can't fix:** a biased judge, a gold answer that is wrong, unequal wiring between systems, or a question set that doesn't resemble real use. Those are handled by B, by the fairness rules ([`PLAN.md`](../archive/PLAN_V2.md) §3) and by the real slice, not by p-values.

## Which metrics each system exposes

| | Line citations | `retrieved` meetings | Abstain flag | Tokens |
|---|---|---|---|---|
| top-k (b0), oracle (b2o), agent (b3), Concentrate | yes | yes | yes | yes |
| summaries (b1) | meetings only | yes | yes | yes |
| Mem0 (b4), Graphiti (b5) | **no** (cite nothing at line level) | yes (source meetings of memories / facts) | yes | yes |
| Granola's agent (real slice) | meetings only | cited meetings only | **no** | **not observable** |

Where a system doesn't expose something, its metric shows **n/a** or **not exposed**. That means "unknown", never zero.
