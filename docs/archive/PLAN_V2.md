# Hindsight v2: the plan

**Status:** proposal for Kian's decision. Nothing in it is built yet. Every phase starts only on Kian's go.

## 0. Goal and "done"

**Goal:** a Granola engineer opens the repo and, within 60 seconds, understands four things:
1. what is being measured;
2. why it matters to Granola;
3. what the results say, including where the author's own system lost;
4. that every number can be traced and re-run.

**Done means:**
- the README answers the four points above with one hero figure;
- a single "How Hindsight measures" page defines every metric;
- every result page has figures that are generated from data, not drawn by hand;
- the repo tree is obvious;
- every claim survives a red-team pass.

---

## 0.5 Design principle: outside-in

**The rule:** the benchmark must be useful to Granola *because* it's external. If Granola could reproduce a result, or beat it, with internal data, that result is worth little to them. So everything is built **only on public interfaces** (Granola's public API/MCP and its note/transcript format) and on **data anyone can share**.

**What an outside benchmark can give Granola that internal data can't:**
1. **Shareable ground truth.** Granola's real data is private customer meetings. It can't be published, shared with vendors, or used in public comparisons, and it has no known correct answers. Hindsight's synthetic meetings are realistic, in Granola's exact format, privacy-free, and every answer is known by construction. Granola can't easily make this from its own data.
2. **Comparability.** A public benchmark lets Granola compare itself with *anyone* on the same questions: other meeting tools, generic memory products (Mem0, Graphiti, Zep), and plain frontier models. Internal tests only compare Granola with Granola.
3. **The outside-in view.** External agents (Claude, ChatGPT) reach Granola only through the MCP. A black-box test of the MCP measures exactly what those agents, Granola's fastest-growing usage, experience. That includes latency, answer quality and failure modes, as seen from the outside.
4. **Independence.** Results from an outside party carry more credibility than a vendor's own claims, with users, with partners and in marketing.

**What this rules out, or demotes:**
- **Concentrate is a reference system, not a product.** It is there to show what a memory approach gains and loses.
- **b3 (our imitation of Granola's agent) is demoted** to "a generic agentic-retrieval baseline". The real Granola agent is only ever tested as a black box.
- **No claims about Granola internals.** We report only what's observable through public interfaces.

**What this adds** (sections 2, 3, 8):
- **Two tracks:**
  1. **Public track (synthetic):** any system that can ingest a corpus (Mem0, Graphiti, Concentrate, plain RAG, long-context models, and any vendor willing to load the corpus) runs on Northwind with known answers.
  2. **Black-box track (bring your own meetings):** products that can't load our corpus, such as Granola or any meeting tool with an MCP, are tested on a user's own account with that user's own questions, through the product's public MCP. The kit (`concentrate real-kit`) becomes a general tool: point it at any meeting MCP, write or verify questions, score them, keep everything local.
- **An MCP adapter layer:** one interface that can query any meeting-memory MCP server (Granola's, Concentrate's, others'), so a new product needs an adapter, not new benchmark code.
- **A conformance / regression suite for meeting MCPs:** a fixed set of black-box checks that Granola could run against its own MCP on every release, using a test account seeded with synthetic meetings, *if* Granola allows importing notes into an account. That's an open question, written up as ASSUMED until verified.

---

## 0.6 Benchmark, not audit: everything is a track

Every investigation is a **track**: a task plus metrics that any system or product can be scored on. Granola is one entry in each track, never the subject of a one-off audit. That's what makes this a benchmark (comparable, reusable, product-neutral) rather than a review of one company.

| Track | Task, for any system | Metrics | Entries |
|---|---|---|---|
| **Question answering** (built) | Answer questions about many meetings | accuracy, confident error, citations, cost, latency | 7 systems on synthetic data; Granola and others on real meetings |
| **Retention** (built) | Answer after transcripts are deleted | deletion cost by question type, recovery per layer | any system; real-meeting ladder via the public MCP |
| **Notes faithfulness** (in progress) | Write notes for a meeting; is every line backed by what was said? | support / inferred / unsupported / contradicted by claim type; cross-meeting bleed | any notetaker on a user's own meetings; summariser baselines on synthetic meetings |
| **Agent interface quality** (in progress) | An outside agent asks a meeting product a question; how good is the payload? | Granola's four answer types, evidence granularity, stability across repeats, latency | any meeting MCP: Granola, Concentrate's MCP, others |
| **Contextual integrity** (designed, [SHARING_LEAKS_DESIGN](SHARING_LEAKS_DESIGN.md)) | Answer an asker who can only see some meetings, without revealing the rest | leak rate, over-restriction, inference-leak rate | any memory system, on the synthetic company with access labels |

**How tracks are chosen** (Kian's rules): the problem is evidenced recently; it isn't Granola's visible top priority; and an outside measurement or technique could moderately or heavily improve it.

## 1. What the benchmark measures

There is deliberately **no single score**. One number would hide trade-offs: a system can be cheap and wrong, or right and slow. Results are shown along six dimensions, plus a cost/quality frontier.

| # | Dimension | Question it answers | Metrics | How it's computed | Status |
|---|---|---|---|---|---|
| A | **Correctness** | Is the answer right? | accuracy (95% bootstrap CI); partial credit; exact-match cross-check | An LLM judge compares each answer with a **known answer**. Known answers are planted by the generator (synthetic) or approved by the meeting owner (real). | ✅ have |
| B | **Judge reliability** | Can we trust the grader? | second-judge agreement, Cohen's κ; human hand-check agreement | A different model re-grades everything. A blind human sample is graded by hand. | ✅ have (human check not done yet) |
| C | **Grounding** | Can it show its work, and does it make things up? | citation precision/recall vs gold lines; **faithfulness / hallucination rate**; **source recall** (was the right meeting among those pulled?); **sources pulled per answer** | Citations are matched to gold line IDs. Faithfulness runs the existing entailment checker (`memory/verify.py`) over each answer and the lines it cites. Source recall comes from the retrieved meetings logged per answer. | ◐ citations ✅; source stats free to add; faithfulness ~$1–2 |
| D | **Knowing what it doesn't know** | Does it admit ignorance and know when it's likely wrong? | abstention precision/recall on unanswerable questions; **calibration**: reliability diagram, expected calibration error (ECE), Brier score; selective accuracy ("accuracy when confident") | Unanswerable questions exist. Calibration needs each system to report a 0–1 confidence with its answer. | ◐ abstention ✅; calibration needs new runs |
| E | **Efficiency** | What does it cost, and how fast is it? | $/question at **equal prices**; tokens in/out; one-off ingest $ amortised over N questions, with break-even N; latency p50/p95; tool calls | From each answer's token log, re-priced with one price list. | ✅ have (the pricing fix must go into the tooling) |
| F | **Robustness** | Does it hold up when conditions change? | by question type; by corpus size (scale curve); by transcript realism (v1 → v2 → v2.1); by retention (transcript-deletion curve); **synthetic vs real gap** | The same metrics, sliced by condition. | ◐ partial runs exist |

**Statistics everywhere:** paired tests (McNemar, paired bootstrap) with Holm correction, so a tie is never reported as a win. Every report records a power / minimum-detectable-gap note.

**Weaknesses, written next to each metric on the metrics page:** LLM judges are fallible; synthetic known answers share a blind spot with extraction-based systems; real known answers were LLM-drafted; n is small.

---

## 2. What the benchmark is based on: the datasets

| Dataset | What it is | What it tests | Limits |
|---|---|---|---|
| **Northwind v1** (XL) | Synthetic company: 184 meetings, 296 questions | the first comparison | templated wording (96% filler); questions follow planted facts |
| **Northwind v2** | Same world, realistic wording, lengths and noise | whether results hold on realistic text | not yet run |
| **v2.1 extras** | Hybrid rooms, renames, corrections, exact quotes, who-said | the long-tail failure the real slice exposed | small n per category |
| **Real slice: dev** | 47 LLM-drafted questions on Kian's 4 meetings | development only; it shaped the fallback | seen; bulk-approved answers |
| **Real slice: test** (new) | ~15 questions **hand-written by Kian** after the design freeze | the honest real-world test | one person, 4 meetings |
| **Multi-seed** (proposed) | 3 synthetic companies from different seeds (free to generate) | whether results generalise beyond one world | costs answer runs |

---

## 3. The systems, and fairness rules

**Systems:**
- query-time: top-k, summaries, oracle (a ceiling), the agent (a re-implementation of Granola's shape);
- write-time: Mem0, Graphiti, Concentrate;
- Concentrate + transcript fallback;
- and, on real data only, Granola's actual agent.

**Fairness protocol:** these rules come from the red team, and each one becomes a written rule on the systems page.
1. **The same inputs for everyone.** Either every system gets the org directory or none does. Run the directory ablation.
2. **The same tuning budget,** or report the asymmetry. Fix the known wiring gaps: Mem0 top_k 25 → 100 (~$2, no re-ingest). Feed Graphiti the summaries (~$60 re-ingest, so defer it, or keep and label it "default wiring").
3. **The same price list** for $/question (interactive Opus price), with batch savings reported separately.
4. **Any system tuned on dev is never shown test answers.**

---

## 4. Experiments menu: each has a question, a method and a cost

API credit is limited, so each experiment is approved one at a time with an exact token-counted quote. **$0** means it re-analyses saved results.

| ID | Question | Method | Cost | Priority |
|---|---|---|---|---|
| X0 | How many sources does each system pull, and does it find the right meeting? | re-analyse the saved reports | $0 | 1 |
| X1 | **Does the fallback close the gap on unseen real questions?** | Granola agent vs Concentrate (off / on_abstain) vs top-k on Kian's ~15 hand-written questions | ~$2–4 | **1** |
| X2 | How often does each system hallucinate? | run the entailment checker (Haiku) on every saved answer and its cited lines | ~$1–2 | 2 |
| X3 | Is the Mem0 gap just our wiring? | rerun Mem0 with top_k 100 on the 120 test questions | ~$2 | 2 |
| X4 | How much does the directory help Concentrate? | rebuild without the directory, then answer the 120 test questions | ~$7.5 | 3 |
| X5 | Do systems know when they're wrong? | add a confidence field; rerun Concentrate + agent + top-k on the 90 dev questions | ~$5–8 | 3 |
| X6 | What does deleting transcripts lose on exact-quote / who-said questions? | retirement curve on the v2.1 extras | ~$8 | 3 |
| X7 | Do results hold on realistic text (v2)? | rebuild Concentrate on v2; run a cheap subset of systems | ~$15–25 | 4 |
| X8 | Do results generalise across companies? | 3 seeds × cheap systems × 40 questions | ~$15 | 4 |

**Phase 2 budget (X1–X3): ~$6–8.**

---

## 5. Figures: each answers one question

They're generated by `concentrate figures <report>` into `docs/figures/` as deterministic SVGs, committed, and rebuilt by CI. Library: matplotlib (to be added as a dependency). The web explorer stays as the interactive version.

| Figure | Answers |
|---|---|
| **Hero: synthetic vs real** | "Why did the tie on synthetic data become a loss on real meetings?" |
| Leaderboard with CI whiskers | Who's ahead, and is it real? |
| Cost vs accuracy frontier (equal prices, amortisation slider values) | What's the best system for a given budget? |
| Pairwise win/loss matrix with significance | Which gaps are real? |
| Accuracy by question type (heatmap) | Where does each system break? |
| Failure taxonomy (stacked bars) | *Why* is it wrong? |
| Transcript-deletion curve | What does deleting transcripts cost? |
| Scale curve | Does retrieval degrade as meetings pile up? |
| Fallback before/after (paired) | Did the hybrid help, and where? |
| Judge agreement | Can we trust the grader? |
| Reliability diagram (after X5) | Do systems know when they're wrong? |
| Token and cost breakdown | Where does the money go? |

---

## 6. Architecture diagrams

Written in Mermaid, which renders on GitHub, plus one polished hero diagram as SVG.
1. **The system map:** Granola (API/MCP) → sources → store → the seven systems → harness → reports → analysis / figures / explorer.
2. **Query-time vs write-time:** the core idea in one picture (open-book vs notebook).
3. **The Concentrate pipeline:** Enhance → Extract → Gate → bitemporal store → answer, with the fallback branch.
4. **The evaluation pipeline:** questions → answers → judge(s) → paired statistics → figures; where every number comes from.
5. **The provenance chain:** answer → cited line IDs → transcript line → meeting, with data hash and git SHA.

---

## 7. Repo organisation

```
README.md               60-second version: what, why, hero figure, results, caveats
docs/
  PLAN.md               this file
  benchmark/            DESIGN (was BENCHMARK), METRICS (new), DATASETS, FIDELITY, FIDELITY_V2, V2_EXTRAS
  systems/              OVERVIEW + FAIRNESS (new), concentrate/, baselines/, MCP_SERVER
  results/              INDEX (new: one line per experiment + figure), FINAL_COMPARISON, REAL_SLICE,
                        RETIREMENT, ECONOMICS, FALLBACK, BRIEF, RED_TEAM, ANALYSIS
  figures/              generated SVGs
  engineering/          HARDENING, SCHEMA_NOTES, RESULTS_EXPLORER, WEB_DEMO, DESIGN (decision log)
  guide/                TOUR, CONCEPTS, EXERCISES
  archive/              SCOPE, PHASE4_DESIGN, PROBE_2026-11-02, PROBE_V2, UI_REFERENCES, CONCENTRATE_TUNING
packages/concentrate/   delete the empty stubs api/, profile/, redact/, verify/ (0 lines each)
evals/reports/          unchanged; add evals/reports/INDEX.md listing each run and what it was for
```

Moving files breaks links, so the reorganisation runs **alone, first**, with a link-checker. Every other workstream branches after it.

---

## 8. How it gets built (and how Kian stays in control)

**Phase 1: structure and measurement ($0, parallel sessions, no overlapping files)**
1. **Reorg:** move docs, fix every link, delete the empty stubs, add the results index. This runs alone, first.
2. **Metrics:**
   - `docs/method/METRICS.md`, the full section 1, with a worked example per metric;
   - the $0 metrics: source recall, sources pulled, completeness;
   - equal-price costing inside `analyze` and `economics`.
3. **Figures:** the `concentrate figures` command, all the $0 figures, and CI regenerating them.
4. **Diagrams:** the five diagrams, the hero SVG, and the README rewritten around the hero figure.
5. **Black-box track:**
   - generalise `real-kit` into a "bring your own meetings" kit with an **MCP adapter layer**: Granola's MCP, Concentrate's MCP, and a template for any other meeting MCP;
   - keep all private data local;
   - document exactly which public interfaces are used, and nothing else.

**Phase 2: cheap, high-value experiments (~$6–8, each quoted and approved)**
- X1, the fresh real test, runs once Kian has written his questions;
- then X2 (hallucination) and X3 (Mem0 wiring).

**Phase 3: if credit allows,** X4–X8, chosen one at a time.

**Control rules:**
- Kian approves each phase.
- Each finished piece comes with a 3-line plain summary, and Kian explains it back.
- Every number goes through the red-team check before it reaches the README.
- Exact token counts are used before any money is spent.

---

## 9. Decisions for Kian

0. Agree with the **outside-in principle** (section 0.5), including demoting Concentrate to a reference system and generalising the real-data kit into a black-box "bring your own meetings" track for any meeting MCP?
1. Is the **measurement framework** (section 1) right, and is anything missing?
2. Approve the **repo reorganisation** (section 7)? It moves files and changes links.
3. Is **Phase 1 a go?** It costs $0 API and uses Claude Code usage.
4. **Credit balance** from the Anthropic console, to size Phases 2 and 3.
5. Your **~15 hand-written test questions**, which unblock X1.
