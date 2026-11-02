# Precomputed meeting memory vs per-query retrieval: what a benchmark says

*Kian Lakhani · draft of 2026-11-02 · repo: github.com/<user>/hindsight · every number traces to a file in [FINDINGS_CHECKLIST.md](FINDINGS_CHECKLIST.md)*

<!--
EDITOR: `{{TBD: source}}` marks a number that waits on tonight's final XL test run.
Pick the headline that matches the result, delete the other two, and delete this comment.
  (A) Concentrate wins: "On {{TBD}} held-out questions, precomputed memory beat per-query retrieval by {{TBD}} points (p = {{TBD}}, Holm), at {{TBD}}× lower $/question. The gap is concentrated in {{TBD: categories}}."
  (B) Mixed: "Precomputed memory and per-query retrieval are statistically tied overall ({{TBD}} vs {{TBD}}, p = {{TBD}}). Memory wins on {{TBD}}, loses on {{TBD}}, and is {{TBD}}× cheaper per question once ingest is paid."
  (C) Concentrate loses: "Per-query agentic retrieval beat every precomputed memory, including mine, by {{TBD}} points. Memory's advantages here are cost, latency and proactive features (the brief), not question-answering accuracy."
-->

## The question

In July 2026 Granola's CEO said agents should get a compressed, always-ready context instead of searching meetings at question time: ["apple juice from concentrate"](https://www.platformer.news/granola-chris-pedregal-interview/). That's a testable claim. **Does a memory built ahead of time answer an agent's questions about months of meetings better, cheaper or faster than retrieving at query time? And if it does, can the transcripts behind it be deleted?** I built an open benchmark to measure this. Concentrate is my entry, but the benchmark is the main contribution, and the write-up is meant to hold whichever way the result goes.

## What I built

- **Northwind, an open meeting-memory benchmark.** The memory benchmarks I found (LongMemEval, LoCoMo, BEAM, RealMem) are all built on chat logs. Northwind is a synthetic company with 184 meetings and 296 questions (90 dev / 206 test). The world is generated first, so every answer is verified and linked to its gold transcript lines. It uses Granola's exact API JSON shape, and noise rates are calibrated on my real synced meetings. The questions cover the cases where meetings differ from chat: unlabelled speakers, three different Sams, values *as of* a date, and commitments that slip, get reassigned or get dropped.
- **7 systems under one harness, all with the same answer model (Opus 5.5) and judge.** Query-time: top-k hybrid retrieval, summaries only, a full-context ceiling, and **b3, an agent whose tools mirror Granola's MCP**. Write-time: Mem0, Graphiti, and Concentrate (speaker/entity resolution → facts that cite their lines → entailment gate → bitemporal store).
- **Statistics you can rely on.** Paired McNemar exact tests with Holm correction across 21 pairs, paired bootstrap CIs, a power analysis, and a cost-accuracy Pareto frontier that amortises ingest. A **second judge** (Sonnet) re-grades every answer: on the pilot it agreed 97.5% of the time (κ 0.89) and the ranking didn't change. There's also a blind hand-check export.
- **Built to run unattended:** a read-only Granola MCP source, deterministic generation (same seed, identical bytes), timeouts plus a stall watchdog with resumable builds, and ~240 offline tests.

## What Granola's live MCP does well, and where it slips

Two read-only probes on my own account (Plus plan, **4 meetings**, 14 questions plus repeats). These show that behaviours exist; they aren't error rates.
- **Strong:** commitment tracking (owner, status, and "no later evidence" kept separate from "explicitly not done", with citations, stable over 3 repeats). It also abstains honestly when asked broadly about single-microphone speech, scopes explicit dates correctly, and its retrieval tools are fast (≈0.3–0.4 s).
- **Identity across meetings:** it missed an attendee known only by display name, and the second of two people who share a first name. In both cases the attendee metadata was never joined to the text.
- **Attribution consistency:** the same in-person commitment was attributed differently depending on how the question was asked.
- **Cost of reasoning per query:** `query_granola_meetings` took a median of **8.5 s** (n = 16). Answers start from summaries, and exact details need more transcript calls.

## Headline results (Northwind-XL, held-out test split, run once)

| System | Accuracy [95% CI] | $/question | Ingest $ (one-off) | p50 latency | vs Concentrate (Holm p) |
|---|---|---|---|---|---|
| b0 top-k | {{TBD: XL test report}} | {{TBD}} | n/a | {{TBD}} | {{TBD: analysis.json}} |
| b1 summaries | {{TBD}} | {{TBD}} | n/a | {{TBD}} | {{TBD}} |
| b2 full context (ceiling) | {{TBD}} | {{TBD}} | n/a | {{TBD}} | {{TBD}} |
| b3 MCP-style agent | {{TBD}} | {{TBD}} | n/a | {{TBD}} | {{TBD}} |
| Mem0 | {{TBD}} | {{TBD}} | {{TBD: meta.ingest}} | {{TBD}} | {{TBD}} |
| Graphiti | {{TBD}} | {{TBD}} | {{TBD: meta.ingest}} | {{TBD}} | {{TBD}} |
| Concentrate | {{TBD}} | {{TBD}} | {{TBD: meta.ingest}} | {{TBD}} | n/a |

Where systems differ by category: {{TBD: two or three sentences from analysis.md, e.g. temporal / entity_disambiguation / commitment_status / aggregation}}. The most common failure type per system: {{TBD: failures.jsonl}}. At n = 206 the test can detect gaps of about {{TBD: MDE from analysis.json}} points (the pilot suggests ~9). Smaller gaps are reported as ties.

## Can you delete transcripts?

On the **dev split (90 questions, preliminary)**, Concentrate's accuracy was 89% with every transcript kept and 88% with every transcript older than the question deleted, leaving only cited facts and digests. Only 4 of 90 verdicts ever changed, and 3 of those changes don't follow N, which points to noise. The memory is ~5% of the raw transcript tokens. Keeping only summaries (b1) scored 64%. Top-k retrieval over deleted transcripts wasn't run for accuracy (budget), but at N = 14 days its context holds only 32% of the gold lines (2% for entity questions). The one plausible real loss is a speaker's *role*, which the transcript label carried and the fact didn't. Final test-split curve: {{TBD: retirement report on test, if run}}. **Caveat: the benchmark has no verbatim-quote questions, and that's exactly what deletion would cost.**

## Unit economics

| | Query-time (b3 agent) | Write-time (Concentrate) |
|---|---|---|
| Ingest $/meeting | n/a | {{TBD: economics on XL build}} |
| $/question | {{TBD}} | {{TBD}} |
| Input tokens/question | {{TBD}} | ≈9k (dev, measured) |
| $/user/month at {{TBD}} meetings + {{TBD}} questions | {{TBD}} | {{TBD}} |
| Break-even volume (questions per corpus) | n/a | {{TBD: analysis.json break-even}} |

## A result that's already in: the pre-meeting brief

A memory built ahead of time can answer questions nobody has asked yet. Before each meeting, the brief lists attendees' open commitments, what changed since the group last met, open customer requests, and the last meeting. It uses SQL as-of reads over the bitemporal store, takes ~6 ms, makes no LLM call, and can't leak later meetings. On a 36-meeting test sample, against top-k + Sonnet over earlier meetings only:
- **Open commitments:** memory recall **91%** (158/174) vs **30%** (52/174). The trade-off is precision: **43% vs 90%**, mostly from owner attribution in in-person 1:1s.
- **What changed since we last met:** recall **58% vs 5%**. This rests on only **19 gold items**, so it's directional.
- Customer requests: 78% vs 56% recall. The baseline showed more already-closed requests (38% vs 22% stale).

## Limitations

- **Synthetic data.** It's calibrated on 4 real meetings from one person. v1 had heavily templated filler (78% duplicated passages). v2 cuts that to 4.9%, but sentence frames still repeat (54% vs 0.6% real). Headline numbers use {{TBD: v1 or v2}}.
- **b3 re-implements the shape of Granola's agent; it isn't Granola's agent.** The real one can't read synthetic data. My real-data slice against the actual agent is {{TBD: real-slice status}}.
- **n.** 206 test questions, 90 dev for retirement, 36 meetings for the brief, 4 real meetings for the probes. One answer model, and the primary judge is the same model (the second judge mitigates this).
- **I tuned on dev only and ran test once.** Results I didn't like are reported in `docs/DESIGN.md`.

## What I'd do next at Granola

1. **Resolve identity at write time.** Join attendee metadata (emails, display names) to transcript mentions when the meeting is ingested, and expose it as a lookup. That directly targets the two identity misses above.
2. **Ship the brief as a bitemporal commitment ledger.** Key series on `recurring_event_id` and fix in-person owner attribution, the biggest precision lever.
3. **Before deleting transcripts, run the retirement curve on consented real data** with a verbatim-quote category, and keep line-ID pointers from facts to any archived text.
