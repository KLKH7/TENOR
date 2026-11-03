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
| "an offline test suite run in CI" (no count claimed) | `.github/workflows/ci.yml`; `docs/HARDENING.md` §2 | ✅ The roster.py SyntaxError at f040b3a was fixed in `aa6c3fd` (`docs/RED_TEAM.md` R8). |

## Result 1: real meetings (the new headline)

Source: `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results". On `feat/granola-mcp-source` (b8a5cf5) that section still reads "Pending", and the numbers are only in the README "Results" §1. Raw per-question data stays private in `data/real/`; only aggregates are public.

| Claim | Source | Status |
|---|---|---|
| Granola 92% [82, 100], Concentrate 59% [44, 74], n = 39; CIs don't overlap | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" main table; README "Results" §1 | ⚠️ Re-check once B's final REAL_SLICE.md is merged. |
| Paired 14–1 (sign test p ≈ 0.001) | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" "The gap is large" | ⚠️ same |
| Top-k (k = 1) 77% [64, 90] on the same 39; 3.3 s latency from its 47-question run | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" table rows "b0 … (same 39)" and "(all 47)" | ⚠️ **The README says 76% [62, 89], n = 37** (the stopped first run). The final file says 77% on the same 39. I used 77%; the README needs aligning. |
| Latencies 7.9 s / 4.2 s, not like for like | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" table and "Latency isn't like-for-like" | ✅ |
| Who said 88% vs 0%; commitments 100 vs 75; which person 80 vs 40; what changed 83 vs 100 | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" "Accuracy by category" | ✅ (n = 4–8 per cell; quoted as anecdotes) |
| 43 facts + 11 commitments; 10 of 16 misses were abstentions | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" Method (build) and "What this says" 2 | ✅ |
| Ignored the named system-audio speaker labels | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" "What this says" 3 and 6 | ✅ |
| Second judge 97%, κ 0.93, ranking unchanged | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" "Judges agree" | ✅ |
| LLM-drafted, bulk-approved with 0 edits; at least one gold looks wrong; 6 unanswerable not run | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" "Gold quality", "Coverage" | ✅ |
| Concentrate memory built from the same notes via the MCP | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results" Method; `docs/DESIGN.md` "Real data via Granola's MCP" | ✅ |
| Pedregal: "might want to still go back and chat with that meeting" | Platformer interview (coordinator's verified excerpt, 2026-11-03) | ✅ |

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

## Result 2: synthetic benchmark

Report `evals/reports/20261103T002456Z.md` (test split, 120 questions, Opus 5.5, seed 42, git `aeafca5` dirty, `northwind-xl` = generator `northwind-1`). Analysis: `evals/reports/20261103T002456Z.analysis.md`. Corrections from the red-team audit, `docs/RED_TEAM.md`. The README "Results" §2 uses the same numbers.

| Claim | Source | Status |
|---|---|---|
| Accuracy and CI per system (95/92/90/88/81/72/71) | `evals/reports/20261103T002456Z.md` | ✅ |
| Tied (Holm p = 1.0); 10 disagreements, 6–4 | `evals/reports/20261103T002456Z.analysis.md` §1; `docs/RED_TEAM.md` "What survives" | ✅ |
| $/q at equal interactive prices: 0.082/0.040/0.137/0.328/0.017/0.029/0.017 | `docs/RED_TEAM.md` R1 table; README footnote ¹ | ✅ |
| Agent ≈0.07–0.08 with prompt caching | `docs/RED_TEAM.md` R1 footnote ¹ | ✅ (an estimate, not measured; labelled as such) |
| ~3.5× (≈2× vs a caching agent) | `docs/RED_TEAM.md` R1 "Like for like" | ✅ |
| Ingest $5; break-even ~50 questions per corpus (51; ~140 against a caching agent) | `docs/RED_TEAM.md` R1 ingest table and "Break-even" | ✅ The $5 is interactive Sonnet ingest with caching ($4.96, `docs/RED_TEAM.md` R9). |
| Mem0: 25 of 5,292 memories per question; Graphiti never given the summaries | `docs/RED_TEAM.md` R2 | ✅ |
| Directory decided 1/120 questions; build effect unmeasured | `docs/RED_TEAM.md` R3 | ✅ |
| Gold errors ("reported complete on None", q694, and others) | `docs/RED_TEAM.md` R5 | ✅ |
| Second judge changed none of Concentrate's 120 verdicts | `docs/RED_TEAM.md` R4; `evals/reports/20261103T002456Z.judgecheck.claude-sonnet-5-5.md` | ✅ |
| MDE ~7 points for the top pairs at n = 120 | `evals/reports/20261103T002456Z.analysis.md` §2 | ✅ |
| Test run once; tuning on dev only | `docs/CONCENTRATE_TUNING.md`; `docs/RED_TEAM.md` "No test peeking" | ✅ |

## Threats to validity

| Claim | Source | Status |
|---|---|---|
| Same author built the generator and the extractor | README "Reasons to be skeptical" | ✅ |
| Only Concentrate gets the org directory; ablation not run | README "Reasons to be skeptical" | ✅ |
| Unequal tuning (concrete: query layer anchors on generator templates) | `docs/RED_TEAM.md` R6; `docs/CONCENTRATE_TUNING.md` | ✅ |
| v1 wording templated; v2 and v2.1 not run | `docs/FIDELITY.md`, `docs/FIDELITY_V2.md`, `docs/BENCHMARK_V2_EXTRAS.md` | ✅ |
| Second judge run on the final report | `evals/reports/20261103T002456Z.judgecheck.claude-sonnet-5-5.md` | ✅ (replaces the earlier "pilot only" claim) |

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

The memo now uses only `docs/RED_TEAM.md` R1's per-question figures and the break-even per corpus (above). The earlier $/user/month table was dropped: it rested on a guessed usage (40 meetings, 30 questions) and on `evals/economics/20261103T002456Z.md`, which re-uses Batch-discounted `cost_usd` (flagged to the coordinator). Re-derive it before quoting.

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

## Limitations, transcript deletion and next steps

| Claim | Source | Status |
|---|---|---|
| b3 isn't Granola's agent | `README.md` "Honest limits" | ✅ |
| "Evidence against deleting transcripts, for a hybrid" | inference from Result 1 (fact-only memory: 0% on who said; 10 of 16 misses were missing details) plus the retirement caveat (no exact-wording questions in synthetic dev) | ✅ framed as my reading of the evidence, not a measurement of deletion on real data |
| Hybrid next step (transcript fallback, speaker labels, needs fresh questions) | `docs/REAL_SLICE.md` @ `c86807d` "What this says" 6 | ✅ |
| v2.1 adds exact-quote and speaker-attribution questions, not run | `docs/BENCHMARK_V2_EXTRAS.md` intro | ✅ |
| Directory ablation (~$5 ingest + $2.60 answers) | `docs/RED_TEAM.md` R3 "Fix" | ✅ |
| Fix Mem0/Graphiti wiring and gold errors, re-score | `docs/RED_TEAM.md` R2, R5 | ✅ |
| Identity at write time targets the probe misses | `docs/PROBE_V2.md` §1.1, §4.1; `docs/MCP_SERVER.md` `resolve_entity` | ✅ |

## Outreach-only claims

| Claim | Source | Status |
|---|---|---|
| Fields tagged VERIFIED/ASSUMED | `docs/SCHEMA_NOTES.md` | ✅ |
| Pilot saturated at 90–100% | `README.md` status box | ✅ |
| Builds hung; traced to SDK timeout defaults | `docs/HARDENING.md` §1 (the cause is *inferred*, with no stack trace) | ✅ Keep "traced to", not "proved". |
| The emails say "Chris" (Pedregal) | Platformer interview, verified | ✅ |
| "tested my own system against Granola's and reported it lost" | `docs/REAL_SLICE.md` @ `claude/ws-b-real-slice-9332a8` (`c86807d`) "Results"; this memo | ✅ |
| "red team cut the cost claim from ~6× to ~3.5×" | `docs/RED_TEAM.md` R1 (the "as reported" vs "full price" columns: 0.137 ÷ 0.022 ≈ 6.2; ÷ 0.0395 ≈ 3.5) | ✅ |
