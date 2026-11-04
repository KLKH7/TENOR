# Enhance v2: speaker attribution for in-person meetings

Status: opt-in. `enhance-v1` stays the default, so tonight's memory builds are unchanged.
Select v2 with `CONCENTRATE_ENHANCER=enhance-v2` (memory builds), `real_enhancer("enhance-v2")`,
`enhance(..., version="enhance-v2")`, or `python -m concentrate.enhance.evaluate --version
enhance-v2 [--lookahead]`. The enhancer name is part of the store config, so a v2 build gets
its own store. v1 output was checked byte-for-byte against the pre-change evaluator on base and
XL v2. Spend: $0, because everything is deterministic and scored against regenerated gold.

## 1. Measure first: where v1 stands

All three fixtures have the same in-person meetings: Priya's 1:1s with one report each (base: 3
meetings, XL: 11). Everyone shares one microphone, so the candidates are the owner and the
report. One XL 1:1 (Priya / Alex) was recorded on iOS and has diarization labels.

The XL v1 fixture (generator `northwind-1`) no longer re-renders under the current generator
(`northwind-2`). Its gold was regenerated from a detached checkout at `2c96ac1^` and dumped to
JSON. Scoring then runs the current Enhance against that dump.

"Coverage" means resolved lines / all lines. "Precision" means correct / resolved. "Top-1"
means the best candidate is right, counting abstentions.

| Fixture | In-person lines | v1 coverage | v1 precision | v1 top-1 | Owner-always baseline |
|---|---:|---:|---:|---:|---:|
| base (`northwind-2`) | 81 | 33.3% | 63.0% | 71.6% | 64.2% |
| XL v1 (`northwind-1`) | 302 | 4.6% | 50.0% | 70.9% | 70.5% |
| XL v2 (`northwind-2`) | 836 | 0.0% | n/a | 65.3% | 61.5% |

By method, v1 in person is `owner_mic` (17/27 on base, 7/14 on XL v1) or `unresolved`. The 2
diarized meetings resolve nothing: the joint cluster assignment's confidence is
sigmoid(owner prior 0.4) = 0.599, just under the 0.6 threshold.

**What actually goes wrong downstream: commitment sentences.** Block-majority gold isn't the
right yardstick for commitments. Granola merges in-person speech into blocks of 5–140 words,
so "I'll take the skills matrix" can sit inside a block that is mostly someone else's. The
table below scores the speaker of the commitment sentence itself: the 14 made/resolved
commitment beats in XL in-person meetings.

| Fixture | v1 correct | v1 credited to owner (wrong) | v1 abstained |
|---|---:|---:|---:|
| XL v1 | 0/14 | **14/14** | 0 |
| XL v2 | 0/14 | 0 | 14 (`? priya\|<report>`) |

XL v1 is the brief's bug. The `northwind-1` generator wrote "I'll take the skills matrix *for
Megan*". v1's "names someone else in the third person" cue (−0.5) then pushes the line to
Priya at 0.71. Generator 1.1 removed that phrasing, so on XL v2 the same lines abstain.

## 2. What v2 adds

### Task continuity (the cue that matters)

People take a task in one meeting and report on it in another: "I'll take the skills matrix,
done by Thursday", then later "The skills matrix is done" or "...slipped, I'll get it done next
week". `concentrate.enhance.tasks` pulls out task phrases with a narrow grammar: a first-person
take/own verb + "the NP", or "the NP" + is done/finished/ready/shipped/late, or "the NP
slipped".

The index records which speakers said each phrase, but only on **hard-identified** lines:
platform-named system audio, and the owner's microphone in remote calls. Enhance's own guesses
never feed it, so the cue can't amplify its own mistakes.

When an unattributed line (in person, or unnamed system audio) talks about task T, each
attendee who said T elsewhere gets +2.5 × their share of T's identified mentions, in log-odds.
In a 1:1 that takes the report from 0.40 to 0.83.

There are two modes:

- **online** (default): only earlier meetings count. This is what the memory pipeline gets,
  because it calls `enhance(note, directory, history)`.
- **`lookahead=True`** (batch, `enhance_all`): every meeting's identified lines count, later
  ones included. A second pass also adds unnamed system-audio lines that v2 attributes at
  ≥ 0.9 by a method other than task continuity (~98% precise, per the curve below).
  This is the "second pass over the series". The pipeline doesn't use it yet; see open
  questions.

### Diarized owner prior

On iOS-diarized recordings, the owner prior on the largest cluster is worth 1.5 instead of 0.4,
because it is the owner's phone. A clean two-cluster 1:1 now resolves at 0.82.

### Considered and not shipped, with the measurement behind each

- **Turn-taking / question→answer adjacency, and an HMM with forward–backward over the
  microphone run.** The synthetic in-person meetings can't support either. 95% of their
  blocks are filler turns, and the generator draws each filler speaker i.i.d. (owner 30%,
  otherwise uniform over attendees; `synth/pad.py`).

  Measured on gold, P(speaker changes after a block ending in "?") vs otherwise is 54.5% vs
  46.7% on XL v2, 49.4% vs 38.8% on XL v1, and 28.6% vs 60.9% on base (n = 14). That's noise
  around the i.i.d. rate. With i.i.d. speakers, a forward–backward chain has nothing to
  propagate except the prior. Worse, it would spread one confident task line onto its
  neighbours, which would be wrong here.

  Real meetings do have this structure, so this is a fidelity gap in the corpus, not evidence
  that the idea is bad. It should be tested on the real slice (workstream B) or after a
  generator change, not tuned here.
- **Role priors** ("the report makes the commitments in a 1:1"). This would bake in the
  generator's script. Every in-person commitment in the corpus is the report's, so a
  role prior would look perfect and prove nothing.
- **Abstention and "? A|B" candidates** are unchanged from v1 and still apply.

## 3. Results

### Coverage vs precision

In-person lines, block-majority gold. Each cell is coverage / precision at that confidence
threshold; unresolved lines count with their best candidate. The shipping threshold is 0.6.

| Fixture | Version | ≥0.5 | ≥0.6 | ≥0.7 | ≥0.8 |
|---|---|---|---|---|---|
| base | v1 | 100% / 72% | 33% / 63% | 0% | 0% |
| base | v2 | 100% / 72% | 60% / 78% | 27% / 95% | 27% / 95% |
| XL v1 | v1 | 100% / 71% | 5% / 50% | 5% / 50% | 0% |
| XL v1 | v2 online | 100% / 70% | 12% / 65% | 12% / 65% | 9% / 74% |
| XL v1 | v2 lookahead | 100% / 71% | 12% / 73% | 12% / 73% | 11% / 75% |
| XL v2 | v1 | 100% / 65% | 0% | 0% | 0% |
| XL v2 | v2 online | 100% / 66% | 10% / 93% | 10% / 93% | 10% / 93% |
| XL v2 | v2 lookahead | 100% / 66% | 11% / 93% | 11% / 93% | 11% / 93% |

By method on XL v2 (lookahead): `diarization_propagation` 74/80 (92.5%), `task_continuity`
8/8, and the other 748 lines abstain. On base, all of the gain comes from the diarized meeting
(21/22).

Unnamed remote system audio barely moves: XL v2 coverage 57.9% → 58.0%, precision 91.9% →
92.0%. There is one regression: on XL v1, a task-attributed line propagates along the remote
chain onto two following backchannels from another speaker.

### Commitment sentences

The 14 XL in-person commitment beats, scored by the sentence's speaker:

| Fixture | v1 | v2 online | v2 lookahead |
|---|---|---|---|
| XL v1 (correct / wrongly owner) | 0 / 14 | 4 / 10 | **9 / 5** |
| XL v2 (correct / wrongly owner / abstain) | 0 / 0 / 14 | 2 / 0 / 12 | **8 / 0 / 6** |

v2 is never confidently wrong on XL v2. The 6 lines it still abstains on have no identified
evidence anywhere: either the task only ever comes up in in-person 1:1s, or its remote mention
was itself unnamed and not confidently attributed.

On XL v1, the remaining 5 errors are the generator-1 "for Tomás" phrasing outvoting an absent
task cue.

On XL v1, block-level story-beat precision for v2 online drops (50% → 39%), while sentence-level
accuracy rises (0 → 4). Both are true: task lines inside mixed blocks get credited to the
sentence's speaker, and the block-majority gold then counts that as wrong.

## 4. Downstream estimate (no rebuild, no API)

We take the XL v1 memory store behind the brief (`78a4767dabf454ae/6b9c4de7`) and re-attribute
each item's cited source line with v2. The result is checked against gold for the speaker of
that sentence.

- **Brief: false commitment items.** The store holds 8 commitments whose source is an
  in-person line. All 8 are owned by `priya`, and all 8 actually belong to the report. These
  are the "items attributed to the note owner" that repeat across the brief's false positives
  (docs/results/BRIEF.md says ~10 distinct items).
  - v2 lookahead: 4/8 correct (Lena, Elena, Megan, Hana), 0 new errors; 4 still go to Priya
    (Theo, Tomás ×2, Nadia).
  - v2 online: 0/8, because each commitment is the first identified mention of its task.

  On XL v2, the same 4 still-wrong items would become `? priya|<report>` instead of `priya`.
  The extractor's unresolved-owner path then stops them from being credited to Priya.
- **Gate: rejections for an unresolved speaker.** The store has 7 `not_entailed` rejections
  citing an unresolved speaker. v2 lookahead resolves 1 correctly: "The invoice export slipped
  ..." → `sam_p`, a real commitment the gate dropped. v2 online resolves 0.
  - Of the other 6, 5 claim an **org** as subject (Redwood, Lumen, Pinecrest). Naming the
    person wouldn't fix those. What would: when every candidate belongs to the same customer
    org, the speaker's org is certain even if the person isn't (follow-up below).
  - The last one, "The role editor mockups is done" (Ruth), has no identified evidence for
    its task.

So expect roughly half of the owner-attributed in-person commitment false positives to go,
but only with lookahead (or after the memory pipeline gets a batch pre-pass). Online v2 alone
moves the brief very little on XL v1.

## Trade-offs and honesty notes

- The task grammar matches how this corpus phrases commitments, which is also common real
  phrasing ("I'll take X", "X is done", "X slipped"). Real meetings phrase tasks in far more
  ways, so expect lower recall there. Precision should hold, because the cue only fires
  through identified speakers.
- Weights (2.5 for task continuity, 1.5 for the diarized owner) were set by reasoning (1:1
  confidence ≈ 0.83 and 0.82), not by tuning on XL. XL is held out as in v1, and base has no
  in-person commitments, so there was nothing to tune them on.
- The in-person evaluation is small: 11 meetings, 14 commitment sentences, 1 diarized meeting.
  Treat the percentages as indicative.

## Open questions and follow-ups

1. **Lookahead in the pipeline.** Incremental ingestion only sees history. Options: (a)
   pre-compute the task index over the whole corpus before a build (simple, but a later
   meeting then changes an earlier note's attribution, so builds are no longer incremental);
   (b) re-attribute only commitment lines when a later identified mention arrives, as a
   memory-side "owner correction" event. (b) fits the bitemporal store better.
2. **Org-level attribution** when all candidates share an org (5 of the 7 gate rejections).
3. **Turn-taking/HMM**: test on the real slice or a generator with real turn structure before
   building it.
