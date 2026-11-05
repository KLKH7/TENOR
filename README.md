# Hindsight: an open benchmark for meeting memory in the age of AI agents

**When an agent asks "what did we decide about pricing, and who owns the follow-up?", should it search transcripts on the spot, or read a memory built ahead of time?**
Hindsight is a suite of evaluations for how AI systems capture, keep, recall and hand off what was said in meetings. It's measured from the outside, using only public interfaces: shareable synthetic meetings with known answers, plus a black-box kit that tests any meeting product through its MCP on your own meetings.

**Reference product: [Granola](https://www.granola.ai).** Hindsight is built around Granola's public note format, API and MCP, because Granola is a widely used meeting tool with an open MCP. Every track is defined so that any product or memory system can be scored; adding another meeting product takes one adapter.

*Independent research prototype; [not affiliated with Granola](#not-affiliated-with-granola).*

![What deleting transcripts costs on real meetings: Granola's agent 89%, notes only 57%, notes plus a fact layer 66%, plus reading the transcript when needed 87%](docs/figures/deletion_sim.svg)

## What we found

Every headline below comes from **real meetings**: the author's own 4 Granola meetings, 39–47 questions, gold answers drafted by an LLM and approved in bulk by the meeting owner. The questions were seen during development, so these are **DEV results**: strong evidence of where to look, not held-out claims. Synthetic results are kept [lower down as diagnostics](#synthetic-diagnostics-not-evidence-of-real-world-performance); on the one question where both exist, they pointed the wrong way.

- **Deleting transcripts costs about a third of the answers, and most of all on detail questions.** Granola already lets users auto-delete transcripts, after which chat works from notes only; its help page says "Chat quality will be degraded". We rebuilt that from outside on 47 real questions. Granola's agent, with transcripts, scored 89%; our answer model over Granola's own notes alone scored 57% (17 lost, 2 gained, sign test p = 0.001). On the 18 questions whose answer isn't in the notes it fell from 94% to 44% ([`DELETION_SIM`](docs/benchmark/DELETION_SIM.md)).
- **Reaching the transcript recovers the loss; a fact layer doesn't.** Adding our extracted facts to the notes reached 66% (+9 points, 4 gained and 0 lost, p = 0.13: not significant). Reading transcript passages only when the notes and facts fall short reached 87% (10–0 over facts alone, p = 0.002). That models "keep a cold copy of the transcript", not deletion.
- **Without transcripts, our model said "I don't know" instead of bluffing.** Across the notes-only and fact-layer conditions there were no confident wrong answers: every miss was a hedge or a decline. Granola's agent, with transcripts, gave the only 3 confident wrong answers (2 of them on unanswerable questions). This is our model's behaviour, not Granola's own degraded chat, which we can't observe.
- **Reading real speech: Granola's agent 92%, our fact memory 59%** (n = 39; paired 14–1, p ≈ 0.001). All 4 meetings fit in context, so this measures reading real speech, not searching at scale. The fact memory scored 0 of 8 on "who said X" (Granola 7 of 8), and 10 of its 16 misses were declines: the detail was never extracted ([`REAL_SLICE`](docs/results/REAL_SLICE.md)).
- **A transcript fallback closed most of that gap (DEV).** Letting the fact memory read transcript passages when its facts can't answer took it from 12/22 to 20/22 on the questions reached before the budget cap (8 fixed, 0 broken, p = 0.008); Granola's agent scored 19/22 on the same questions. These questions shaped the design, so a fresh test is needed ([`fallback`](docs/systems/concentrate/fallback.md)).

## Why an outside benchmark

Everything here is built **only on public interfaces** and **data anyone can share**. That's the point: it gives Granola things its internal data can't.

1. **Shareable ground truth.** Real customer meetings are private and have no known answers. Northwind's synthetic meetings are in Granola's exact format, privacy-free, and every answer is known by construction.
2. **Comparability.** Any system can run on the same questions: other meeting tools, generic memory libraries and plain frontier models, not just Granola against Granola.
3. **The outside-in view.** External agents (Claude, ChatGPT) reach Granola only through its MCP. A black-box test of the MCP measures what those agents actually experience: latency, answer quality and failure modes.
4. **Independence.** Results from an outside party are more credible than a vendor's own claims.

This is also why **Concentrate, the memory system built here, is a reference system, not a product pitch.** Granola can build a better one internally; it's here to show what a precomputed memory gains and loses. Our re-implementation of Granola's agent (b3) is just a generic agentic-retrieval baseline; the real Granola agent is only ever tested as a black box. We make no claims about Granola internals.

## What we measure

There is deliberately **no single score**: one number hides trade-offs, such as a system that is cheap and wrong. Every metric is defined, with a worked example, in [`docs/benchmark/METRICS.md`](docs/benchmark/METRICS.md).

| | Dimension | Question it answers | Main metrics | Status |
|---|---|---|---|---|
| A | Correctness | Is the answer right? | accuracy with 95% bootstrap CI, exact-match cross-check | ✅ |
| B | Judge reliability | Can we trust the grader? | second-judge agreement, Cohen's κ, human hand check | ◐ human check not done yet |
| C | Grounding | Can it show its work? | citation precision/recall vs gold lines, hallucination rate, source recall | ◐ citations only |
| D | Knowing what it doesn't know | Does it abstain, and know when it's likely wrong? | abstention on unanswerable questions, calibration | ◐ abstention only |
| E | Efficiency | What does it cost, and how fast? | $/question at equal prices, ingest cost and break-even, latency p50/p95 | ✅ |
| F | Robustness | Does it hold up when conditions change? | by question type, corpus size, transcript realism, transcript deletion, synthetic vs real | ◐ partial |

Comparisons are paired (McNemar, paired bootstrap) with Holm correction, so a tie is never reported as a win.

## Five tracks

Each track is a task plus metrics that any system can be scored on. Granola is the reference entry, alongside open baselines.

| Track | The task, for any system | Status |
|---|---|---|
| **Question answering** | Answer questions across many meetings: who said what, decisions, what changed, commitments, which person | ✅ Granola, top-k and Concentrate on real meetings, DEV ([`REAL_SLICE`](docs/results/REAL_SLICE.md)); 7 systems on synthetic diagnostics |
| **Retention** | Answer after transcripts are deleted: what's lost per question type, and what recovers it | ✅ real-meeting ladder, DEV ([`DELETION_SIM`](docs/benchmark/DELETION_SIM.md)); synthetic curve as a diagnostic ([`RETIREMENT`](docs/results/RETIREMENT.md)) |
| **Notes faithfulness** | Write notes for a meeting: is every line backed by what was said? | 🛠 in progress |
| **Agent interface quality** | An outside agent asks a meeting product a question: answer type (incl. "wrong, but confident"), evidence, stability, latency | 🛠 in progress |
| **Contextual integrity** | Answer someone who can only see some meetings, without revealing the rest | 📐 designed ([`SHARING_LEAKS_DESIGN`](docs/benchmark/SHARING_LEAKS_DESIGN.md)) |

Two kinds of data feed the tracks:

| | Synthetic (public) | Your own meetings (black-box) |
|---|---|---|
| **Who it's for** | any system that can ingest a corpus: memory libraries, RAG, long-context models, any vendor willing to load it | products that can't load our corpus, such as Granola or any meeting tool with an MCP |
| **Data** | *Northwind*: a synthetic company in Granola's exact format, 184 meetings, 296 questions (90 dev / 206 test) with known answers | the user's own meetings and questions, through the product's public MCP; everything stays local, only aggregates are shared |
| **Answers known by** | construction: the world is generated first | the meeting owner |
| **Docs** | [`benchmark/DESIGN.md`](docs/benchmark/DESIGN.md) | [`benchmark/BLACKBOX.md`](docs/benchmark/BLACKBOX.md) |

Existing memory benchmarks (LongMemEval, LoCoMo, BEAM, RealMem) are chat logs; we found none built on meetings.

**What a read-only probe of Granola's live MCP found (2026-11-02).** Granola's MCP is now an agent (`query_granola_meetings` searches, reads and cites), so the baseline to beat is per-query agentic retrieval, not naive top-k. On a handful of real meetings we still saw a cross-meeting identity miss (a teammate found in one meeting, missed in another where the invite showed only their email handle), commitments owned by "Unidentified speaker" when one microphone was shared in person, and summary-first answers that cost extra transcript round-trips for exact details. These are reproducible observations, not a measured error rate ([`PROBE_2026-11-02`](docs/archive/PROBE_2026-11-02.md)).

## Results

All results in this section are from the author's real meetings, through Granola's public MCP (read-only). They are **DEV**: the questions were seen while building the systems.

<!-- RESULTS:START -->
### 1. Retention: what transcript deletion costs ([`docs/benchmark/DELETION_SIM.md`](docs/benchmark/DELETION_SIM.md))

The same 47 real questions under four conditions, each adding one layer. B, C and D use our answer model (Opus 5.5) and prompt; A is Granola's own agent. Run `20261104T012414Z`, $6.42; aggregate: [`real-deletion-sim-dev.aggregate.json`](evals/reports/real-deletion-sim-dev.aggregate.json).

| Condition | Accuracy [95% CI] | Correct / partial / declined / wrong but confident ¹ | Transcript-needed (n = 18) | Paired vs row above (wins–losses, sign test) |
|---|---|---|---|---|
| A: Granola's agent, transcripts kept | **89% [79, 98]** | 42 / 2 / 0 / 3 | 94% | — |
| B: notes only (≈ chat after auto-deletion) | **57% [43, 70]** | 27 / 4 / 16 / 0 | 44% | 2–17, p = 0.001 |
| C: notes + our fact layer | 66% [51, 79] | 31 / 2 / 14 / 0 | 56% | 4–0, p = 0.13 |
| D: C + transcript passages when C can't answer | **87% [77, 96]** | 41 / 3 / 3 / 0 | 89% | 10–0, p = 0.002 |

- **Deletion's cost falls on detail.** On transcript-needed questions notes-only loses 50 points (0–9, p = 0.004); on notes-sufficient questions it still loses 35 (0–8, p = 0.008), so either our tag rule over-calls "the notes have it" or the model under-uses the notes. This run can't tell which.
- **The fallback fired on 60% of questions** (28 of 47). It reads transcripts, so it isn't deletion; it is "delete the hot copy, keep a cold one".
- **Judges agree** on 96–100% of verdicts per condition (second judge Sonnet 5.5, all 188 answers re-judged).
- **What A − B mixes.** B is our model over Granola's notes, not Granola's degraded chat, and it sees every note (an upper bound at 4 meetings). C − B and D − C don't have this problem.

¹ Counts out of 47, derived from the aggregate's accuracy, partial, confident-error and hedged-miss shares. *Declined* = not correct, not partial, not confident: an abstention or "the material doesn't cover it". *Wrong but confident* = judged incorrect without hedging (Granola's agent has no abstain flag, so its prose is read for hedges).

### 2. Question answering: reading real speech ([`docs/results/REAL_SLICE.md`](docs/results/REAL_SLICE.md))

All 4 meetings fit in a model's context, so this compares how well systems **read real speech**, not how well they search. Retrieval at scale isn't tested here.

| System | Accuracy [95% CI] | n | Median latency |
|---|---|---|---|
| **Granola's agent** (`query_granola_meetings`, live) | **92% [82, 100]** | 39 | 7.9 s |
| Top-k retrieval (k = 1 of 4 meetings) | 77% [64, 90] | 39 ² | 3.3 s |
| **Concentrate** (our fact memory) | **59% [44, 74]** | 39 | 4.2 s |

- Paired by question, Granola's agent was right where Concentrate was wrong 14 times, and the reverse once (sign test p ≈ 0.001). A second judge agreed on 97% of verdicts (κ 0.93).
- By category, Concentrate scores **0% on "who said X"** (Granola 88%) but **leads on "what changed"** (100% vs 83%, n = 6). Where it did answer a "who said" question, it twice named the wrong person.
- Of Concentrate's 16 non-correct answers, 10 were declines: it kept 43 facts and 11 commitments for the 4 meetings, and the questions asked about details it never extracted.
- 8 of the 47 questions weren't run for Granola and Concentrate because of budget: all 6 unanswerable and 2 temporal.
- Latency isn't like for like: Granola's is one remote call including its agent loop; ours is one local answer call.
- ² Top-k also ran on all 47 questions: 81% [68, 91], including 6 of 6 unanswerable.

### 3. Transcript fallback (DEV, not a claim) ([`docs/systems/concentrate/fallback.md`](docs/systems/concentrate/fallback.md))

When Concentrate's facts can't answer, it retrieves transcript passages and answers again. On the 22 real questions it reached before the budget cap, it rose from 12/22 to 20/22, fixing 8 and breaking none (p = 0.008). Granola's agent scored 19/22 on the same questions. These questions motivated the design, so the real test needs fresh questions written after the design freeze. It's off by default.
<!-- RESULTS:END -->

## Synthetic diagnostics (not evidence of real-world performance)

Northwind, the synthetic benchmark, is useful for debugging systems and for anyone who can't share meetings. It is **not** evidence of how a system does on real meetings, for five reasons:

1. **The same author built the generator and Concentrate's extractor,** so the synthetic questions ask about exactly the kind of fact the extractor keeps.
2. **Questions come from planted facts,** which favours extraction. Real questions ask about whatever was said.
3. **v1 is templated:** 78% of longer transcript passages are exact copies of 69 filler sentences, which makes retrieval unrealistically easy. v2 cuts that to 4.9%, but 54% of long passages still share their first six words with another, against 0.6% in real meetings ([`FIDELITY_V2`](docs/benchmark/FIDELITY_V2.md)). The results below are on v1.
4. **It is near its ceiling:** an oracle that reads only the right meetings scores 95%, so the top systems have little room to separate.
5. **It got the deletion question wrong.** On synthetic dev questions, adding a fact layer to notes-only recovered most of the loss (64% → 88%, [`RETIREMENT`](docs/results/RETIREMENT.md)). On real meetings the same idea barely helped (57% → 66%, not significant).

![The lesson: a memory system that tied on synthetic questions lost on real meetings](docs/figures/hero_synthetic_vs_real.svg)

**The lesson.** The same three approaches tied on synthetic test questions (left) and separated on real meetings (right).

**Seven systems on 120 held-out synthetic test questions** (Northwind-XL v1, 184 meetings, same answer model and judge for all; 2026-11-02). Sources: [`report`](evals/reports/20261103T002456Z.md) · [`significance`](evals/reports/20261103T002456Z.analysis.md) · [`red-team audit`](docs/results/RED_TEAM.md).

| System | Accuracy [95% CI] | $ / question ¹ | p50 latency | One-off ingest |
|---|---|---|---|---|
| Oracle (reads only the gold meetings; a ceiling) | 95% [91, 98] | 0.082 | 4.5 s | — |
| Concentrate | 92% [86, 96] | 0.040 | 3.8 s ² | $5 |
| Agent over search + transcripts (our re-implementation) | 90% [84, 95] | 0.137 (≈0.07–0.08 with prompt caching ³) | 13.2 s | — |
| Top-k hybrid retrieval (k = 10 meetings) | 88% [82, 94] | 0.328 | 6.4 s ² | — |
| Mem0 (our default wiring) | 81% [73, 88] | 0.017 | 3.3 s | $17 |
| Graphiti (our default wiring) | 72% [63, 79] | 0.029 | 4.1 s | $70 ⁴ |
| Summaries only | 71% [62, 78] | 0.017 | 4.7 s ² | — |

- **Concentrate, the agent and top-k are statistically tied** (McNemar with Holm correction). Concentrate and the agent disagree on 10 questions, splitting 6–4. A second judge changed none of Concentrate's 120 verdicts.
- **Cost:** Concentrate is about 3.5× cheaper per question than the agent at equal prices (about 2× against an agent with prompt caching) and, counting ingest, cheaper only once a set of meetings gets more than about 50 questions.
- **Mem0 and Graphiti trail because of our wiring, not those libraries.** Mem0 saw only 25 of its 5,292 memories per question; Graphiti was never given the meeting summaries. Fixed, the red team expects 87–92% and 82–88%.
- **Only Concentrate gets an org directory;** it decided 1 of 120 questions outright, and its effect on the memory build is unmeasured.
- **Retirement curve (dev):** deleting every transcript moves Concentrate only from 89% to 88%, but no synthetic dev question needs exact wording or who-said-what. The real-meeting ladder above shows what goes missing when those are asked.
- The [v2.1 extras](docs/benchmark/V2_EXTRAS.md) add exact-quote and speaker-attribution questions; they haven't been run.

¹ Re-priced from token counts at the same interactive Opus 5.5 price ($4/M input, $20/M output). The raw report mixes Batch API (50% off) and full-price rows. ² Batched systems' latency comes from the 10 questions run interactively, not all 120. ³ Red-team estimate, not measured. ⁴ Includes $10.66 of work lost to a crashed embedded database and redone ([`docs/engineering/HARDENING.md`](docs/engineering/HARDENING.md)).

## Reasons to be skeptical

- **The real data is small.** 4 meetings, one person's account, 39–47 questions; per-category cells of 4–8 questions are anecdotes.
- **The real gold answers were LLM-drafted and approved in bulk** (0 edits), not checked one by one; at least one looks wrong.
- **Every real result is DEV.** The questions were seen while building Concentrate, the fallback and the tag rule. **In progress:** hand-written held-out test questions, and paired Granola + Wispr Flow recordings of the same meetings.
- **The notes-only condition is our model, not Granola's.** It approximates chat after auto-deletion; Granola's real degraded chat could do better (a better prompt) or worse (retrieval misses at scale).
- **4 meetings don't test search.** Everything fits in context, so the real results measure reading, not retrieval at scale.
- **One answer model, one primary judge.** The judge is also the answer model; a second judge measures that bias rather than removing it. The blind human hand check is still to do.
- **b3 re-implements the shape of Granola's agent; it isn't Granola's agent.** The real one only reads the connected user's account, so it appears only in the real-meeting results.
- **The synthetic benchmark is a diagnostic** (see [above](#synthetic-diagnostics-not-evidence-of-real-world-performance)).
- **Granola has far more data and signal** (audio, millions of meetings) than this project. The aim is an independent, reproducible measurement, not a better product.

## Quick start

Needs Python 3.11 and [uv](https://docs.astral.sh/uv/). Everything in this first block is free: no API key, no LLM calls.

```bash
uv sync                                                # install (locked)
uv run pytest                                          # ~270 tests, no network
uv run concentrate synth --seed 42 --scale xl          # generate the 184-meeting benchmark (~5 s, deterministic)
uv run concentrate eval --xl --split dev --estimate    # price an eval run without running it
uv run concentrate fidelity data/fixtures/northwind    # style statistics of a corpus
```

The first `eval` downloads a small embedding model from Hugging Face. The 40-meeting base set is committed at `data/fixtures/northwind`; the XL set is regenerated into `data/fixtures/large/` (git-ignored).

**Running systems costs API credit.** Copy `.env.example` to `.env` and set `ANTHROPIC_API_KEY`. Every command prints an estimate first and asks before spending more than $2. LLM calls are disk-cached, so reruns are free.

```bash
uv run concentrate eval --split dev --systems b1 --limit 10      # small pilot on the base set (~$0.60)
uv run concentrate ingest --help                                 # build Mem0 / Graphiti / Concentrate memories (resumable)
uv run concentrate mcp-serve --xl                                # serve a built XL memory over MCP (stdio)
```

**The web app** (note viewer and results explorer, port 3100; reads `evals/reports/` directly):

```bash
cd web && npm install && npm run dev    # then open http://localhost:3100/results
```

**Your own Granola notes** sync through the official MCP on any plan. It opens your browser once to sign in, keeps tokens in `~/.config/concentrate/`, and never calls write tools. With a Business/Enterprise API key (`GRANOLA_API_KEY` in `.env`) the REST API works too. Synced notes go to a local SQLite file and are never committed. `concentrate real-kit --help` lists the black-box kit's commands.

```bash
uv run concentrate sync --source granola-mcp   # or: --source granola
```

## Architecture

![Hindsight architecture: public Granola interfaces and the synthetic generator feed one Source protocol, a note store, seven systems, one eval harness, and reports](docs/figures/architecture.svg)

Every system gets the same questions, answer model and judge, so differences come from what each puts in front of the model. All Granola I/O lives behind one `Source` protocol, and fixtures use the exact API response shape, validated by the same Pydantic models as real data. Five more diagrams (query-time vs write-time, the Concentrate pipeline, the evaluation pipeline, provenance) are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

**Concentrate**, the reference memory, resolves speakers and entities, extracts atomic facts that cite their transcript lines, drops any fact its cited lines don't support, and stores facts with the dates they were true. The techniques aren't new (Graphiti, Mem0 and others already do time-aware facts); the meeting-specific parts are what's being tested. The name comes from a July 2026 interview in which Granola's CEO contrasted agents reading meetings at query time with handing them a compressed, always-ready context, ["apple juice from concentrate"](https://www.platformer.news/granola-chris-pedregal-interview/).

## Docs map

The full map is [`docs/README.md`](docs/README.md); the current plan is [`docs/PLAN.md`](docs/PLAN.md).

| Doc | What it covers |
|---|---|
| [`docs/report/HINDSIGHT_2PAGE.md`](docs/report/HINDSIGHT_2PAGE.md) ([PDF](docs/report/HINDSIGHT_2PAGE.pdf)) | **Start here:** short summary, with figures and analysis |
| [`docs/report/HINDSIGHT_REPORT.md`](docs/report/HINDSIGHT_REPORT.md) | Full technical report, paper style |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Five diagrams with links to the code |
| [`docs/benchmark/METRICS.md`](docs/benchmark/METRICS.md) | Every metric, how it's computed, and its weaknesses |
| [`docs/benchmark/DESIGN.md`](docs/benchmark/DESIGN.md) | Benchmark card: construction, categories, systems, limitations |
| [`data/README.md`](data/README.md) | Dataset card for Northwind (Hugging Face format) |
| [`docs/benchmark/FIDELITY.md`](docs/benchmark/FIDELITY.md), [`FIDELITY_V2.md`](docs/benchmark/FIDELITY_V2.md), [`V2_EXTRAS.md`](docs/benchmark/V2_EXTRAS.md) | How close the synthetic transcripts are to real ones; the v2.1 extras |
| [`docs/results/`](docs/results/) | [Real slice](docs/results/REAL_SLICE.md), [red-team audit](docs/results/RED_TEAM.md), [analysis](docs/results/ANALYSIS.md), [retirement curve](docs/results/RETIREMENT.md), [notes vs transcript](docs/results/NOTES_VS_TRANSCRIPT.md), [economics](docs/results/ECONOMICS.md), [brief](docs/results/BRIEF.md) |
| [`docs/systems/`](docs/systems/) | [Concentrate](docs/systems/concentrate/memory.md) and its [fallback](docs/systems/concentrate/fallback.md), the [Mem0 and Graphiti baselines](docs/systems/baselines/), the [MCP server](docs/systems/MCP_SERVER.md) |
| [`docs/engineering/`](docs/engineering/) | [Decision log](docs/engineering/DESIGN.md) (including bad results), [Granola field provenance](docs/engineering/SCHEMA_NOTES.md) (VERIFIED / ASSUMED), [hardening](docs/engineering/HARDENING.md), [results explorer](docs/engineering/RESULTS_EXPLORER.md) |
| [`docs/guide/TOUR.md`](docs/guide/TOUR.md) | A guided tour of the repo for a new reader |
| [`docs/archive/`](docs/archive/) | Earlier plans and the [live MCP probe](docs/archive/PROBE_2026-11-02.md) |
| [`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md) | Adding a system, running the benchmark; reporting a problem |

## Not affiliated with Granola

This is an independent research prototype. It reads and writes data in the formats of Granola's public REST API and official MCP server, and was calibrated on the author's own Granola meetings. It makes no claims about Granola internals; everything reported about Granola is observable through public interfaces. The web UI is inspired by Granola's visual language but uses none of its name, logo or fonts.

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
