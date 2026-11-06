# Pre-meeting brief (workstream H)

A memory you build ahead of time can do something a search box can't: tell you what you need
to know *before you ask*. The brief is that product feature. Given an upcoming meeting (title,
start, attendees, customer), it lists:

1. **Open commitments** of every attendee: status (`open`, `slipped`, `reassigned`,
   `no_follow_up`), current due date, original due date, and whether it's overdue;
2. **What changed since this group last met**: new or superseded facts about the attendees,
   the customer, or anything the series discussed before;
3. **Open customer requests** (customer meetings only);
4. **Last time**: the previous meeting in the series, with its digest.

Every item cites transcript line IDs (`[not_x:0042]`).

```bash
uv run concentrate brief not_McGrj4gw9NNyT2
```

```bash
uv run concentrate brief-eval --split test --limit 36 --baseline
```

Code: [`memory/brief.py`](../../../packages/tenor/memory/brief.py) (the brief, no LLM),
[`evals/brief.py`](../../../packages/tenor/evals/brief.py) (gold, scoring, baseline),
[`prompts/brief_baseline.v1.md`](../../../prompts/brief_baseline.v1.md), tests in
[`tests/test_brief.py`](../../../tests/test_brief.py).

## How it works

The brief is a set of **as-of queries** over the bitemporal store. Every row in the store has a
*transaction time* (`recorded_at`: when the memory learnt it, i.e. the start of the meeting it
came from). A brief for a meeting starting at time *t* only reads rows with `recorded_at < t`,
and replays commitment events recorded before *t*. So a brief for a past meeting is exactly the
brief you would have got that morning, with no leakage from later meetings. This is also what
makes it evaluable: we can generate a brief "as of" every meeting in the corpus and grade it.

- **Commitments**: replay each commitment's events (`slipped`, `reassigned`, `done`,
  `dropped`) up to *t*; drop closed ones; keep those whose current owner attends.
- **Changes**: the "group" is the meeting series (same title). The window is
  (previous series meeting, *t*): things said *at* the previous meeting are excluded, since
  the group heard them. Relevant subjects = attendees ∪ customer ∪ subjects of facts from earlier
  series meetings. Facts that rest on the same lines as a commitment event ("the export is done")
  are left out because they already show as commitments (decided on dev, see below).
- **Requests**: request/complaint facts about the customer, valid at *t* as known at *t*.

No LLM call is needed: it's a few SQL reads, ~6 ms per brief on the XL store (measured over the 130 targets). An optional prose pass is not
built (see Open questions).

## Evaluation

**Ground truth** comes from the generator's world (`world.json`), not from any model. For each
target meeting (130 XL meetings whose series met before; 37 dev / 93 test by a seeded hash of
the note ID) we compute the ideal brief as of its start, using the same definitions as above but
on the world's true commitments (with outcomes and the meetings where they were made and
resolved) and true facts. Gold transcript lines are found by locating each item's protected
spoken span in the fixture transcript; these spans survive ASR noise verbatim, so the gold lines
don't depend on re-rendering (the XL fixtures were built by an older generator version, whose
render no longer reproduces byte-for-byte).

**Matching a shown item to a gold item:** it cites a line within ±2 of a gold line in the
same meeting, or it comes from a gold meeting and its words overlap the gold text (content-word
Jaccard ≥ 0.2). Commitments with a resolvable owner must also agree on the owner (the
`commitments_any_owner` row relaxes this). Greedy one-to-one, best match first.

**Metrics (per section, micro-averaged over meetings):**
- precision = matched live items / items shown;
- recall = matched live items / gold live items;
- **staleness** = items shown that match something *already resolved* (a commitment reported
  done, a request closed) / items shown;
- citation precision = true positives whose cited lines hit a gold line. For memory items it's
  close to tautological, since most matches are made through the lines; it's informative for the
  baseline.

**Baseline** ("agent + top-k, no memory"): the same hybrid retrieval as the B0 baseline (BM25 +
MiniLM embeddings, RRF) over 15-utterance chunks and AI summaries, **restricted to meetings that
started before the target**, top 24 chunks, plus the previous series meeting's AI summary (a
calendar-aware agent would fetch it). The query names the meeting, attendees and customer.
Claude Sonnet 5.5 (effort low) writes the same four sections as JSON (`brief_baseline.v1`).

## Results (Northwind-XL, 2026-11-02)

Memory store: the rebuilt XL store (`data/cache/concentrate/78a4767dabf454ae/6b9c4de7`, 184
meetings, `extract`/`entail` as configured in that build). Reports: `evals/reports/brief-*.json`
(git SHA 42f8787 + uncommitted workstream-H code; seed 42).

**Test split, 36-meeting sample (both systems on the same meetings):**

| Section | Memory P / R | Memory stale | Top-k LLM P / R | Top-k stale |
|---|---|---|---|---|
| Commitments | 43% / **91%** | 3% | **90%** / 30% | 3% |
| Commitments (any owner) | 45% / 95% | 4% | 91% / 30% | 3% |
| Changes since last met | **35% / 58%** | 0% | 3% / 5% | 0% |
| Customer requests | **78% / 78%** | 22% | 62% / 56% | 38% |
| Last meeting correct | 100% | | 100% | |

Baseline citation precision: commitments 67%, changes 0%. Baseline spend: $0.80 for 36 briefs
(+ $0.26 for 12 dev briefs); memory briefs cost $0 at read time.

**Memory only, full test split (93 meetings):** commitments P 50% / R 91% (stale 3%), changes
P 45% / R 68%, requests P 92% / R 92% (stale 8%), last meeting 100%. The older XL store
(`538d0b7f`) scores similarly on commitments (P 50% / R 94%), better on requests (100/100),
worse on changes (P 20%).

### What this says
- **Recall is what precomputed memory buys you.** Open commitments are scattered across weeks
  of earlier meetings. Top-k retrieval finds the few that rank highly, and the LLM reports those
  accurately (90% precision) but misses 70% of them. The memory has every commitment and its
  event history, so it finds 91%.
- **"What changed since we last met" is nearly impossible without time-indexed memory.** The
  baseline mostly restates values the group heard *at* its last meeting (outside the window)
  or picks up values from the wrong period: 3% precision. The memory query is a range scan on
  transaction time.
- **Staleness is low for both on commitments (3%)**, but on requests the baseline shows more
  already-closed requests (38% vs 22%): it sees the request being made but not, in its top-k,
  the later meeting where it was closed.

### Where the memory brief is weak (honest list)
- **Commitment precision is ~45–50%.** On dev, ~90% of false positives are 10 distinct items
  attributed to the note owner (Priya). She attends every meeting, so each wrong or
  untracked item repeats in every brief. Two causes:
  - *Attribution:* in-person 1:1s put everyone on one microphone, so "I'll take the skills
    matrix" is credited to the note owner instead of the report. This is the known channel-
    attribution limit.
  - *Untracked:* the owner's "I'll take that back to the team" promises in customer calls are
    plausible commitments that the world model doesn't track. So this precision is partly a
    gold-coverage artefact: it's a lower bound.
- **Changes recall is 58–68%.** The misses on dev (inspected on the older store) are mostly company metrics (ARR, signups,
  runway) stated at all-hands and reviewed in the metrics series. The memory stores them under
  different subject IDs than the series' earlier facts, so they fall outside the relevant-subject
  set.
- **Request staleness is 22% on the new store:** some closures ("SSO shipped") weren't extracted
  as ending the request.

## Decisions and trade-offs
- **Series = same title.** Simple, and correct for this corpus (recurring 1:1s, customer calls,
  standups). Real calendars have a `recurring_event_id`, which would be better: the MCP
  `list_meeting_series` tool suggests Granola has it.
- **One change from dev tuning:** dedupe changes against commitment lines. This came from
  inspecting dev false positives ("X is done" stored as a status fact). Nothing else was tuned,
  and the test numbers were run once.
- **Baseline is given the previous series meeting's summary** to be fair to it. Without this,
  "last meeting" would be a free win for memory.
- **Sampling:** the baseline runs on an evenly spaced 36-meeting test sample, to stay inside
  the $3 budget. The memory row for the same 36 is shown so the comparison is like for like.

## Open questions
- Should "changes" also include the **latest value of key metrics** even if unchanged? A
  product person might want "ARR is $348k" in every metrics-review brief.
- Owner attribution in in-person meetings is the biggest precision lever. Is it worth a
  dedicated pass, e.g. first-person task phrasing plus the 1:1 counterpart as the prior owner?
- An LLM prose pass over the structured brief (citations preserved) is easy to add behind a
  flag. It isn't built because it adds cost without changing what's correct.
