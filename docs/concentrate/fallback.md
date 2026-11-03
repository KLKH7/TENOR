# Transcript fallback: facts for state and change, transcripts for everything else

Status: **built and tested; default `off`.** Dev results below. Not yet tested on fresh questions.
Code: `packages/concentrate/memory/fallback.py`, wired in `agents/concentrate_system.py`.
Tests: `tests/test_fallback.py` (no network).

## Why

On the author's real meetings, Granola's agent beat Concentrate 92% vs 59% (n = 39,
[`REAL_SLICE.md`](../REAL_SLICE.md)):
- Concentrate scored 0/8 on "who said X";
- 10 of its 16 misses were "the material doesn't mention it".

Write-time extraction keeps the facts the extractor thought mattered (43 facts and 11
commitments for 4 meetings). Real questions ask about the long tail: who proposed something, a
date mentioned once. The transcript (L0) is still stored, so the memory only needs a way to
read it when the facts fall short.

## Design

```
question ─► memory context (facts, commitments, digests, cited lines)  ── as today
              │
              ├─ fallback="always":     + transcript passages (fixed budget) ─► one answer call
              └─ fallback="on_abstain": answer from memory
                                          └─ abstained / "the material doesn't cover it"?
                                               └─ + transcript passages ─► second answer call
```

- **Passages.**
  - The units are b0's 15-utterance transcript chunks, indexed with the same BM25 + MiniLM `HybridIndex`, fused with RRF.
  - Selection: the best-ranked chunks until `fallback_tokens` (default 3,500) is reached. They are shown grouped by meeting, in line order.
  - Lines the memory context already shows are skipped.
  - **Every line keeps its ID** (`[note:0012]`), so the answer still cites lines.
- **Time and retirement.**
  - Only meetings held on or before the question's as-of date are searched.
  - With `retire_after_days=N`, meetings older than N days are **never** read. The fallback does not undo retirement (C). This is tested.
- **When `on_abstain` fires.** It fires when the memory-only answer sets `abstain`, or says the material doesn't contain the answer: "doesn't mention", "no record of …", "can't determine", and similar (`says_not_covered`).
  - "No record of a follow-up" is excluded: `answer.v1` asks for exactly that phrase on commitment questions, and it's a real answer.
  - The second call gets the same memory context plus the passages, under the same `answer.v1` prompt and model.
- **Speaker attribution.** Passages show the raw channel label: `Me` = the owner's microphone (everyone, in person), `Them` = system audio, plus any name Granola itself attached. Enhance's resolved name appears only as `Enhance guess: <name>`, and the block tells the model to name a speaker only when the words or the channel make it clear.
  - Reason: on real data, Enhance credited one attendee with other people's lines. Raw-plus-guess shows that uncertainty instead of hiding it.
- **Accounting.** Each answer's `SystemResult.extra` (written into report rows) records:
  - `fallback_fired`;
  - the fallback's extra input and output tokens and its extra cost.

  So reports can split the cost and accuracy of fired vs not-fired questions.
- **Batch mode.** `turn_request` is the first (memory-only) call, so batch prefetch works. A fallback call runs interactively.
- **Switches.**
  - `ConcentrateSystem(fallback="off"|"on_abstain"|"always", fallback_tokens=3500)`;
  - `concentrate tune-c --variants fallback_on_abstain,fallback_always` (XL dev);
  - `concentrate real-slice --systems c,c_on_abstain,c_always` (real).

## Free coverage check ($0, before any spend)

"Covered" means the gold evidence is somewhere in what the answer model sees.

**XL dev (90 questions, 282 gold lines).** The XL memory already reaches most gold lines, so the
fallback has little to add here:

| What the model sees | Gold lines covered |
|---|---|
| memory context (as today) | 243 / 282 (86%) |
| passages alone, 2k / 3.5k / 6k tokens | 29% / 34% / 38% |
| **memory + passages, 2k / 3.5k / 6k** | 89% / **89%** / 89% |

**Real slice (the 39 questions run before; aggregates only, content stays in `data/real/`).**
- Real questions have no gold *line* IDs, only a drafted evidence quote. Each quote piece was fuzzy-matched to its best transcript line in the evidence meeting (≥ 60% word recall): a "pseudo-gold" line.
- 35 of the 39 questions get at least one pseudo-gold line (53 lines in all).

| What the model sees | Questions with a pseudo-gold line in view | Pseudo-gold lines | Among Concentrate's 14 wrong answers | who_said (7) |
|---|---|---|---|---|
| memory context (as today) | 25 / 35 | 34 / 53 (64%) | 5 / 14 | 4 / 7 |
| memory + 2k passages | 33 / 35 | 47 / 53 | 12 / 14 | 7 / 7 |
| **memory + 3.5k passages** | **33 / 35** | **47 / 53 (89%)** | **12 / 14** | **7 / 7** |
| memory + 6k passages | 34 / 35 | 50 / 53 | 13 / 14 | 7 / 7 |

- `on_abstain` would fire on **20 of 39** real memory-only answers, and on about 20% of XL dev answers.
- Coverage is a ceiling, not an accuracy. The model still has to use the passage, and attribute speakers correctly.
- One real bug was found by this check: MCP note IDs are UUIDs, not `not_…`, so lines already in the memory context weren't being skipped. Fixed and tested.

**Reading.** The fallback is aimed at exactly the real-data failure. With 3.5k tokens of
passages, the evidence for 12 of Concentrate's 14 scoreable wrong answers comes into view; for
"who said", 7 of 7. On XL the memory was already near its ceiling, so expect little change
there. That serves as a check that the fallback doesn't hurt.

## Dev results

DEV_RESULTS

## Honesty notes

- **These are dev numbers.** The 47 real questions have been seen, and they motivated this design. The 90 XL dev questions were used for earlier tuning. Neither set can confirm the design.
- **The final claim needs fresh real test questions, written after the design is frozen** (`real-kit draft` on new meetings, or self-written), run once.
- The XL test split was not touched.
- The pseudo-gold lines are a heuristic built from LLM-drafted evidence quotes. They are used only for this free coverage check, never for scoring.
