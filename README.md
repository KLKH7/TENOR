# Hindsight: an open benchmark for meeting memory in the age of AI agents

**What happens to an AI agent's answers when meeting transcripts are deleted, or squeezed into notes?**
Hindsight measures it from the outside, through public interfaces only, on real meetings. [Granola](https://www.granola.ai) is the reference product.

![Deleting transcripts cost 32 points on real meetings; our fact layer recovered 9; reading the transcript when notes fall short recovered most of it](docs/figures/retention_steps.svg)

## What we found

Real meetings, DEV results: 4 meetings, 39–47 questions, seen during development.

- **Deleting transcripts costs a third of the answers.** Granola's agent with transcripts: 89%. Notes only: 57% (2 gained, 17 lost, p = 0.001).
- **The loss is in the details.** Questions the notes can't answer drop from 94% to 44%.
- **Reaching the transcript recovers it; a fact layer doesn't.** Notes + extracted facts: 66% (not significant). Reading the transcript only when needed: 87%.
- **Reading real speech, Granola's agent beat our fact memory 92% to 59%** (paired 14–1, p ≈ 0.001).
- **Granola's notes invent nothing, but about 1 claim in 7 overreaches:** 84% supported, 0% made up, 14% go beyond what was said (132 claims, [`NOTES_FAITHFULNESS`](docs/benchmark/NOTES_FAITHFULNESS.md)).
- **Through the MCP, Granola's wrong answers look like its right ones:** no line-level citations and no "unsure" flag (3 of 47 wrong but confident, [`AGENT_INTERFACE`](docs/results/AGENT_INTERFACE.md)).

*Independent research prototype; [not affiliated with Granola](#not-affiliated-with-granola).*

## Results on real meetings

All from the author's 4 Granola meetings, read through Granola's public MCP (read-only). Gold answers were LLM-drafted and approved by the meeting owner.

### Retention: what deleting transcripts costs

Same 47 questions, four conditions; each adds one layer. Full write-up: [`DELETION_SIM`](docs/benchmark/DELETION_SIM.md).

| Condition | Accuracy [95% CI] | Correct / partial / declined / wrong but confident ¹ | Questions notes can't answer (n = 18) | vs row above ² |
|---|---|---|---|---|
| A. Granola's agent, transcripts kept | **89%** [79, 98] | 42 / 2 / 0 / 3 | 94% | — |
| B. Notes only (≈ chat after auto-deletion) | **57%** [43, 70] | 27 / 4 / 16 / 0 | 44% | 2–17, p = 0.001 |
| C. B + our extracted facts | 66% [51, 79] | 31 / 2 / 14 / 0 | 56% | 4–0, p = 0.13 |
| D. C + transcript when C can't answer | **87%** [77, 96] | 41 / 3 / 3 / 0 | 89% | 10–0, p = 0.002 |

- **Without transcripts, our model declined; it didn't bluff.** Granola's 3 confident errors all came with transcripts (2 on unanswerable questions).
- **D is not deletion.** It reads a kept copy of the transcript, on 60% of questions here.
- **B uses our answer model**, not Granola's own degraded chat, so A vs B mixes deletion with a model change.

¹ Counts out of 47. *Declined* = abstained or said the material doesn't cover it. *Wrong but confident* = judged wrong with no hedge. ² Wins–losses on the same questions, exact sign test.

![Granola's four answer types per condition: without transcripts our model hedged rather than bluffed](docs/figures/answer_types.svg)

![Accuracy by tag and question type for each condition, n in every cell](docs/figures/question_heatmap.svg)

### Question answering: reading real speech

All 4 meetings fit in a model's context, so this tests reading, not search. Write-up: [`REAL_SLICE`](docs/results/REAL_SLICE.md).

| System | Accuracy [95% CI] | n | Median latency |
|---|---|---|---|
| **Granola's agent** (live, via MCP) | **92%** [82, 100] | 39 | 7.9 s |
| Top-k retrieval (whole best-matching meeting) | 77% [64, 90] | 39 | 3.3 s |
| **Concentrate** (our fact memory) | **59%** [44, 74] | 39 | 4.2 s |

- **Concentrate misses details it never extracted.** 0 of 8 on "who said X" (Granola 7 of 8); 10 of its 16 misses were declines.
- **It leads only on "what changed"** (6 of 6 vs 5 of 6), what its time-stamped store is built for.
- **A transcript fallback closed most of the gap (DEV only).** 12/22 → 20/22 (8 fixed, 0 broken, p = 0.008); Granola 19/22 on the same questions ([`fallback`](docs/systems/concentrate/fallback.md)).

## Synthetic diagnostics (not evidence of real-world performance)

Northwind is a synthetic company in Granola's format: 184 meetings, 296 questions with known answers.
On it, our fact memory tied the transcript readers (92 / 90 / 88%) and facts recovered deletion (64% → 88%). Real meetings reversed both.
Why: the same author built the generator and the extractor, and the questions target planted facts. Details and all seven systems: [`SYNTHETIC_DIAGNOSTICS`](docs/results/SYNTHETIC_DIAGNOSTICS.md).

![Same approaches on synthetic vs real meetings: memory-only fell 33 points, the fact layer 22, the agent over transcripts held](docs/figures/synthetic_vs_real.svg)

## How it works

![How Hindsight works: meetings go into products through adapters, five tracks test them, scores come out](docs/figures/overview.svg)

- **Outside-in.** Only public interfaces: Granola's API note format and its read-only MCP. No claims about Granola internals.
- **Black-box kit.** Asks any meeting product's MCP your questions, on your meetings. Only content-free scores leave your machine ([`BLACKBOX`](docs/benchmark/BLACKBOX.md)).
- **Concentrate** is a reference memory, not a product: it extracts cited, time-stamped facts to show what precomputing gains and loses ([`memory`](docs/systems/concentrate/memory.md)).
- **No single score.** Six dimensions, from correctness to cost ([`METRICS`](docs/benchmark/METRICS.md)). Comparisons are paired, so a tie is never called a win.

| Track | Task, for any system | Status |
|---|---|---|
| Question answering | Answer across meetings: who said what, decisions, changes, commitments | ✅ real (DEV) |
| Retention | Answer after transcripts are deleted | ✅ real (DEV) |
| Notes faithfulness | Is every line of the notes backed by what was said? | ✅ real (4 meetings) + baseline |
| Agent interface quality | Answer type, evidence, stability, latency through the MCP | ✅ real (DEV) |
| Contextual integrity | Answer without revealing meetings the asker can't see | 📐 [designed](docs/benchmark/SHARING_LEAKS_DESIGN.md) |

## Reasons to be skeptical

- **Small.** 4 meetings, one person, 39–47 questions.
- **Gold not hand-checked.** LLM-drafted, approved in bulk with 0 edits.
- **DEV, not test.** Every real question was seen during development.
- **Our model, not Granola's**, answers the notes-only condition.
- **No search at scale.** 4 meetings fit in context.
- **LLM judge.** A second judge agreed on 96–100% of verdicts; no human check yet.
- **In progress:** hand-written held-out test questions; paired Granola + Wispr Flow recordings of the same meetings.

## Related work

- **Memory benchmarks use chat, not meetings.** LongMemEval, LoCoMo, MemoryAgentBench, BEAM and RealMem test memory over chat or generated dialogue.
- **Meeting benchmarks grade a model given the transcript.** QMSum, MeetingQA, ELITR-Bench, AutoMin, MeetBench-XL and MeetingProbe; none tests a shipped product through its agent interface.
- **Nothing we found tests retention:** what an agent loses once transcripts are deleted.
- **The closest product comparison scores notes only:** four note takers on the same 5 AMI meetings, run by one of the vendors.
- **Lessons we take:** fact compression loses detail (LongMemEval found it too); write held-out questions to a fixed schema with review (QMSum).

Table, gap and full references: [`docs/RELATED_WORK.md`](docs/RELATED_WORK.md).

## Quick start

Needs Python 3.11 and [uv](https://docs.astral.sh/uv/). This block is free: no API key, no LLM calls.

```bash
uv sync                                                # install (locked)
uv run pytest                                          # offline tests
uv run concentrate synth --seed 42 --scale xl          # generate Northwind (deterministic)
uv run concentrate eval --xl --split dev --estimate    # price a run without running it
```

Running systems needs `ANTHROPIC_API_KEY` in `.env` (see `.env.example`). Every command prints an estimate and asks before spending over $2.

**Your own meetings.** Sync through Granola's official MCP (browser sign-in; never calls write tools), then use the black-box kit. Synced notes stay in a local, uncommitted SQLite file.

```bash
uv run concentrate sync --source granola-mcp
uv run concentrate real-kit --help
```

## Docs

| Start here | |
|---|---|
| [`docs/report/HINDSIGHT_REPORT.md`](docs/report/HINDSIGHT_REPORT.md) | Full technical report |
| [`docs/README.md`](docs/README.md) | Map of every doc |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Diagrams, linked to the code |
| [`docs/STYLE.md`](docs/STYLE.md) | How docs and figures are written |
| [`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md) | Adding a system; reporting a problem |

## Not affiliated with Granola

Independent research prototype. It uses the formats of Granola's public REST API and official MCP, calibrated on the author's own meetings. Everything reported about Granola is observable through public interfaces. The web UI borrows Granola's visual language but none of its name, logo or fonts. The name "Concentrate" comes from a [July 2026 interview](https://www.platformer.news/granola-chris-pedregal-interview/) with Granola's CEO ("apple juice from concentrate").

## Citation

```bibtex
@software{lakhani2026hindsight,
  author = {Lakhani, Kian},
  title  = {Hindsight: an open benchmark for agent memory over meeting notes},
  year   = {2026},
  note   = {Research prototype; includes the Northwind meeting-memory benchmark and the Concentrate memory system}
}
```

See also [`CITATION.cff`](CITATION.cff). Code is [MIT](LICENSE); the synthetic Northwind data is [CC BY 4.0](data/README.md#license).
