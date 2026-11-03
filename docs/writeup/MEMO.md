# I tested my meeting memory against Granola's agent. On real meetings, it lost.

*Kian Lakhani · 2026-11-03 · repo: github.com/<user>/hindsight · every number traces to a file in [FINDINGS_CHECKLIST.md](FINDINGS_CHECKLIST.md)*

**Headline.** I asked open-ended questions about my own real meetings. **Granola's actual agent scored 92%; my precomputed memory, Concentrate, scored 59%** (n = 39; the 95% CIs don't overlap; paired 14–1). On "who said X" Concentrate got 0% to Granola's 88%. It led only on "what changed" (100% vs 83%, n = 6). On my synthetic benchmark the two **tie** (92% vs 90%), but only because those questions target exactly the facts my extractor keeps. **The lesson for Granola:** compressing meetings into facts loses the long tail that real questions ask about. That's evidence *against* deleting transcripts, and *for* a hybrid: facts for state and change, transcripts kept and searchable.

## The question

In a [July 2026 Platformer interview](https://www.platformer.news/granola-chris-pedregal-interview/), Chris Pedregal said that an agent connected to Granola through Claude does "a relatively dumb job at extracting information". He said Granola should instead send agents "apple juice from concentrate". He also said Granola is looking into "whether we should delete the transcript automatically", while noting you "might want to still go back and chat with that meeting". Granola is already researching these ideas, so my contribution is an **independent measurement**: does memory compressed ahead of time answer agents' questions as well as retrieving at question time? And can the transcripts then go?

## What I built

- **Northwind**, an open meeting-memory benchmark. The memory benchmarks I found are built on chat logs. Northwind has 184 synthetic meetings in Granola's exact API shape, calibrated on my real meetings, and 296 questions with verified answers and gold transcript lines.
- **7 systems under one harness**, all with the same answer model (Opus 5.5) and judge: top-k, summaries only, an oracle, an agent modelled on Granola's MCP, Mem0, Graphiti, and **Concentrate** (entity resolution → line-cited facts → entailment gate → bitemporal store).
- **A real-meeting slice.** Questions about my 4 real meetings, answered by Granola's live `query_granola_meetings` (read-only) and by Concentrate built from the same notes through the MCP.
- **Rigour:** paired McNemar tests with Holm correction, bootstrap CIs, a second judge, and a red-team audit of my own headline ([RED_TEAM.md](../RED_TEAM.md)). The audit corrected several of my claims, and the numbers below are the corrected ones.

## Result 1: real meetings, where Granola's agent wins

| System | Accuracy [95% CI] | n | Median latency |
|---|---|---|---|
| **Granola's agent** (live) | **92%** [82, 100] | 39 | 7.9 s ¹ |
| Top-k, reads the 1 best-matching meeting | 77% [64, 90] | 39 | 3.3 s ² |
| **Concentrate** | **59%** [44, 74] | 39 | 4.2 s |

¹ One remote MCP call including Granola's agent loop, against one local answer call for ours. Not like for like. ² Measured on its run over all 47 questions.

- **By category** (5–8 questions each, so read these as anecdotes):
  - who said X: Granola 88%, Concentrate **0%**;
  - commitment status: 100% vs 75%;
  - which person: 80% vs 40%;
  - what changed: **Concentrate 100%**, Granola 83%.
- **Why Concentrate loses.** It kept 43 facts and 11 commitments for 4 meetings, and it answers only from those. 10 of its 16 misses were "the material doesn't mention it". The detail was in the transcript; it just wasn't a fact the extractor had kept. On "who said", it also ignored Granola's named system-audio speaker labels. Simply reading the right meeting's transcript (top-k) beats reading my extracted notebook.
- **Second judge:** agreed on 97% of verdicts (κ 0.93), and the ranking is unchanged.

## Result 2: synthetic benchmark, a tie that the setup makes easy

120 held-out test questions. $/question is at equal interactive Opus prices.

| System | Accuracy [95% CI] | $/question |
|---|---|---|
| Oracle (gold meetings only) | 95% [91, 98] | 0.082 |
| **Concentrate** | **92%** [86, 96] | **0.040** |
| Agent, my re-implementation of Granola's MCP | 90% [84, 95] | 0.137 (≈0.07–0.08 if it cached prompts) |
| Top-k (10 meetings) | 88% [82, 94] | 0.328 |
| Mem0 (my default wiring) | 81% [73, 88] | 0.017 |
| Graphiti (my default wiring) | 72% [63, 79] | 0.029 |
| Summaries only | 71% [62, 78] | 0.017 |

What survived the red team:
- **Concentrate, the agent and top-k are statistically tied** (Holm p = 1.0). Concentrate and the agent disagreed on 10 questions, splitting 6–4.
- **Cost:** Concentrate is ~3.5× cheaper per question than the agent, or ~2× against an agent that caches its prompts. Counting ingest ($5), it only comes out cheaper once a set of meetings gets more than **~50 questions** (~140 against the caching agent).
- **Beating summaries (+21) is solid.** Beating Mem0 (+11) and Graphiti (+20) **reflects my wiring, not those libraries.** Mem0 saw 25 of its 5,292 memories per question, and Graphiti never received the summaries.
- **The org directory that only Concentrate gets** decided 1 of 120 questions outright. Its effect on the build is unmeasured.
- **Gold errors exist** (e.g. a generator bug producing "reported complete on None"), so the 95% oracle is "ceiling minus gold errors".

**Why the two results disagree.** I wrote both the question generator and the extractor, so the synthetic questions ask about the kinds of facts the extractor keeps. Real questions don't respect that boundary. This is the external-validity gap the real slice was built to find, and it found it.

## What this means for deleting transcripts

On synthetic dev questions, deleting every old transcript barely moved Concentrate (89% → 88%; summaries alone 64%). But no question there needed exact wording or who said what, and the real slice shows those are exactly the questions people ask. **I'd read the evidence as: keep transcripts searchable, and add precomputed facts where they're demonstrably better.** That means state, change over time ("what changed": 100% vs 83% real; 58% vs 5% recall in the brief below), and open commitments. This matches Pedregal's own instinct that you might want to go back and chat with an old meeting. Compression can be a cheaper index over transcripts. On this evidence it can't replace them.

## What Granola does well, and where memory still helps

- **Granola's agent, from read-only live probes** (4 meetings: these show behaviours, not rates). Its commitment tracking is strong: it separates "no later evidence" from "not done", and stays stable across repeats. It abstains honestly on single-microphone speech, and its retrieval tools answer in ~0.3 s. **Gaps:** it missed an attendee known only by display name and the second of two people who share a first name, because attendee metadata wasn't joined to the text.
- **The pre-meeting brief**, a feature memory makes possible. As-of SQL reads (~6 ms, no LLM call) list attendees' open commitments and what changed since the group last met. On 36 synthetic test meetings, compared with top-k + Sonnet:
  - open commitments: recall **91% vs 30%**, but precision 43% vs 90%;
  - what changed: recall **58% vs 5%**, on only 19 gold items.

## Limits

- **Real slice:** 39 questions, 4 meetings, one person. The questions were LLM-drafted and the gold answers bulk-approved by me with 0 edits; at least one gold answer looks wrong. The 6 unanswerable questions weren't run, so abstention is unmeasured. The questions have now been seen, so any fix needs fresh ones.
- **Synthetic:** home advantage, unequal tuning (Concentrate was tuned on dev; the others are untuned), saturation near the oracle, and an agent that's my re-implementation. n = 120 detects only gaps of ~7 points or more.

## What I'd run next

1. **A hybrid Concentrate on fresh real questions:** fall back to transcript retrieval when the facts don't cover a question, and use Granola's speaker labels. The test is whether it closes the 92% vs 59% gap without losing "what changed".
2. **Benchmark v2.1**, which adds exact-quote and speaker-attribution questions, so the synthetic benchmark can show this loss too. Also the **directory ablation**.
3. **Fix the Mem0 and Graphiti wiring and the gold errors,** then re-score. That's cheap, and it removes the claims I can't currently defend.

## What I'd do at Granola

1. **Keep transcripts, and treat compression as an index over them, not a replacement.** Measure any retention policy against open-ended questions (who said, exact wording) before deleting anything.
2. **Precompute what search does badly:** a bitemporal store of state changes and commitments, behind "what changed since we last met" and pre-meeting briefs.
3. **Resolve identity at write time:** join attendee metadata (emails, display names) to mentions, which targets the identity misses above.
