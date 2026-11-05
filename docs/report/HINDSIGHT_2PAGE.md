# What Does Breadth over Fidelity Cost? An Outside-In Measurement of Meeting Memory for Agents

**Kian Lakhani** · 4 November 2026 · Summary of the [full technical report](HINDSIGHT_REPORT.md) · Independent research; not affiliated with Granola

---

### Summary

Granola has chosen breadth over fidelity: its agent reads many meetings' notes rather than a few transcripts, and users may auto-delete transcripts. I measured the cost of that choice using only public interfaces. On synthetic data, a precomputed fact memory matches transcript reading at roughly 3.5× lower cost per question. On real meetings the tie becomes a loss: **Granola's agent 92% vs a fact memory 59%**. A deletion simulation on real meetings shows **notes alone lose about 32 points**; adding extracted facts recovers little, while **reading the transcript only when notes fall short recovers nearly all of it**. These real-meeting results are development-stage evidence from four meetings.

---

## 1. Question

Granola's agent reads about 100 meetings at a high level rather than 10 in full detail, goes "deeper only when needed", and treats "wrong, but confident" as the failure to avoid [3]. Transcript auto-deletion has shipped: afterwards, chat is "limited to whatever is in your notes", and Granola notes that "Chat quality will be degraded" [2]. The CEO has also described pre-compressing meeting context for agents ("apple juice from concentrate") [1].

**Question:** what does each level of fidelity buy back, per question type, as experienced by an external agent?

## 2. Method

- **Outside-in.** Only public interfaces are used: Granola's API note format and its official read-only MCP. No claims are made about Granola internals.
- **Synthetic track.** *Northwind* is a generated company: 184 meetings in Granola's exact JSON schema, with 296 questions whose answers are known by construction. 120 held-out test questions were run.
- **Black-box track.** The author's 4 real meetings and 47 questions, asked through each product's public MCP.
- **Systems** (one shared answer model, Opus 5.5):
    - query-time: top-k retrieval, summaries only, an agent over search and transcript tools;
    - write-time: Mem0, Graphiti, and **Concentrate**, a reference fact memory that extracts cited, time-stamped facts;
    - a gold-meeting oracle as a ceiling.
- **Measurement.** Six dimensions: correctness, judge reliability, grounding, confident error (answered *and* wrong), equal-price cost, and robustness. Comparisons are paired (McNemar or exact sign test, Holm-corrected, 95% bootstrap CIs), and a second model re-grades every answer.

## 3. Results

### 3.1 Synthetic: a tie at lower cost

**Table 1.** Synthetic held-out test (n = 120). Cost is at equal interactive prices; ingest is one-off.

| System | Accuracy [95% CI] | $ / question | Ingest |
|---|---|---|---|
| Oracle (ceiling) | 95% [91, 98] | 0.082 | — |
| Concentrate (fact memory) | 92% [86, 96] | 0.040 | $5 |
| Agent over search + transcripts | 90% [84, 95] | 0.137 | — |
| Top-k retrieval | 88% [82, 94] | 0.328 | — |
| Mem0 (default wiring) | 81% [73, 88] | 0.017 | $17 |
| Summaries only | 71% [62, 78] | 0.017 | — |

- **Fact memory, agent and top-k are statistically indistinguishable.** Fact memory vs agent splits 6–4 on discordant questions (Holm p = 1.0).
- **The cost advantage is real but conditional.** About 3.5× cheaper than the agent per question (about 2× against an agent with prompt caching), and cheaper overall only once a set of meetings receives more than about 50 questions ([cost frontier](../figures/cost_frontier.svg)).

### 3.2 Real meetings: the tie becomes a loss

![Figure 1](../figures/hero_synthetic_vs_real.svg)

**Figure 1.** The same comparison on synthetic data (left) and on the author's real meetings (right). Granola's agent 92% [82, 100] vs fact memory 59% [44, 74], n = 39. Paired: 14 questions only Granola answered correctly, 1 only the fact memory did (p ≈ 0.001).

The fact memory had extracted 43 facts across the 4 meetings. Ten of its 16 misses were abstentions ("not in the material"), and it scored **0 of 8 on "who said X"** (Granola 7 of 8). It led only on "what changed" (6 of 6 vs 5 of 6), the category a time-stamped store is designed for.

### 3.3 Deletion simulation on real meetings

Four conditions on the same 47 questions, each adding one layer: **(A)** Granola's agent with transcripts; **(B)** notes only, answered by our model over Granola's own notes (≈ chat after auto-deletion); **(C)** B plus the extracted fact layer; **(D)** C plus reading the transcript only when the notes and facts don't cover the question.

![Figure 2](../figures/deletion_sim.svg)

**Figure 2.** Accuracy by question type, and confident-error rate, for each condition.

**Table 2.** Paired effects of each layer (all 47 questions; wins–losses on discordant questions, exact sign test).

| Step | Δ accuracy | Wins–losses | p | On transcript-needed questions (n = 18) |
|---|---|---|---|---|
| Granola → notes only (deletion) | **−32 pts** | 2–17 | 0.001 | −50 pts (0–9, p = 0.004) |
| Notes → + facts | +9 pts | 4–0 | 0.13 | +11 pts |
| + facts → + transcript when needed | **+21 pts** | 10–0 | 0.002 | +33 pts (6–0, p = 0.03) |

## 4. Analysis

1. **The cost of deletion falls on detail questions, and it is large.** Notes alone answered 44% of transcript-needed questions against Granola's 94%. That's the quantitative version of "chat quality will be degraded".
2. **Compression into facts does not substitute for the transcript on real questions.** The fact layer's +9 points isn't distinguishable from zero at this n. On synthetic data the same layer appeared to recover nearly everything (notes only 64% → with facts 88% on the dev set). The difference is the questions: synthetic questions target the facts a generator planted, which is exactly what an extractor keeps, while real questions target whatever was said.
3. **Selective depth recovers the loss.** Reading the transcript only on demand restored 87% (vs 89% for Granola), with no confident errors. It fired on 60% of questions here, so on small corpora it isn't cheap. This is Granola's own "deeper only when needed" principle. **The implication is about retention, not retrieval: once transcripts are deleted, the deeper tier no longer exists.**
4. **Confident error was rare but concentrated.** Granola's agent erred confidently on 3 of 47, two of them unanswerable questions. Our notes-only condition hedged instead. On synthetic data a notes-only reader answered 41% of transcript-needed questions confidently and wrongly, so this behaviour depends on the model and prompt and is worth measuring directly.
5. **Robustness checks.** A second judge agreed on 96–100% of verdicts per condition and on 97% of synthetic verdicts (κ 0.90), with no change in ranking. A red-team audit corrected the cost comparison (originally overstated) and attributed the Mem0 and Graphiti gaps to our configuration. With n = 120, the minimum detectable gap between top systems is about 7 points; smaller differences are reported as ties.

## 5. Implications

- **Retain a reachable detail tier.** Delete transcripts from hot storage if needed, but keep a cold copy that's read only when notes don't cover a question.
- **Make degradation specific.** "Who said what", exact wording and passing details are what notes lose; users enabling deletion could be told so.
- **Evaluate memory on real questions.** Synthetic benchmarks built from planted facts systematically favour extraction-based memory.

## 6. Limitations

1. **The real-meeting evidence is 4 meetings from one person,** with LLM-drafted gold answers approved in bulk, and questions seen during development. It is **DEV, not a held-out test.**
2. **Condition B uses our answer model over Granola's notes,** not Granola's actual post-deletion chat, so A − B mixes deletion with a model change.
3. **One synthetic company, and the same author built both the question generator and the fact extractor.**
4. **Mem0 and Graphiti were under-configured.** The primary judge is an LLM; no human check has been done.

**In progress:** a held-out test on questions written by hand after the design freeze, more meetings and contributors, and other meeting products through the same black-box kit.

**Code, data, figures and every number's source:** [github.com/KLKH7/hindsight](https://github.com/KLKH7/hindsight).

---

1. C. Newton, interview with Chris Pedregal, *Platformer*, 30 Jul 2026. https://www.platformer.news/granola-chris-pedregal-interview/
2. Granola Help Centre, "Transcript auto-deletion". https://docs.granola.ai/help-center/consent-security-privacy/transcript-auto-deletion
3. Granola, "How Granola thinks about designing agents", 6 May 2026. https://www.granola.ai/blog/how-granola-thinks-about-designing-agents
