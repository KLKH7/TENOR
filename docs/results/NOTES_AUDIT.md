# Notes audit: are Granola's AI notes grounded in their own transcript?

**Question.** Granola's CEO has said in public (Every's *AI & I* podcast, 15 Jul 2026) that a worry about AI notes is inferences the meeting didn't contain. Granola's own prompting post (Dec 2025) states the rule: only claim what you can point to in the transcript. Granola hasn't published a grounding number. So we ask: on real meetings, how many claims in Granola's AI notes can a checker point to in the transcript?

**Short answer (4 real meetings, 132 claims, one person's data):**
- **No claim was invented outright.** Neither checker labelled a single claim `unsupported` (0 of 132; 95% CI upper bound 3%). The deterministic pre-check found **no** number, date or name in the notes that the meeting didn't contain (20 numbers, 6 date words and 80 capitalised words checked).
- **About 1 in 8 claims goes beyond what was said**, and the checkers disagree on the exact figure:
  - Haiku 5.5 (cheap, primary) calls 8% of claims ungrounded, CI [4, 13].
  - Sonnet 5.5 (stricter, every claim) calls 16% ungrounded, CI [11, 23].
  - Nearly all of these are `inferred`: the core fact was said, but the note adds a qualifier, firms a discussion up into a decision, or assigns an owner nobody confirmed.
- **Possible contradictions are rare: 3 of 132 (2%, CI [1, 6]), flagged by Sonnet only.** Each is a small inversion (an attribute flipped, a figure of speech read backwards, a specific example swapped for a generic one). These need the owner's check before anyone relies on them.
- **The checker is the weak link.** Haiku and Sonnet agree on 86% of claims, but chance-corrected agreement is only κ = 0.32. Sonnet re-checking its own sample (in a different batch) reaches κ = 0.56. Treat the 8–16% band as the result, not either end of it.

Committed aggregate: [`evals/reports/notes-audit.aggregate.json`](../../evals/reports/notes-audit.aggregate.json). Code: [`evals/notes_audit.py`](../../packages/concentrate/evals/notes_audit.py). Prompt: [`prompts/notes_audit.v1.md`](../../prompts/notes_audit.v1.md). The run cost **$0.26** in total (quoted beforehand with `count_tokens`). It re-runs for $0 from the cache:

```bash
uv run concentrate notes-audit --quote   # exact input tokens, price, no spend
uv run concentrate notes-audit --yes
```

## 1. Method

**Data.** The author's 4 real meetings, synced through Granola's official, read-only MCP into a private store. Each meeting has Granola's AI notes (`summary_markdown`) and its transcript. Nothing else was used. Per-claim output lives only in the gitignored `data/real/`. This page and the JSON hold aggregates and generic descriptions only.

**1. Claims.** `split_claims` turns each meeting's notes markdown into atomic claims: one per bullet or numbered item, and one per sentence when an item or paragraph has several. Headings (19) are dropped but kept as each claim's section. *Lead-ins* (14) are dropped too: an all-bold item (often an owner's name over their tasks) or an item ending in ":". The text of a lead-in becomes the context of the lines under it, so "**Name**" followed by "will send the deck" is checked as that person's task. Result: **132 claims** (33 / 39 / 23 / 37 per meeting).

**2. Check.** There is one call per meeting. The transcript goes in the system prompt as numbered `[i] Me|Them (name if Granola gave one): text` lines, with a prompt-cache breakpoint. The calendar context goes there too: title, date, owner = Me, invitees. All of that meeting's claims go in the user message. For each claim the model returns a short reason, a label, a claim type and the line numbers it relied on. The labels:

| Label | Meaning |
|---|---|
| `supported` | stated or directly implied (paraphrase and transcription errors are fine); **must cite lines** |
| `inferred` | plausible but not said outright: motive, sentiment, emphasis, a generalisation, or a named owner the channel labels can't establish |
| `unsupported` | nothing in the transcript says it |
| `contradicted` | the transcript says otherwise |

A `supported` answer without a valid line number would be relabelled `unsupported`. That never happened.

**Pre-check (deterministic, $0).** It flags any number written in digits that the transcript never says, as digits or as words ("twenty five" = 25, "5k" = five thousand, ordinals for dates). It does the same for month and weekday names. For names, it flags capitalised mid-sentence words found in neither the transcript nor the calendar context. Names found only in the calendar context are counted separately.

**3. Reliability.**
- Haiku 5.5 at low effort labels every claim; this is the primary, cheap checker the brief asked for.
- Sonnet 5.5 re-labels a seeded random 30% of claims (n = 40).
- Sonnet also labels **every** claim in a separate pass (the *strict* pass). We added this after the 30% sample came back at κ ≈ 0, because one checker's rate on its own would have been misleading.
- A private spot-check list (`data/real/notes_audit_spotcheck.md`) has each of the 29 claims any checker did not call `supported`. Each entry shows both checkers' labels and reasons, the cited lines, and the transcript lines sharing the most words with the claim, so the owner (the ground truth) can verify them by hand.

## 2. Results

### Overall (all 132 claims; 95% Wilson CIs)

| Label | Haiku 5.5 (primary) | Sonnet 5.5 (strict) |
|---|---|---|
| supported | 122 (92%, [87, 96]) | 111 (84%, [77, 89]) |
| inferred | 10 (8%, [4, 13]) | 18 (14%, [9, 21]) |
| unsupported | 0 (0%, [0, 3]) | 0 (0%, [0, 3]) |
| contradicted | 0 (0%, [0, 3]) | 3 (2%, [1, 6]) |
| **ungrounded** (= not supported) | 10 (8%, [4, 13]) | 21 (16%, [11, 23]) |

Per meeting (Haiku), ungrounded claims were 3 of 33, 2 of 39, 3 of 23 and 2 of 37. No meeting stands out.

### By claim type

Each checker assigns its own type, and they agree on the type 74% of the time, so the two columns don't share denominators.

| Claim type | Haiku: n | Haiku: ungrounded | Sonnet: n | Sonnet: ungrounded (inferred / contradicted) |
|---|---|---|---|---|
| decision | 18 | 1 (6%, [1, 26]) | 29 | 5 (17%, [8, 35]) (3 / 2) |
| action item / owner | 36 | 4 (11%, [4, 25]) | 26 | 6 (23%, [11, 42]) (6 / 0) |
| number / date | 2 | 0 | 1 | 0 |
| attribution (who said what) | 2 | 0 | 2 | 0 |
| opinion / sentiment | 13 | 1 (8%, [1, 33]) | 6 | 1 (17%, [3, 56]) (1 / 0) |
| summary-level generalisation | 2 | 1 (50%, [9, 91]) | 1 | 0 |
| other fact | 59 | 3 (5%, [2, 14]) | 67 | 9 (13%, [7, 24]) (8 / 1) |

**Which types go ungrounded most:** **action items** (11% and 23%) and **decisions** (6% and 17%), under both checkers. They are also the claims a reader acts on. Two of the three possible contradictions are decisions. The CIs overlap heavily with "other fact", so treat this as a pattern seen twice, not a measured difference. Numbers/dates, attributions and generalisations are too rare in these notes (n ≤ 2) to say anything about. The notes state few numbers and rarely say who said what.

### Pre-check

The claims hold 20 numbers written in digits, 6 month or weekday words and 80 capitalised mid-sentence words. **None** is missing from the transcript. A first version flagged 3 hyphenated compounds of the "AI-generated" kind. Those were false positives (the lowercase half isn't a name), the rule was fixed, and a test covers it. 3 claims name a person who appears only in the calendar invite, never aloud, which is the context Granola's notes model also has. The upshot: on these notes, grounding failures are **semantic, not lexical**. A cheap string check catches none of them, and a model has to read the claim.

### Reliability of the checker

| Comparison | n | Raw agreement | κ (4 labels) | κ (supported vs not) |
|---|---|---|---|---|
| Haiku vs Sonnet, 30% random sample | 40 | 78% | −0.04 | −0.05 |
| Haiku vs Sonnet strict, all claims | 132 | 86% | 0.32 | 0.32 |
| Sonnet strict vs Sonnet sample (same claims, different batch) | 40 | 88% | 0.56 | 0.55 |

- **Haiku is lenient.** Of the 21 claims Sonnet calls ungrounded, Haiku calls 15 supported. Of the 10 Haiku calls `inferred`, Sonnet supports 4. Only 6 claims are `inferred` under both.
- **The κ of −0.04 on the 30% sample is a base-rate effect, not random labels.** Haiku called 39 of those 40 claims supported, so chance agreement is already high and any disagreement drives κ to 0. The full-pass comparison (n = 132) is the better estimate.
- **Even Sonnet is not stable.** The same model on the same claim, batched with different neighbours, changed its label 5 times in 40. "Inferred" is a judgement call, and the line between "directly implied" and "plausible reading" is where most disagreements fall.

## 3. What the failures look like (generic, our own words)

These are made-up illustrations of each pattern we saw. They are not quotes and do not come from the meetings.

- **Added qualifier (inferred).** Someone says "a few of last quarter's invoices are missing". The note says "invoice records are incomplete: a recurring problem". The first half was said; "recurring" was not. This is the most common pattern.
- **Discussion promoted to decision (inferred).** Two vendors are discussed and one is mentioned last. The note says "consensus on vendor B". Nobody said they agreed.
- **Owner assigned beyond what was confirmed (inferred).** "Can you two take the onboarding doc?" One person says yes; the other says nothing. The note lists both as owners.
- **Extra detail (inferred).** The meeting proposes recording who approved a change. The note says "record approver and approval date". The date was never mentioned.
- **Conflation (inferred).** One remark about a discount code and another about members-only lists merge into "members-only lists, unlocked by discount code".
- **Flipped attribute (contradicted).** "The channel closes when the project ends" becomes "a permanent channel per project".
- **Figure of speech read backwards (contradicted).** An idiom meaning "over-explain to them" is written up as its opposite, a remark about the listeners themselves.
- **Specific swapped for generic (contradicted).** "Look at our client's website for ideas" becomes "look at well-designed sites in that industry".

## 4. Caveats

- **Four meetings, one person.** All were small working meetings. With n = 132 claims, the CIs are wide. This is a measurement of four notes, not of Granola.
- **The checker is an LLM.** The two checkers disagree by a factor of two on the ungrounded rate, and Sonnet disagrees with itself 12% of the time across batches. No label here has been confirmed by a human yet. The spot-check list exists so the meeting owner can confirm or overturn a sample, and the contradicted claims come first.
- **"Inferred" is a judgement call.** Notes are meant to compress, and some inference is the point of a summary. An `inferred` claim is not necessarily wrong. It is a claim a reader can't verify by clicking through to a line.
- **Notes model version.** These notes were written when the meetings happened. They may come from an older Granola notes model than the one Granola ships today. Granola's AI notes may also use context we don't see (the user's typed notes, templates, earlier meetings). "Not in the transcript" means not in the transcript, not necessarily invented.
- **Transcripts are imperfect.** They are speech-to-text and channel-attributed (`Me` = microphone, `Them` = everyone else). In one meeting all audio came in on a single channel, so "who said it" could not be checked from channels at all. A claim the notes model got right from audio the transcript garbled would show here as ungrounded.
- **The prompt is v1 and untuned.** It was written once and never tuned on these labels (tuning on four meetings would overfit). The strict pass was added after seeing the 30% sample's κ. That decision changes what we report, not the labels themselves.

## 5. What Granola could do

This is a suggestion, and it rests on the measurement above. It is not a claim about Granola's internals.

1. **Run a per-line grounding check before the notes are shown.** A model-based check is needed because the failures are semantic: a string check found nothing. The check labels each line as supported or not, citing transcript lines, the same job this audit does after the fact. It costs one cheap call per meeting with the transcript cached. Here it came to well under a cent per meeting with Haiku 5.5, and about $0.03 per meeting for the Sonnet-class pass that turned out to be needed.
2. **Put a magnifying-glass citation on every claim.** A supported claim gets an icon that jumps to the transcript lines it rests on. An unsupported one gets a quiet marker such as "inferred: not said in so many words", rather than deletion. Readers would then see the 8–16% of claims that need a second look.
3. **Be strictest on action items and decisions.** They go ungrounded most often here, and they are the lines people act on. A "who agreed?" check fits them well: a decision or owner line has to cite a line where someone said yes.
4. **Measure the checker, not just the notes.** Our two checkers disagreed enough to change the headline by a factor of two. A small owner-labelled set (like the spot-check list here) would calibrate the check before it gates anything a user sees.

## 6. Provenance

| | |
|---|---|
| Date | 2026-11-05 (UTC) |
| Git SHA | in the aggregate JSON `meta.git_sha` |
| Checker | `claude-haiku-5-5`, effort low, prompt `notes_audit.v1` |
| Re-check and strict pass | `claude-sonnet-5-5`, same prompt; 30% sample seeded (`--seed 0`) |
| Cost | $0.26 total (Haiku $0.01, Sonnet sample $0.15, Sonnet strict $0.10); quoted beforehand with exact input tokens |
| Prompt caching | the transcript is a cache breakpoint. With one call per meeting per model nothing is reused, so here caching cost about 25% extra on input (the write premium). It pays off only on retries or repeat checks of one meeting |
