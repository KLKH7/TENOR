# Hindsight: What Does Breadth Over Fidelity Cost? An Outside-In Benchmark for Agent Memory over Meeting Notes

**Kian Lakhani** · Technical report · 4 November 2026 · Independent research prototype, not affiliated with Granola

## Abstract

Agents increasingly read meeting memory through MCP servers, and Granola, the meeting-notes product this work targets, has made a public trade-off: its agent reads many meetings shallowly rather than a few in detail, and users may auto-delete transcripts so that chat works from notes alone [2, 3]. We ask what that trade-off costs, measured only through public interfaces. Hindsight pairs a synthetic corpus in Granola's API format with known answers by construction (Northwind: 184 meetings, 296 questions) and a black-box kit that scores any meeting MCP on a user's own meetings. Three findings. (1) On 120 held-out synthetic questions, a write-time fact memory (Concentrate, our reference system) ties an agent over search and transcripts (92% vs 90%; Holm p = 1.0) at about 3.5× lower per-question cost at equal prices. (2) On the author's real meetings the tie becomes a loss: Granola's own agent 92% vs Concentrate 59% (n = 39; paired 14–1, p ≈ 0.001). (3) Simulating transcript deletion on real meetings (DEV, n = 47), accuracy falls from 89% with transcripts to 57% on notes alone; a fact layer recovers little (66%), while reading the transcript when notes fall short recovers most (87%). The main limitation is the real data: 4 meetings from one person, LLM-drafted gold answers approved in bulk, and questions seen during development.

## 1. Motivation

External agents (Claude, ChatGPT) reach Granola only through its MCP. In a July 2026 interview Granola's CEO said that an agent connected to Granola through Claude can "look at the 20 most relevant meetings" and does "a relatively dumb job at extracting information", proposed sending agents "apple juice from concentrate" instead, and said Granola was "looking into whether we should delete the transcript automatically" [1]. (Our own read-only probe found the MCP has since become an agent that searches, reads and cites, so the 20-meeting description is out of date [11].) Granola's agent-design post states the trade-off explicitly: "breadth over fidelity", reading about 100 meetings at a high level rather than 10 in full, with transcripts costing roughly 10× the tokens of notes, and going deeper only when needed; the failure it most wants to avoid is "wrong, but confident" [3]. Transcript auto-deletion has shipped: afterwards chat is "limited to whatever is in your notes", and the help page warns "Chat quality will be degraded" [2]. Granola can measure this internally. What an outside benchmark adds is shareable ground truth, comparability across products, and the view an external agent actually gets.

## 2. Benchmark

**Outside-in principle.** Everything is built on public interfaces (Granola's REST API format and its official, read-only MCP) and on data that can be shared [5, 12]. We make no claims about Granola internals.

**Northwind (public track).** A synthetic company whose world (people, projects, decisions, commitments and their changes) is generated first, then rendered into meetings in Granola's exact API JSON shape, validated by the same Pydantic models as real data; the same seed yields byte-identical output [5]. It has 184 meetings and 296 questions (90 dev / 206 test) across nine categories, including who-said-what without speaker labels, disambiguating people who share a first name, and as-of-date state. A fidelity audit compared its transcripts to real ones and found a serious flaw in v1: 78% of transcript passages of six or more words were exact copies drawn from 69 filler sentences, which makes retrieval unrealistically easy [6, 7]. Generator v2 cuts this to 4.9%, but 54% of long passages still share their first six words with another (0.6% in real meetings) [7]. **All synthetic results below are on v1.** A v2.1 set adds exact-quote and speaker-attribution questions; it has not been run [8].

**Black-box track.** For products that cannot load our corpus, the kit asks the product's public MCP the user's own questions, through a single allowlisted tool (`query_granola_meetings` for Granola); a wrapper refuses every other tool before anything is sent, and only content-free aggregates can be written to committable files [9].

**Measurement.** There is no single score. [METRICS.md](../benchmark/METRICS.md) defines six dimensions: (A) correctness, (B) judge reliability, (C) grounding, (D) knowing what it doesn't know, including *confident error* (answered and judged wrong), (E) efficiency at equal prices ($4/M input, $20/M output tokens) with ingest amortised, and (F) robustness [10]. All comparisons are paired: 95% bootstrap CIs (2,000 resamples), McNemar's exact test (exact sign tests on the real data), Holm correction across all pairs, and a power table so that "tied" means "within the detectable gap" (about 7 points for the top pairs at n = 120). A second judge (Sonnet 5.5) re-grades every answer.

## 3. Systems

*Query-time* systems do all their work when a question arrives: top-k hybrid retrieval (b0), summaries only (b1), an oracle that reads only the gold meetings (b2o, a ceiling), and an agent over search and transcript tools (b3, our re-implementation of the *shape* of Granola's agent; the real one is only ever tested as a black box). *Write-time* systems build memory as meetings arrive: Mem0 (b4), Graphiti (b5) and **Concentrate**, which resolves speakers and entities, extracts atomic facts that cite transcript lines, drops facts their cited lines don't entail, and stores facts with the dates they were true [13]. Concentrate is a **reference system, not a product**: it exists to show what a precomputed memory gains and loses. All systems use the same answer model (Opus 5.5) and judge. The red-team audit [14] found three fairness problems we report rather than hide: Mem0 saw 25 of its 5,292 memories per question and Graphiti was never given the meeting summaries (so their gaps are about *our wiring*); only Concentrate reads an org directory (it decided 1 of 120 questions outright; its effect on the memory build is unmeasured); and Concentrate's query layer and extractor were tuned on dev against the generator's own templates, while the baselines were untuned.

## 4. Results

### 4a. Synthetic: a statistical tie at lower cost (TEST, n = 120)

**Table 1.** Northwind-XL v1 test split, run `20261103T002456Z` [15, 16]. Cost re-priced at equal interactive prices [14].

| System | Accuracy [95% CI] | $/question | One-off ingest |
|---|---|---|---|
| Oracle (ceiling) | 95% [91, 98] | 0.082 | — |
| **Concentrate** | **92% [86, 96]** | **0.040** | $4.96 |
| Agent over search + transcripts (b3) | 90% [84, 95] | 0.137 | — |
| Top-k retrieval (k = 10) | 88% [82, 94] | 0.328 | — |
| Mem0 (our wiring) | 81% [73, 88] | 0.017 | $17 |
| Graphiti (our wiring) | 72% [63, 79] | 0.029 | $70 (incl. $10.66 redone after a crash) |
| Summaries only | 71% [62, 78] | 0.017 | — |

Concentrate, the agent and top-k are tied: Concentrate and the agent disagree on 10 questions, splitting 6–4 (McNemar p = 0.75, Holm 1.0), and the second judge changed none of Concentrate's 120 verdicts (97.3% agreement overall, κ 0.90) [14, 16]. The cost advantage is real but modest: $0.040 vs $0.137 is about 3.5× at equal prices, about 2× against an agent with prompt caching (a red-team *estimate*, not measured), and counting ingest Concentrate is cheaper only once a set of meetings receives more than about 50 questions (51; about 140 against a caching agent) [14]. Beating summaries (+21 points) survives Holm correction. Concentrate vs Mem0 (+11, Holm p = 0.028) is also statistically reliable, but we attribute both the Mem0 and Graphiti gaps to our wiring of those libraries, not the libraries themselves; the red team expects 87–92% and 82–88% once fixed [14].

![Cost frontier](../figures/cost_frontier.svg)

**Figure 1.** Accuracy vs $/question with ingest amortised over 10, 100 and 1,000 questions per corpus (TEST, n = 120, equal prices).

### 4b. Real meetings: the tie becomes a loss (real DEV, n = 39)

![Synthetic vs real](../figures/hero_synthetic_vs_real.svg)

**Figure 2.** Left: synthetic test tie. Right: the author's 4 real meetings, where Granola's agent leads Concentrate 92% vs 59% (top-k with k = 1: 77%).

Through the black-box kit, on 4 real meetings (3 remote, 1 in person), 47 questions were LLM-drafted and their gold answers approved by the owner in bulk with 0 edits; a budget cap stopped Granola and Concentrate after 39 (the 8 unrun were all 6 unanswerable and 2 temporal) [17]. Granola's agent scored 92% [82, 100], Concentrate 59% [44, 74]; paired, Granola alone was right 14 times and Concentrate alone once (sign test p ≈ 0.001); the judges agree on 97% of verdicts (κ 0.93). Why: Concentrate kept 43 facts and 11 commitments for the 4 meetings and answers only from those, while the real questions ask about long-tail details. Ten of its 16 non-correct answers were abstentions ("the material doesn't mention it"), and it scored 0% on "who said X" (Granola 88%), where it twice named the wrong person. It led only on "what changed" (100% vs 83%, n = 6), the category its time-aware store is built for. A transcript fallback (retrieve passages when the facts don't cover a question) took Concentrate from 12/22 to 20/22 on the questions reached before the budget cap, 8 fixed and 0 broken (p = 0.008), against Granola's 19/22 on the same questions; these questions motivated the design, so this is DEV only [18].

### 4c. Deletion simulation on real meetings (real DEV, n = 47)

We cannot switch deletion on inside Granola, so we rebuild four conditions outside it, each adding one layer: **A**, Granola's agent with transcripts; **B**, our answer model over Granola's own notes for every meeting (an approximation of post-deletion chat); **C**, B plus Concentrate's facts, every transcript deleted; **D**, C plus transcript passages only when C abstains or says the material doesn't cover the question [4]. B, C and D share the model and prompt; A − B therefore mixes deletion with a model and prompt change.

**Table 2.** Run `20261104T012414Z` at `6dc2cb6`, seed 42, $6.42 [4, 19]. Tags from a portable string-matching rule.

| Condition | Accuracy [95% CI] | Confident error | Transcript-needed (n = 18) | Paired vs previous row |
|---|---|---|---|---|
| A: Granola, transcripts kept | 89% [79, 98] | 6% | 94% | — |
| B: notes only | 57% [43, 70] | 0% | 44% | 2 wins / 17 losses, p = 0.001 |
| C: notes + fact layer | 66% [51, 79] | 0% | 56% | 4 / 0, p = 0.13 |
| D: + transcript fallback | 87% [77, 96] | 0% | 89% | 10 / 0, p = 0.002 |

Deletion costs 32 points overall and 50 on transcript-needed questions. Without transcripts our model hedged rather than bluffed: none of B's or C's wrong answers was confident. Granola's only confident errors (6%) came on 2 of the 6 unanswerable questions. The fact layer alone recovers little and not reliably (+9, 4–0, p = 0.13). The fallback recovers most of the gap (87% vs 89%), but it reads transcripts, so it models "keep a cold copy", not deletion. One result counts against our own tagging: notes-only scored only 57% even on *notes-sufficient* questions, so either the rule over-calls sufficiency or the model under-uses the notes. On synthetic dev data the picture was very different: deleting every transcript moved Concentrate only from 89% to 88%, and notes-only scored 64% against 88% for notes plus facts [20]. The fact layer looked strong there because synthetic questions target exactly the facts it extracts.

![Transcript fallback](../figures/fallback_dev.svg)

**Figure 3.** Concentrate with and without the transcript fallback by category on 22 real DEV questions (8 won, 0 lost).

### 4d. Notes-sufficient vs transcript-needed (synthetic TEST)

We tag each question *notes-sufficient* if its answer survives in the gold meetings' notes, else *transcript-needed*, computed deterministically rather than hand-labelled [21]. On the synthetic test set, summaries-only (b1, a notes-only reader) answered **0 of 17** transcript-needed questions correctly, against 76–82% for every transcript reader. **That 0/17 is close to tautological:** b1 reads summaries and the tag asks whether the summary holds the answer, so it is near 0% by construction. The measured quantities are what happens instead: b1 answered 7 of 17 *confidently and wrongly* (41% [18, 65], against 0–6% for transcript readers), and transcript readers recover 76–82 points. n is 17 and the share of transcript-needed questions (about 13%) is a generator setting. On real meetings the same rule tags 18 of 41 answerable questions (44%) transcript-needed, probably an overestimate since the rule is stricter than the reference on synthetic data.

![Notes vs transcript](../figures/notes_vs_transcript.svg)

**Figure 4.** Accuracy and confident error by tag, synthetic test report `20261103T002456Z`.

## 5. Discussion

Synthetic and real results disagree because the synthetic questions are generated from the facts the generator planted, which is the kind of fact Concentrate's extractor is built to keep; the same author designed both. Real questions, drafted from full transcripts, ask about whatever was said, and a write-time memory loses the long tail before the question arrives. The synthetic benchmark therefore could not show the failure mode that matters most, and v2.1 adds exact-quote and speaker-attribution questions to close that gap. For deletion, the real-meeting simulation points the same way: what recovered the loss was *reaching the transcript when the notes fell short*, not a richer precomputed layer. That is Granola's own design principle, "go deeper only when needed" [3], applied to retention: if transcripts are deleted, the "deeper" tier disappears, and our evidence (DEV, n = 47) suggests that tier carries most of the accuracy on detail questions. The cheapest mitigation consistent with these results is to keep transcripts reachable on demand (for example, a cold copy read only when notes don't cover a question) and to make the "degraded" warning specific about which questions suffer. On the confident-error axis our evidence is mixed: our notes-only model hedged, but synthetic notes-only reading produced 41% confident errors on transcript-needed questions, and Granola's degraded chat may behave differently from either.

## 6. Limitations

1. **Four real meetings, one person.** Every real number comes from the author's own account; 39–47 questions; per-category cells of 4–8 are anecdotes.
2. **LLM-drafted, bulk-approved gold.** Real gold answers were drafted by an LLM and approved in bulk with 0 edits; at least one looks wrong, and 22 of 47 questions have at least one non-correct verdict that an owner spot-check could move.
3. **Real results are DEV, not TEST.** All real questions were seen while building Concentrate and the fallback; no held-out real test exists yet.
4. **Simulated notes-only uses our model.** Condition B is our answer model over Granola's notes, not Granola's actual post-deletion chat, and it sees all notes (an upper bound at 4 meetings).
5. **One synthetic company,** generated by one seed and one generator version (v1, with known templating).
6. **LLM judge.** The primary judge is also the answer model; a second judge measures that bias rather than removing it, and the blind human hand check has not been done.
7. **Small n per category** on both tracks; the minimum detectable gap at n = 120 is about 7 points for the top pairs.
8. **Under-tuned baselines.** Mem0, Graphiti and b3 used default settings and untuned prompts; Concentrate was tuned on dev and alone received the org directory.
9. **The author built both the generator and the extractor,** which is the likely cause of the synthetic tie hiding the real loss.
10. **Tag rule is coarse.** The portable notes-sufficiency rule agrees with the generator reference 90% (κ 0.63) on v1 test and misfires on paraphrased dates and generic status words.

## 7. Next steps

(1) **The fresh real test (X1, in progress):** Granola's agent vs Concentrate with and without the fallback vs top-k on the owner's hand-written held-out questions, written after the design freeze [22]. (2) **v2 and v2.1 runs:** rebuild on the less templated corpus and run the retirement curve on exact-quote and who-said questions. (3) **Fairness fixes:** Mem0 with a larger retrieval budget, Graphiti with summaries, and a no-directory Concentrate ablation. (4) **More products through the black-box kit,** since any meeting MCP can be described by a JSON spec. (5) **More meetings and contributors,** so real results stop being one person's.

## 8. Reproducibility

Every eval records git SHA, model, prompt versions, seed and date; LLM calls are disk-cached, so reruns are free. Synthetic test report `evals/reports/20261103T002456Z.json` (sha256 prefix `6e5919e818fe`; git `aeafca5`, dirty; Opus 5.5; seed 42; generator `northwind-1`). Deletion-simulation aggregate `evals/reports/real-deletion-sim-dev.aggregate.json` (`6bc288f59ce8`; run at `6dc2cb6`). Real-slice snapshot `docs/figures/data/real_slice.json` (`1458a32d7155`). Figures are regenerated and checked in CI from these inputs [23].

```bash
uv sync && uv run pytest                                   # offline tests
uv run concentrate synth --seed 42 --scale xl              # regenerate Northwind-XL (deterministic, free)
uv run concentrate eval --xl --split test --estimate       # price a run before spending
uv run concentrate analyze 20261103T002456Z                # paired stats, power, cost frontier
uv run concentrate figures 20261103T002456Z                # regenerate every figure
uv run concentrate notes-vs-transcript                     # re-tag saved answers ($0)
uv run concentrate deletion-sim --quote --split dev        # exact token-counted quote, no paid calls
```

To score Granola (or any meeting MCP) on your own meetings: write questions (`real-kit add --split test` or `real-kit import-test`), then `uv run concentrate real-kit run --product granola --split test --estimate` and run without `--estimate`. Everything about your meetings stays under the gitignored `data/real/`; only a content-free aggregate can be committed [9].

## References

Web sources were verified at source on 3 November 2026 (recorded in [`FINDINGS_CHECKLIST.md`](../writeup/FINDINGS_CHECKLIST.md)); repository files are at this branch.

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
