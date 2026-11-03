# Deck outline (10 slides, ~10 minutes)

Same claims as [MEMO.md](MEMO.md); sources in [FINDINGS_CHECKLIST.md](FINDINGS_CHECKLIST.md). Every chart should be generated from a report file, never redrawn by hand. Footer on every slide: *synthetic benchmark · n shown · dev/test split*.

## 1. Apple juice from concentrate?
- Chris Pedregal ([Platformer](https://www.platformer.news/granola-chris-pedregal-interview/), Jul 2026): connected to Claude, the agent does "a relatively dumb job at extracting information"; Granola should send "apple juice from concentrate" instead. He framed it as a direction, not a shipped product.
- In the same interview: Granola is looking into "whether we should delete the transcript automatically", and is trying an enhanced transcript "built for agents".
- Granola is already researching these ideas; this project is an independent measurement of them, not a claim to have invented them.
- What gets measured: precomputed memory vs per-query retrieval (accuracy, $, latency), then what deleting transcripts costs.
- **Chart:** none. A two-column picture: "look it up when asked" vs "study ahead of time".

## 2. There was no meeting-memory benchmark, so I built one
- The memory benchmarks I found (LongMemEval, LoCoMo, BEAM, RealMem) are built on chat logs.
- Northwind: 184 meetings, 296 questions (90 dev / 206 test), with the world generated first so every answer is verified and line-cited.
- Granola's exact JSON shape; noise calibrated on real synced meetings.
- 9 categories: temporal, latest state, commitments, the three Sams, unanswerable, and more.
- **Chart:** bar chart of questions per category (BENCHMARK.md table).

## 3. Seven systems, one harness
- Query-time: top-k, summaries only, full-context ceiling, and **b3, an agent shaped like Granola's MCP**.
- Write-time: Mem0, Graphiti, Concentrate.
- Same answer model and judge for every system; the only difference is what's put in front of the model.
- Paired McNemar + Holm, paired bootstrap CIs, a second judge (pilot: 97.5% agreement, κ 0.89).
- **Chart:** the README pipeline diagram (sources → store → query-time / write-time → harness).

## 4. Granola's live MCP: strong, with identity gaps (n = 4 meetings: behaviours, not rates)
- Does well: commitment tracking (owner, status, "no later evidence" vs "not done"), stable across repeats; honest abstention on single-mic speech; explicit dates handled correctly.
- Missed a display-name-only attendee, and the second of two people who share a first name.
- The same in-person commitment got different owners depending on how the question was phrased.
- The agent takes 8.5 s median; its retrieval tools take ≈0.3 s. Today's baseline is an agent, so that's what we compare against.
- **Chart:** a tool-latency table (PROBE_V2 §2) beside three anonymised "question → what happened" cards.

## 5. Headline: a three-way tie at the top, at very different costs
- 120 test questions: Concentrate 92%, MCP-shaped agent 90%, top-k 88%. Statistically tied (Holm p = 1.00); Concentrate and the agent split their 10 disagreements 6–4.
- At equal prices, Concentrate is ~3.5× cheaper per question than the agent ($0.040 vs $0.137) and faster (3.8 s vs 13.2 s p50).
- Reliable gaps: Concentrate beats Mem0 (+11), Graphiti (+20) and summaries (+21). The oracle ceiling is 95%.
- With 120 questions the test can only detect gaps of about 7 points among the top systems.
- **Chart:** cost ($/q at equal prices, ingest amortised over 1k questions, log x) vs accuracy, with CI whiskers and the frontier line. Draw it from `evals/reports/20261103T002456Z.jsonl` tokens, not the raw `cost_usd` (which mixes Batch and full prices).

## 6. Threats to validity (own slide, before anything else is claimed)
- Home advantage: I wrote both the question generator and Concentrate's extractor.
- Only Concentrate gets the org directory; the ablation isn't run yet.
- Unequal tuning: Concentrate was tuned on dev, the others weren't. Graphiti's 72% likely reflects my wiring.
- Saturation: the oracle gets 95% on templated v1 wording. And the agent is my re-implementation, not Granola's.
- **Chart:** none. Five lines, big type.

## 7. Where each system breaks
- Aggregation separates systems most: Concentrate and the agent 83%; top-k and Mem0 42%; Graphiti and summaries 33% (n = 12, not significant after correction).
- "Partial list" is the top failure type (48 of 134 wrong answers). Concentrate's 10 errors: 4 partial lists, 4 reasoning/reading, 1 stale, 1 wrong abstention.
- One worked example per failure type, drilled down to its transcript lines.
- **Chart:** systems × categories accuracy heatmap, plus a stacked bar of failure types per system.

## 8. Can you delete transcripts?
- Dev (90 questions): Concentrate 89% keeping everything → 88% after deleting all earlier transcripts; summaries alone get 64%.
- The memory is ~5% of the raw transcript tokens.
- Top-k over deleted transcripts: only 32% of gold lines left in context at N = 14 days.
- Not tested: verbatim quotes (v2.1 adds them) and the test split. Dev only.
- **Chart:** `docs/retirement_curve.svg` (accuracy vs N days, Concentrate vs b1), with gold-line coverage on a second panel.

## 9. What memory unlocks: the pre-meeting brief
- Open commitments recall 91% vs 30% for top-k + LLM, but precision 43% vs 90% (in-person attribution).
- "What changed since we last met": 58% vs 5% recall (19 gold items: directional).
- ~6 ms, no LLM call, no leakage from later meetings (as-of reads).
- **Chart:** grouped bars of precision/recall per brief section, memory vs baseline, with the n under each.

## 10. What I'd run next, and do at Granola
- Run next: the directory ablation; v2/v2.1 corpora (realistic wording, exact-quote questions); the real slice against Granola's actual agent (in progress).
- Next 1: resolve identity at write time (attendee metadata ↔ mentions).
- Next 2: ship the brief on a bitemporal commitment ledger keyed by recurring event.
- Next 3: run the retirement curve on consented real data, with a verbatim-quote category, before deleting anything.
- **Chart:** none. Repo link and a "run it yourself" command block.
