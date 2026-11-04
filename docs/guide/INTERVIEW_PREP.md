# Interview prep: 25 questions a Granola engineer might ask

Each answer is a **sketch**, not a script: say it in your own words. Every sketch points at evidence
in the repo, so you can open the file if they ask "show me". The rule behind all of them:
**claim less than the evidence supports, and name the weakness before they do.**

Status to have in your head (2026-11-02):
- **Pilot:** done, 10 dev questions, saturated: b2 100%, b0 90%, b3 90%, b1 70%. No significant gaps.
- **XL:** the final XL test comparison is **pending**.
- **Brief:** done (XL test sample of 36).
- **Real-data slice and retirement curve:** on separate branches.
- **Spend:** ~$65 of $200 API credit (SCOPE.md).
- **Tests:** 270 pass with no API keys.

---

## A. The big picture

### 1. "In two minutes, what is this?"
An open benchmark for agent memory over meeting notes, plus a comparison of seven ways to answer
questions about months of meetings. They range from "search transcripts when asked" (what Granola's
MCP agent does) to "build a memory ahead of time" (Mem0, Graphiti, and my own pipeline, Concentrate).
Every system gets the same answer model, prompt and judge. Only the context differs, and I measure
accuracy, cost and latency.
- *Evidence:* [README](../../README.md), [TOUR.md](TOUR.md), [`docs/benchmark/DESIGN.md`](../benchmark/DESIGN.md).

### 2. "Why meetings? Aren't there memory benchmarks already?"
LongMemEval, LoCoMo, BEAM and RealMem are chat logs. In my survey I found none on meeting
transcripts. Meetings break chat assumptions:
- the speaker is often unknown (one "Them" channel);
- the same person appears as a display name, an email handle or a misheard name;
- facts change over weeks;
- commitments are made in one meeting and resolved or silently dropped in another.

The question categories map to those failure modes: `entity_disambiguation`, `temporal`,
`commitment_status`, `latest_state`.
- *Evidence:* [`docs/benchmark/DESIGN.md`](../benchmark/DESIGN.md) "Question categories".

### 3. "What did you find?"
Be precise about what's measured:
1. **The live probe of Granola's MCP** (read-only, a handful of my own meetings):
   - a cross-meeting identity miss (a teammate known only by email handle in one meeting);
   - "Unidentified speaker" owning commitments in an in-person meeting;
   - summary-first answers that need extra round-trips for exact details.

   These are observations, not error rates.
2. **The pilot saturated.** On 10 questions everything scored 70–100%, and *no* gap was significant
   after Holm correction. That's why I built the 184-meeting XL set.
3. **The pre-meeting brief:** memory found 91% of open commitments vs 30% for top-k + Sonnet, but
   with lower precision (43% vs 90%).
4. The headline XL numbers aren't in yet, and I committed in the README to publishing them good or bad.
- *Evidence:* [`docs/archive/PROBE_2026-11-02.md`](../archive/PROBE_2026-11-02.md), `evals/reports/20261102T100403Z.analysis.md`, [`docs/results/BRIEF.md`](../results/BRIEF.md).

---

## B. The hard ones

### 4. "Your agent isn't our agent."
Correct. b3 re-implements the *shape* of Granola's MCP agent:
- the same four tools, with the same limits (title/summary search; transcript search returning
  240-char passages without speakers; chunked transcript reads);
- a 12-turn budget.

It can't be Granola's agent, because the real one reads only the connected user's account, so it
can't run on synthetic Northwind. Two things narrow the gap:
1. the live probe, which ran the *real* agent on real meetings;
2. the real-data slice (branch `ws-b-real-slice`), where I hand-write questions about my own
   meetings and score Granola's real `query_granola_meetings` against Concentrate on the same data.

Its probe measured `query_granola_meetings` at a median 8.5 s.

One deliberate deviation I should mention: our transcript chunks carry line IDs so every system can
cite at line level. Granola's passages carry timestamps.
- *Evidence:* `AgentTools` [`agents/baselines.py:419`](../../packages/concentrate/agents/baselines.py#L419), DESIGN.md "Phase 3".

### 5. "Isn't synthetic data meaningless?"
It's the project's biggest weakness, and I lead with it. But "meaningless" is too strong, for three reasons:
1. **It's the only way to have verified answers at scale.** The world is generated first, so every
   one of 296 questions has a known answer and gold transcript lines. Hand-labelling 228k words would
   be slower and less reliable.
2. **It's calibrated on real data.** The format matches the API exactly (same Pydantic models,
   `tests/test_contract.py`). Speaker naming (82% of remote "Them" lines named), utterance lengths
   and in-person blocks were measured on my real meetings.
3. **I measured how fake it is, and published that.**
   - v1: 78% of long passages were exact copies of 69 filler sentences, which made retrieval too easy.
   - v2: exact duplicates down to 4.9%.
   - But 54% of long passages still share their first six words, against 0.6% in real meetings.

So it can rank systems on *specific skills* (which Sam, as-of a date), but absolute accuracy on real
meetings will differ. The real-data slice is the check.
- *Evidence:* [`docs/benchmark/FIDELITY.md`](../benchmark/FIDELITY.md), [`docs/benchmark/FIDELITY_V2.md`](../benchmark/FIDELITY_V2.md), README "Honest limits".

### 6. "Why wouldn't we just do this ourselves?"
You could, and probably better: you have audio, millions of meetings, and you've publicly described
prototyping enhanced transcripts. I'm not claiming new techniques. What an outsider adds:
- an **independent, reproducible measurement**, including of your own approach;
- a **public meeting benchmark** others can run;
- **concrete probe findings** on your live agent;
- numbers on a decision you said you're weighing: **transcript retirement**.

And it's evidence of how I'd work on your team: honest evals, cost discipline, and fidelity to
your real API.
- *Evidence:* DESIGN.md "Framing: benchmark first".

### 7. "What are the weaknesses?"
List them crisply:
1. **Synthetic data**, with template signatures still visible (Q5).
2. **b3 isn't Granola's agent** (Q4).
3. **Same-model judge.** Opus answers and judges, which is mitigated by a Sonnet re-judge
   (κ 0.89 on 40 answers) and a blind hand-check export.
4. **Small samples so far.** The pilot couldn't separate anything; XL test (206 questions) can detect
   gaps of about 9 points at the pilot's disagreement rate (DESIGN.md "Report v2").
5. **In-person speaker attribution is bad.** 21% on base in-person lines, *worse* than "always the
   owner" (64%), and 0% coverage on diarized lines. Remote unnamed lines reach 57% overall
   (91% on the lines it's confident about).
6. **Line IDs are positional,** so old reports only reproduce against fixtures at their git SHA.
7. **Gate and failure taxonomy are string-based,** so `stale_value` and `wrong_entity` counts are lower bounds.
- *Evidence:* `python -m concentrate.enhance.evaluate data/fixtures/northwind`, [`docs/results/ANALYSIS.md`](../results/ANALYSIS.md), [`docs/engineering/RESULTS_EXPLORER.md`](../engineering/RESULTS_EXPLORER.md).

### 8. "A lot of this was written by AI agents. How much of it do you understand?"
Be direct. I set the scope, the non-negotiables (CLAUDE.md: fidelity, source boundary, honest evals,
budget gate) and the decisions in DESIGN.md. Agents wrote much of the code in parallel workstreams.
I then made myself own it:
- this guide;
- the exercises (I can run any part and predict its output);
- reading the code paths in TOUR.md.

Pick any file and I'll walk through it. Then *do* it: open
[`memory/store.py:246`](../../packages/concentrate/memory/store.py#L246) and explain restate/supersede/contradict,
or [`sources/ratelimit.py:69`](../../packages/concentrate/sources/ratelimit.py#L69) and explain why one rate limiter wasn't enough.

---

## C. Technical "why did you…"

### 9. "Why both a token bucket and a sliding window?"
The docs say "burst 25 per 5 s" and "5 req/s sustained". A bucket of 25 refilling at 5/s allows ~50
requests in the first 5 s (25 at once, plus 25 refilled), which the window rejects. The window is the
binding constraint. The test asserts zero 429s against a fake server enforcing its own limit.
- *Evidence:* DESIGN.md "Rate limiting", [CONCEPTS §1](CONCEPTS.md#1-getting-data-from-granola).

### 10. "Why is the sync watermark the server's `updated_at`, not your clock?"
Clock skew. If my clock runs ahead, "since my last run" skips notes. I use the server's max observed
`updated_at`, re-scan a 10-minute overlap (the `updated_after` boundary semantics are undocumented),
skip unchanged notes cheaply, and keep a pending queue for 404s. Known risk: a note whose processing
outlasts the overlap without bumping `updated_at`; `sync --full` is the backstop. (Webhooks exist, but
their payload has no content, so they would only trigger the same fetch.)
- *Evidence:* [`sources/poller.py:38`](../../packages/concentrate/sources/poller.py#L38).

### 11. "Why build a memory at write time instead of better retrieval at query time?"
It's a volume question, and I treat it as one. Write time pays an ingest cost once per corpus, then
answers from a compact cited context. Query time pays every question. The analysis amortises ingest
over a question volume and reports the **break-even volume** on the Pareto frontier. A
structured memory also enables things retrieval can't do cheaply:
- as-of queries ("price as of week 3");
- commitment lifecycles ("no follow-up" ≠ "done");
- proactive features like the pre-meeting brief.
- *Evidence:* [`evals/pareto.py:77`](../../packages/concentrate/evals/pareto.py#L77) `breakeven_volume`.

### 12. "How do you stop the memory hallucinating?"
A grounding gate. Every extracted fact must cite line IDs **in this meeting**, quote text that's a
substring of those lines, and any number or date in its value must be *said* in them. Spoken numbers
are normalised ("twenty five dollars" → 25). Only uncertain items (confidence < 0.75 or a relative
date) go to an LLM entailment check, which fails closed. Rejections are stored with reasons, and
`gate_eval` injects corrupted facts to measure the gate's precision and recall.
- *Evidence:* [`memory/verify.py`](../../packages/concentrate/memory/verify.py), `check_item` at line 142.

### 13. "What's bitemporal and why do meetings need it?"
Two clocks per fact:
- **valid time:** when it was true;
- **transaction time:** when we learnt it.

Questions like "what was the price as of Sept 15?" need valid time. Avoiding leakage needs
transaction time: an "as of" answer must not use a fact recorded later. A new value supersedes the
old one (closes it) rather than deleting it, and same-day conflicts are flagged, not resolved.
- *Evidence:* [`memory/store.py:92`](../../packages/concentrate/memory/store.py#L92) `live_at`, [EXERCISES #4](EXERCISES.md).

### 14. "Why deterministic speaker attribution instead of asking an LLM?"
It's free, explainable (each line records *why*: `addressed`, `turn_continuity`, `platform_name`…),
and measurable against ground truth, because the generator knows the true speaker of every line. For
remote meetings Granola already names 82% of "Them" lines, so the work is the unnamed remainder and
cross-meeting identity. The honest part: it's weak in person, and an LLM fallback for low-confidence
lines is the obvious next step.
- *Evidence:* [`enhance/speakers.py`](../../packages/concentrate/enhance/speakers.py), [`docs/systems/concentrate/enhance.md`](../systems/concentrate/enhance.md).

### 15. "How do you know a difference between systems is real?"
Paired tests, because every system answers the same questions:
- McNemar's exact test on the discordant pairs;
- a paired bootstrap CI of the gap;
- Holm–Bonferroni across all pairs.

I also compute power from the observed disagreement rate, so I can say in advance what gap a sample
can detect. The pilot said "nothing is significant; you'd need a ~62-point gap at n=10". The analysis
prints that in its first paragraph.
- *Evidence:* [`evals/stats.py`](../../packages/concentrate/evals/stats.py), `evals/reports/20261102T100403Z.analysis.md`.

### 16. "Can I trust your judge?"
- It's an LLM judge (counting only "correct").
- Exact match and citation P/R are shown next to it.
- A second-model re-judge agreed 97.5% (κ 0.89) on the pilot, with the ranking unchanged.
- There's a blind hand-check workflow for a human.

Weakness: the primary judge is the answer model, so I'd want the hand-check done on the XL results
before making strong claims.
- *Evidence:* [`evals/judge_check.py`](../../packages/concentrate/evals/judge_check.py), BENCHMARK.md "Judge check".

### 17. "How do you control cost?"
- Every LLM call goes through one client with a disk cache keyed by a hash of the full request, and
  a ledger of tokens and $ per stage.
- `--estimate` prices a run offline.
- Anything over $2 asks first, and `CONCENTRATE_BUDGET_USD` is a hard cap checked before each question.
- The Batch API halves the price of single-turn systems and the judge. The results land in the same
  cache files, so there's no second code path.
- Prompt caching makes b2 (150k tokens of context) about $0.03 per question after the first.
- *Evidence:* [`llm/client.py`](../../packages/concentrate/llm/client.py), [CONCEPTS §3](CONCEPTS.md#3-calling-llms-cheaply).

### 18. "A build hung for 30 minutes. What happened and what did you change?"
No timeout was set, so the SDK used 600 s reads with silent retries: up to ~50 minutes on one dead
socket. Fixes:
- explicit timeouts scaled to `max_tokens`;
- our own retry loop with full-jitter backoff and a deadline, logged at WARNING;
- a heartbeat watchdog that dumps stacks and exits 75 after 15 silent minutes;
- crash-safe resume: torn progress lines are repaired, and each meeting is one transaction.

Not fixed and documented: batch creation isn't idempotent, and abandoned attempts may be billed.
- *Evidence:* [`docs/engineering/HARDENING.md`](../engineering/HARDENING.md).

### 19. "How is it reproducible?"
- Same seed gives byte-identical data (tested), and the committed fixture must equal a fresh `synth --seed 42`.
- Every report records git SHA and dirty flag, model, prompt versions, seed, split and spend.
- Prompts are versioned files (`extract.v2`, `entail.v2`, `judge.v1`).
- `--offline` re-runs from cache with zero API calls.
- The explorer re-verifies citation scores against fixtures at the report's SHA.
- *Evidence:* `tests/test_synth.py:37,42`, [EXERCISES #1](EXERCISES.md).

---

## D. Product and Granola

### 20. "What would you build at Granola with this?"
Three things, in order of how cheap they are to try:
1. **Cross-meeting identity in the agent:** link display name ↔ email handle ↔ directory, which is
   the probe's miss.
2. **A commitments view with lifecycle:** open, done, slipped, reassigned, no follow-up. That's the
   brief's 91% recall, once precision is fixed.
3. **Evidence for transcript retirement:** keep verified facts and digests (~5% of tokens) and
   measure what's lost before deleting anything.
- *Evidence:* [`docs/results/BRIEF.md`](../results/BRIEF.md), [`docs/archive/PHASE4_DESIGN.md`](../archive/PHASE4_DESIGN.md).

### 21. "Can we delete old transcripts?"
My retirement workstream (branch `ws-c-retirement`, not yet merged) ran it on XL dev (90 questions):
Concentrate went 89% → 88% when every transcript was retired, against 64% for summaries-only. But
there's no question category that needs a verbatim quote, and b0 with retired transcripts wasn't run
(~$29). So: promising for facts and commitments, unmeasured for exact wording.
- *Evidence:* the WS-C hand-over; [CONCEPTS "Compression tiers"](CONCEPTS.md#compression-tiers-l0l3).

### 22. "Privacy?"
- The MCP source is read-only by construction: it calls five read tools and never write tools.
- OAuth tokens live in `~/.config/concentrate` with mode 600 and are never logged.
- Secrets come only from env and are never in `repr` or logs (there's a test for that).
- `fidelity` prints aggregates only, never transcript text.
- My real data stays out of git.
- Facts carry a `privacy` field, and private content (health, compensation) is planted in the
  synthetic 1:1s for a redaction eval that's planned but not built.
- *Evidence:* [`tests/test_granola_api.py:128`](../../tests/test_granola_api.py#L128), CLAUDE.md "Secrets".

---

## E. Reflection

### 23. "What would you do differently?"
1. **Real data earlier.** I'd have collected a small real question set in week one, not after building
   everything on synthetic data.
2. **Content-hash line IDs, or pin fixtures to a version from day one.** Positional IDs cost a lot of
   defensive code.
3. **A smaller, deeper scope.** Fewer systems run on more questions would have reached significance sooner.
4. **Fewer parallel agent workstreams,** with me writing the core (store, gate) by hand, so ownership
   came first instead of after.

### 24. "What would you do next with two more weeks?"
1. Run the XL test once and publish the numbers.
2. Do the blind hand-check of the judge on XL.
3. Finish the real-data slice against Granola's live agent.
4. Add an LLM fallback for low-confidence in-person speakers, and measure it against the 64%
   owner baseline.
5. Add a verbatim-quote question category to make the retirement curve honest.
6. LLM-polish the filler (~$28 on Sonnet) to remove the template signature.

### 25. "What did you learn?"
Pick two real ones:
- **Evals are where the work is.** The pilot saturating taught me to plan sample size with power
  analysis *before* spending money.
- **Systems fail in boring ways.** A missing timeout, a torn log line, positional IDs: hardening and
  provenance mattered more than clever extraction.

Optionally, a third: managing many AI agents in parallel is fast, but understanding has to be built
deliberately. Hence this guide.
