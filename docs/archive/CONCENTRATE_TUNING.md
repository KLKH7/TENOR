# Concentrate tuning on XL dev (workstream L, 2026-11-02)

Goal: make Concentrate as strong as is honestly possible before the final test comparison.
**Only the 90 dev questions were looked at or used for any decision.** No test question, answer or
score was opened. Every run below is `concentrate tune-c` (which refuses `--split test`) on the
same harness, answer model (`claude-opus-5-5`, `answer.v1`), judge (`judge.v1`) and seed (42) as
every other system.

Store: `data/cache/concentrate/78a4767dabf454ae/6b9c4de7`. Built with `enhance-v1`, `extract.v2`
(ea88800a), `entail.v2` (e0b6e663) and the robust date checker: 184 meetings, $4.96, 318 fact rows
(191 inserted, 127 supersessions), 117 commitments, 73 commitment events, 13 gate rejections.

## Results (dev, 90 questions)

| | Code | Accuracy [95% CI] | Exact match | Abstain P / R | Cite P / R | Input tok/q | $/q | Report |
|---|---|---|---|---|---|---|---|---|
| **Before** | `03e6fef` | 89% [82%, 96%] (80/90) | 75% | 73% / 100% | 67% / 86% | 8,983 | 0.022 | `evals/reports/20261102T222559Z` |
| **After** | `b113208` | **90% [83%, 96%]** (81/90) | 77% | 89% / 100% | 66% / 88% | 9,175 | 0.023 | `evals/reports/20261102T223309Z` |

Per-question changes, before → after:
- q735: partially correct → correct.
- q812, q813: incorrect → partially correct.
- No question got worse.

Honest reading: **+1 correct answer is inside the noise** (the CIs overlap almost completely,
n = 90). The fixes are real but small in score terms, because the baseline was already strong
outside list-style aggregation. The two list questions moved from "there are no commitments" to
lists with every reassignment (q812: all 11 of the gold's reassignments, with 2 original owners
reported as unresolved). They are still graded partially correct, for reasons covered under
*Open problems*.

Spend for this workstream: baseline $2.21 + after-fix and 2 ablations $4.85 + gate-fix
measurement $0.18 + $0.001 = **$7.24** (budget $8). No rebuild.

## Method: error analysis before changing anything

Two passes, cheapest first.

**1. A deterministic coverage diagnostic ($0).** For each dev question, the gold transcript
lines are checked at each stage:
- were they **stored**, i.e. does any fact, commitment or event cite them?
- were they **rejected** by the gate?
- were they **in the context** that `build_context` gives the answer model?

This splits "the memory doesn't have it" from "the memory has it but the question didn't
reach it". On the baseline store: 282 gold lines, 241 stored (85%), 197 in context (70%).
The biggest hole: two list questions ("which commitments were reassigned?", "...never followed
up on?") got an almost empty context. 46 stored gold lines never reached the model, because
the question names no entity and shares few words with any task.

**2. Answer-level analysis of every non-correct baseline answer,** sorted into the buckets in
the brief.

The baseline answered 80 of 90 correctly. The 10 that weren't (report
`evals/reports/20261102T222559Z`, run on code `03e6fef`; the report header says `b113208 (dirty)`
because the SHA is stamped at the end of the run, after the query edits had started, but the
process had already imported the old `query.py`):

| Bucket | Questions | What happened |
|---|---|---|
| **Lookup miss** (stored, not retrieved) | q812, q813 | "Which commitments were reassigned / never followed up on?": 24 and 22 stored gold lines, **0 in context**. The answer model said no commitments exist. |
| Lookup miss (cap) | q797 | "List everything Tomás committed to": the 15-commitment cap, in insertion order, cut some of them (partially correct). |
| **Entity resolution** (wrong Sam) | q794 | "Sam the backend engineer" resolved to Sam Patel, Sam Okafor *and* Sam Rivera. One of Sam Patel's commitments is stored with an unresolved owner (`?sam_p\|kofi`), so the list was incomplete (partially correct). |
| **Extraction miss** | q248 | "Who volunteered to own Lighthouse?": the line "I can own it." (Kofi) was never stored as `lighthouse.owner`. The next line (the October 30 estimate) was stored, so the model saw Kofi but not the volunteering. |
| **Commitment lifecycle** | q735 | The slip was stored, but without its new due date ("next week" was not resolved to 2026-10-23), so the answer said "the following week" (partially correct). |
| **Answer-model reasoning** | q830 | Gave the four correct SCIM customers, then added a hedge listing bulk-import customers (partially correct). |
| Gold / judge strictness (not a memory error) | q174, q550 | The transcript says "We're ready to sign for the full rollout"; the gold is "signed". Concentrate answered faithfully. **Flagged to the benchmark owner, not tuned toward.** |
| Gold / judge strictness | q007 | Gold "SSO (open)". The answer said SSO is non-negotiable for Beacon Freight but didn't say the request is still open. |
| Gate false reject | none in failures | Of 13 rejections, 2 touch dev gold lines (q692, q792), and both questions were still answered correctly from other evidence. See the gate section below. |
| Temporal / as-of | none | 17/18 temporal and 11/11 latest-state correct. The one temporal miss is q550 (gold issue above). |

Biggest bucket: **lookup misses (3 questions, ~50 stored gold lines unreachable)**, all
query-side and free to fix. Then entity resolution (1), extraction (1), lifecycle (1),
answer reasoning (1), and 3 questions that are arguably benchmark-label issues.

## What changed and why (query side only, no rebuild)

All in `memory/query.py`, ranked by expected dev impact per unit of risk:

1. **List-wide commitment lookups.**
   - Before: "Which commitments were reassigned / never followed up on?" matched no entity and no task words, so the context had no commitments at all.
   - Now: these intents list every commitment whose replayed status matches as of the question date: a `reassigned` event, or `no_follow_up`. The cap for list questions is 40 instead of 15.
   - Effect: gold-in-context for those two questions went from 0 to 24 and from 0 to 22.
2. **Role-qualified names.**
   - Before: "Sam the backend engineer" resolved to all three Sams, so the commitments section mixed Sam Okafor's and Sam Rivera's work in with Sam Patel's. This is the brief's "wrong Sam" bucket.
   - Now: when one namesake's directory role appears in the question, the parser keeps only that one.
3. **Named-meeting anchoring.**
   - Questions often say *in the "Eng standup" meeting on Monday, August 31*. The quoted title, narrowed by the date, now resolves to that meeting's note ID.
   - That meeting's commitments rank first, its facts (including restatements, through `evidence`) get their own section, and its digest is always included.
   - It needs a quoted title: a bare date ("as of Friday, October 9") is the as-of date, not a meeting.
4. **Commitment ranking before the cap.** Commitments are now sorted by named meeting, then task-word overlap, before the 15-row cap. Previously the cap kept the first 15 in insertion order.

Measured with the coverage diagnostic, gold-in-context rose from **197 to 243 of 282** (70% to
86%). Mean context stayed ~4k tokens.

## Ablations

Each ablation removes one component **at answer time**. They all use the same cached store, so
the only thing that differs is the context the answer model sees. They are run against the
*after* code, in one report (`20261102T223309Z`). "Gold in context" is the free deterministic
diagnostic: the share of the 282 dev gold lines that appear in the context.

| Variant | What it removes | Accuracy | Δ vs full | Gold in context | Questions that changed |
|---|---|---|---|---|---|
| **full** | none | **90%** | n/a | 86% | n/a |
| no_entities | query-time entity resolution (structured lookup by entity, commitments by owner); search only | 89% | **−1 pt** (−1 question) | 80% | q735 correct → partial; q794 partial → incorrect |
| no_supersession | validity intervals and CURRENT/superseded/status labels; facts become an append-only log with the date each was stated | 84% | **−6 pts** (−5 questions) | 86% | q629, q692, q735, q808 → partial; q805 → incorrect |
| no_digests | meeting digests | *not run* (skipped by decision) | n/a | 84% | n/a |
| gate off | the grounding gate | *not run*; analysed instead | ≈ 0 on dev | n/a | see below |
| enhancer → stub | speaker/mention resolution at ingest | *not measured* (needs a ~$5 rebuild; skipped by decision) | n/a | n/a | n/a |

What each one tells us:
- **Supersession is the component that earns its keep on this benchmark.** Without validity
  state the context has the same lines (gold in context is unchanged at 86%), but the model has
  to work out "what's current" or "is this still open" from a pile of dated statements, and it
  gets it wrong more often:
  - latest-state q629 (Tidewater's SCIM request);
  - "open requests as of today", q805 and q808;
  - the commitment-status questions q692 and q735.

  Aggregation drops 50% → 25%, latest-state 100% → 91%. This is exactly the
  structured-time-model claim.
- **Query-time entity linking barely moves accuracy (−1 question), although it moves coverage
  (−6 pts).** Northwind questions name entities in plain words that BM25 and embeddings already
  match, so hybrid search recovers most of what the structured lookup gives. Where it matters is
  ambiguity: without it, "Sam the backend engineer" falls back to search and the answer becomes
  incorrect (q794). Expect this gap to grow on real transcripts, where names are misspelt and
  shared.
- **The gate is invisible to QA accuracy, by design.** It rejected 13 of ~650 extracted items.
  Seven are commitment updates rejected over unresolved speakers; 5 of those are false rejects,
  measured below. Adding the false rejects back changes 0 dev answers. Its value is
  *precision*: what is in the memory was said. `gate_eval` measures that, not this benchmark.
  An honest "gate off" QA ablation would be ≈ 0 here and would say nothing about safety.
- **Digests** cost 2 pts of gold-in-context when removed. The QA run was skipped to save budget.
- **The Enhance stage is unmeasured.** The previous XL store (enhance-v1 + extract.v1) is not a
  stub control, so we make no claim.

## Gate false rejects on commitment updates (proposed fix, not yet in a build)

`entail.v2` rejects 7 of the store's 13 rejected items as commitment **updates**, because the
speaker is unresolved (`Them (? Sam Patel|Kofi Mensah|...)`) and the claim names the owner. But an
update's owner is not a new claim: it is inherited from the commitment it was matched to (by id
or task), which already passed the gate. The fix is to drop the owner from update claims
("The commitment 'Finish the invoice export' is slipped (new due 2026-09-16)").

Measured without a rebuild:
- Re-checking the 7 rejected updates with the owner-free claim (Haiku 5.5, $0.001): **5 flip to
  kept**. The 2 still rejected look like genuine rejects (the line says the skills matrix is
  *Nadia's*, not Tomás's; "next week" is ambiguous).
- The 5 flipped events were inserted into a *copy* of the store, and the affected dev questions
  were answered ($0.18):

  | Question | Baseline store | Store + fix |
  |---|---|---|
  | q692 (Sam: invoice export slip) | correct | correct |
  | q792 (Theo: peer feedback list) | correct | correct |
  | q794 (everything Sam committed to) | partially correct | partially correct |

**Dev impact: Δ = 0 questions.** All three were already answered the same way without the 5
events: the model recovered the slip and the completion from other cited lines. The fix makes
the memory *more complete and more correct* (5 real lifecycle events, 2 slips and 3 completions, that were silently dropped), and it would matter more once transcripts are retired (C). But
this benchmark's dev split can't see it. So we recommend it for the next rebuild for correctness,
not for a dev score.

Rollout: the change belongs in `verify.claim_text`, released with an `entail.v3` prompt version
so the store's config hash changes and the rebuild is explicit. It costs a full rebuild (~$5).
It is **not** wired in here, so the code and the cached store stay consistent.

## Open problems found (not fixed)

- **The commitment lifecycle has a precision problem (q813).** The store has 48 commitments with
  no recorded follow-up; the gold for "never followed up on" lists 18. Two causes:
  - the extractor misses some done/slip updates, so the commitment looks abandoned;
  - it keeps soft asks that the benchmark doesn't count as commitments ("check with the team
    and get back to Beacon Freight").

  The answer listed 40 (the cap) and was graded partially correct. Fixing this is ingest-side
  (update recall in `extract`, plus the gate fix below).
- **Unresolved owners (q812, q794).** 2 of the 11 reassigned commitments have owners stored as
  `?sam_p|kofi` / `?megan|sam_o|alex_k|ruth`. Keeping candidates instead of guessing is the
  honest extract.v2 behaviour, but it leaves "who" open, so these answers are partially correct.

- **Neighbouring lines (q248).** Facts carry only the lines they cite. Showing ±1 line around
  each cited line would have surfaced "I can own it." next to Kofi's estimate. It is cheap,
  but it adds ~30–50% context tokens. It is not tried here because there was no budget for another
  dev run. Next query-side candidate.
- **Commitment slips without a new due date (q735).** The extractor leaves `new_due` empty when
  the slip is relative ("next week"). Resolving relative slip dates against the meeting date is
  an `extract.v3` / rebuild change.
- **Predicate drift is mild but real.** Deal status lives under `plan`, company metrics under an
  invented subject `company_metrics` (questions say "Northwind"), and the CSV usage export is stored
  as both `request:csv_export` and `request:csv_usage_export`. Hybrid search
  covered these on dev (single-hop and latest-state questions were 91% and 100% correct), so no
  synonym merge was added: it would be tuning without a dev failure to justify it.
- **Wrong-subject gate check via mention links.** Not implemented: it is an ingest-side change
  (rebuild), and no dev failure was caused by a wrong subject.
- **Possible gold issues (dev):** q174 and q550 ("ready to sign" vs "signed"), q007 ("open"
  status). Reported to the coordinator, not tuned toward.
