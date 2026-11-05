# Notes grounding checker

A reusable tool: give it one meeting's transcript and a system's notes, and it returns, per claim, a verdict plus the transcript lines and a quote that back it. This doc says how it works and how well it does on AMI, where humans have already marked the answers. For engineers who want to run it or judge it.

**Headline (AMI, TEST meetings, Haiku 5.5, $0.04 for the whole run):**

| What | Result (TEST, n) | 95% CI |
|---|---|---|
| Edited or injected sentences flagged `unsupported` / `contradicted` | **89%** (98 of 110) | [82, 94] |
| Human summary sentences called `supported` | **78%** (61 of 78) | [68, 86] |
| Human sentences flagged `unsupported` / `contradicted` | 18% (14 of 78) | [11, 28] |
| Evidence recall: cited lines hit a human-linked line | **92%** (33 of 36) | [78, 97] |

- **It catches planted errors.** Swapped roles, negated decisions and sentences copied in from another meeting are flagged 84–95% of the time (§3.3).
- **Its evidence is real.** When it calls a human sentence supported, 92% of the time it cites a line the AMI annotator also linked to that sentence.
- **It also flags 1 in 5 human sentences.** By our read, about half of those flags are fair: the human summary overreaches or is wrong. The rest are checker errors (§3.2). That read is ours and not independent.

Code: [`evals/grounding.py`](../../packages/concentrate/evals/grounding.py) (the checker), [`evals/grounding_ami.py`](../../packages/concentrate/evals/grounding_ami.py) (the validation). Prompt: [`prompts/grounding.v1.md`](../../prompts/grounding.v1.md). Results: [`evals/reports/grounding/`](../../evals/reports/grounding/).

```bash
uv run concentrate ground --meeting note.json --notes notes.md --quote   # price it, $0
uv run concentrate ground --meeting note.json --notes notes.md --yes     # writes grounding.json
uv run concentrate ground --ami IS1008a --quote                           # AMI meeting + its human summary
uv run concentrate ground-ami --quote                                     # the validation's $0 parts
uv run concentrate ground-ami --yes                                       # the validation (~$0.04)
```

## 1. What it does

**Input.** A `MeetingNote` (its utterances are the transcript) and notes as markdown. `--meeting` takes a `MeetingNote` as JSON; `--db` with `--note-id` reads a synced store; `--ami` builds one from AMI.

**Output.** One JSON record per claim:

| Field | Meaning |
|---|---|
| `verdict` | `supported` · `inferred` · `unsupported` · `contradicted` (same labels as the [notes faithfulness track](../tracks/NOTES.md)) |
| `evidence` | `utterance_id`s of the cited lines (support, or what it contradicts) |
| `quote`, `quote_id` | the cited line sharing most words with the claim, cut to 25 words. Copied, never generated, so it is always verbatim |
| `decided_by` | `excerpt` or `full`: which pass gave the verdict |
| `precheck` | numbers, dates and names in the claim that the transcript never says ($0) |

**Pipeline.** Cheap first, the full transcript only when needed.
1. **Claims:** `split_claims` from the faithfulness track (one per bullet or sentence).
2. **Candidate lines ($0):** per claim, the 8 lines sharing the most IDF-weighted, stemmed words (`nearest_lines`), plus one neighbour each side.
3. **Excerpt pass:** one call per meeting, showing only the union of candidate lines.
4. **Full pass:** one more call, with the whole transcript, for claims that had no candidates or that the excerpt pass did not call `supported`. Its verdict is final. So a retrieval miss costs a call; on its own it can't turn a true claim into a flagged one.

A `supported` verdict with no valid cited line becomes `unsupported`.

## 2. How we validate it on AMI

AMI (CC BY 4.0) gives each scenario meeting a human summary. It also gives **summary links**: for each summary sentence, the dialogue acts an annotator marked as its source. So we know the answers without asking another LLM.

| Set | What | n | Expected verdict |
|---|---|---|---|
| **Positives** | AMI's human abstract / decisions / problems / actions sentences | 92 | `supported`, citing a linked line |
| Swap role or person | one role ("Project Manager") or name swapped for another the sentence doesn't name | 41 | flagged |
| Change number | one number changed (+1, +2 or ×2) | 3 | flagged |
| Negate | the main clause's "will", "is", "decided" flipped; never inside "whether / that / which" clauses | 44 | flagged |
| Inject, same series | a sentence from another meeting of the same project (IS1008b into IS1008a) | 25 | flagged |
| Inject, other series | a sentence from an unrelated project (IB4003 into IS1008a) | 25 | flagged |

*Flagged* means `unsupported` or `contradicted`. Edits are rule-based and seeded (`--seed 0`), so the set is byte-identical on every run.

- **Meetings:** IS1008a–d and IB4003. IB4003 has no summary links, so evidence recall uses IS1008a–d only (65 linked sentences).
- **Split:** IS1008a is DEV, the rest TEST. The prompt and the edit rules were written before any checker output was seen. The edit rules were fixed once after reading the generated edits (not the verdicts): negations inside "that" clauses and duplicate names were dropped.
- **No comparison leak:** each edit is checked in a separate call from the sentence it came from, so the checker can't spot it by contrast.
- **Transcript:** AMI's manual transcript, one line per dialogue act, all on the `Them` channel. The speaker's scenario role is shown ("Them (Project Manager)"), or "Speaker A" in IB4003.
- **Paired precision:** each edited sentence is paired with one original from the same meeting: its own source, or a seeded pick for injections. Precision is flagged edits ÷ (flagged edits + flagged pairs).

## 3. Results

### 3.1 The $0 parts

| Measure | Result | n |
|---|---|---|
| Candidate lines (top 8) hit a human-linked line | 71% [59, 80] | 65 |
| …within 2 lines of one | 83% [72, 90] | 65 |
| Excerpt size, human-summary calls | 36% of transcript lines | 2,702 lines |
| Pre-check flags (lexical only), human sentences | 4% [2, 11] | 92 |
| Pre-check flags, injected from another series | 32% [17, 52] | 25 |
| Pre-check flags, swaps / negations | 2% / 5% | 41 / 44 |

**Lexical checks can't do this job.** The pre-check catches some cross-project injections (new names like "Gisella") and almost nothing else. Swaps and negations reuse the meeting's own words.

### 3.2 Positives: the human summary

| Section (all 5 meetings) | n | supported | inferred | unsupported | contradicted |
|---|---|---|---|---|---|
| abstract | 37 | 33 | 2 | 0 | 2 |
| actions | 22 | 19 | 0 | 2 | 1 |
| problems | 17 | 15 | 1 | 0 | 1 |
| **decisions** | 16 | **5** | 2 | 2 | **7** |
| **all** | 92 | 72 (78%) | 5 | 4 | 11 |

DEV alone: 11 of 14 supported. Evidence recall over all 47 supported linked sentences: 91% [80, 97]. 62% of cited lines are lines the annotator linked; the rest are mostly nearby lines the annotator didn't mark (summary links are sparse, not a full list).

**Who is wrong on the 20 human sentences not called supported?** We read each one against the transcript. This is one reader, the session that built the checker, so it is not independent. Item IDs are in the per-item file.

| Our read | n | Example (AMI's sentence, CC BY 4.0) |
|---|---|---|
| **The flag is fair:** the human sentence overreaches or is wrong | 5 | IS1008d: "the group decided that the current budget would not allow the addition of the LCD display". The PM says the cost is under budget, and the same summary later says the LCD will be included |
| **Judgement call:** `inferred` is defensible | 6 | IS1008a: "instructed all participants to work together on their respective design plans" |
| **Checker error** | 9 | IS1008d: "The Marketing Expert will begin his marketing plan after production has begun". The transcript says it, but by first name ("leave it to the Ed for later"), and the checker can't tie "Ed" to the role |

The 9 checker errors have four causes:
- **Names, not roles (3).** People say "Ed" or "you"; the summary says "the Marketing Expert".
- **Too strict on "decided" (3).** Loose agreement ("we've kinda come to some agreement") is read as no decision.
- **Lost context (1).** "He will also present…" is checked alone, without the sentence before it.
- **Misreading (2).** E.g. "We don't have cafeteria" outweighed by a remark about a cafeteria downstairs.

**Decisions are where human summaries and the checker disagree most.** 9 of 16 decision sentences are flagged. Some are annotator overreach, some checker strictness. Granola's notes showed a similar pattern on real meetings ([faithfulness §4.2](../tracks/NOTES.md#42-by-claim-type-granola)).

### 3.3 Negatives: planted errors

| Edit (TEST) | n | Recall: flagged | Paired precision | Recall incl. `inferred` |
|---|---|---|---|---|
| Swap role or person | 31 | 84% [67, 93] | 84% [67, 93] | 87% |
| Change number | 2 | 2 of 2 | 2 of 3 | 2 of 2 |
| Negate | 37 | 92% [79, 97] | 72% [58, 83] | 95% |
| Inject, same series | 20 | 85% [64, 95] | 81% [60, 92] | 90% |
| Inject, other series | 20 | 95% [76, 99] | 86% [67, 95] | 100% |
| **All edits** | 110 | **89% [82, 94]** | | |

DEV (IS1008a): 27 of 28 edits flagged. In TEST, 67 of 68 `contradicted` verdicts on edits cite the lines they contradict.

- **Numbers can't be measured here.** AMI's summaries have 3 numbers in total.
- **Negation precision is lowest** because the same decision sentences whose negations get caught were often flagged in their original form too (§3.2).

The 12 TEST misses:
- **Edit is actually true (3).** A same-series injection ("How to accommodate the future growth of the department") was also discussed in the target meeting. A negated decision ("did not decide to contact another company") matches the transcript better than the original.
- **Names and "both of you" (3).** The PM says "both of you, you can come up with the prototype", so a role swap on who builds it can't be checked from role labels.
- **Double negative (1).** "will not be designed without a display" passed as supported.
- **A swap inside a long list (1)** and **`inferred` instead of flagged (4).**

### 3.4 Cost

| | |
|---|---|
| Run | 230 sentences, 54 calls, 165 claims escalated to the full pass |
| Spent | **$0.0445** (estimate beforehand: $0.022–0.071) |
| One meeting's human summary | under $0.01 (IS1008a: $0.001–0.003 estimated) |
| Excerpt vs full transcript | 101k vs 323k estimated input tokens, summed over the 27 call groups |

Escalation is high here (72% of sentences) because most sentences are edits that should fail. On real notes, where most claims are supported, the excerpt pass decides most of them.

## 4. Threats to validity

- **The "known answers" aren't perfect.** Human summaries overreach, and our edits are sometimes true by accident. The 78% supported rate is therefore a floor on checker accuracy, not a measurement of it. Our own read of the disagreements (§3.2) is unverified.
- **Small n, one domain.** 5 meetings and 2 projects, all scripted design or office-planning meetings. CIs are wide, and numbers are untested (n = 3).
- **The transcript is easier than a product's.** AMI's manual transcript is near-perfect and shows each speaker's role. A notetaker's transcript has ASR errors and usually only `Me` / `Them`. Role swaps will be harder there.
- **The edits are easier than real errors.** One clean edit per sentence. Real notes fail more subtly ([faithfulness §4.3](../tracks/NOTES.md#43-what-the-failures-look-like)): an added qualifier, a discussion written up as a decision.
- **One cheap model, one prompt, one run.** Haiku 5.5 at low effort, `grounding.v1`, no repeat runs. On real notes, Haiku was more lenient than Sonnet ([faithfulness §4.6](../tracks/NOTES.md#46-checker-reliability)). Here it caught most planted errors, so leniency on edits like these isn't the problem.
- **Evidence recall is measured against sparse links.** A cited line the annotator didn't link isn't wrong, so "cited lines that are linked" (62%) is a lower bound.

## 5. Next

- **Run it on Granola's and Wispr Flow's notes for the same AMI meetings**, once those recordings sync. Same checker, same prompt. Only scores get committed.
- **A Sonnet 5.5 column:** estimated $0.44–1.43 for the same run. Not run, to save budget.
- **Fixes the misses point to:** give the checker the participant names that map to roles when a source has them; keep the previous sentence as context for pronoun claims.

## 6. Provenance

| | |
|---|---|
| Date | 2026-11-05 (UTC) |
| Git SHA | `meta.git_sha` in [`ami-validation.claude-haiku-5-5.json`](../../evals/reports/grounding/ami-validation.claude-haiku-5-5.json) |
| Checker | `claude-haiku-5-5`, effort low, prompt `grounding.v1`, k = 8, window = 1 |
| Seed | 0 (edits and injection picks) |
| Data | AMI Meeting Corpus manual annotations 1.6.2, CC BY 4.0 (Carletta et al., 2005). Summary sentences quoted here are AMI's |
| Files | `ami-zero-cost.json` ($0 parts and estimate), `ami-validation.<model>.json` (scores), `.items.jsonl` (per sentence: text, verdict, cited line indices, reason; no transcript quotes) |
