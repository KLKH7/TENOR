# Red team: does tonight's headline survive?

**Target.** README headline from report `20261103T002456Z` (120 XL test questions, Opus 5.5 answers and judge):
*"Concentrate ties the best query-time systems at about 1/6 the cost, and beats Mem0, Graphiti and summaries."*

**Verdict.** The **tie** survives. **"About 1/6 the cost" does not**: it compares a batch-discounted price with a full price. Like for like, the advantage is about **3.5×**, or about **2×** if the agent used ordinary prompt caching. It only holds once a corpus gets more than about 50 questions. **"Beats Mem0 / Graphiti"** holds for *our default wiring* of those libraries. It is not a claim about the libraries: both were starved of retrieval budget or input, and the Mem0 gap is probably inside the noise once that is fixed. **"Beats summaries"** survives cleanly. The directory leak is real but small in measured effect (1 question decided outright). Selection and reproducibility hold up. A second judge (Sonnet) changes none of Concentrate's verdicts.

Spend: **$1.86** (Sonnet second judge, Batch API; estimated at $0.69, so over the $1 pre-approval, see the judge section). Everything else was offline or read from cache ($0).
Method: every number below was computed from `evals/reports/20261103T002456Z.*`, the shared LLM cache and the code at `f040b3a`. The baseline-wiring section draws on a code audit (file:line references inline), which I spot-checked.

---

## Findings, ranked

### INVALIDATES (a headline claim as worded)

**R1. "About a sixth the cost" compares prices on different bases.**
- **Batching was uneven.** The harness batches every system with a `turn_request` (`evals/runner.py:182-184`): b0, b1, b2o and Concentrate. Their 110 non-sample questions ran at the **50% Batch API price**. The b3 agent, Mem0 and Graphiti paid **full price on all 120** (`meta.batch.batched_systems` in the report JSON).
- **The $/q column therefore mixes price bases.** Concentrate's $0.022 is 10 questions at full price and 110 at half price. The agent's $0.137 is all full price.
- **The agent also gets no prompt caching across its turns.** Only the system prompt can be cached (`llm/client.py:392`, `cache_system`). Each of its ~5 turns re-sends the whole growing conversation at full price: 29.6k input tokens per question.

| $/question, Opus 5.5 | as reported | all full price (interactive, as a user would wait for it) |
|---|---|---|
| Concentrate | 0.022 | **0.0395** |
| b3 agent | 0.137 | 0.137 (≈ **0.07–0.08** with standard message-level prompt caching, estimated ¹) |
| b0 top-k | 0.177 | **0.328** |
| b2o oracle | 0.046 | 0.082 |
| Mem0 | 0.017 | 0.017 |
| Graphiti | 0.029 | 0.029 |
| summaries | 0.009 | 0.017 |

¹ Estimate, not measured. It assumes linear context growth over 5.04 turns per question (605 calls / 120). The final-turn context (~10k tokens) is written to cache at 1.25×, and earlier prefixes are read at 0.1×. That makes input ≈ 14k token-equivalents instead of 29.6k, plus $0.019 of output.

**Like for like: about 3.5× cheaper per query than the agent, or about 2× against a cache-aware agent. Not 6×.**

**Ingest amortisation** at full query price (ingest over the 184-meeting corpus: Concentrate $4.96, Mem0 $17.02, Graphiti $59.58):

| questions per corpus | Concentrate | b3 agent | b0 top-k | Mem0 | Graphiti |
|---|---|---|---|---|---|
| 10 | **0.536** | 0.137 | 0.328 | 1.719 | 5.987 |
| 100 | 0.089 | 0.137 | 0.328 | 0.187 | 0.624 |
| 1,000 | 0.045 | 0.137 | 0.328 | 0.034 | 0.088 |

- **Break-even against the agent** (no caching for the agent): Concentrate about **51** questions per corpus, Mem0 142, Graphiti 549. Against a cache-aware agent, Concentrate needs about 140.
- **"Including ingest, the agent and top-k are dominated"** comes from `analyze`'s 1,000-question volume. It is false at 10 questions and true from about 50 up.
- *Fix (free):*
  - report $/q at one price basis (full interactive price for every system, since users wait for answers);
  - give the break-even N instead of "dominated";
  - optionally add message-level `cache_control` to the agent and re-price from the cache.

**R2. "Beats Mem0 (+11) and Graphiti (+20), all reliable" is a claim about our wiring, not about Mem0 or Graphiti.**

*Mem0* (mem0ai 2.2.1):
- `search(top_k=25)` over **5,292 memories** (`agents/mem0_system.py:574`), so about 0.5% of the store reaches the model: 3.3k input tokens per question against Concentrate's 8.8k.
- For most of its "wrong" answers, the needed memory **exists in the store but wasn't retrieved**:
  - q659: infra dependency slipped;
  - q643 and q644: dry run failed on refunds;
  - q837: Ruth owns Kite;
  - q831: all three Slack-integration requests.
- About 15 of its 23 misses are retrieval-budget misses, not architecture.

*Graphiti* (graphiti-core 0.31):
- The **AI summary and typed notes are never ingested** unless a meeting has no transcript (`agents/graphiti_system.py:358-359`). Mem0 and Concentrate both get them. Its single-hop failures (64%) are summary-only facts: q060, q062, q066, q118, q335 and q008.
- No cross-encoder reranker, no episode search, and extraction thinking is off.

**Fair ranges if the wiring were fixed** (judgement, from classifying every wrong answer):

| System | Reported | Fair range |
|---|---|---|
| Mem0 | 81% | **87–92%** |
| Graphiti | 72% | **82–88%** |

The minimum detectable effect for Concentrate vs Mem0 is about 10 points (`.analysis.md`, power table). **The Mem0 gap probably wouldn't survive. The Graphiti gap probably would, at half its size.**

*Fix:*
- Mem0: rerun with `top_k` 100 (≈6k tokens, still under Concentrate's budget). No re-ingest is needed, so it costs about $2.
- Graphiti: re-ingest summaries and notes as text episodes and add a reranker, about $60. Or keep the result but word it as "our default wiring".

### WEAKENS

**R3. The org directory helps Concentrate. The measured direct effect is small; the indirect effect is unmeasured.**

Only Concentrate reads `directory.json` (`evals/runner.py:155-158`). It feeds three places:
- the extractor's cached system prompt (`memory/entities.py:61`, `prompt_block`);
- project aliases and the hint "Beacon (not the customer Beacon Freight)";
- a query-time role resolver that turns "Sam the backend engineer" into Sam Patel (`memory/query.py:70-82`, `_by_role`).

None of the 37 role strings appears in any note file (0 hits for every role across 186 files; "backend engineer" appears only in lower case in speech, 5 times).

Question by question, over all 120:
- **Decided by the directory: 1.** q246 asks "…and what is their role?", and the gold is "Sam Okafor (Product Designer)". Only Concentrate stated the role from the directory. The agent got credit for inferring "designer". b0, b2o, Mem0 and Graphiti named the right person, said "role not stated", and were marked wrong.
- **Ambiguity removed by the directory: 4.** These use the "Sam the backend engineer" / "Alex the engineering manager" template (q686, q688, q694, q697). All systems got 3 of 4 (the 4th is a gold bug), so there is no differential.
- **Role in the gold but judged leniently: 2.** On q247 and q249 every system was marked correct without stating the role.
- **No effect from as-of filtering.** Concentrate filters facts recorded after `as_of` (`memory/query.py:233`), and the baselines see every note. Only 16 of 120 questions have an earlier as-of, and accuracy on them is 14–15 of 16 for every system.

Net measured effect: Concentrate gains +1 question over b0, Mem0, Graphiti and the oracle, and 0 over the agent. What is **not** measured is how much the directory improves the whole extracted memory (speaker and entity resolution at ingest). Only the ablation answers that.

*Fix:* run the directory ablation. Build from attendee metadata only, as the `real-kit directory` already does for real data. That is about $5 of ingest plus $2.60 of answers. Until then, keep the README caveat and add "measured direct effect: 1/120 questions".

**R4. Judge audit: no leniency toward Concentrate found, but two coin-flip verdicts.** Sonnet re-judged all 840 answers and disagreed with Opus on **0 of Concentrate's 120** (details in the section below).

From hand-reading all 10 Concentrate vs agent disagreements:
- **2 are coin-flips that cancel.**
  - **q643** (agent credited, Concentrate not): both answers say the date was dropped at the 9/21 standup and reset to Oct 8 at the 9/22 sync. The content is near-identical, the verdicts opposite.
  - **q848** (Concentrate credited, agent not): Concentrate's "no definitive answer on record" counted as an abstention. The agent's "they parked it and promised to check" did not, though both say the same thing.
- **2 penalise the agent for hedging rather than stating the label.** On q560 and q593 the agent never said "on track". This is strict but consistent with the gold.
- **The rest are genuine:**
  - Concentrate wins: q120, q348, q644.
  - Agent wins: q652, q711, q814.

The 6–4 split is a tie, as the README says.

**R5. Gold errors cap the oracle and add noise for everyone.**
- **q694:** gold reads "Done (reported complete on None)", a generator bug. All 7 systems fail.
- **q036:** gold attributes to Beacon Freight an SSO statement that Northwind's Felix made. All 7 fail, and every answer says so correctly.
- **Also flagged by the audit:**
  - q840 (ambiguous within-meeting order; all 7 fail);
  - q832 (Corvid did raise SSO; the agent was penalised for being right);
  - q818 and q652 (ambiguous golds).

The 95% oracle is therefore "ceiling minus gold errors", not a ceiling. This doesn't change the ranking, since everyone pays, but it compresses the top. *Fix:* fix the generator (the `None` date, the attribution of SSO) and re-score offline.

**R6. Unequal tuning is concrete, not abstract.**
- **Concentrate's query layer anchors on the generator's question templates:**
  - a quoted meeting title plus date (`memory/query.py:106`);
  - role phrases;
  - list-intent regexes;
  - a cap of 40 commitments for list questions.
- **Its extractor's update kinds mirror the gold format exactly.** The extractor has done / slipped / reassigned / dropped (`prompts/extract.v2.md:38`). The generator emits "Slipped to X, then completed".
- **The baselines got no tuning:** b3, Mem0 and Graphiti use untuned prompts and default settings.
- **Tuning was confined to dev** (`CONCENTRATE_TUNING.md`), and I found no evidence otherwise (R10). But fitting the author's own templates is home advantage that dev/test separation cannot remove. Only an independently written corpus (v2, or the real slice) can.

**R7. Latency rests on n = 10 for Concentrate.**
- The p50 holds on the same 10 questions: Concentrate **3.9 s** vs agent **12.4 s**, against the agent's 13.2 s over all 120.
- **The p95 (14.7 s) is the 10th-largest of 10 values, i.e. meaningless.**
- The latency also excludes ingest lag: a meeting is answerable only after its roughly 4–5 s extraction.

*Fix:* report the p50 with "n = 10" in the table, and drop the p95 for batched systems.

### COSMETIC / REPRODUCIBILITY

**R8. HEAD `f040b3a` doesn't import.**
- `synth/roster.py:179`: a comment swallowed the closing quote and arguments of the Jamie `Person(...)`, so every `uv run concentrate …` command dies with a `SyntaxError`, and so would `pytest`. I fixed it on this branch, keeping the new `.example` address. With the fix, `uv run pytest` passes.
- **The default `--k` changed from 10 to 20** after the report. A plain `eval --offline` at HEAD misses the cache and stops early with an empty table. Without `--offline` it would try to spend about $37. The reproduction command must say `--k 10`.
- The report was produced from a **dirty** tree (`aeafca5 (dirty)`).

**R9. README ingest figures are fine but unlabelled.** $5 for Concentrate is Sonnet interactive ingest with prompt caching ($4.96). `ECONOMICS.md` uses Batch-priced ingest (1.4¢/meeting, about $2.56). State which one.

**R10. Graphiti shows a "superseded" flag from `expired_at`, regardless of as-of** (`graphiti_system.py:534`). This leaks a little future information *in Graphiti's favour*. Negligible.

---

## What survives (checked, not assumed)

- **No answer-time leakage of gold.** No system reads `world.json` or `questions.jsonl` at answer time.
  - The only gold access is b2o reading `gold_utterance_ids` (`agents/baselines.py:338`), which is intended.
  - The runner hashes those files only for provenance (`evals/runner.py:311-316`).
  - `QuestionLike` exposes only `id`, `question` and `as_of` (`agents/baselines.py:63-69`). The concrete `Question` object carries gold fields, but grep finds no system that touches them.
- **Selection was deterministic and fixed before results.** The 120 are a round-robin by category over the test split in file order (`evals/runner.py:89-110`), and my independent re-implementation selects the identical 120.
  - The stratified-limit rule was committed in `3148e5f` (05:10). "120 test questions" was planned in `48424dc` (17:29). Results came in `20184ed` (19:37).
  - The seed only affects bootstrap CIs, not selection.
  - The selection exhausts aggregation (12/12) and nearly exhausts multi-hop and unanswerable, so it is "stratified and capped", not random.
- **No test peeking.** There were two test runs:
  - `20261102T225639Z` at `5e28f36`, without Graphiti;
  - `20261103T002456Z`, which added Graphiti.

  Concentrate's config hash (`6b9c4de75b6e`) and every accuracy figure are identical across them.
- **Exact offline reproduction.** `eval --xl --split test --limit 120 --systems b0,b1,b2o,b3,b4,b5,c --offline --batch --k 10 --seed 42` costs $0.00 and reproduces all 840 rows with identical verdicts, answers and costs, and the identical summary table, latency included.
- **The tie with the agent and top-k** (McNemar with Holm: p = 1.0; discordance 8%; MDE ≈ 7 points) is honestly stated already.
- **Concentrate vs summaries** (+21, 27 discordant pairs to 2) doesn't depend on wiring choices.
- **Concentrate is cheaper per query than the agent at any price basis**, by 2–3.5×, once a corpus sees more than about 50–140 questions.
- **The agent was wired fairly.** No question hit `max_turns=12` (at most 16 tool calls). Its misses are reading errors or gold issues, not budget. It lacks Granola's `list_meetings` and `query_granola_meetings`, which is a modest under-model.

---

## Judge audit (Sonnet second judge on all 840 answers)

Report: [`evals/reports/20261103T002456Z.judgecheck.claude-sonnet-5-5.md`](../../evals/reports/20261103T002456Z.judgecheck.claude-sonnet-5-5.md). Same `judge.v1` prompt, Batch API.

- **Agreement:** 97.3% on correct / not correct (Cohen's κ 0.90); 96.2% on the 3-way verdict (κ 0.87).
- **Does the second judge disagree more on Concentrate's "correct" verdicts? No. It disagrees on none.**

| System | Primary | Sonnet | Δ | Disagreements | Primary correct → Sonnet not | Sonnet correct only |
|---|---|---|---|---|---|---|
| concentrate | 91.7% | 91.7% | 0.0 | **0** | 0 | 0 |
| b3 agent | 90.0% | 88.3% | −1.7 | 4 | 3 (incl. q246, credited for "designer") | 1 (q644) |
| b0 top-k | 88.3% | 86.7% | −1.7 | 2 | 2 | 0 |
| Mem0 | 80.8% | 81.7% | +0.8 | 5 | 2 | 3 |
| Graphiti | 71.7% | 69.2% | −2.5 | 7 | 5 | 2 |
| summaries | 70.8% | 69.2% | −1.7 | 4 | 3 | 1 |
| oracle | 95.0% | 95.8% | +0.8 | 1 | 0 | 1 |

What this means:
- **The primary judge was not lenient toward Concentrate's style.** Under Sonnet, the gap between Concentrate and the agent *widens* to 3.4 points, which is still inside the noise (MDE ≈ 7).
- The only ranking change is at the bottom: summaries and Graphiti swap places.
- **What this does not rule out:**
  - **Same-family bias.** Both judges, and the answer model, are Anthropic models.
  - **Judge rules applied identically to both systems that still favour one answer style.** Two examples:
    - "No definitive answer is on record" counts as an abstention, but a substantive "they parked it" does not (q848).
    - Hedged answers that omit the gold's status label are failed (q560, q593 for the agent).
  - Neither judge catches these. A blind hand check would, and `concentrate hand-check` exists but hasn't been run on this report.

**Budget note.** `judge-check --estimate` said **$0.69**; the run actually cost **$1.86**, over the $1 the coordinator approved. The estimator (`cli.py:354-359`) uses the chars/3.5 heuristic on system + user text and misses schema overhead and output. It under-estimated by 2.7×. This guard protects the "ask before >$2" rule, so it should use the real tokenizer or a calibrated factor.

---

## Five objections a hostile Granola engineer would raise

1. **"You wrote the exam and the student. Of course it ties the agent."** *Not answered by the data.*
   - Dev/test separation is real (R10 above), but the query layer fits the generator's templates (R6).
   - Only v2, the extras, or the real slice can answer this, and none has been run for accuracy.
2. **"Your 6× is a batch discount."** *Correct.* Like for like it's 3.5×, about 2× against a cache-aware agent, and nothing below about 50 questions per corpus (R1).
3. **"Mem0 and Graphiti were set up to lose."** *Largely correct.*
   - The facts were in Mem0's store and never retrieved at k=25.
   - Graphiti never saw the summaries (R2).
   - The data supports "beats our default wiring", not "beats Mem0".
4. **"The org directory is privileged information Granola doesn't have."** *Partly answered.*
   - The measured direct effect is 1/120 questions (R3).
   - The indirect effect on the extracted memory is unmeasured until the ablation runs.
   - Granola does have calendar attendees and emails. It doesn't have titles.
5. **"At a 95% oracle on templated synthetic data, a 2-point gap is noise and the benchmark can't separate the systems that matter."** *Correct, and the README already concedes the statistical half.*
   - About 5 of the remaining errors are gold bugs (R5).
   - The question isn't whether Concentrate is better. It's whether this benchmark could show it if it were. At n = 120 it can't detect less than about 7 points against the agent.

---

## Rewritten README headline (defensible version)

> **Results (first comparison, 2026-11-02).** On 120 held-out synthetic test questions, Concentrate (92%), our Granola-style agent (90%) and top-k retrieval (88%) are **statistically tied**. They disagree on few questions, and this benchmark can't detect gaps under about 7 points. At the same interactive Opus price, Concentrate's answers cost **about 3.5× less than the agent's** ($0.040 vs $0.137 per question; about 2× if the agent used prompt caching). Concentrate's one-off ingest (~$5 for 184 meetings) pays for itself after **about 50 questions per corpus**.
>
> - **Tied at the top.** Paired McNemar with Holm correction: no reliable difference among Concentrate, the agent and top-k. Concentrate and the agent disagreed on 10 questions, split 6–4, and two of those verdicts are judge coin-flips.
> - **Cheaper per query, not per corpus.** Below about 50 questions per corpus, the agent is cheaper once ingest is counted.
> - **Clearly better than summaries alone** (+21 points, reliable).
> - **Ahead of Mem0 (+11) and Graphiti (+20) as we wired them.** Both used default settings: Mem0 retrieved 25 of 5,292 memories, and Graphiti never ingested the AI summaries. A fair comparison would likely shrink the Mem0 gap into the noise. Read these as lower bounds for those libraries.
> - **Concentrate had help the others didn't:** an org directory (roles, orgs, aliases) and query code tuned on dev to this generator's question templates. The directory decided 1 of 120 questions outright; its effect on the extracted memory hasn't been measured.

## Sentences that overclaim

| Where | Sentence | Problem | Replace with |
|---|---|---|---|
| README.md:28 | "Concentrate costs about a sixth as much per question as the agent." | Batch price vs full price (R1) | "…about 3.5× less at the same interactive price (~2× against a cache-aware agent)." |
| README.md:28 | "Including ingest, the agent and top-k are dominated on cost vs accuracy." | Only at about 50+ questions per corpus; `analyze` used 1,000 (R1) | "With ingest spread over 100+ questions per corpus, …; below about 50, the agent is cheaper." |
| README.md:29 | "Concentrate beats Mem0 (+11 points), Graphiti (+20) … and all three gaps are reliable." | Reliable for our wiring only (R2) | "…beats our default wiring of Mem0 and Graphiti…" |
| README.md:20 table | `$ / question` column | Mixes batch-priced and full-priced rows | One price basis, footnoted |
| README.md:20 table | Concentrate p50 "3.8 s ¹" next to agent "13.2 s" | n = 10 vs n = 120 | Add "(n = 10)"; drop p95 for batched systems |
| README.md:35 | "Only Concentrate gets an org directory … The directory ablation hasn't been run yet." | Fine, but add the measured floor | "…It decided 1/120 questions directly (q246); the ablation is pending." |
| README.md:3 / What's inside | "each with a verified answer" | At least q694 ("reported complete on None") and q036 are wrong (R5) | "…answers verified by construction (known gold errors: q036, q694, …)" |
| README.md:73 Quick start | `uv run pytest` / `concentrate eval …` | HEAD doesn't import (R8) | Fix `roster.py`; add `--k 10` to the reproduction command |
| docs/writeup/MEMO.md:23 | "Statistics you can rely on." | The statistics are sound; the inputs (wiring, price basis) are what mislead | "Paired significance tests and power analysis (see their limits in RED_TEAM.md)." |
| docs/writeup/MEMO.md:15 | "…(e.g. knowing which Sam you mean)" as a Concentrate capability | In this benchmark that comes from the directory, which Granola lacks (R3) | Say the directory provides it |
| `.analysis.md` summary | "b5_graphiti, b3_agentic, b0_topk are beaten on both by another system" | At 1,000 questions per corpus and on mixed price bases | Give the volume and price basis in the sentence |

## Cheapest fixes, in order

1. **Free, a doc change:** restate cost on one price basis with the break-even N; add "n = 10" to latency; reword the Mem0/Graphiti line. Use the headline above.
2. **Free, a code change:** fix `roster.py`; pin `--k 10` in the documented reproduction command.
3. **About $2:** rerun Mem0 with `top_k=100` from its existing store.
4. **About $8:** run the directory ablation (attendee-only directory) for Concentrate.
5. **About $60:** re-ingest Graphiti with summaries and notes as text episodes, plus a reranker. Or keep the "as we wired it" wording permanently.
6. **Free, re-scoring:** fix the generator's gold bugs (q694 `None` date, q036 attribution) and re-score offline.
