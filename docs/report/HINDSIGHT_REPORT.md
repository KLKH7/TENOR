# Hindsight: What Does Breadth Over Fidelity Cost? An Outside-In Benchmark for Agent Memory over Meeting Notes

**Kian Lakhani** · Technical report · 4 November 2026 · Independent research prototype, not affiliated with Granola

*What this is: the full technical report. It covers method, real-meeting results, synthetic diagnostics, limitations and how to reproduce every number.*

## Abstract

Granola trades fidelity for breadth: its agent reads many meetings' notes rather than a few transcripts, and users can auto-delete transcripts [2, 3]. We measure what that costs on **real meetings**, through public interfaces only. All results are DEV (questions seen during development) on the author's 4 Granola meetings.

- **Deletion is costly, and the cost falls on detail.** Granola's agent with transcripts: 89% of 47 questions. Our model over Granola's notes alone: 57% (paired 2–17, p = 0.001). On questions the notes can't answer: 94% → 44%.
- **Reaching the transcript recovers the loss; a fact layer doesn't.** Notes + extracted facts: 66% (4–0, p = 0.13). Transcript passages only when needed: 87% (10–0, p = 0.002).
- **Without transcripts, our model declined rather than bluffed.** No confident wrong answers.
- **Reading real speech, Granola's agent beat a precomputed fact memory 92% to 59%** (n = 39, paired 14–1, p ≈ 0.001).
- **Synthetic data showed the opposite on both points,** so it is reported only as a diagnostic.
- **Main limitation:** 4 meetings, one person, LLM-drafted gold approved in bulk, DEV only.

## 1. Motivation

External agents (Claude, ChatGPT) reach Granola only through its MCP, the protocol agents use to call its tools.

- **The CEO's framing [1].** An agent connected through Claude can "look at the 20 most relevant meetings" and does "a relatively dumb job at extracting information". He proposed sending agents "apple juice from concentrate", and said Granola was "looking into whether we should delete the transcript automatically". (Our probe found the MCP is now an agent that searches, reads and cites, so the 20-meeting description is out of date [11].)
- **The design choice [3].** "Breadth over fidelity": read ~100 meetings at a high level rather than 10 in full. Transcripts cost ~10× the tokens of notes. Go deeper only when needed. The worst output is "wrong, but confident".
- **The shipped feature [2].** After auto-deletion, chat is "limited to whatever is in your notes", and "Chat quality will be degraded".

Granola can measure this internally. An outside benchmark adds independence, comparability across products, and the view an external agent actually gets.

**Related work** (table and lessons: [`RELATED_WORK.md`](../RELATED_WORK.md)).

- **Memory benchmarks use chat or generated dialogue** [24–28]. Meeting benchmarks give a model the transcript and grade the model [29–33]. None we found tests a shipped product through its agent interface, or what deleting transcripts costs.
- **The closest product comparison** ran four note takers on the same 5 AMI meetings and scored their notes, not questions through an agent; a vendor ran it [36].
- **Notes faithfulness** follows the claim-by-claim shape of FActScore and MiniCheck [34, 35].
- **LongMemEval also found** that replacing sessions with extracted facts loses detail [24, §5.2]. **QMSum's protocol** (fixed query schema, annotator training, expert review) is the model for our held-out questions [29, §3.2].

## 2. Benchmark

- **Outside-in.** Only public interfaces: Granola's REST API format and its official read-only MCP [5, 12]. No claims about Granola internals.
- **Black-box kit: the source of every headline.** It asks a product's MCP the user's questions through one allowlisted tool (`query_granola_meetings` for Granola). A wrapper refuses every other tool before anything is sent. Only content-free aggregates can be committed [9].
- **Real data.** The author's 4 Granola meetings (3 remote, 1 in person). An LLM drafted 47 questions in seven categories (who said X, decisions, what changed, commitment status, which person, temporal, unanswerable). The owner approved the gold answers in bulk, with 0 edits [17].
- **What it tests.** All 4 meetings fit in a model's context, so this measures *reading real speech*, not retrieval at scale.
- **Answer types.** Following Granola's "wrong, but confident" [3], each answer is *correct*, *partial*, *declined* (abstains or says the material doesn't cover it) or *wrong but confident* (judged incorrect with no hedge) [4, 10].
- **Statistics.** Exact sign tests on questions where two conditions disagree; 95% bootstrap CIs (2,000 resamples); a second judge (Sonnet 5.5) re-grades every answer. Six measurement dimensions are defined in [METRICS.md](../benchmark/METRICS.md) [10].
- **Northwind (synthetic, diagnostic only).** A generated company in Granola's exact JSON shape: 184 meetings, 296 questions with known answers [5]. §4c explains why it is not evidence.

## 3. Systems

| System | What it reads at question time |
|---|---|
| Granola's agent | Granola's own agent, live, as a black box |
| Top-k retrieval | the whole best-matching meeting (k = 1 of 4) |
| **Concentrate** | extracted facts that cite transcript lines, with the dates they were true [13] |
| Concentrate + fallback | the facts, plus transcript passages when the facts can't answer [18] |

- **Concentrate is a reference system, not a product.** It shows what precomputing gains and loses.
- **Our systems share one answer model** (Opus 5.5) and one judge.

## 4. Results

### 4a. Retention: what deletion costs and what recovers it (real DEV, n = 47)

We can't switch deletion on inside Granola, so we rebuild it outside. Four conditions; each adds one layer [4]:
**A** Granola's agent, transcripts kept · **B** our model over Granola's notes (≈ chat after deletion) · **C** B + Concentrate's facts · **D** C + transcript passages when C declines.

![Deletion simulation](../figures/deletion_sim.svg)

**Figure 1.** Accuracy by tag (notes-sufficient, transcript-needed) and confident-error rate per condition.

**Table 1.** Run `20261104T012414Z` at `6dc2cb6`, seed 42, $6.42 [4, 19]. Counts out of 47, derived from the aggregate's shares. Tags from a string-matching rule.

| Condition | Accuracy [95% CI] | Correct / partial / declined / wrong-confident | Transcript-needed (n = 18) | vs row above (wins–losses) |
|---|---|---|---|---|
| A: Granola, transcripts kept | 89% [79, 98] | 42 / 2 / 0 / 3 | 94% | — |
| B: notes only | 57% [43, 70] | 27 / 4 / 16 / 0 | 44% | 2–17, p = 0.001 |
| C: notes + fact layer | 66% [51, 79] | 31 / 2 / 14 / 0 | 56% | 4–0, p = 0.13 |
| D: + transcript fallback | 87% [77, 96] | 41 / 3 / 3 / 0 | 89% | 10–0, p = 0.002 |

- **Deletion costs 32 points; 50 on transcript-needed questions** (0–9, p = 0.004).
- **It turns right answers into declines, not bluffs.** B declined 16 of 47 and gave no confident wrong answer.
- **Granola's 3 confident errors came with transcripts,** 2 of them on the 6 unanswerable questions.
- **Facts recover little** (+9, not significant).
- **The fallback recovers most of the gap** (87% vs 89%). It fired on 60% of questions (28/47). It reads transcripts, so it models "keep a cold copy", not deletion.
- **Against our tagging:** notes-only scored only 57% on *notes-sufficient* questions (0–8 vs Granola, p = 0.008). Either the rule over-calls sufficiency or the model under-uses the notes.
- **What A − B mixes.** B is our model and prompt, not Granola's, and it sees every note (an upper bound at 4 meetings). C − B and D − C share model and prompt.
- **Judges agree** on 96–100% of verdicts per condition.

### 4b. Question answering: reading real speech (real DEV, n = 39)

A budget cap stopped Granola and Concentrate at 39 of 47 questions (the 8 unrun: all 6 unanswerable and 2 temporal) [17].

| System | Accuracy [95% CI] |
|---|---|
| Granola's agent | **92%** [82, 100] |
| Top-k retrieval | 77% [64, 90] |
| Concentrate | **59%** [44, 74] |

- **Paired 14–1** for Granola's agent (sign test p ≈ 0.001). Judges agree on 97% (κ 0.93).
- **Why Concentrate loses:** it kept 43 facts and 11 commitments for the 4 meetings, and real questions ask about long-tail details.
- **10 of its 16 misses were declines** ("the material doesn't mention it").
- **0 of 8 on "who said X"** (Granola 7 of 8). Where it did answer, it twice named the wrong person.
- **It leads only on "what changed"** (6 of 6 vs 5 of 6), what its time-stamped store is built for.
- **The fallback lifted it from 12/22 to 20/22** (8 fixed, 0 broken, p = 0.008), against Granola's 19/22. DEV only: these questions motivated the design [18].

![Transcript fallback](../figures/fallback_dev.svg)

**Figure 2.** Concentrate with and without the transcript fallback, by category, on 22 real DEV questions (8 won, 0 lost).

### 4c. Synthetic diagnostics: not evidence of real-world performance

Northwind is reported for completeness, and because it failed in an instructive way. Five reasons it isn't evidence:

1. **Same author** built the generator and Concentrate's extractor.
2. **Planted-fact questions** ask exactly what an extractor keeps.
3. **v1 is templated.** 78% of long passages are exact copies of 69 filler sentences. v2 cuts this to 4.9%, but 54% still share their first six words (real: 0.6%) [6, 7].
4. **Near ceiling.** An oracle reading only the gold meetings scores 95%.
5. **It got deletion wrong.** Synthetic dev: facts lifted notes-only from 64% to 88%, and deleting every transcript moved Concentrate only from 89% to 88% [20]. Real: 57% → 66%, not significant.

![Synthetic vs real](../figures/hero_synthetic_vs_real.svg)

**Figure 3.** The lesson. Left: synthetic test, where three approaches tie. Right: real meetings, where Granola's agent leads Concentrate 92% vs 59%.

**Table 2.** Northwind-XL v1 test split (n = 120), run `20261103T002456Z` [15, 16]. Cost at equal interactive prices [14].

| System | Accuracy [95% CI] | $/question | One-off ingest |
|---|---|---|---|
| Oracle (ceiling) | 95% [91, 98] | 0.082 | — |
| Concentrate | 92% [86, 96] | 0.040 | $4.96 |
| Agent over search + transcripts (b3) | 90% [84, 95] | 0.137 | — |
| Top-k retrieval (k = 10) | 88% [82, 94] | 0.328 | — |
| Mem0 (our wiring) | 81% [73, 88] | 0.017 | $17 |
| Graphiti (our wiring) | 72% [63, 79] | 0.029 | $70 (incl. $10.66 redone after a crash) |
| Summaries only | 71% [62, 78] | 0.017 | — |

- **Top three tie.** Concentrate vs the agent (b3, our re-implementation of the *shape* of Granola's agent) splits 6–4 (McNemar p = 0.75, Holm 1.0). The smallest gap detectable at n = 120 is ~7 points [10, 16].
- **Second judge** changed none of Concentrate's 120 verdicts (97.3% agreement, κ 0.90) [14].
- **Cost.** ~3.5× cheaper than the agent per question (~2× against a prompt-caching agent, an estimate). Counting ingest, cheaper only past ~50 questions per set of meetings [14].
- **Fairness [14].** Mem0 and Graphiti gaps are our wiring (Mem0 saw 25 of 5,292 memories; Graphiti got no summaries). Only Concentrate reads an org directory (it decided 1 of 120 questions). Concentrate was tuned on dev; the baselines weren't.
- **Notes-only on transcript-needed questions: 0 of 17. That 0/17 is close to tautological:** the tag asks whether the summary holds the answer. The measured part: 7 of 17 were confident wrong answers (41% [18, 65]) [21].

## 5. Discussion

- **The real results agree with each other.** A fact memory loses the long tail before the question arrives. A fact layer recovers little of what deletion costs.
- **What worked both times was reaching the transcript** when notes or facts fell short.
- **That is Granola's "go deeper only when needed" [3], applied to retention.** Delete the transcripts and the deeper tier is gone. Here (DEV, n = 47) that tier carried most of the accuracy on detail questions.
- **Cheapest mitigation:** keep a cold copy read only when notes fall short, and make the "degraded" warning name what suffers: who said what, exact wording, passing details.
- **Failure mode depends on the model.** Our notes-only model declined; a synthetic notes-only reader gave 41% confident errors. Granola's own degraded chat should be measured directly.
- **Why synthetic pointed the other way:** its questions are built from planted facts, which an extractor keeps.

## 6. Limitations

1. **Four real meetings, one person.** 39–47 questions; per-category cells of 4–8 are anecdotes.
2. **Gold not hand-checked.** LLM-drafted, approved in bulk with 0 edits. At least one looks wrong; 22 of 47 questions have a non-correct verdict an owner spot-check could move.
3. **DEV, not TEST.** Every real question was seen while building Concentrate, the fallback and the tag rule.
4. **Notes-only is our model,** not Granola's post-deletion chat, and sees all notes.
5. **No search at scale.** All 4 meetings fit in context.
6. **LLM judge.** The primary judge is also the answer model; no blind human check yet.
7. **Coarse tag rule.** Agrees with the generator reference 90% (κ 0.63) on synthetic test; misfires on paraphrased dates and generic status words.
8. **Synthetic diagnostics:** one company, one templated generator version, under-tuned baselines, same author on both sides.

## 7. Next steps

1. **Fresh real test (X1, in progress):** hand-written held-out questions, written after the design freeze [22].
2. **Paired Granola + Wispr Flow recordings** of the same meetings (in progress).
3. **More meetings and contributors,** so results stop being one person's, and enough meetings to test search.
4. **More products through the black-box kit;** any meeting MCP is one JSON spec.
5. **Synthetic upkeep, as diagnostics:** v2/v2.1 runs [8], baseline wiring and directory fixes.

## 8. Reproducibility

Every eval records git SHA, model, prompt versions, seed and date. LLM calls are disk-cached, so reruns are free.

| Input | sha256 prefix | Run details |
|---|---|---|
| `evals/reports/real-deletion-sim-dev.aggregate.json` | `6bc288f59ce8` | run `20261104T012414Z` at `6dc2cb6`, seed 42 |
| `docs/figures/data/real_slice.json` | `1458a32d7155` | content-free real-slice snapshot |
| `evals/reports/20261103T002456Z.json` | `6e5919e818fe` | git `aeafca5` (dirty), Opus 5.5, seed 42, generator `northwind-1` |

Figures are regenerated and checked in CI from these inputs [23].

```bash
uv sync && uv run pytest                                   # offline tests
uv run concentrate deletion-sim --quote --split dev        # exact token-counted quote, no paid calls
uv run concentrate figures 20261103T002456Z                # regenerate every figure
uv run concentrate synth --seed 42 --scale xl              # regenerate Northwind-XL (deterministic, free)
uv run concentrate analyze 20261103T002456Z                # paired stats, power, cost frontier
uv run concentrate notes-vs-transcript                     # re-tag saved answers ($0)
```

**On your own meetings:**
1. Write questions: `real-kit add --split test` or `real-kit import-test`.
2. Price it: `uv run concentrate real-kit run --product granola --split test --estimate`.
3. Run it without `--estimate`.

Everything about your meetings stays in the gitignored `data/real/`; only a content-free aggregate can be committed [9].

## References

Web sources were verified at source on 3 November 2026; repository files are at this branch.

1. C. Newton, interview with Granola CEO Chris Pedregal, *Platformer*, 30 Jul 2026. https://www.platformer.news/granola-chris-pedregal-interview/ (accessed 2026-11-03)
2. Granola Help Centre, "Transcript auto-deletion". https://docs.granola.ai/help-center/consent-security-privacy/transcript-auto-deletion (accessed 2026-11-03)
3. Granola, "How Granola thinks about designing agents", 6 May 2026. https://www.granola.ai/blog/how-granola-thinks-about-designing-agents (accessed 2026-11-03)
4. [`docs/benchmark/DELETION_SIM.md`](../benchmark/DELETION_SIM.md): deletion simulation design and DEV results.
5. [`docs/benchmark/DESIGN.md`](../benchmark/DESIGN.md): benchmark card.
6. [`docs/benchmark/FIDELITY.md`](../benchmark/FIDELITY.md): v1 fidelity audit.
7. [`docs/benchmark/FIDELITY_V2.md`](../benchmark/FIDELITY_V2.md): v2 fidelity audit.
8. [`docs/benchmark/V2_EXTRAS.md`](../benchmark/V2_EXTRAS.md): v2.1 extras.
9. [`docs/benchmark/BLACKBOX.md`](../benchmark/BLACKBOX.md): black-box kit.
10. [`docs/benchmark/METRICS.md`](../benchmark/METRICS.md): metric and statistics definitions.
11. [`docs/archive/PROBE_2026-11-02.md`](../archive/PROBE_2026-11-02.md): read-only probe of Granola's MCP.
12. [`docs/engineering/SCHEMA_NOTES.md`](../engineering/SCHEMA_NOTES.md): Granola field provenance (VERIFIED / ASSUMED).
13. [`docs/systems/concentrate/memory.md`](../systems/concentrate/memory.md): Concentrate.
14. [`docs/results/RED_TEAM.md`](../results/RED_TEAM.md): red-team audit (R1 cost, R2 wiring, R3 directory, R6 tuning, judge audit).
15. [`evals/reports/20261103T002456Z.md`](../../evals/reports/20261103T002456Z.md): synthetic test report.
16. [`evals/reports/20261103T002456Z.analysis.md`](../../evals/reports/20261103T002456Z.analysis.md): paired significance and power.
17. [`docs/results/REAL_SLICE.md`](../results/REAL_SLICE.md): real-meeting slice.
18. [`docs/systems/concentrate/fallback.md`](../systems/concentrate/fallback.md): transcript fallback.
19. [`evals/reports/real-deletion-sim-dev.aggregate.json`](../../evals/reports/real-deletion-sim-dev.aggregate.json): deletion-simulation aggregate.
20. [`docs/results/RETIREMENT.md`](../results/RETIREMENT.md): synthetic retirement curve.
21. [`docs/results/NOTES_VS_TRANSCRIPT.md`](../results/NOTES_VS_TRANSCRIPT.md): notes-sufficient vs transcript-needed.
22. [`docs/PLAN.md`](../PLAN.md): experiment plan (X1–X8).
23. [`docs/figures/README.md`](../figures/README.md): figure inputs and CI check.

Related work, verified at source on 4 November 2026 (details in [`RELATED_WORK.md`](../RELATED_WORK.md)):

24. D. Wu et al. "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory." ICLR 2025. https://arxiv.org/abs/2410.10813 (accessed 2026-11-04)
25. A. Maharana et al. "Evaluating Very Long-Term Conversational Memory of LLM Agents" (LoCoMo). ACL 2024. https://aclanthology.org/2024.acl-long.747/ (accessed 2026-11-04)
26. Y. Hu, Y. Wang, J. McAuley. "Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions" (MemoryAgentBench). arXiv 2025. https://arxiv.org/abs/2507.05257 (accessed 2026-11-04)
27. M. Tavakoli et al. "Beyond a Million Tokens: Benchmarking and Enhancing Long-Term Memory in LLMs" (BEAM). ICLR 2026. https://arxiv.org/abs/2510.27246 (accessed 2026-11-04)
28. H. Bian et al. "RealMem: Benchmarking LLMs in Real-World Memory-Driven Interaction." Findings of ACL 2026. https://aclanthology.org/2026.findings-acl.703/ (accessed 2026-11-04)
29. M. Zhong et al. "QMSum: A New Benchmark for Query-based Multi-domain Meeting Summarization." NAACL 2021. https://arxiv.org/abs/2104.05938 (accessed 2026-11-04)
30. A. Prasad et al. "MeetingQA: Extractive Question-Answering on Meeting Transcripts." ACL 2023. https://aclanthology.org/2023.acl-long.837/ (accessed 2026-11-04)
31. T. Thonet, J. Rozen, L. Besacier. "ELITR-Bench: A Meeting Assistant Benchmark for Long-Context Language Models." COLING 2025. https://arxiv.org/abs/2403.20262 (accessed 2026-11-04)
32. Y. Hu et al. "MeetBench-XL: Calibrated Multi-Dimensional Evaluation and Learned Dual-Policy Agents for Real-Time Meetings." AAAI 2026 workshop. https://arxiv.org/abs/2602.03285 (accessed 2026-11-04)
33. S. Khairy et al. "Evaluation-as-Search: Adaptive Discovery of Grounding Failures in Meeting Assistants" (MeetingProbe, MARC). arXiv 2026. https://arxiv.org/abs/2608.20392 (accessed 2026-11-04)
34. S. Min et al. "FActScore: Fine-grained Atomic Evaluation of Factual Precision in Long Form Text Generation." EMNLP 2023. https://arxiv.org/abs/2305.14251 (accessed 2026-11-04)
35. L. Tang, P. Laban, G. Durrett. "MiniCheck: Efficient Fact-Checking of LLMs on Grounding Documents." EMNLP 2024. https://arxiv.org/abs/2404.10774 (accessed 2026-11-04)
36. Scribbl. "AI note takers tested on five public meetings (October 2026)." https://github.com/msallese31/ai-note-taker-test-2026 (accessed 2026-11-04)
