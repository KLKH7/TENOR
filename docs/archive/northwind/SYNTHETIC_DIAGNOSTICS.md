# Synthetic diagnostics (not evidence of real-world performance)

Results on Northwind, the synthetic benchmark. Use them to debug systems, not to judge them: on real meetings they pointed the wrong way.

## Why this is a diagnostic only

1. **Same author, both sides.** One person built the question generator and Concentrate's fact extractor.
2. **Planted-fact questions favour extraction.** Questions ask about the facts the generator planted, exactly what an extractor keeps.
3. **v1 text is templated.** 78% of long transcript passages are exact copies of 69 filler sentences. v2 cuts this to 4.9%, but 54% of long passages still share their first six words (real meetings: 0.6%) ([FIDELITY_V2](FIDELITY_V2.md)). Results below are v1.
4. **Near ceiling.** An oracle that reads only the right meetings scores 95%.
5. **It got deletion wrong.** Synthetic: adding facts to notes-only recovered most of the loss (64% → 88%, dev, [RETIREMENT](RETIREMENT.md)). Real: 57% → 66%, not significant ([DELETION_SIM](../../tracks/RETENTION.md)).

![The lesson: a tie on synthetic questions became a loss on real meetings](../../figures/hero_synthetic_vs_real.svg)

**The lesson.** The same three approaches tie on synthetic test questions (left) and separate on real meetings (right).

## Seven systems, 120 held-out test questions

Northwind-XL v1: 184 meetings, same answer model (Opus 5.5) and judge for all, run 2026-11-02. Sources: [report](../../../evals/reports/20261103T002456Z.md) · [significance](../../../evals/reports/20261103T002456Z.analysis.md) · [red-team audit](RED_TEAM.md).

| System | Accuracy [95% CI] | $ / question ¹ | p50 latency | One-off ingest |
|---|---|---|---|---|
| Oracle (reads only the gold meetings; a ceiling) | 95% [91, 98] | 0.082 | 4.5 s | — |
| Concentrate | 92% [86, 96] | 0.040 | 3.8 s ² | $5 |
| Agent over search + transcripts (our re-implementation) | 90% [84, 95] | 0.137 (≈0.07–0.08 with prompt caching ³) | 13.2 s | — |
| Top-k hybrid retrieval (k = 10 meetings) | 88% [82, 94] | 0.328 | 6.4 s ² | — |
| Mem0 (our default wiring) | 81% [73, 88] | 0.017 | 3.3 s | $17 |
| Graphiti (our default wiring) | 72% [63, 79] | 0.029 | 4.1 s | $70 ⁴ |
| Summaries only | 71% [62, 78] | 0.017 | 4.7 s ² | — |

- **Top three tie.** Concentrate, the agent and top-k: no reliable gap (McNemar, Holm-corrected). Concentrate and the agent split 10 disagreements 6–4.
- **Second judge** changed none of Concentrate's 120 verdicts.
- **Cost.** Concentrate is ~3.5× cheaper per question than the agent at equal prices (~2× against a prompt-caching agent). Counting ingest, it wins only past ~50 questions per set of meetings.
- **Mem0 and Graphiti trail because of our wiring, not the libraries.** Mem0 saw 25 of its 5,292 memories per question; Graphiti never got the meeting summaries. Fixed, the red team expects 87–92% and 82–88%.
- **Org directory.** Only Concentrate gets it; it decided 1 of 120 questions outright. Its effect on the memory build is unmeasured.
- **Retirement curve (dev).** Deleting every transcript moves Concentrate only from 89% to 88%. No synthetic dev question needs exact wording or who-said-what.
- **Not run yet:** the [v2.1 extras](V2_EXTRAS.md) (exact-quote and speaker-attribution questions).

¹ Re-priced at one interactive Opus 5.5 price ($4/M input, $20/M output); the raw report mixes Batch (50% off) and full-price rows. ² From the 10 questions run interactively. ³ Red-team estimate, not measured. ⁴ Includes $10.66 redone after a crashed embedded database ([HARDENING](../../engineering/HARDENING.md)).

More: [ANALYSIS](ANALYSIS.md) (significance, power, failures) · [ECONOMICS](ECONOMICS.md) (cost) · [NOTES_VS_TRANSCRIPT](NOTES_VS_TRANSCRIPT.md) (what notes-only reading loses).
