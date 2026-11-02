# Findings checklist

This file pairs every claim in [MEMO.md](MEMO.md) (and the deck and emails that reuse it) with the file that backs it. Status:
- ✅ backed by a committed file on this branch;
- 🔀 backed by a file on a workstream branch that **isn't merged yet** (re-check the number after the merge);
- ⏳ placeholder `{{TBD}}`, filled from tonight's run;
- ⚠️ needs a manual check before sending.

**Rule before sending:** no ⏳ left, every 🔀 re-checked on the merged branch, every ⚠️ resolved or the claim removed.

## The question

| Claim | Source | Status |
|---|---|---|
| CEO quote "apple juice from concentrate", July 2026, Platformer link | `README.md` on `main` and on the `worktree-agent-*` branches cites `https://www.platformer.news/granola-chris-pedregal-interview/` | ⚠️ Read the article and check the exact wording, the date and that it's Chris Pedregal speaking. The current branch's README doesn't contain the link (ws-s is rewriting README). |
| "Granola has said it's weighing deleting old transcripts" (founder email, retirement section) | `README.md` §What's here item 5; `docs/SCOPE.md` §3.2 | ⚠️ The repo asserts this but gives no primary source. Find the quote/link, or soften to "I understand Granola is weighing…". |

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
| Read-only MCP source | `CLAUDE.md`; `docs/DESIGN.md` "Real data via Granola's MCP"; ws-b's `ReadOnlyCaller` test | ✅ / 🔀 (`claude/ws-b-real-slice-9332a8`) |
| Deterministic: same seed, identical bytes | `docs/BENCHMARK.md`; byte-equality fixture test | ✅ |
| Timeouts, stall watchdog, resumable builds | `docs/HARDENING.md` §2–3 | ✅ |
| "~240 offline tests" | `docs/HARDENING.md` §4 (238 at that point) | ⚠️ Re-count with `uv run pytest --co -q` after the merges. |

## Live MCP probes

| Claim | Source | Status |
|---|---|---|
| Plus plan, 4 meetings, read-only | `docs/PROBE_2026-11-02.md` header | ✅ |
| 14 questions plus repeats (16 agent calls) | `docs/PROBE_V2.md` "Design" | 🔀 `claude/ws-b-real-slice-9332a8` |
| Commitment tracking strong, stable over 3 repeats | `docs/PROBE_V2.md` §1, §3 | 🔀 |
| Honest abstention on broad single-mic questions; explicit dates scoped | `docs/PROBE_V2.md` §1.2, §3 | 🔀 |
| Retrieval tools ≈0.3–0.4 s | `docs/PROBE_V2.md` §2 | 🔀 |
| Missed a display-name-only attendee and the second of two people sharing a first name | `docs/PROBE_V2.md` §1.1 | 🔀 **Don't** cite probe 1's one-of-two meeting miss as a current failure: probe 2 didn't reproduce that exact case (§1.1, first bullet). |
| Same commitment attributed differently depending on phrasing | `docs/PROBE_V2.md` §1.2 | 🔀 |
| `query_granola_meetings` median 8.5 s (n = 16, 1.6–17.7 s) | `docs/PROBE_V2.md` §2 | 🔀 A lower bound at 4 meetings; say so if asked. |
| Answers are summary-first; details need transcript calls | `docs/PROBE_2026-11-02.md` §1 (the `transcript_note`) | ✅ |
| No meeting content is quoted anywhere in the write-up | `docs/PROBE_V2.md` "Privacy" | ✅ Re-check the deck's slide 5 cards when they're drawn. |

## Headline results

| Claim | Source | Status |
|---|---|---|
| Accuracy, CI, $/q, p50 latency per system | `evals/reports/<XL test stamp>.json` / `.md` | ⏳ |
| Ingest $ per write-time system | the same report's `meta.ingest[system].cost_usd` | ⏳ |
| Holm-adjusted p vs Concentrate; category gaps | `evals/reports/<stamp>.analysis.json` / `.analysis.md` (`concentrate analyze`) | ⏳ |
| Failure types per system | `evals/reports/<stamp>.failures.jsonl` | ⏳ |
| MDE at n = 206 ("pilot suggests ~9") | `docs/DESIGN.md` "Report v2" pilot finding; final from `analysis.json` | ✅ pilot / ⏳ final |
| Test run once, dev-only tuning | `docs/BENCHMARK.md` categories note; `CLAUDE.md` | ⚠️ Confirm in the final report's meta (split, SHA, date) that test ran exactly once. |
| Which corpus version (v1 or v2) the headline uses | report `meta.fixtures` | ⏳ v1 and v2 numbers must not be mixed (`docs/FIDELITY_V2.md` §4). |

## Retirement

| Claim | Source | Status |
|---|---|---|
| Dev, 90 questions: Concentrate 89% at N = ∞ → 88% at N = 0 | `docs/RETIREMENT.md` Results; `evals/reports/retirement/20261102T224523Z.curve.md` | 🔀 `claude/ws-c-retirement-a0ae2e` |
| 4 of 90 verdicts change; 3 of them not monotonic in N | `docs/RETIREMENT.md` "Honest read" 1 | 🔀 |
| Memory ≈ 5% of raw transcript tokens (31k vs 624k) | `docs/RETIREMENT.md` Results | 🔀 |
| b1 summaries 64% | `docs/RETIREMENT.md` Results | 🔀 |
| b0 not run for accuracy; 32% of gold line text at N = 14, 2% for entity questions | `docs/RETIREMENT.md` b0 contexts table | 🔀 |
| A speaker's role is the one plausible loss (q245) | `docs/RETIREMENT.md` "Honest read" 1 | 🔀 |
| No verbatim-quote category | ws-c hand-over note; benchmark category list in `docs/BENCHMARK.md` | ✅ |
| Test-split curve | — | ⏳ (may not be run; if not, say "dev only") |

## Unit economics

| Claim | Source | Status |
|---|---|---|
| Ingest $/meeting, $/q, $/user/month, break-even | `concentrate economics` (`claude/ws-f-economics-67a6f4`, code only, no doc yet) + `analysis.json` break-even | ⏳ 🔀 |
| Concentrate ≈9k input tokens/question (dev) | `docs/RETIREMENT.md` "Input tok/q" for concentrate@∞ (8,983) | 🔀 |

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
| Real-data slice status | `docs/REAL_SLICE.md` (results pending author verification) | 🔀 ⏳ |
| Primary judge = answer model | `docs/BENCHMARK.md` Metrics | ✅ |
| Next step 1 (write-time identity) targets the probe misses | `docs/PROBE_V2.md` §4.1; `docs/MCP_SERVER.md` `resolve_entity` | 🔀 / ✅ |
| Next step 2 (`recurring_event_id`, attribution lever) | `docs/BRIEF.md` "Decisions" and "Open questions" | ✅ |
| Next step 3 (verbatim-quote category, line-ID pointers) | `docs/RETIREMENT.md` caveat; `docs/PHASE4_DESIGN.md` compression tiers | 🔀 / ✅ |

## Outreach-only claims

| Claim | Source | Status |
|---|---|---|
| Fields tagged VERIFIED/ASSUMED | `docs/SCHEMA_NOTES.md` | ✅ |
| Pilot saturated at 90–100% | `README.md` status box | ✅ |
| Builds hung; traced to SDK timeout defaults | `docs/HARDENING.md` §1 (the cause is *inferred*, with no stack trace) | ✅ Keep "traced to", not "proved". |
| The emails say "Chris" | the Platformer URL slug | ⚠️ Confirm the name before sending. |
