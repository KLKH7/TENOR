# I tested my meeting memory against Granola's agent. On real meetings, it lost.

*Kian Lakhani · 2026-11-03 · repo: github.com/<user>/hindsight · every number traces to a file in [FINDINGS_CHECKLIST.md](FINDINGS_CHECKLIST.md)*

**Headline.** I asked open-ended questions about my own real meetings. **Granola's actual agent scored 92%; my precomputed memory, Concentrate, scored 59%** (n = 39; the 95% CIs don't overlap; paired 14–1). On "who said X" Concentrate got 0% to Granola's 88%. It led only on "what changed" (100% vs 83%, n = 6). On my synthetic benchmark the two **tie** (92% vs 90%), but only because those questions target exactly the facts my extractor keeps. **The lesson for Granola:** Granola already lets users auto-delete transcripts, after which chat runs on notes alone. On synthetic questions, a fact-and-digest layer kept beside the notes recovered most of what notes alone lose (88% vs 64%). But real questions also ask who said what, and facts didn't keep that (0%). Facts are worth storing before a transcript is deleted; they don't replace it.

## The question

In a [July 2026 Platformer interview](https://www.platformer.news/granola-chris-pedregal-interview/), Chris Pedregal said that an agent connected to Granola through Claude does "a relatively dumb job at extracting information". He said Granola should instead send agents "apple juice from concentrate". He also said you "might want to still go back and chat with that meeting". Granola has since done much of this. Users can already [auto-delete transcripts](https://docs.granola.ai/help-center/consent-security-privacy/transcript-auto-deletion) after anything from 1 day to 1 year (enterprise admins can enforce it), after which chat works from notes only and the docs warn "Chat quality will be degraded". Its [agent-design post](https://www.granola.ai/blog/how-granola-thinks-about-designing-agents) (May 2026) chooses "breadth over fidelity": the agent reads many meetings at a high level and goes deeper only when needed, and the failure it most wants to avoid is "wrong, but confident". So my contribution is an **independent measurement** of two questions. Does memory compressed ahead of time answer as well as retrieving at question time? And **when transcripts are deleted, how much do answers degrade, for which question types, and what beyond notes recovers it?**

## What I built

- **Northwind**, an open meeting-memory benchmark. The memory benchmarks I found are built on chat logs. Northwind has 184 synthetic meetings in Granola's exact API shape, calibrated on my real meetings, and 296 questions with verified answers and gold transcript lines.
- **7 systems under one harness**, all with the same answer model (Opus 5.5) and judge: top-k, summaries only, an oracle, an agent modelled on Granola's MCP, Mem0, Graphiti, and **Concentrate** (entity resolution → line-cited facts → entailment gate → bitemporal store).
- **A real-meeting slice.** Questions about my 4 real meetings, answered by Granola's live `query_granola_meetings` (read-only) and by Concentrate built from the same notes through the MCP.
- **Rigour:** paired McNemar tests with Holm correction, bootstrap CIs, a second judge, and a red-team audit of my own headline ([RED_TEAM.md](../results/RED_TEAM.md)). The audit corrected several of my claims, and the numbers below are the corrected ones.

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
- **Why Concentrate loses.** It kept 43 facts and 11 commitments for 4 meetings, and it answers only from those. 10 of its 16 misses were "the material doesn't mention it". The detail was in the transcript; it just wasn't a fact the extractor had kept. On "who said", it also ignored Granola's named system-audio speaker labels.
- **Granola's design held up.** It goes deeper only when needed, and on "who said" questions that meant reading the transcript, which it did (88%). Concentrate couldn't, and twice it confidently named the wrong person: the "wrong, but confident" failure Granola designs against. Simply reading the right meeting's transcript (top-k) also beats reading my extracted notebook.
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

## When transcripts are deleted: what degrades, and what recovers it

Granola already lets users delete transcripts, so the useful question is what to keep alongside the notes. On synthetic dev questions (90; [retirement curve](../results/RETIREMENT.md)), I deleted every transcript older than each question:

| Kept after deletion | Accuracy | Examples by type (n = 8–11 each) |
|---|---|---|
| Notes only (≈ Granola after auto-deletion) | 64% | entity 30%, aggregation 0%, commitments 67%, latest state 73% |
| Notes + Concentrate's facts and digests (~5% of transcript tokens) | 88% | entity 60%, aggregation 50%, commitments 89%, latest state 100% |
| Everything kept (for reference) | 89% | |

- **What recovers it:** a fact layer extracted *before* deletion recovers about 24 points over notes alone, and costs ~5% of the transcript's size.
- **What it doesn't recover:** the synthetic set has no "who said" or exact-wording questions. On real meetings those are exactly where facts fail (0% on who said), and only the transcript answers them. So for a user who has deletion turned on, those questions degrade whatever else is kept.
- **Not yet measured:** the real-data version of this curve. That's the experiment I'd run first.

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
2. **The retirement curve on real data,** plus **benchmark v2.1**, which adds exact-quote and speaker-attribution questions so the synthetic curve can show the loss too. Also the **directory ablation**.
3. **Fix the Mem0 and Graphiti wiring and the gold errors,** then re-score. That's cheap, and it removes the claims I can't currently defend.

## What I'd do at Granola

1. **Extract a fact layer before auto-deletion runs,** and measure degradation per question type on real data, so the "Chat quality will be degraded" warning can say *which* questions degrade (who said, exact wording) and which don't (state, commitments).
2. **Precompute what search does badly:** a bitemporal store of state changes and commitments, behind "what changed since we last met" and pre-meeting briefs.
3. **Resolve identity at write time:** join attendee metadata (emails, display names) to mentions, which targets the identity misses above.
