# Agent-interface quality: results

**Question.** When an external agent asks a meeting product through its MCP, what does it receive? Does the payload say when it is unsure, how fine-grained is its evidence, does the same question get the same answer twice, and how long does it take? Track definition and metrics: [docs/tracks/AGENT_INTERFACE.md](../tracks/AGENT_INTERFACE.md).

**Short answer (real DEV, one person's meetings):**
- **Granola** (`query_granola_meetings`, n = 47) is accurate (89% useful and comprehensive [77, 95]), but its payload is one prose string with **meeting-level links only** (0 of 47 point inside a meeting) and **no abstain or coverage field**. Every answer (47/47) carries the same standing note telling the agent to call `search_transcripts` itself. Its confident errors cluster on unanswerable questions: **2 of 6** [10, 70], reproduced unchanged in all three asks.
- **Concentrate** (`ask_memory`, n = 39, an older in-process run) is **less accurate** (59% useful and comprehensive [43, 73]), but when it is wrong it says so: **0 wrong but confident** [0, 9]. 41% of its answers are *incomplete and clearly so*. Its payload has an abstain field on every answer and **line-level citations** on 74% (the rest are abstentions with no citation). Caveat: its 39 answers include **none** of the unanswerable questions, so its 0 is not comparable with Granola's 2 of 6.
- On the 39 questions **both** were judged on in the **same judge run**, Granola is 36 / 1 / 1 / 1 across the four types and Concentrate 23 / 0 / 16 / 0. One product is mostly right and rarely flags doubt; the other is often unsure and always says so.

$0 of LLM spend. Verdicts are reused from existing judged runs. The only live calls were 24 read-only `query_granola_meetings` re-asks for Granola's stability check. These are DEV results on one account: read them as where to look, not as claims about either product's users.

```bash
uv run concentrate agent-interface
```

Aggregate: [`evals/reports/agent-interface.aggregate.json`](../../evals/reports/agent-interface.aggregate.json). Per-question rows, repeats and the human-check lists stay under `data/real/agent_interface/<product>/` (private).

## 1. One row per product

| | Granola | Concentrate |
|---|---|---|
| Interface | `query_granola_meetings`, Granola's public MCP | `ask_memory(answer=true)`, `concentrate real-kit serve` |
| Payload source | recorded MCP payloads | in-process `answer.v1` output wrapped in the `ask_memory` response shape (same code path, not sent over MCP) |
| Verdicts | deletion-sim DEV run (judge 1 Opus, judge 2 Sonnet) | real-slice run `20261103T004019Z` (judge 1 only; stopped on budget) |
| Questions | 47 (6 unanswerable) | 39 (0 unanswerable) |
| **Useful and comprehensive** | 42 · 89% [77, 95] | 23 · 59% [43, 73] |
| **Useful but incomplete** | 1 · 2% [0, 11] | 0 · 0% [0, 9] |
| **Incomplete and clearly so** | 1 · 2% [0, 11] | 16 · 41% [27, 57] |
| **Wrong but confident** | 3 · 6% [2, 17] | 0 · 0% [0, 9] |
| Wrong but confident, strict flags | 3 | 1 |
| Evidence granularity: line / meeting / none | 0 / 46 / 1 | 29 / 0 / 10 |
| Machine-readable abstain field | 0/47 | 39/39 |
| Abstains | 1/47 (prose) | 10/39 (field) |
| Any flag (hedge, caveat, gap, abstain) | 11/47 · 23% [14, 37] | 28/39 · 72% [56, 83] |
| Payload instructs the caller to call a tool | 47/47 | 0/39 |
| Quotes the meeting verbatim (3+ words) | 0/47 | 4/39 |
| Median answer length | 23 words | 57 words |
| Latency p50 / p90 | 8.1 s / 9.8 s (end to end, over MCP) | 4.2 s / 7.5 s (in-process, no transport) |
| Stability (12 questions × 3 asks) | key terms stable 10/12, cited meetings identical 11/12, latency CV 7% | not measured (each ask costs an answer-model call) |

**Paired, same judge run** (`20261103T004019Z`, the 39 questions both answered, judge 1):

| | Useful & comprehensive | Useful but incomplete | Incomplete, clearly so | Wrong but confident |
|---|---|---|---|---|
| Granola | 36 · 92% [80, 97] | 1 | 1 | 1 · 3% [0, 13] |
| Concentrate | 23 · 59% [43, 73] | 0 | 16 · 41% [27, 57] | 0 · 0% [0, 9] |

**Reading.** On this run the two products fail in opposite ways. Granola is right far more often. When it is wrong, the payload looks the same as when it is right. Concentrate is wrong or unsure far more often, but every one of those answers is flagged, either by the abstain field or by a stated gap. An agent can therefore route around Concentrate's failures and can't route around Granola's. Neither result is a recommendation. The Concentrate run is older (dirty tree, `0cad9c5`), and its accuracy loss is documented in [REAL_SLICE.md](REAL_SLICE.md). Concentrate's later conditions with transcript fallback are scored in the deletion simulation, not here, because `ask_memory` doesn't serve them.

## 2. Granola in detail

### 2.1 What the payload contains (n = 47)

| What an external agent receives | Share |
|---|---|
| Prose answer with markdown links to `notes.granola.ai/d/<id>` (whole-meeting links) | 46/47 (1 cites nothing: it declined) |
| Any pointer inside a meeting (transcript chunk, line ID, timestamp) | **0/47** [0, 8] |
| The standing `<transcript_note>`: "answer comes from notes... call `search_transcripts`... answer from what was said" | **47/47** [92, 100] |
| The answer text itself refers to a transcript | 2/47 |
| A quoted span of 3+ words | **0/47** (the only quotes are 1–2 word labels or names) |
| Hedge / coverage caveat / gap statement / decline | 2 / 3 / 8 / 1 of 47 |

By question type:

| Category | n | words (median) | meetings cited | flagged | latency p50 |
|---|---|---|---|---|---|
| who_said | 8 | 10.5 | 1.0 | 0 | 7.5 s |
| temporal | 6 | 14 | 1.0 | 0 | 7.8 s |
| unanswerable | 6 | 17 | 0.8 | 3 | 8.3 s |
| disambiguation | 5 | 20 | 1.2 | 1 | 7.9 s |
| decision | 8 | 26 | 1.0 | 1 | 8.1 s |
| commitment_status | 8 | 44.5 | 1.0 | 6 | 7.9 s |
| change | 6 | 72 | 1.7 | 0 | 10.3 s |

By tag: notes-sufficient n = 23, transcript-needed n = 18, unanswerable n = 6. **The payload looks the same whether or not the question needed the transcript**: the same meeting-level links, the same standing note, latency p50 7.7 s vs 8.1 s. Nothing in it tells the caller which questions needed the transcript. Who-said answers are the shortest (a name and a link, about 10 words), with no evidence an agent could check without a second call.

### 2.2 Four answer types, by tag

| Tag | n | Useful & comprehensive | Useful but incomplete | Incomplete, clearly so | Wrong but confident |
|---|---|---|---|---|---|
| notes_sufficient | 23 | 21 (91%) | 1 | 1 | 0 [0, 14] |
| transcript_needed | 18 | 17 (94%) | 0 | 0 | 1 (6%) [1, 26] |
| unanswerable | 6 | 4 (67%) | 0 | 0 | **2 (33%) [10, 70]** |

With judge 2's verdicts: 40 / 3 / 1 / 3. With strict flags: 42 / 2 / 0 / 3. The wrong-but-confident count is 3 under every variant. The "incomplete and clearly so" bucket is nearly empty because the payload rarely says it is incomplete: 11 of 47 answers carry any flag, and all 3 coverage caveats are on commitment-status questions.

### 2.3 Stability: 12 questions, three asks each

Sample (seed 42): all 5 non-correct answers, then round-robin by category. It covers all 7 categories and all 3 tags. Each question was asked twice more on 2026-11-04 (24 calls).

| Measure | Result |
|---|---|
| Key-term pattern identical across asks | 10/12 · 83% [55, 95] |
| A key term present in one ask, absent in another | 2/12 · 17% [5, 45] |
| Flag status identical | 9/12 · 75% [47, 91] |
| Cited meetings identical | 11/12 · 92% [65, 99]; mean pairwise Jaccard 0.97 |
| Latency per question | median SD 0.55 s, CV 7%; repeat asks p50 7.4 s, p90 8.6 s |
| Sent to human check | 6 of 12 (private list) |

**Attribution inconsistency, quantified.** Both key-term flips were on entity questions: 1 of 2 sampled who-said questions and 1 of 2 disambiguation questions; 0 of the other 8 sampled questions flipped. Both flips were on questions the recorded answer got wrong or partly wrong. In one, the gold person was missing from the recorded answer and present in both repeats. In the other, the gold term appeared in 1 of 3 asks, and that ask cited a different set of meetings. So a confidently wrong who-said answer was **not reproducible**. An agent asking twice would have got two different people, with no signal that either was uncertain. This confirms the probe's observation on n = 4 entity questions; it is not a rate.

**What did not move.** The two confident errors on unanswerables came back unhedged in all 3 asks. Flag status changed on 3 questions whose key terms did not: a caveat or gap statement present in one ask was missing in another. One of them was a correctly declined unanswerable that said "not covered" in only 1 of 3 asks. When Granola does caveat an answer, the caveat is not a stable property of that answer.

### 2.4 Three generic cases

- **An unhedged answer to a question nobody answered.** An unanswerable question about a detail the meetings never settle got a fluent, specific answer citing one meeting, with no caveat, three times in a row.
- **A who-said answer that changed its mind.** A question asking which person said something got one person in the recorded answer and the correct person in both repeats. Each was about 25 words with one meeting link and no span or confidence signal.
- **A caveat that comes and goes.** A commitment-status question was answered correctly all three times. Only the first answer carried a caveat about what the record did or did not show.

## 3. Concentrate in detail (n = 39)

- **Evidence**: 29 of 39 answers cite transcript line IDs (2.7 distinct line IDs per answer, averaged over all 39); the other 10 are abstentions with no citation. 4 of 39 quote the meeting verbatim.
- **Flags**: 28 of 39 flagged, mostly gap statements (24) and the abstain field (10). Every answer judged partial or incorrect (16) was flagged. Four are flagged only by gap statements, so under strict flags 3 partial answers become *useful but incomplete* and 1 incorrect answer becomes *wrong but confident*.
- **By tag**: notes-sufficient 14/23 useful and comprehensive, transcript-needed 9/16; incomplete and clearly so 9/23 and 7/16.
- **Who-said**: all 8 flagged; median 62 words.
- **Not measured**: stability (repeats cost LLM calls; budget rule) and unanswerables (not reached in that run). Its latency excludes MCP transport, so it is not comparable with Granola's.

## 4. What Granola could change

Small, testable changes to the `query_granola_meetings` payload. Each can be scored on this track:

1. **Return a transcript span with each attribution.** Next to each name in a who-said answer, return the chunk ID (`search_transcripts` already returns them) or a start time. *Track metric:* granularity = line on who-said questions; key-term stability on repeats.
2. **Make coverage a field, not boilerplate.** Replace the standing note (identical on 47/47) with a per-answer field such as `coverage: notes_only | transcript_checked | not_found`, so the caller only calls `search_transcripts` when it needs to. *Metric:* `instructs_caller` falls; the field differs between notes-sufficient and transcript-needed questions.
3. **An explicit abstain or confidence field.** Even a coarse `found: true | false | partial` would separate "not in your meetings" from a confident answer. *Metric:* `abstain_field`; wrong but confident on unanswerables (2 of 6 here); flag stability (changed on 3 of 12 here).
4. **Return one evidence sentence per claim.** A short verbatim line from the note or transcript lets the caller verify without a second round trip. *Metric:* `verbatim_quote` (0 of 47 today).
5. **Deterministic retrieval for the same query.** Cited meetings matched on 11 of 12 repeats; pinning or returning the retrieval set would make the remaining flips debuggable. *Metric:* `citations_identical`.

These are suggestions from outside: we see only the payload, not why it is shaped this way (cost, latency, or keeping MCP answers short on purpose).

## 5. Limits

- One account, DEV questions. Granola n = 47, Concentrate n = 39, stability on 12 Granola questions; the confidence intervals are wide and stated everywhere.
- The two rows come from different runs and code states. Only the paired table compares them on the same questions with the same judge run.
- All classifiers are regexes over English prose ([tests](../../tests/test_agent_interface.py)). They were checked by reading masked fragments of real answers, not by a second annotator.
- Concentrate's payload is reconstructed from in-process answers in the `ask_memory` shape, not captured over MCP.
- Latency is end to end from this machine. Granola's repeats ran up to a day after most of its recorded answers.
