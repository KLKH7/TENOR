# Findings checklist

This file pairs every claim in [MEMO.md](MEMO.md) (and the deck, and the private outreach drafts in `data/private/OUTREACH.md` that reuse it) with the file that backs it. Status:
- ✅ backed by a committed file on this branch;
- (2026-11-03) all sources are on `feat/granola-mcp-source` (rebased onto `origin/main`), including the final REAL_SLICE.md;
- ⚠️ needs a manual check before sending.

**Rule before sending:** every ⚠️ resolved or the claim removed.

## The question

| Claim | Source | Status |
|---|---|---|
| Pedregal: connected to Claude, the agent does "a relatively dumb job at extracting information"; Granola should send "apple juice from concentrate" | [Platformer interview by Casey Newton, 30 Jul 2026](https://www.platformer.news/granola-chris-pedregal-interview/), read in full by the coordinator session | ✅ The exact wording is verified. He says "connected to Claude", not "MCP": tying it to the MCP connector is our inference, so the memo doesn't say "MCP" in the quote. He frames it as a direction ("we're in this awkward phase right now"), not a shipped feature. |
| Granola is looking into "whether we should delete the transcript automatically", and researching compression | same interview | ✅ Stated directly by Pedregal. "Working memory" is Newton's intro wording, not his, so don't put it in quotation marks as his. |
| Enhanced transcript "built for agents" (which Sam you mean) | same interview ("something we're playing around with") | ✅ |
| Granola never stored audio; MCP is the "fastest-growing chart" | same interview | ✅ Not used in the memo; available if needed. |
| "Granola is already researching these ideas; this is independent measurement, not invention" | follows from the rows above | ✅ framing |
| Granola already ships transcript auto-deletion: users choose 1 day–1 year, enterprise admins can enforce it; afterwards chat works from notes only; "Chat quality will be degraded" | [Granola help centre: Transcript auto-deletion](https://docs.granola.ai/help-center/consent-security-privacy/transcript-auto-deletion), verified at source by the private usefulness audit (2026-11-03) | ✅ Re-open the page before sending, as product docs change. |
| Agent-design post (May 6, 2026): "breadth over fidelity"; reads many meetings at a high level (100 rather than 10 in full, "We chose 100"); transcripts ~10× the tokens of notes; goes deeper only when needed; avoids "wrong, but confident" | [How Granola thinks about designing agents](https://www.granola.ai/blog/how-granola-thinks-about-designing-agents), verified at source by the usefulness audit | ✅ The memo quotes only "breadth over fidelity" and "wrong, but confident". |

## What I built

| Claim | Source | Status |
|---|---|---|
| The memory benchmarks found (LongMemEval, LoCoMo, BEAM, RealMem) are chat logs, none on meetings | `docs/benchmark/DESIGN.md` intro ("survey on 2026-11-02") | ✅ (scoped as "the ones I found") |
| 184 meetings, 296 questions, 90 dev / 206 test, 9 categories | `docs/benchmark/DESIGN.md` "At a glance", "Question categories" | ✅ |
| World-first generation, verified answers with gold lines | `docs/benchmark/DESIGN.md` "How it's built"; `docs/engineering/DESIGN.md` "Ground truth first" | ✅ |
| Exact Granola API JSON shape, validated by the same models | `docs/benchmark/DESIGN.md` "Fidelity"; `tests/test_contract.py` | ✅ |
| Noise calibrated on real synced meetings | `docs/archive/PROBE_2026-11-02.md` §6; `docs/benchmark/FIDELITY.md` | ✅ (n = 4 meetings, one owner) |
| 7 systems, same answer model (Opus 5.5) and judge | `README.md` "How a run works"; `docs/engineering/DESIGN.md` Phase 3 | ✅ |
| b3 mirrors the MCP's tools, 12-turn budget | `docs/engineering/DESIGN.md` Phase 3 | ✅ |
| Concentrate pipeline (resolve → extract → entailment gate → bitemporal store) | `docs/archive/PHASE4_DESIGN.md` | ✅ |
| McNemar exact + Holm over 21 pairs, paired bootstrap, power, Pareto with amortised ingest | `docs/results/ANALYSIS.md` §1–3; `tests/test_analysis.py` | ✅ |
| Second judge: 97.5% agreement, κ 0.89, ranking unchanged (pilot, 40 answers) | `docs/benchmark/DESIGN.md` Metrics; `evals/reports/20261102T100403Z.judgecheck.claude-sonnet-5-5.md` | ✅ (base pilot, not XL) |
| Blind hand-check export | `docs/benchmark/DESIGN.md` Metrics (`concentrate hand-check`) | ✅ |
| Read-only MCP source | `CLAUDE.md`; `docs/engineering/DESIGN.md` "Real data via Granola's MCP"; ws-b's `ReadOnlyCaller` test | ✅ |
| Deterministic: same seed, identical bytes | `docs/benchmark/DESIGN.md`; byte-equality fixture test | ✅ |
| Timeouts, stall watchdog, resumable builds | `docs/engineering/HARDENING.md` §2–3 | ✅ |
| "an offline test suite run in CI" (no count claimed) | `.github/workflows/ci.yml`; `docs/engineering/HARDENING.md` §2 | ✅ The roster.py SyntaxError at f040b3a was fixed in `aa6c3fd` (`docs/results/RED_TEAM.md` R8). |

## Result 1: real meetings (the new headline)

Source: `docs/results/REAL_SLICE.md` "Results" (merged, final); README "Results" §1 agrees. Raw per-question data stays private in `data/real/`; only aggregates are public.

| Claim | Source | Status |
|---|---|---|
| Granola 92% [82, 100], Concentrate 59% [44, 74], n = 39; CIs don't overlap | `docs/results/REAL_SLICE.md` "Results" main table; README "Results" §1 | ✅ |
| Paired 14–1 (sign test p ≈ 0.001) | `docs/results/REAL_SLICE.md` "Results" "The gap is large" | ✅ |
| Top-k (k = 1) 77% [64, 90] on the same 39; 3.3 s latency from its 47-question run | `docs/results/REAL_SLICE.md` "Results" table rows "b0 … (same 39)" and "(all 47)" | ✅ The README now also says 77%, n = 39. |
| Latencies 7.9 s / 4.2 s, not like for like | `docs/results/REAL_SLICE.md` "Results" table and "Latency isn't like-for-like" | ✅ |
| Who said 88% vs 0%; commitments 100 vs 75; which person 80 vs 40; what changed 83 vs 100 | `docs/results/REAL_SLICE.md` "Results" "Accuracy by category" | ✅ (n = 4–8 per cell; quoted as anecdotes) |
| 43 facts + 11 commitments; 10 of 16 misses were abstentions | `docs/results/REAL_SLICE.md` "Results" Method (build) and "What this says" 2 | ✅ |
| Concentrate twice confidently named the wrong person on "who said" (the "wrong, but confident" link) | `docs/results/REAL_SLICE.md` "What this says" 3 ("twice named the wrong person") | ✅ The coordinator's brief said 3 times; the public file says twice, so the memo says twice. |
| Granola "went deeper" on who-said questions (88%) | `docs/results/REAL_SLICE.md` category table; PROBE_V2 §1 notes that `query_granola_meetings` reads summaries, then transcripts | ✅ Inferred from its answers and tool design; we don't see its internal tool calls. |
| Ignored the named system-audio speaker labels | `docs/results/REAL_SLICE.md` "Results" "What this says" 3 and 6 | ✅ |
| Second judge 97%, κ 0.93, ranking unchanged | `docs/results/REAL_SLICE.md` "Results" "Judges agree" | ✅ |
| LLM-drafted, bulk-approved with 0 edits; at least one gold looks wrong; 6 unanswerable not run | `docs/results/REAL_SLICE.md` "Results" "Gold quality", "Coverage" | ✅ |
| Concentrate memory built from the same notes via the MCP | `docs/results/REAL_SLICE.md` "Results" Method; `docs/engineering/DESIGN.md` "Real data via Granola's MCP" | ✅ |
| Pedregal: "might want to still go back and chat with that meeting" | Platformer interview (coordinator's verified excerpt, 2026-11-03) | ✅ |

## Live MCP probes

| Claim | Source | Status |
|---|---|---|
| Plus plan, 4 meetings, read-only | `docs/archive/PROBE_2026-11-02.md` header | ✅ |
| 14 questions plus repeats (16 agent calls) | `docs/archive/PROBE_V2.md` "Design" | ✅ |
| Commitment tracking strong, stable over 3 repeats | `docs/archive/PROBE_V2.md` §1, §3 | ✅ |
| Honest abstention on broad single-mic questions; explicit dates scoped | `docs/archive/PROBE_V2.md` §1.2, §3 | ✅ |
| Retrieval tools ≈0.3–0.4 s | `docs/archive/PROBE_V2.md` §2 | ✅ |
| Missed a display-name-only attendee and the second of two people sharing a first name | `docs/archive/PROBE_V2.md` §1.1 | ✅ **Don't** cite probe 1's one-of-two meeting miss as a current failure: probe 2 didn't reproduce that exact case (§1.1, first bullet). |
| Same commitment attributed differently depending on phrasing | `docs/archive/PROBE_V2.md` §1.2 | ✅ |
| `query_granola_meetings` median 8.5 s (n = 16, 1.6–17.7 s) | `docs/archive/PROBE_V2.md` §2 | ✅ A lower bound at 4 meetings; say so if asked. |
| Answers are summary-first; details need transcript calls | `docs/archive/PROBE_2026-11-02.md` §1 (the `transcript_note`) | ✅ |
| No meeting content is quoted anywhere in the write-up | `docs/archive/PROBE_V2.md` "Privacy" | ✅ Re-check the deck's slide 5 cards when they're drawn. |

## Result 2: synthetic benchmark

Report `evals/reports/20261103T002456Z.md` (test split, 120 questions, Opus 5.5, seed 42, git `aeafca5` dirty, `northwind-xl` = generator `northwind-1`). Analysis: `evals/reports/20261103T002456Z.analysis.md`. Corrections from the red-team audit, `docs/results/RED_TEAM.md`. The README "Results" §2 uses the same numbers.

| Claim | Source | Status |
|---|---|---|
| Accuracy and CI per system (95/92/90/88/81/72/71) | `evals/reports/20261103T002456Z.md` | ✅ |
| Tied (Holm p = 1.0); 10 disagreements, 6–4 | `evals/reports/20261103T002456Z.analysis.md` §1; `docs/results/RED_TEAM.md` "What survives" | ✅ |
| $/q at equal interactive prices: 0.082/0.040/0.137/0.328/0.017/0.029/0.017 | `docs/results/RED_TEAM.md` R1 table; README footnote ¹ | ✅ |
| Agent ≈0.07–0.08 with prompt caching | `docs/results/RED_TEAM.md` R1 footnote ¹ | ✅ (an estimate, not measured; labelled as such) |
| ~3.5× (≈2× vs a caching agent) | `docs/results/RED_TEAM.md` R1 "Like for like" | ✅ |
| Ingest $5; break-even ~50 questions per corpus (51; ~140 against a caching agent) | `docs/results/RED_TEAM.md` R1 ingest table and "Break-even" | ✅ The $5 is interactive Sonnet ingest with caching ($4.96, `docs/results/RED_TEAM.md` R9). |
| Mem0: 25 of 5,292 memories per question; Graphiti never given the summaries | `docs/results/RED_TEAM.md` R2 | ✅ |
| Directory decided 1/120 questions; build effect unmeasured | `docs/results/RED_TEAM.md` R3 | ✅ |
| Gold errors ("reported complete on None", q694, and others) | `docs/results/RED_TEAM.md` R5 | ✅ |
| Second judge changed none of Concentrate's 120 verdicts | `docs/results/RED_TEAM.md` R4; `evals/reports/20261103T002456Z.judgecheck.claude-sonnet-5-5.md` | ✅ |
| MDE ~7 points for the top pairs at n = 120 | `evals/reports/20261103T002456Z.analysis.md` §2 | ✅ |
| Test run once; tuning on dev only | `docs/archive/CONCENTRATE_TUNING.md`; `docs/results/RED_TEAM.md` "No test peeking" | ✅ |

## Threats to validity

| Claim | Source | Status |
|---|---|---|
| Same author built the generator and the extractor | README "Reasons to be skeptical" | ✅ |
| Only Concentrate gets the org directory; ablation not run | README "Reasons to be skeptical" | ✅ |
| Unequal tuning (concrete: query layer anchors on generator templates) | `docs/results/RED_TEAM.md` R6; `docs/archive/CONCENTRATE_TUNING.md` | ✅ |
| v1 wording templated; v2 and v2.1 not run | `docs/benchmark/FIDELITY.md`, `docs/benchmark/FIDELITY_V2.md`, `docs/benchmark/V2_EXTRAS.md` | ✅ |
| Second judge run on the final report | `evals/reports/20261103T002456Z.judgecheck.claude-sonnet-5-5.md` | ✅ (replaces the earlier "pilot only" claim) |

## Retirement

| Claim | Source | Status |
|---|---|---|
| Dev, 90 questions: Concentrate 89% at N = ∞ → 88% at N = 0 | `docs/results/RETIREMENT.md` Results; `evals/reports/retirement/20261102T224523Z.curve.md` | ✅ |
| 4 of 90 verdicts change; 3 of them not monotonic in N | `docs/results/RETIREMENT.md` "Honest read" 1 | ✅ |
| Memory ≈ 5% of raw transcript tokens (31k vs 624k) | `docs/results/RETIREMENT.md` Results | ✅ |
| b1 summaries 64% ("notes only ≈ Granola after auto-deletion") | `docs/results/RETIREMENT.md` Results; b1 = top-20 meetings' AI summary + typed notes | ✅ The ≈ is an analogy: b1 uses our synthetic summaries, not Granola's notes. |
| Per type, notes only → notes + facts at N = 0: entity 30 → 60, aggregation 0 → 50, commitments 67 → 89, latest state 73 → 100 | `docs/results/RETIREMENT.md` "Accuracy by category" (b1 and 0 columns) | ✅ n = 8–11 per type |
| "Recovers about 24 points" | 88 − 64, `docs/results/RETIREMENT.md` "Honest read" 4 | ✅ |
| Real-data retirement curve not run | `docs/results/RETIREMENT.md` (XL dev only) | ✅ |
| b0 not run for accuracy; 32% of gold line text at N = 14, 2% for entity questions | `docs/results/RETIREMENT.md` b0 contexts table | ✅ |
| A speaker's role is the one plausible loss (q245) | `docs/results/RETIREMENT.md` "Honest read" 1 | ✅ |
| No verbatim-quote category | ws-c hand-over note; benchmark category list in `docs/benchmark/DESIGN.md` | ✅ |
| Test split not run; dev only | `docs/results/RETIREMENT.md` limits ("Test was not run") | ✅ |
| v2.1 adds exact-quote and speaker-attribution questions, not run | `docs/benchmark/V2_EXTRAS.md` intro | ✅ |

## Unit economics

The memo now uses only `docs/results/RED_TEAM.md` R1's per-question figures and the break-even per corpus (above). The earlier $/user/month table was dropped: it rested on a guessed usage (40 meetings, 30 questions) and on `evals/economics/20261103T002456Z.md`, which re-uses Batch-discounted `cost_usd` (flagged to the coordinator). Re-derive it before quoting.

## Pre-meeting brief

| Claim | Source | Status |
|---|---|---|
| As-of SQL reads, no LLM, ~6 ms, no leakage | `docs/results/BRIEF.md` "How it works" | ✅ |
| 36-meeting test sample, baseline = top-k (24 chunks) + Sonnet 5.5 over earlier meetings only | `docs/results/BRIEF.md` Evaluation; `evals/reports/brief-test-20261102T223053Z.json` meta | ✅ The baseline uses Sonnet, not the Opus answer model. Say so if asked. |
| Commitments recall 91% (158/174) vs 30% (52/174) | same report, `summary.{memory,baseline}.commitments` | ✅ |
| Commitments precision 43% vs 90% | same | ✅ |
| Changes recall 58% vs 5%, 19 gold items | same, `changes` (memory tp 11/19, baseline tp 1/19) | ✅ The n is tiny; keep the "directional" wording. |
| Requests recall 78% vs 56%; stale 22% vs 38% | same, `requests` | ✅ (9 gold items) |
| Precision loss mostly from in-person owner attribution | `docs/results/BRIEF.md` "Where the memory brief is weak" | ✅ (inspected on dev) |

## Limitations, transcript deletion and next steps

| Claim | Source | Status |
|---|---|---|
| b3 isn't Granola's agent | `README.md` "Honest limits" | ✅ |
| "Facts are worth storing before a transcript is deleted; they don't replace it" (who said what can't be recovered) | retirement (88% vs 64% on synthetic dev) plus Result 1 (0% on who said for fact-only memory) | ✅ framed as my reading; deletion wasn't measured on real data |
| "Extract a fact layer before auto-deletion, and make the degraded warning specific" | recommendation, from the two rows above and the help-centre page | ✅ |
| Hybrid next step (transcript fallback, speaker labels, needs fresh questions) | `docs/results/REAL_SLICE.md` "What this says" 6 | ✅ |
| v2.1 adds exact-quote and speaker-attribution questions, not run | `docs/benchmark/V2_EXTRAS.md` intro | ✅ |
| Directory ablation (~$5 ingest + $2.60 answers) | `docs/results/RED_TEAM.md` R3 "Fix" | ✅ |
| Fix Mem0/Graphiti wiring and gold errors, re-score | `docs/results/RED_TEAM.md` R2, R5 | ✅ |
| Identity at write time targets the probe misses | `docs/archive/PROBE_V2.md` §1.1, §4.1; `docs/systems/MCP_SERVER.md` `resolve_entity` | ✅ |

## Outreach-only claims

| Claim | Source | Status |
|---|---|---|
| Fields tagged VERIFIED/ASSUMED | `docs/engineering/SCHEMA_NOTES.md` | ✅ |
| Pilot saturated at 90–100% | `README.md` status box | ✅ |
| Builds hung; traced to SDK timeout defaults | `docs/engineering/HARDENING.md` §1 (the cause is *inferred*, with no stack trace) | ✅ Keep "traced to", not "proved". |
| The emails say "Chris" (Pedregal) | Platformer interview, verified | ✅ |
| "tested my own system against Granola's and reported it lost" | `docs/results/REAL_SLICE.md` "Results"; this memo | ✅ |
| "red team cut the cost claim from ~6× to ~3.5×" | `docs/results/RED_TEAM.md` R1 (the "as reported" vs "full price" columns: 0.137 ÷ 0.022 ≈ 6.2; ÷ 0.0395 ≈ 3.5) | ✅ |
