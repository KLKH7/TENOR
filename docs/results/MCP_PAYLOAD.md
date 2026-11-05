# What Granola's MCP returns to an external agent

**Question.** When an outside agent (Claude, ChatGPT, Cursor) asks Granola's `query_granola_meetings`, what does it actually get back? How long is it, what does it cite, does it carry transcript-level evidence or only note-level summaries, does it say when it is unsure, and does the same question get the same answer twice?

**Short answer (real DEV, n = 47 questions, one person's meetings; 12 of them re-asked twice more):**
- The payload is a **short prose answer with note-level links**: median 22 words, about 1 meeting cited per answer. It carries **0 pointers inside a meeting** (no transcript chunk, line ID or timestamp) in 47 of 47 answers.
- **Every** answer (47/47) ends with the same standing note telling the calling agent that the answer comes from condensed notes and that it should call `search_transcripts` itself and re-answer. Transcript verification is pushed to the caller, every time, whatever the question.
- On Granola's own four answer types (our rule, below): **89% useful and comprehensive** [77, 95], **6% wrong but confident** [2, 17], 2% useful but incomplete, 2% incomplete and clearly so. The confident errors cluster on **unanswerable questions: 2 of 6** [10, 70] were answered with confidence and no caveat.
- Re-asking is **mostly stable** (verdict-relevant key terms unchanged on 10 of 12 questions, cited meetings identical on 11 of 12), but the instability is concentrated where the first answer was wrong: **both** key-term flips were on attribution-type questions (who said / which person) that the recorded answer got wrong or partly wrong. The two confident errors on unanswerables reproduced identically in all three asks.

Everything here is $0: no LLM calls. 24 extra read-only `query_granola_meetings` calls were made for the stability check. Verdicts come from the existing deletion-simulation DEV run (judge.v1, Opus; second judge Sonnet). This is DEV data on one account, so read it as where to look, not as a claim about Granola's users.

```bash
uv run concentrate mcp-payload            # offline: recorded answers + recorded repeats
uv run concentrate mcp-payload --live     # also records any missing repeats (read-only, <= 24 calls)
```

Aggregate: [`evals/reports/mcp-payload.aggregate.json`](../../evals/reports/mcp-payload.aggregate.json). Code: [`evals/mcp_payload.py`](../../packages/concentrate/evals/mcp_payload.py). Tests: [`tests/test_mcp_payload.py`](../../tests/test_mcp_payload.py). Per-question rows and the human-check list stay under `data/real/mcp_payload/` (private).

## 1. Payload anatomy

Each recorded answer is the raw text `query_granola_meetings` returned. We parse it with the same parser the black-box track uses ([`blackbox/products.py`](../../packages/concentrate/blackbox/products.py)) and measure it with regexes only.

| What an external agent receives | Share of 47 answers |
|---|---|
| Prose answer with markdown citation links to `notes.granola.ai/d/<id>` (whole-meeting links) | 46/47 (1 answer cites nothing: it declined) |
| Any pointer *inside* a meeting: transcript chunk ID, line/utterance ID, timestamp | **0/47** [0, 8] |
| The standing `<transcript_note>` ("answer comes from notes... call `search_transcripts`... answer from what was said") | **47/47** [92, 100] |
| The answer text itself refers to a transcript | 2/47 |
| A quoted span of 3+ words (and so any verbatim quote of the meeting) | **0/47** (the only quotes are 1–2 word labels or names) |
| A hedge (may, might, appears, likely, unclear) | 2/47 |
| A coverage caveat ("based on the available meetings", "based on the latest update") | 3/47 |
| A gap statement ("the meeting did not name...", "no later confirmation...") | 8/47 |
| An outright decline ("I couldn't find any...") | 1/47 |
| A machine-readable field for any of the above (confidence, coverage, abstain) | none: the payload is one prose string |

Median length 214 characters (22 words); 1.19 citation markers and 1.09 distinct meetings per answer. Latency (end to end, as recorded): **p50 8.1 s, p90 9.8 s**.

By question type (medians):

| Category | n | words | meetings cited | flagged (gap / caveat / hedge / decline) | latency p50 |
|---|---|---|---|---|---|
| who_said | 8 | 10.5 | 1.0 | 0 / 0 / 0 / 0 | 7.5 s |
| temporal | 6 | 13.5 | 1.0 | 0 / 0 / 0 / 0 | 7.8 s |
| unanswerable | 6 | 17 | 0.8 | 3 / 0 / 0 / 1 | 8.3 s |
| disambiguation | 5 | 19 | 1.2 | 1 / 0 / 0 / 0 | 7.9 s |
| decision | 8 | 26 | 1.0 | 0 / 0 / 1 / 0 | 8.1 s |
| commitment_status | 8 | 44.5 | 1.0 | 4 / 3 / 1 / 0 | 7.9 s |
| change | 6 | 69.5 | 1.7 | 0 / 0 / 0 / 0 | 10.3 s |

By tag (from `data/real/tags.p2t.json`): notes-sufficient n = 23, transcript-needed n = 18, unanswerable n = 6. **The payload looks the same for transcript-needed questions as for notes-sufficient ones**: same note-level links, no spans, the same standing disclaimer, latency p50 7.7 s vs 8.1 s. Nothing in the payload tells the caller *which* questions needed the transcript. Two of the 18 transcript-needed answers mention a transcript in the prose, but neither points to where in it.

**Reading.** Who-said answers are the shortest (about 10 words: a name and a link) and carry no evidence an agent could check without a second tool call. That is the category where the stability check found attribution flipping (section 3).

## 2. Granola's four answer types

Granola's agent-design post sorts answers into four types: useful and comprehensive; useful but incomplete; incomplete and clearly so; and "wrong, but confident". **The taxonomy is theirs; the rule that applies it to these answers is ours.** It is [`mcp_payload.bucket`](../../packages/concentrate/evals/mcp_payload.py), applied in this order:

1. judged `correct` → **useful and comprehensive** (this includes unanswerables the judge accepted as properly declined);
2. judged `partially_correct` or `incorrect`, and **flagged** → **incomplete and clearly so**;
3. judged `partially_correct`, not flagged, answerable → **useful but incomplete**;
4. otherwise (`incorrect` and not flagged, or a non-correct unflagged answer to an unanswerable question) → **wrong but confident**.

*Flagged* means the answer contains a hedge, a coverage caveat, a gap statement or a decline (section 1). The *strict* variant drops gap statements, because on status questions "there's no later confirmation" is often the answer itself rather than a caveat on it.

| Bucket | Overall (judge 1) | Strict flags | Judge 2 verdicts |
|---|---|---|---|
| Useful and comprehensive | 42/47 · 89% [77, 95] | 42/47 | 40/47 · 85% [72, 93] |
| Useful but incomplete | 1/47 · 2% [0, 11] | 2/47 | 3/47 |
| Incomplete and clearly so | 1/47 · 2% [0, 11] | 0/47 | 1/47 |
| Wrong but confident | 3/47 · 6% [2, 17] | 3/47 | 3/47 |

By tag (judge 1):

| Tag | n | Useful & comprehensive | Useful but incomplete | Incomplete, clearly so | Wrong but confident |
|---|---|---|---|---|---|
| notes_sufficient | 23 | 21 (91%) | 1 | 1 | 0 [0, 14] |
| transcript_needed | 18 | 17 (94%) | 0 | 0 | 1 (6%) [1, 26] |
| unanswerable | 6 | 4 (67%) | 0 | 0 | **2 (33%) [10, 70]** |

**Reading.** Granola's agent is accurate on these questions, including the transcript-needed ones (two answers say they read a transcript, so it can reach them internally). Its weak spot is the one Granola names: on questions the meetings do not answer, a third of answers were confident and unhedged. The "incomplete and clearly so" bucket is almost empty because the payload almost never says it is incomplete: 11 of 47 answers carry any flag, and the only explicit coverage caveats (3) are on commitment-status questions. Both the bucket counts and the CIs are tiny-sample (n = 47, 5 non-correct answers); the wrong-but-confident count is the same under all three variants.

Caveats: the judge reads the same prose we regex, so a judge that accepts a hedged answer as correct moves it to bucket 1 regardless of our flag. One unanswerable that the judge accepted shows no gap or decline signal our regexes recognise; it is on the private human-check list.

## 3. Stability: the same question, three times

Sample: 12 questions chosen by [`stratified_sample`](../../packages/concentrate/evals/mcp_payload.py) (seed 42): every answer not judged correct (5), then round-robin over categories, unanswerables first. It covers all 7 categories (unanswerable 3, who_said 2, change 2, disambiguation 2, one each of decision, commitment_status and temporal) and all 3 tags (notes-sufficient 6, transcript-needed 3, unanswerable 3). Each was asked twice more on 2026-11-04 (24 calls), so each has 3 answers.

String-level measures only. "Verdict-relevant fact" means the gold answer's key terms: for each answer, which of them appear (case- and punctuation-insensitive). Unanswerables have no key terms, so for them we compare the flag status (flagged/declined or not).

| Measure | Result (12 questions × 3 asks) |
|---|---|
| Key-term pattern identical across asks | 10/12 · 83% [55, 95] |
| A key term present in one ask and absent in another | 2/12 · 17% [5, 45] |
| Flag status identical across asks | 9/12 · 75% [47, 91] |
| Cited meetings identical across asks | 11/12 · 92% [65, 99]; mean pairwise Jaccard 0.97 |
| Latency, per question | median SD 0.55 s, median CV 7%; repeat asks p50 7.4 s, p90 8.6 s |
| Flagged for a human check | 6 of 12 (private list) |

**Attribution inconsistency, quantified.** Both key-term flips were on entity questions: 1 of 2 sampled who-said questions and 1 of 2 disambiguation questions; 0 of the 8 other sampled questions flipped. Both flipped questions were ones the recorded answer got wrong or partly wrong. In one, the gold person was absent from the recorded answer and present in both repeats; in the other, the gold term appeared in one of three asks, and that ask cited a different meeting set. So a confidently wrong who-said answer was **not reproducible**: an agent asking twice would have received two different people with no signal that either was uncertain. n is tiny (2 of 4 entity questions); this confirms the probe's observation rather than measuring a rate.

**What did not move.** The two confident errors on unanswerable questions came back unhedged in all 3 asks: stable, but stably wrong. Flag status changed on 3 questions without the key terms changing: in each, a caveat or gap statement present in one ask was dropped in another, including one correctly declined unanswerable whose "not covered" phrasing appeared in only 1 of 3 asks. The caveat, when there is one, is not a stable property of the answer.

Key-term presence is a proxy: a name can appear in the wrong role. Every case where the heuristic is ambiguous (pattern changed, flag changed without key terms, partial key-term match) is on the private human-check list, together with the two judge disagreements, and not resolved here.

## 4. Three generic cases

- **An unhedged answer to a question nobody answered.** An unanswerable question about a detail the meetings never settle got a fluent, specific answer citing one meeting, with no caveat, three times in a row. The payload gave the caller nothing to distinguish it from a correct answer.
- **A who-said answer that changed its mind.** A question asking which person said something got one person in the recorded answer and the correct person in both repeats. The answer was about 25 words with one meeting link, no transcript span and no confidence signal in any of the three.
- **A caveat that comes and goes.** A commitment-status question was answered correctly all three times, but only the first answer carried a caveat about what the record did or did not show; the other two stated the same status with none.

## 5. What Granola could change

Small, testable changes to the `query_granola_meetings` payload, each checkable with the classifiers in this page:

1. **Return a transcript span with each attribution.** For who-said and quote-like answers, include the transcript chunk ID (the same `chunk_ids` `search_transcripts` already returns) or a start time next to each name. *Test:* `span_refs > 0` on who-said answers, and stability of the cited span across repeats.
2. **Make coverage a field, not boilerplate.** Replace the standing `<transcript_note>` (identical on 47/47 answers) with a per-answer field, e.g. `coverage: notes_only | transcript_checked | not_found`. The caller then calls `search_transcripts` only when it is needed, instead of on every question. *Test:* the field differs between notes-sufficient and transcript-needed questions; fewer follow-up calls per answered question.
3. **An explicit abstain / confidence field.** Even a coarse `found: true | false | partial` would let an agent separate "not in your meetings" from a confident answer. *Test:* the wrong-but-confident rate on unanswerables (2 of 6 here) falls, and the flag is stable across repeats (it changed on 3 of 12 here).
4. **Return the evidence sentence.** One short verbatim line from the note or transcript per claim (0 of 47 answers quote the meeting today) lets the caller verify without a second round trip. *Test:* `verbatim_quotes > 0`, matched against the cited meeting's text.
5. **Deterministic retrieval for the same query.** Cited meetings were identical on 11 of 12 repeats; pinning retrieval (or returning the retrieval set) would make the remaining flips debuggable. *Test:* `citations_identical` and key-term stability across repeats.

These are suggestions from outside: we see only the payload, not why it is shaped this way (cost, latency, or keeping MCP answers short on purpose).

## 6. Limits

- One account, 47 DEV questions, 12 re-asked; the CIs are wide and stated everywhere.
- All classifiers are regexes over English prose. Their tests use synthetic text ([`tests/test_mcp_payload.py`](../../tests/test_mcp_payload.py)); they were checked by reading masked fragments of the real answers, not by a second annotator.
- Verdicts are the deletion-simulation judges' (judge 1 primary, judge 2 shown as a sensitivity check); no new judging was done.
- Latency is end to end from this machine and includes transport; repeats ran a day after most of the recorded answers.
