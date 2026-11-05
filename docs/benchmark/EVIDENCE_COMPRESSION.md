# Breadth and fidelity: how much of a meeting does an agent need? (plan)

*Pre-registered plan, written 2026-11-05 before any results. Changes after the first run are logged at the bottom with a reason.*

**The question.** An agent answering from notes alone loses about a third of its answers; reading the transcript when needed recovers almost all of them ([DELETION_SIM](DELETION_SIM.md): 57% → 87%, Granola with transcripts 89%). Between "notes" and "the whole transcript" lies a design space. **How small can a kept evidence layer be and still answer like the full transcript?**

**Why it matters to Granola: breadth *and* fidelity.** Granola's agents post frames a forced choice: "should the agent read 10 meetings in detail, or 100 at a high level?", and answers "We chose 100." Under the heading "Breadth over fidelity", it notes a transcript is "roughly 10x the number of tokens compared to meeting notes", and that Chat once spent 38 seconds on one query ([Granola, May 2026](https://www.granola.ai/blog/how-granola-thinks-about-designing-agents); quotes verified 2026-11-05). Notes give breadth but drop detail; transcripts give detail but cost 10×. A compact, query-agnostic layer that answers like the transcript at a fraction of its size would let an agent read **many** meetings **and** get the details right, at the same speed and cost. **The user-facing feature: Chat that gets specifics right across many meetings without getting slower.** Granola's CEO has described the same direction: compressing meetings into "apple juice from concentrate" and an "enhanced transcript that was built for agents" ([Platformer, 30 Jul 2026](https://www.platformer.news/granola-chris-pedregal-interview/), verified). As of 2026-11-05, nothing public shows such a layer: the MCP exposes notes and the raw transcript. **Secondary benefit:** the same layer could soften chat after transcript auto-deletion ("Chat quality will be degraded", [docs](https://docs.granola.ai/help-center/consent-security-privacy/transcript-auto-deletion)), but research found no user complaints about that, and compliance teams demonstrably accept only summary-level memory after deletion, so deletion is not the pitch. Granola may have built or rejected this internally; see §6.

## 1. Hypotheses (fixed now)

| | Hypothesis | Pass if |
|---|---|---|
| **H1** | Some query-agnostic method keeps ≤ 20% of transcript tokens and stays within 5 points of full-transcript accuracy | Lower 95% bound of (method − full) > −5 points on TEST questions |
| **H2** | Choosing *what* to keep matters: the best method beats random lines at the same budget | Paired sign test, p < 0.05, at 10% and 20% |
| **H3** | Most of the gap is detail questions (who said what, exact numbers, passing remarks) | Gap closed on "notes can't answer" questions ≥ gap closed overall |
| **H4** (breadth) | Under a **fixed context budget** spanning many meetings, kept layers for every meeting beat both (i) notes for every meeting and (ii) full transcripts for the few meetings that fit, on detail questions | Paired sign test, p < 0.05, against both; needs the larger AMI set (≥ 30 meetings) |

All three are reported whether they pass or fail.

## 2. Conditions

Every condition gives the **same reader** the meeting's notes plus a kept layer. Compression happens **at ingest, before any question is seen** (query-agnostic); this is the rule that makes it a fair test.

| Condition | Kept layer | Uses an LLM at ingest? |
|---|---|---|
| **Notes only** (floor) | nothing | no |
| **Full transcript** (ceiling) | 100% | no |
| **Random lines** | random utterances up to the budget (seeded) | no |
| **Notes-anchored** | lines the [grounding checker](GROUNDING_CHECKER.md) cites as evidence for the notes, plus their neighbours | yes (the checker) |
| **Salience** | one call per meeting ranks lines by "would someone later ask about this?"; keep the top lines up to the budget | yes |
| **Enhanced transcript** | the CEO's idea, as we read it: an LLM rewrite that keeps who said what, numbers, names, decisions and commitments, drops filler | yes |
| **Facts** (prior result) | Concentrate's extracted facts | yes |

**Budgets:** 5%, 10%, 20%, 40% of transcript tokens (abstractive methods are given a target length and their actual size is reported).

## 3. Data and questions

| Set | Meetings | Questions | Role |
|---|---|---|---|
| **Kian's real meetings, DEV** | 4 | 47 (seen before) | Tune budgets and prompts only |
| **Kian's real meetings, TEST** | same 4 | ~15 hand-written, held out | Headline (private, aggregates only) |
| **AMI** | 6 (IS1008a–d, IB4003, EN2002b) | ~60, built from the human annotations (decisions, actions, problems, who-said by role, what changed across IS1008a–d), no LLM drafting | Headline (public, per-question results shareable) |

AMI controls: a **closed-book** run (no meeting at all) flags questions the model already knows; headline AMI numbers use only questions it fails.

## 4. Measures

- **Accuracy** (judge: correct / partial / wrong / declined), second judge on a 30% sample with κ.
- **Granola's four answer types**, so a method that turns declines into confident errors is visible.
- **Kept size**: tokens as % of transcript. **Verbatim share**: % of kept words copied from the transcript (a privacy footprint, §6).
- **Cost**: ingest dollars per meeting-hour; query input tokens per answer.
- The **curve**: accuracy vs kept size per method, with the notes floor and transcript ceiling drawn on it.

## 5. Cost (estimate before running)

| Part | Calls | Estimate |
|---|---|---|
| Ingest (salience, enhanced, notes-anchored) on 10 meetings × 4 budgets | ~120 | < $0.10 (Haiku) |
| Answers: ~120 questions × 22 conditions, Haiku 5.5 reader | ~2,600 | ~$1.5 |
| Judge, Haiku 5.5, plus 30% Sonnet re-judge | ~3,400 | ~$1.5 |
| **Total, Haiku reader** | | **~$3** |
| Optional: Sonnet reader on 20% budget + ceiling + floor, batched | ~500 | ~$4 |

Over the $2 line, so it runs only after Kian approves and checks the Console balance. The $0 parts (AMI questions, random baseline, closed-book prompts, harness) are built first.

## 6. Why Granola may have considered this and chosen otherwise

We can't see inside Granola. These are reasons a reasonable team might **not** ship a compressed evidence layer; the paper has to address each, not dismiss them.

| Reason they might have opted against it | What we measure or say about it |
|---|---|
| **Deletion means deletion.** Users who auto-delete want the words gone; keeping verbatim lines defeats the point and may conflict with consent or enterprise policy (admins can turn off transcripts over MCP) | Report **verbatim share** for every method. Test abstractive layers that keep facts but not words. If only verbatim layers work, say so plainly |
| **Ingest cost at scale.** Processing every meeting up front costs tokens whether or not anyone asks | Report dollars per meeting-hour and compare with query-time reading ([ECONOMICS](../results/ECONOMICS.md)) |
| **Compression loses things, and you can't know which.** Our own fact extraction lost (66%) | The curve shows exactly what is lost per budget; H3 shows where |
| **Query-time reading already works.** Their Chat went from 38 s (May) to ~8 s in our runs | Compare against "read the transcript when needed", not just notes |
| **Staleness and regeneration.** Notes get edited and regenerated; a frozen layer drifts | Out of scope; stated as a limit |
| **Product complexity.** Another artefact to store, secure and explain | Stated as a limit; the recommendation section weighs it |

## 7. Threats to validity

- **Small n.** ~120 questions; CIs will be wide. A within-5-points claim needs the lower bound, not the point estimate.
- **One reader model.** Haiku may under-use a long layer; the optional Sonnet run checks the key points.
- **Question design favours whatever it was built from.** AMI questions come from human annotations, which are written at summary level; detail questions need care (§3).
- **Our "enhanced transcript" is our guess** at what the CEO meant, not Granola's design.

## 8. Order of work

1. $0: AMI question set from annotations, closed-book prompts, harness with the floor, ceiling and random conditions.
2. $0: ingest-time compressors wired up; sizes and verbatim share computed.
3. Cost estimate printed; Kian approves; run DEV; fix only DEV-visible bugs.
4. Run TEST and AMI once. Report all three hypotheses.

## Change log

*Nothing run yet. Build notes from the $0 parts (§8 steps 1–2), 2026-11-05, before any answer:*

- **AMI set expanded to 311 questions over 33 meetings, for power** (13 DEV, 298 TEST; [data/ami_questions](../../data/ami_questions/README.md)). Two subsets, reported apart: `recorded` (the 6 meetings played into the products, 68 q) and `annotation_only` (6 more series + 3 non-scenario meetings, chosen by a seeded site-balanced rule, 243 q, stand-in notes only). EN2002b gives no questions; IB4003–IB4011 have no gold evidence. Templates v2: other annotators' role names and clause subjects handled; the original 68 are unchanged.
- **Stand-in notes.** Until Granola/Wispr notes for the AMI meetings are synced, the notes are AMI's own human summary, the text most gold answers come from. Notes-only will be near its ceiling on those questions; that run measures nothing about compression and is not reported as a result.
- **Oracle meeting choice.** The reader gets the question's own meeting(s), so the test isolates compression from retrieval.
- **Cost is well above §5 with 311 questions.** Exact quote (`concentrate compression --quote --exact`): **$14.06 expected, $15.51 high** for the required plan (Haiku reader), against ~$3 in §5; with the batched Sonnet reader on 20% + floor + ceiling, **$41.93 / $45.56**. Largest parts: AMI answers $5.28, the 30% Sonnet re-judge $5.02. The next step alone ($0 conditions + closed-book on all 311) is **$3.85 / $4.23**. Every paid run is pilot-first under a hard cap (`--pilot`, `--max-usd`).
- **$0 sizes.** Random lines hit their budgets (5/10/20/40% → 5.0/10.0/20.0/40.0% mean) and keep 6/10/20/36% of gold evidence lines (n = 61 questions with evidence): as expected for random, a baseline, not a finding.

**Reframe, 2026-11-05, before any answer was produced.** The framing moved from "keep memory after transcript deletion" to **breadth and fidelity**, after a research pass found no user complaints about chat after deletion and found compliance teams accept only summary-level memory once transcripts are deleted. Consequences: verbatim methods stay as **candidates** (transcripts still exist in the main use case), not just references; H1–H3 are unchanged; **H4 is added** with a breadth condition (fixed context budget across many meetings, retrieval included, not the oracle meeting). Deletion is reported as a secondary result, using only non-verbatim layers.
