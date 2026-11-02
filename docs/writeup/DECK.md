# Deck outline (10 slides, ~10 minutes)

Same claims as [MEMO.md](MEMO.md), and the same `{{TBD: source}}` convention. Every chart should be generated from a report file, never redrawn by hand. Footer on every slide: *synthetic benchmark · n shown · dev/test split*.

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

## 4. Granola's live MCP: what it does well
- Commitment tracking is strong and stable (owner, status, "no later evidence" vs "not done").
- Honest abstention on single-microphone speech when asked broadly; explicit dates scoped correctly.
- Retrieval tools ≈0.3–0.4 s with citable chunks.
- Today's baseline is an *agent*, not naive top-k, so that's what this benchmark compares against.
- **Chart:** table of tool latencies (PROBE_V2 §2), with the agent at 8.5 s median beside sub-second tools.

## 5. ...and where it slips (n = 4 meetings: behaviours, not rates)
- Identity: missed a display-name-only attendee, and the second of two people who share a first name.
- Attribution: the same in-person commitment was owned differently depending on how the question was phrased.
- Summary-first answers: exact details cost extra transcript round trips.
- **Chart:** none. Three short anonymised "question → what happened" cards (no meeting content).

## 6. Headline: accuracy, cost, latency
- Overall accuracy with CIs: {{TBD: XL test report}}.
- Reliable pairwise gaps after Holm: {{TBD: analysis.md}}; minimum detectable effect at n = 206: {{TBD}}.
- Pareto frontier at 1k questions per corpus: {{TBD}}.
- Read this slide the same way whoever wins: report ties as ties.
- **Chart:** cost ($/q incl. amortised ingest, log x) vs accuracy scatter with CI whiskers and the frontier line (results explorer `/results`).

## 7. Where each system breaks
- Category heatmap: {{TBD: 2–3 standout cells}}.
- Failure taxonomy: {{TBD: top failure type per system from failures.jsonl}}.
- One worked example per failure type, drilled down to its transcript lines.
- **Chart:** systems × categories accuracy heatmap, plus a stacked bar of failure types per system.

## 8. Can you delete transcripts?
- Dev (90 questions): Concentrate 89% keeping everything → 88% after deleting all earlier transcripts; summaries alone get 64%.
- The memory is ~5% of the raw transcript tokens.
- Top-k over deleted transcripts: only 32% of gold lines left in context at N = 14 days.
- Not tested: verbatim quotes. Test split: {{TBD}}.
- **Chart:** `docs/retirement_curve.svg` (accuracy vs N days, Concentrate vs b1), with gold-line coverage on a second panel.

## 9. What memory unlocks: the pre-meeting brief
- Open commitments recall 91% vs 30% for top-k + LLM, but precision 43% vs 90% (in-person attribution).
- "What changed since we last met": 58% vs 5% recall (19 gold items: directional).
- ~6 ms, no LLM call, no leakage from later meetings (as-of reads).
- **Chart:** grouped bars of precision/recall per brief section, memory vs baseline, with the n under each.

## 10. Limits, and what I'd do next at Granola
- Limits: synthetic data (v2 frames still repeat), b3 isn't Granola's agent, small n, a single answer model.
- Next 1: resolve identity at write time (attendee metadata ↔ mentions).
- Next 2: ship the brief on a bitemporal commitment ledger keyed by recurring event.
- Next 3: run the retirement curve on consented real data, with a verbatim-quote category, before deleting anything.
- **Chart:** none. Repo link and a "run it yourself" command block.
