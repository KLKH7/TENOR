# Precomputed meeting memory vs per-query retrieval: what a benchmark says

*Kian Lakhani · 2026-11-03 · repo: github.com/<user>/hindsight · every number traces to a file in [FINDINGS_CHECKLIST.md](FINDINGS_CHECKLIST.md)*

**Headline.** On 120 held-out test questions, Concentrate (92%), an agent modelled on Granola's MCP (90%) and top-k retrieval (88%) are **statistically tied**. Concentrate and the agent disagreed on only 10 questions, splitting 6–4. Concentrate is about **3.5× cheaper per question than the agent at equal prices** and answers in about 3.8 s vs 13.2 s. It **reliably beats** Mem0 (+11 points), Graphiti (+20) and summaries alone (+21). The threats to validity below decide how far to trust this. Please read them before the table.

## The question

In a [July 2026 Platformer interview](https://www.platformer.news/granola-chris-pedregal-interview/), Chris Pedregal said that an agent connected to Granola through Claude does "a relatively dumb job at extracting information". He said Granola should instead send agents "apple juice from concentrate". He also said Granola is looking into "whether we should delete the transcript automatically", and is trying an enhanced transcript "built for agents". Granola is already researching all three ideas, so my contribution is an **independent, reproducible measurement**, not the ideas themselves. Does memory built ahead of time answer agents' questions about months of meetings better, cheaper or faster than retrieving at query time? And can the transcripts then go?

## What I built

- **Northwind, an open meeting-memory benchmark.** The memory benchmarks I found (LongMemEval, LoCoMo, BEAM, RealMem) are built on chat logs. Northwind is a synthetic company with 184 meetings in Granola's exact API shape, with noise calibrated on my real meetings. It has 296 questions (90 dev / 206 test), each with a verified answer and gold transcript lines. They cover unlabelled speakers, three Sams, values *as of* a date, and commitments that slip or get dropped.
- **7 systems under one harness, all with the same answer model (Opus 5.5) and judge.** Query-time: top-k, summaries only, an oracle ceiling, and **an agent whose tools mirror Granola's MCP**. Write-time: Mem0, Graphiti, and Concentrate (entity resolution → line-cited facts → entailment gate → bitemporal store).
- **Rigour:** paired McNemar tests with Holm correction over 21 pairs, paired bootstrap CIs, power analysis, a Pareto frontier that amortises ingest, and a second judge (on the pilot: 97.5% agreement, κ 0.89). Generation is deterministic, the Granola MCP source is read-only, and builds resume after a stall.

## Granola's live MCP: strong, with identity gaps

These come from two read-only probes on my own account: 4 meetings and 14 questions, so they show behaviours, not rates. **What it does well:** commitment tracking (owner and status, with "no later evidence" kept separate from "not done", cited and stable across repeats), honest abstention on single-microphone speech when asked broadly, correct handling of explicit dates, and retrieval tools that answer in about 0.3 s. **Where it slipped:** it missed an attendee known only by display name and the second of two people who share a first name, because attendee metadata was never joined to the text. The same in-person commitment got a different owner depending on how the question was phrased. And `query_granola_meetings` took a median of 8.5 s.

## Threats to validity (read before the numbers)

1. **Home advantage.** I wrote both the question generator and Concentrate's extractor, and the fact types it extracts match the question categories. Any synthetic benchmark built by a system's author favours that system.
2. **Only Concentrate gets the org directory** (names, roles, emails) to resolve people. The ablation that removes it hasn't been run, so part of the gap over Mem0 and Graphiti may be the directory, not the architecture.
3. **Unequal tuning.** Concentrate was tuned on dev. Mem0, Graphiti and the agent run with my default wiring and untuned prompts. **Graphiti's 72% most likely reflects my integration, not Graphiti itself.**
4. **The benchmark saturates.** An oracle reading only the gold meetings scores 95%, and the top three systems score 88–92%. This is the v1 corpus, whose wording is templated. The harder v2/v2.1 corpora exist but haven't been run.
5. **The agent is my re-implementation** of the shape of Granola's MCP, not Granola's agent, which can't read synthetic data.
6. **Smaller statistical issues.** n = 120 can only detect gaps of about 7 points between the top systems. The primary judge is also the answer model, and the second judge was checked on the pilot only.

## Results (Northwind-XL v1, 120 of the 206 held-out test questions, run once)

Same answer model (Opus 5.5), prompt and judge for every system. $/question is recomputed at **equal, interactive prices** from each answer's tokens. The raw report mixes Batch-API prices (b0, b1, oracle, Concentrate) with full prices (agent, Mem0, Graphiti). Ingest uses Batch prices (it's asynchronous).

| System | Accuracy [95% CI] | $/question | One-off ingest | p50 latency | vs Concentrate (gap, Holm p) |
|---|---|---|---|---|---|
| Oracle (gold meetings only; a ceiling) | 95% [91, 98] | 0.082 | n/a | 4.5 s ¹ | +3, 1.00 |
| **Concentrate** | **92% [86, 96]** | **0.040** | $5 | 3.8 s ¹ | n/a |
| b3 agent (MCP-shaped) | 90% [84, 95] | 0.137 | n/a | 13.2 s | −2, 1.00 |
| b0 top-k (k = 10 meetings) | 88% [82, 94] | 0.328 | n/a | 6.4 s ¹ | −3, 1.00 |
| Mem0 | 81% [73, 88] | 0.017 | $17 | 3.3 s | **−11, 0.028** ✅ |
| Graphiti | 72% [63, 79] | 0.029 | $70 ² | 4.1 s | **−20, <0.001** ✅ |
| Summaries only | 71% [62, 78] | 0.017 | n/a | 4.7 s ¹ | **−21, <0.001** ✅ |

¹ Latency from the 10 questions run interactively. ² Includes $10.66 redone after a crashed embedded database.

- **By category** (12–14 questions each, so no per-category gap survives correction): aggregation separates systems most. Concentrate and the agent get 83%, top-k and Mem0 42%, Graphiti and summaries 33%. Concentrate is lowest of the top three on multi-hop (77% vs 85%). Summaries fall to 31% on decision changes.
- **Failure types:** "partial list" is the most common error overall (48 of 134 wrong answers). Concentrate's 10 wrong answers: 4 partial lists, 4 reasoning/reading, 1 stale value, 1 wrong abstention.
- **Cost-accuracy frontier** (ingest spread over 1,000 questions): summaries, Mem0, Concentrate and the oracle are on it. The agent and top-k are dominated, mostly because they read 30k–81k tokens per question vs Concentrate's 8.8k. Concentrate costs more per question than Mem0 or Graphiti; it's on the frontier because it's more accurate.

## Can you delete transcripts?

On the **dev split (90 questions; test not run)**, Concentrate scored 89% keeping every transcript and 88% after deleting every transcript older than the question. Only 4 verdicts changed, and 3 of those don't follow the retention window, which points to noise. The memory is ~5% of the transcript tokens. Summaries alone scored 64%. Top-k with deleted transcripts keeps only 32% of the gold lines in context at 14 days, and 2% for entity questions. **Caveat:** this corpus has no questions that need the verbatim transcript, and that's exactly what deletion would cost. v2.1 adds exact-quote and speaker-attribution questions; they haven't been run.

## Unit economics (a guess at usage: 40 meetings and 30 questions per user per month; Opus answers, Sonnet ingest)

| | b3 agent | b0 top-k | Concentrate | Mem0 | Graphiti |
|---|---|---|---|---|---|
| Ingest $/meeting (Batch) | n/a | n/a | $0.014 | $0.046 | $0.163 |
| Answer $/question (equal prices) | $0.137 | $0.328 | $0.040 | $0.017 | $0.029 |
| **$/user/month** | **$4.12** | **$9.84** | **$1.74** | **$2.36** | **$7.36** |

Concentrate's ingest pays for itself against the agent once a user asks about ~6 questions a month, about 1 per 7 meetings. Ingest is cheap because it makes ~1.2 LLM calls per meeting, against ~6 for Mem0 and ~17 for Graphiti. **This flips with a small answer model:** with Haiku answering, query-time costs ~$0.10/user/month and write-time loses on cost. So the real decision is which model must answer to be accurate enough, and that's untested here.

## The pre-meeting brief: what memory makes possible

Before each meeting, as-of SQL reads over the store (~6 ms, no LLM call, no leakage from later meetings) list attendees' open commitments and what changed since the group last met. On 36 test meetings, compared with top-k + Sonnet over earlier meetings:
- **Open commitments:** recall **91% vs 30%** (158 vs 52 of 174). Precision goes the other way, **43% vs 90%**, mostly because of owner attribution in in-person 1:1s.
- **What changed since we last met:** recall **58% vs 5%**, but on only 19 gold items, so it's directional.

**Other limits:** the data is synthetic and calibrated on 4 real meetings from one person. v1, used above, is templated: 78% of longer passages are exact copies. I tuned on dev only and ran test once ([tuning log](../CONCENTRATE_TUNING.md)).

## What I'd run next (concrete, in order)

1. **The directory ablation.** Either remove Concentrate's org directory or give it to every system, then re-run test. This is the single biggest unknown in the result.
2. **The v2 / v2.1 corpora.** v2 has realistic wording. v2.1 adds hybrid rooms, identity mess, corrections, and **exact-quote and speaker-attribution questions**, so it can both break the saturation and show what deleting transcripts really costs. I'd also rerun the second judge on the final report.
3. **The real slice, in progress.** ~30 hand-verified questions about my real meetings, answered by Granola's *actual* `query_granola_meetings` and by Concentrate. It's the only comparison against the real product.

## What I'd do at Granola

1. **Resolve identity at write time.** Join attendee metadata (emails, display names) to transcript mentions when the meeting is ingested, and expose it as a lookup. That directly targets the two identity misses above.
2. **Ship the brief as a bitemporal commitment ledger.** Key series on `recurring_event_id` and fix in-person owner attribution, the biggest precision lever.
3. **Before deleting transcripts, run the retirement curve on consented real data** with a verbatim-quote category, and keep line-ID pointers from facts to any archived text.
