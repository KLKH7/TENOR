# What Does Breadth over Fidelity Cost? An Outside-In Measurement of Meeting Memory for Agents

**Kian Lakhani** · 4 November 2026 · Two-page summary of the [full technical report](HINDSIGHT_REPORT.md) · Independent research; not affiliated with Granola

*What this is: a two-page summary of what transcript deletion and note-based memory cost an AI agent on real meetings, measured from outside Granola.*

---

### Summary

- **Deleting transcripts costs a third of the answers.** Granola's agent with transcripts: 89%. Notes only: 57%.
- **The loss is in the details.** Questions the notes can't answer: 94% → 44%.
- **Reaching the transcript recovers it; extracted facts don't.** Facts: 66%. Transcript when needed: 87%.
- **Reading real speech, Granola's agent beat a fact memory 92% to 59%.**
- **Caveat:** 4 meetings, one person, questions seen during development (DEV).

---

## 1. Question

Granola's agent reads ~100 meetings at a high level rather than 10 in full. It goes "deeper only when needed" and avoids "wrong, but confident" answers [3].

Users can auto-delete transcripts. Chat is then "limited to whatever is in your notes", and "Chat quality will be degraded" [2].

Granola's CEO has floated pre-compressed context for agents: "apple juice from concentrate" [1].

Prior benchmarks test memory over chat [4] or a model given one meeting's transcript [5]; none tests shipped products through their agent interfaces, or deletion ([related work](../RELATED_WORK.md)).

**Question:** on real meetings, what does each level of detail buy back, and how do answers fail without it?

## 2. Method

- **Outside-in.** Only public interfaces: Granola's note format and its read-only MCP (the protocol agents use to query it).
- **Data.** The author's 4 Granola meetings, 47 questions. An LLM drafted questions and answers; the owner approved them in bulk.
- **Systems.** Granola's agent (black box); top-k retrieval (reads the best-matching meeting); **Concentrate**, our reference memory of extracted, cited, time-stamped facts.
- **Answer types.** Correct, partial, declined ("not in the material"), or wrong but confident.
- **Statistics.** Paired sign tests on questions where two conditions disagree; 95% bootstrap CIs; a second model re-grades every answer.

## 3. Results (real meetings, DEV)

### 3.1 Retention: what deletion costs, and what recovers it

Four conditions on the same 47 questions. Each adds one layer:
**A** Granola's agent, transcripts kept · **B** notes only (≈ chat after deletion; our model) · **C** B + extracted facts · **D** C + transcript passages when C can't answer.

![Figure 1](../figures/deletion_sim.svg)

**Figure 1.** Accuracy by question type, and confident-error rate, per condition.

**Table 1.** Answer types (counts of 47) and paired change vs the row above (wins–losses, exact sign test).

| Condition | Accuracy | Correct / partial / declined / wrong-confident | vs row above | Notes can't answer (n = 18) |
|---|---|---|---|---|
| A: Granola, transcripts | 89% | 42 / 2 / 0 / 3 | — | 94% |
| B: notes only | 57% | 27 / 4 / 16 / 0 | 2–17, p = 0.001 | 44% |
| C: + facts | 66% | 31 / 2 / 14 / 0 | 4–0, p = 0.13 | 56% |
| D: + transcript when needed | 87% | 41 / 3 / 3 / 0 | 10–0, p = 0.002 | 89% |

### 3.2 Question answering: reading real speech

All 4 meetings fit in context, so this tests reading, not search (n = 39).

| System | Accuracy [95% CI] |
|---|---|
| Granola's agent | **92%** [82, 100] |
| Top-k retrieval | 77% [64, 90] |
| Concentrate (fact memory) | **59%** [44, 74] |

- **Paired: 14–1** for Granola's agent (p ≈ 0.001).
- **Concentrate misses details it never extracted.** 0 of 8 on "who said X" (Granola 7 of 8). 10 of its 16 misses were declines.
- **It leads only on "what changed"** (6 of 6 vs 5 of 6), what a time-stamped store is for.
- **A transcript fallback lifted it from 12/22 to 20/22** (8–0, p = 0.008; Granola 19/22). DEV only: these questions shaped the design.

## 4. Analysis

1. **Deletion's cost is large and falls on detail.** That is the measured version of "chat quality will be degraded".
2. **Facts don't replace the transcript.** +9 points is not significant at this n.
3. **Selective depth recovers the loss.** Reading the transcript on demand: 87% vs Granola's 89%. It fired on 60% of questions, so it isn't free. This is Granola's "deeper only when needed", applied to retention: **once transcripts are deleted, the deeper tier is gone.**
4. **Deletion turned right answers into declines, not bluffs.** Our notes-only model declined 16 of 47 and never answered wrongly with confidence. Granola's 3 confident errors came with transcripts (2 on unanswerable questions).
5. **Judges agree.** A second judge matched 96–100% of verdicts per condition; 97% in question answering (κ 0.93).

**Synthetic data: a diagnostic, not evidence.** On Northwind, a synthetic company in Granola's format, the fact memory tied the transcript readers (92 / 90 / 88%) and facts recovered deletion (64% → 88%). Real meetings reversed both. The same author built the generator and the extractor, and its questions target planted facts.

## 5. Implications

- **Keep a reachable detail tier.** Delete the hot copy if needed; keep a cold copy read only when notes fall short.
- **Make the warning specific.** Notes lose who said what, exact wording and passing details.
- **Test memory on real questions.** Planted-fact benchmarks favour extraction.

## 6. Limitations

1. **Small and DEV.** 4 meetings, one person, bulk-approved LLM-drafted gold, questions seen during development.
2. **B is our model** over Granola's notes, not Granola's own post-deletion chat.
3. **No search at scale.** 4 meetings fit in context.
4. **LLM judge.** No human check yet.

**In progress:** hand-written held-out questions; paired Granola + Wispr Flow recordings; more meetings, contributors and products.

**Code, data, figures and every number's source:** [github.com/KLKH7/hindsight](https://github.com/KLKH7/hindsight).

---

1. C. Newton, interview with Chris Pedregal, *Platformer*, 30 Jul 2026. https://www.platformer.news/granola-chris-pedregal-interview/
2. Granola Help Centre, "Transcript auto-deletion". https://docs.granola.ai/help-center/consent-security-privacy/transcript-auto-deletion
3. Granola, "How Granola thinks about designing agents", 6 May 2026. https://www.granola.ai/blog/how-granola-thinks-about-designing-agents
4. D. Wu et al. "LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory." ICLR 2025. https://arxiv.org/abs/2410.10813
5. M. Zhong et al. "QMSum: A New Benchmark for Query-based Multi-domain Meeting Summarization." NAACL 2021. https://arxiv.org/abs/2104.05938
