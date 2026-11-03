# Findings checklist

This file pairs every claim in [MEMO.md](MEMO.md) (and the deck and emails that reuse it) with the file that backs it. Status:
- ✅ backed by a committed file on this branch;
- (2026-11-03) every workstream branch is merged into `feat/granola-mcp-source` (f040b3a), so the earlier "unmerged branch" marks are gone, and the merged RETIREMENT and PROBE_V2 numbers were re-checked;
- ⚠️ needs a manual check before sending.

**Rule before sending:** every ⚠️ resolved or the claim removed.

## The question

| Claim | Source | Status |
|---|---|---|
| Pedregal: connected to Claude, the agent does "a relatively dumb job at extracting information"; Granola should send "apple juice from concentrate" | [Platformer interview by Casey Newton, 30 Jul 2026](https://www.platformer.news/granola-chris-pedregal-interview/), read in full by the coordinator session | ✅ The exact wording is verified. He says "connected to Claude", not "MCP": tying it to the MCP connector is our inference, so the memo doesn't say "MCP" in the quote. He frames it as a direction ("we're in this awkward phase right now"), not a shipped feature. |
| Granola is looking into "whether we should delete the transcript automatically", and researching compression | same interview | ✅ Stated directly by Pedregal. "Working memory" is Newton's intro wording, not his, so don't put it in quotation marks as his. |
| Enhanced transcript "built for agents" (which Sam you mean) | same interview ("something we're playing around with") | ✅ |
| Granola never stored audio; MCP is the "fastest-growing chart" | same interview | ✅ Not used in the memo; available if needed. |
| "Granola is already researching these ideas; this is independent measurement, not invention" | follows from the three rows above | ✅ framing |

## What I built

| Claim | Source | Status |
|---|---|---|
| The memory benchmarks found (LongMemEval, LoCoMo, BEAM, RealMem) are chat logs, none on meetings | `docs/BENCHMARK.md` intro ("survey on 2026-11-02") | ✅ (scoped as "the ones I found") |
| 184 meetings, 296 questions, 90 dev / 206 test, 9 categories | `docs/BENCHMARK.md` "At a glance", "Question categories" | ✅ |
| World-first generation, verified answers with gold lines | `docs/BENCHMARK.md` "How it's built"; `docs/DESIGN.md` "Ground truth first" | ✅ |
| Exact Granola API JSON shape, validated by the same models | `docs/BENCHMARK.md` "Fidelity"; `tests/test_contract.py` | ✅ |
| Noise calibrated on real synced meetings | `docs/PROBE_2026-11-02.md` §6; `docs/FIDELITY.md` | ✅ (n = 4 meetings, one owner) |
| 7 systems, same answer model (Opus 5.5) and judge | `README.md` "How a run works"; `docs/DESIGN.md` Phase 3 | ✅ |
| b3 mirrors the MCP's tools, 12-turn budget | `docs/DESIGN.md` Phase 3 | ✅ |
| Concentrate pipeline (resolve → extract → entailment gate → bitemporal store) | `docs/PHASE4_DESIGN.md` | ✅ |
| McNemar exact + Holm over 21 pairs, paired bootstrap, power, Pareto with amortised ingest | `docs/ANALYSIS.md` §1–3; `tests/test_analysis.py` | ✅ |
| Second judge: 97.5% agreement, κ 0.89, ranking unchanged (pilot, 40 answers) | `docs/BENCHMARK.md` Metrics; `evals/reports/20261102T100403Z.judgecheck.claude-sonnet-5-5.md` | ✅ (base pilot, not XL) |
| Blind hand-check export | `docs/BENCHMARK.md` Metrics (`concentrate hand-check`) | ✅ |
| Read-only MCP source | `CLAUDE.md`; `docs/DESIGN.md` "Real data via Granola's MCP"; ws-b's `ReadOnlyCaller` test | ✅ |
| Deterministic: same seed, identical bytes | `docs/BENCHMARK.md`; byte-equality fixture test | ✅ |
| Timeouts, stall watchdog, resumable builds | `docs/HARDENING.md` §2–3 | ✅ |
| "an offline test suite run in CI" (no count claimed) | `.github/workflows/ci.yml`; `docs/HARDENING.md` §2 | ⚠️ On f040b3a, `uv run pytest` **fails to collect** (14 errors): `packages/concentrate/synth/roster.py:179` has an inline comment that swallows the rest of a `Person(...)` call (SyntaxError). Fix it before anyone clones the repo. Reported to the coordinator. |

## Live MCP probes

| Claim | Source | Status |
|---|---|---|
| Plus plan, 4 meetings, read-only | `docs/PROBE_2026-11-02.md` header | ✅ |
| 14 questions plus repeats (16 agent calls) | `docs/PROBE_V2.md` "Design" | ✅ |
| Commitment tracking strong, stable over 3 repeats | `docs/PROBE_V2.md` §1, §3 | ✅ |
| Honest abstention on broad single-mic questions; explicit dates scoped | `docs/PROBE_V2.md` §1.2, §3 | ✅ |
| Retrieval tools ≈0.3–0.4 s | `docs/PROBE_V2.md` §2 | ✅ |
| Missed a display-name-only attendee and the second of two people sharing a first name | `docs/PROBE_V2.md` §1.1 | ✅ **Don't** cite probe 1's one-of-two meeting miss as a current failure: probe 2 didn't reproduce that exact case (§1.1, first bullet). |
| Same commitment attributed differently depending on phrasing | `docs/PROBE_V2.md` §1.2 | ✅ |
| `query_granola_meetings` median 8.5 s (n = 16, 1.6–17.7 s) | `docs/PROBE_V2.md` §2 | ✅ A lower bound at 4 meetings; say so if asked. |
| Answers are summary-first; details need transcript calls | `docs/PROBE_2026-11-02.md` §1 (the `transcript_note`) | ✅ |
| No meeting content is quoted anywhere in the write-up | `docs/PROBE_V2.md` "Privacy" | ✅ Re-check the deck's slide 5 cards when they're drawn. |

## Headline results

Final report: `evals/reports/20261103T002456Z.md` (test split, 120 questions, Opus 5.5, seed 42, git `aeafca5` dirty, corpus `northwind-xl` = generator `northwind-1`, spent $3.73). Analysis: `evals/reports/20261103T002456Z.analysis.md`. README "Results" uses the same report.

| Claim | Source | Status |
|---|---|---|
| Accuracy and CI per system (95/92/90/88/81/72/71) | `evals/reports/20261103T002456Z.md` main table | ✅ |
| Concentrate vs agent and top-k tied (Holm p = 1.00); 10 disagreements split 6–4 | `evals/reports/20261103T002456Z.analysis.md` §1 rows `concentrate/b3_agentic` (6 / 4), `concentrate/b0_topk` | ✅ |
| Concentrate beats Mem0 +11 (Holm 0.028), Graphiti +20, summaries +21 (both < 0.001) | `evals/reports/20261103T002456Z.analysis.md` §1 | ✅ |
| Detectable gap ~7 points for the top pairs at n = 120 | `evals/reports/20261103T002456Z.analysis.md` §2 (`concentrate vs b3_agentic`: 7 pts @ 120) | ✅ |
| Oracle 95% = saturation | `evals/reports/20261103T002456Z.md` | ✅ framing |
| p50 latency 3.8 s (Concentrate, 10 rows) vs 13.2 s (agent, 120 rows) | `evals/reports/20261103T002456Z.md` latency column and its footnote | ✅ The Concentrate figure comes from only 10 interactive rows; the table footnote says so. |
| **$/question at equal interactive prices**: Concentrate 0.040, agent 0.137, top-k 0.328, oracle 0.082, summaries 0.017, Mem0 0.017, Graphiti 0.029 | recomputed from `evals/reports/20261103T002456Z.jsonl`: Σ(input_tokens × $4/M + output_tokens × $20/M) ÷ 120 per system, using Opus 5.5 prices from `concentrate.llm.client.PRICING` | ⚠️ **The report's `cost_usd` mixes prices.** 110 of 120 rows for b0, b1, the oracle and Concentrate ran through the Batch API (50% off); the agent, Mem0 and Graphiti ran at full price. At the report's numbers Concentrate looks ~6× cheaper than the agent; at equal prices it's ~3.5×. The README's "about a sixth" and `docs/ECONOMICS.md` inherit the mixed prices. Reported to the coordinator. |
| "~3.5× cheaper than the agent" | 0.1372 ÷ 0.0395 | ✅ (equal prices) |
| Ingest $5 / $17 / $70 (incl. $10.66 redone) | README Results table; `evals/economics/20261103T002456Z.md` "Builds measured" (recorded $/mtg × meetings: 0.0269×184, 0.0925×184, 0.325×183) | ✅ |
| Aggregation 83/83/42/42/33/33; multi-hop 77 vs 85; decision change 31% for summaries | `evals/reports/20261103T002456Z.md` category table | ✅ No per-category gap survives Holm (`evals/reports/20261103T002456Z.analysis.md` §1 per category). |
| 134 wrong answers, 48 partial list; Concentrate 10 = 4 partial + 4 reasoning + 1 stale + 1 abstained wrongly | `evals/reports/20261103T002456Z.analysis.md` §1 summary, §4 table | ✅ |
| Frontier: summaries, Mem0, Concentrate, oracle; agent and top-k dominated | `evals/reports/20261103T002456Z.analysis.md` §3 | ✅ At equal prices the frontier membership doesn't change (Concentrate 0.040 is still below the agent's 0.137 and the oracle's 0.082). |
| Input tokens/question 8.8k vs 30k / 81k | `evals/reports/20261103T002456Z.md` | ✅ |
| Test run once, tuning on dev only | `docs/CONCENTRATE_TUNING.md` (`tune-c` refuses `--split test`) | ✅ |

## Threats to validity

| Claim | Source | Status |
|---|---|---|
| Same author built the generator and the extractor | README "Reasons to be skeptical" | ✅ |
| Only Concentrate gets the org directory; ablation not run | README "Reasons to be skeptical" | ✅ |
| Unequal tuning; Graphiti's 72% likely our wiring | README; `docs/CONCENTRATE_TUNING.md`; `docs/baselines/graphiti.md` | ✅ |
| v1 wording templated; v2 and v2.1 not run | `docs/FIDELITY.md`, `docs/FIDELITY_V2.md`, `docs/BENCHMARK_V2_EXTRAS.md` | ✅ |
| The second judge was checked on the pilot only | only `20261102T100403Z.judgecheck.*` exists in `evals/reports/` | ✅ |

## Retirement

| Claim | Source | Status |
|---|---|---|
| Dev, 90 questions: Concentrate 89% at N = ∞ → 88% at N = 0 | `docs/RETIREMENT.md` Results; `evals/reports/retirement/20261102T224523Z.curve.md` | ✅ |
| 4 of 90 verdicts change; 3 of them not monotonic in N | `docs/RETIREMENT.md` "Honest read" 1 | ✅ |
| Memory ≈ 5% of raw transcript tokens (31k vs 624k) | `docs/RETIREMENT.md` Results | ✅ |
| b1 summaries 64% | `docs/RETIREMENT.md` Results | ✅ |
| b0 not run for accuracy; 32% of gold line text at N = 14, 2% for entity questions | `docs/RETIREMENT.md` b0 contexts table | ✅ |
| A speaker's role is the one plausible loss (q245) | `docs/RETIREMENT.md` "Honest read" 1 | ✅ |
| No verbatim-quote category | ws-c hand-over note; benchmark category list in `docs/BENCHMARK.md` | ✅ |
| Test split not run; dev only | `docs/RETIREMENT.md` limits ("Test was not run") | ✅ |
| v2.1 adds exact-quote and speaker-attribution questions, not run | `docs/BENCHMARK_V2_EXTRAS.md` intro | ✅ |

## Unit economics

`uv run concentrate economics --report evals/reports/20261103T002456Z.jsonl` (free) wrote `evals/economics/20261103T002456Z.md`. Scenario: 40 meetings and 30 questions per user per month (**a guess**, `docs/ECONOMICS.md` assumptions), Sonnet ingest via Batch, Opus answers.

| Claim | Source | Status |
|---|---|---|
| Ingest $/meeting 0.014 / 0.046 / 0.163 (Concentrate / Mem0 / Graphiti) | `evals/economics/20261103T002456Z.md` main table | ✅ The Graphiti build is 183/184 meetings. |
| $/user/month: agent $4.12, Mem0 $2.36, Graphiti $7.36 | `evals/economics/20261103T002456Z.md` (these three answered at full price, so they're unaffected) | ✅ |
| $/user/month: Concentrate $1.74, top-k $9.84 | recomputed: 40 × ingest + 30 × equal-price $/q (Concentrate 40×0.0139 + 30×0.0395; top-k 30×0.3281) | ⚠️ Corrected. `evals/economics/20261103T002456Z.md` prints $1.21 and $5.32 because it re-uses the report's Batch-discounted `cost_usd`, despite saying "answer batch: no". |
| Break-even against the agent ~6 questions/month (~0.14 per meeting) | 0.556 ÷ (0.1372 − 0.0395) = 5.7 | ✅ (equal prices; `evals/economics/20261103T002456Z.md` prints 4.8 with mixed prices) |
| ~1.2 / ~6 / ~17 LLM calls per meeting | `evals/economics/20261103T002456Z.md` "Builds measured" calls/mtg (1.2, 5.9, 16.8) | ✅ |
| Haiku answers: query-time ~$0.10/user/month, write-time loses on cost | `evals/economics/20261103T002456Z.md` sensitivity grid (sonnet ingest × haiku answer: agent $0.10, Concentrate $0.57) | ✅ |

## Pre-meeting brief

| Claim | Source | Status |
|---|---|---|
| As-of SQL reads, no LLM, ~6 ms, no leakage | `docs/BRIEF.md` "How it works" | ✅ |
| 36-meeting test sample, baseline = top-k (24 chunks) + Sonnet 5.5 over earlier meetings only | `docs/BRIEF.md` Evaluation; `evals/reports/brief-test-20261102T223053Z.json` meta | ✅ The baseline uses Sonnet, not the Opus answer model. Say so if asked. |
| Commitments recall 91% (158/174) vs 30% (52/174) | same report, `summary.{memory,baseline}.commitments` | ✅ |
| Commitments precision 43% vs 90% | same | ✅ |
| Changes recall 58% vs 5%, 19 gold items | same, `changes` (memory tp 11/19, baseline tp 1/19) | ✅ The n is tiny; keep the "directional" wording. |
| Requests recall 78% vs 56%; stale 22% vs 38% | same, `requests` | ✅ (9 gold items) |
| Precision loss mostly from in-person owner attribution | `docs/BRIEF.md` "Where the memory brief is weak" | ✅ (inspected on dev) |

## Limitations and next steps

| Claim | Source | Status |
|---|---|---|
| v1: 78% duplicated long passages; v2: 4.9%; frames still repeat (54% vs 0.6%) | `docs/FIDELITY_V2.md` §3 | ✅ |
| b3 isn't Granola's agent | `README.md` "Honest limits" | ✅ |
| Real slice "in progress" | `docs/REAL_SLICE.md` Results ("Pending: the author is verifying the questions") | ✅ |
| Primary judge = answer model | `docs/BENCHMARK.md` Metrics | ✅ |
| Next step 1 (write-time identity) targets the probe misses | `docs/PROBE_V2.md` §4.1; `docs/MCP_SERVER.md` `resolve_entity` | ✅ |
| Next step 2 (`recurring_event_id`, attribution lever) | `docs/BRIEF.md` "Decisions" and "Open questions" | ✅ |
| Next step 3 (verbatim-quote category, line-ID pointers) | `docs/RETIREMENT.md` caveat; `docs/PHASE4_DESIGN.md` compression tiers | ✅ |

## Outreach-only claims

| Claim | Source | Status |
|---|---|---|
| Fields tagged VERIFIED/ASSUMED | `docs/SCHEMA_NOTES.md` | ✅ |
| Pilot saturated at 90–100% | `README.md` status box | ✅ |
| Builds hung; traced to SDK timeout defaults | `docs/HARDENING.md` §1 (the cause is *inferred*, with no stack trace) | ✅ Keep "traced to", not "proved". |
| The emails say "Chris" (Pedregal) | Platformer interview, verified | ✅ |
