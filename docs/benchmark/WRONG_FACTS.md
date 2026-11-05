# Anatomy of a wrong fact

When a meeting product gets a fact wrong (a name, number, role, commitment or decision), at which stage did it go wrong? This doc says how we answer that on AMI meetings played into Granola and Wispr Flow. For engineers who want to run it or attack it.

**Status.** Stage 1 runs at $0. Stage 2 ran once (2026-11-05, $0.08, 387 claims). Most of what that run called `transcript_propagated` was measurement noise, so the class rules and the checker's input changed ([§4](#4-stage-2-first-run-and-what-it-got-wrong)). The re-run is quoted, not yet run. Stage 3 has not run.

## 1. The question and the three stages

A wrong fact in an answer has a history. AMI gives human ground truth for every step, so we can find where the history breaks.

| Stage | Question | Checked against | Cost |
|---|---|---|---|
| **1. Transcription** | Is the fact in the product's transcript as AMI's human transcript has it? | AMI manual words | $0, deterministic |
| **2. Note-writing** | Does the product's note state something its own transcript doesn't support? | product transcript **and** AMI transcript | grounding checker (Haiku 5.5) |
| **3. Answering** | Is the Chat answer wrong although the transcript or notes had it right? | AMI question gold + stages 1–2 | judge + notes check (Haiku 5.5) |

The AMI set is the six recorded meetings (IS1008a–d, IB4003, EN2002b; [AMI_DESIGN.md](AMI_DESIGN.md)). IS1008a is DEV; pilots touch only it.

## 2. How each stage is checked

### Stage 1: transcription (`concentrate.ami.facts`)

**Fact tokens.** Extracted from AMI's manual transcript (dialogue acts; word turns for EN2002b, which has none), after the WER normaliser (`ami/wer.py`):

| Kind | Rule |
|---|---|
| `number` | a run of number words within one utterance ("25" = "twenty-five" = `twenty five`); a lone "one" is skipped (mostly a pronoun) |
| `money_percent` | a number followed by a unit (euros, pounds, dollars, cents, percent) |
| `name` | a word written capitalised mid-sentence and never in lower case: proper names. "I", fillers, months, weekdays and role words are excluded |
| `role` | "project manager", "marketing", "industrial designer", "user interface" |

**Per kind, per meeting × product:**
- **Recall:** share of human fact tokens (with multiplicity) also in the product transcript, any order.
- **Substitutions:** each human fact is located in the product transcript by the 3-grams around it (anchors vote; 2 must agree within 3 words), then the two windows are aligned word by word (edit distance with backtrace). A fact aligned to different words is a substitution ("twenty five" → "thirty five"); to nothing, a deletion; with no anchor, `unaligned`.
- **Name insertions:** words the product writes as names that occur nowhere in the human transcript. `owner_name_insertions` counts the account owner's first name: a product writing "Kian" into AMI audio.
- **Examples:** at most 5 short ones per kind (normalised words, 4 either side). Kian approved short quotes beside the AMI source.

Same rules for both products. Granola's microphone turns are dropped (on AMI the mic only hears the room); Wispr's are kept (its "me" is a name guess). Validated at $0: AMI's own transcript scored against itself gives 100% recall and 0 substitutions on all six meetings.

### Stage 2: note-writing (`concentrate.evals.wrong_facts`)

Each claim in a product's notes (`split_claims`) goes through the [grounding checker](GROUNDING_CHECKER.md) twice, with the same prompt (`grounding.v1`): against the product's own transcript, and against AMI's. "Bad" = unsupported or contradicted.

| Own transcript | AMI transcript | Class | Meaning |
|---|---|---|---|
| supported | supported | `ok` | |
| supported | **bad** | `transcript_propagated` | the note faithfully repeats a transcription error |
| supported | inferred | `borderline` | one notch apart: mostly checker noise |
| inferred | supported | `borderline` | the same, the other way round |
| bad | supported | `human_only` | the transcript lost it; the note has it anyway |
| bad | bad or inferred | `note_error` | the note-writer added it |
| inferred | bad or inferred | `inferred` | overreach, not a hard fact error |

The full own × AMI verdict matrix is saved too, so other class boundaries can be recomputed. `concentrate facts reclassify` re-applies the rules to saved verdicts at $0.

**Checker input (`stage2-input.v2`, logged as `render`).** Both checks get the same three changes:

| Change | Why |
|---|---|
| AMI speakers are labelled by role **plus the name they introduced themselves by** ("User Interface Designer, Agnes") | Granola's notes use first names, AMI labels by role. Names come only from AMI's own words: "my name is X", "I'm X", "I am X" (`facts.spoken_names`). Participant codes carry a name across a series (IS1008a's introductions label IS1008b–d). Found: 4 IS1008 speakers, 3 of 4 in ES2006a; none in IB4003 or EN2002b. |
| A `<speaker_labels>` note: the notes' labels ("Speaker 2", first names) are arbitrary; match people by what they said; check who said it only when the name is in the transcript's labels | Wispr's "Speaker N" cannot be mapped to anyone deterministically. Input only: the prompt file is unchanged. |
| Excerpt retrieval over runs of 2 adjacent lines, k = 16, no widening (was single lines, k = 8, ±1 line) | Better recall of the right lines at a similar excerpt size ([§4](#4-stage-2-first-run-and-what-it-got-wrong)). |

Each row also saves `own_ids` / `human_ids` (utterance IDs) beside `own_lines` / `human_lines`.

### Stage 3: answering (`concentrate.evals.wrong_facts`)

Input: a JSONL of `{question_id, product, answer}` for the 68 `recorded` questions (13 DEV). The shared judge (`judge.v1`) grades each answer. For every wrong answer:

- **In the transcript?** ($0) The question's gold evidence dialogue acts are anchored in the product transcript. An act counts as recovered if ≥ 50% of its content words and all its fact tokens are in the window. Present = at least half the acts recovered.
- **In the notes?** The grounding checker, with the notes as the "transcript" and the gold answer as the claim; `supported` = present. A $0 lexical recall is always recorded; `--lexical-notes` uses only it.

| Transcript had it | Notes had it | Stage |
|---|---|---|
| no | – | `transcription` |
| yes | no | `notes_or_answering` |
| yes | yes | `answering` |
| unknown (no evidence or no recording) | – | `unknown` |

## 4. Stage 2: first run and what it got wrong

First full run: 2026-11-05, `b68b0df`, Haiku 5.5, `grounding.v1`, $0.08, 7 meetings × 2 products, 387 claims.

**The headline was mostly noise.** It reported 20 `transcript_propagated` claims (granola 7, wispr 13). Re-read at $0:

| Of the 20 | n | What it was |
|---|---|---|
| own supported / AMI **inferred** | 13 | one-notch disagreement, now `borderline` |
| own supported / AMI contradicted, the checker's own reason is a label mismatch ("Speaker D, not Speaker 3") | 4 | label noise; needs the v2 re-run to clear |
| who was assigned the UI concept: AMI gives the line to another role | 2 | ambiguous |
| a real transcription error carried into the note | 1 | Wispr heard "Pierrette" as "Garrett"; the note repeats "Garrett" |

So about **1 of 20** is clearly real.

**Re-classified with the new rules ($0, same verdicts):**

| Class | granola (216) | wispr (171) |
|---|---|---|
| `ok` | 202 | 136 |
| `transcript_propagated` | 7 → **1** | 13 → **6** |
| `borderline` | **10** (6 + 4) | **18** (7 + 11) |
| `inferred` | 5 → 1 | 19 → 8 |
| `human_only` | 1 | 3 |
| `note_error` | 1 | 0 |

`borderline` (a + b) = own supported/AMI inferred + own inferred/AMI supported. The 7 left in `transcript_propagated` still include the 4 label cases and 2 ambiguous ones above: the v2 input should move them, but only a re-run can show it.

**"Irrelevant cited lines" were a lookup bug, not retrieval.** `own_lines` are positions in the stripped own transcript (Wispr's `<<<…>>>` banner and Granola's mic turns removed, then re-indexed). Read against the raw note they are off: Wispr is off by one, Granola by the number of mic turns before the line. Example: Wispr ES2006a cites own lines 67, 86, 87 (the interface designer describing her role); raw line 67 is "there's an eraser there". Also, all 4 own `contradicted` verdicts came from the full-transcript pass, which sees every line, so retrieval could not have caused them. Fix: rows now save utterance IDs too.

**Retrieval, measured anyway ($0).** For every claim the AMI check called supported, the "gold" is the content words (stemmed, stop words removed) of the AMI lines it cited. Recall = share of gold words found in the product-side excerpt for that claim. Hit = recall ≥ 50%. Chosen on DEV (IS1008a), then checked on the 6 held-out meetings:

| | Granola recall | hit | words/claim | Wispr recall | hit | words/claim |
|---|---|---|---|---|---|---|
| **DEV** v1: lines, k=8, ±1 | 0.784 | 0.917 | 245 | 0.894 | 1.000 | 557 |
| **DEV** v2: runs of 2, k=16 | **0.831** | **0.958** | 262 | **0.924** | 1.000 | 670 |
| held-out v1 | 0.803 | 0.929 | 196 | 0.907 | 0.985 | 481 |
| held-out v2 | **0.820** | **0.940** | 215 | **0.910** | **0.992** | 564 |

The gain is small: +1.7 points (Granola) and +0.3 (Wispr) held out, for 10–17% more words. Other variants (all meetings; paired bootstrap 95% CI on the change in recall, resampling claims, so it ignores clustering by meeting):

| Variant | Granola Δ | Wispr Δ | Note |
|---|---|---|---|
| runs of 2, k=16 (**kept**) | +0.021 [+0.010, +0.034] | +0.006 [+0.000, +0.013] | only variant up for both at a similar size |
| runs of 3, k=12 | +0.024 [+0.008, +0.040] | −0.008 [−0.022, +0.005] | hurts long Wispr turns |
| single lines, k=12 | +0.043 [+0.030, +0.058] | +0.015 [+0.009, +0.024] | ≈ +40% words |
| k grows with length (n/40) | +0.036 [+0.026, +0.049] | +0.009 [+0.004, +0.016] | no effect on DEV (short), so not chosen |

Retrieval was not the problem: v1 already got at least half the gold words for 93% (Granola) and 99% (Wispr) of claims, and anything the excerpt pass doesn't support is re-checked against the full transcript.

**Re-run quote** (`concentrate facts stage2 --quote`, offline): 14 notes, 387 claims, **$0.059 (no escalation) – $0.130 (worst)**. The first run quoted $0.126 worst and spent $0.08 (measured ÷ quoted = 0.83), so expect about **$0.11**. Every call misses the cache, because the input changed. Not run: it needs Kian's approval.

## 5. What's built

| Piece | Where |
|---|---|
| Fact extraction, anchoring, alignment, stage-1 scores | [`ami/facts.py`](../../packages/concentrate/ami/facts.py) |
| Stage 2 classes and runner; stage 3 judge and attribution; quotes | [`evals/wrong_facts.py`](../../packages/concentrate/evals/wrong_facts.py) |
| Spoken names from AMI self-introductions; role + name speaker labels | `ami/facts.py` `spoken_names`, `human_note(m, names)` |
| Run-of-lines retrieval and the `<speaker_labels>` input (both off by default) | `evals/grounding.py` `nearest_spans`, `labels_note` |
| Recording matcher: `AMI <id>` tags, a title that is just the bare ID, and pins in `data/ami/results/recording_ids.txt` | [`ami/products.py`](../../packages/concentrate/ami/products.py) `match_recordings` |
| Read-only DB reader (never creates or migrates a database) | `core/store.py` `read_notes` |
| CLI `concentrate facts stage1|stage2|reclassify|stage3` | `cli.py` |
| Tests: planted "Kian" insertion, misheard number, a claim the own transcript doesn't support, a claim propagated from a transcript error, `borderline` rules, offline reclassify, self-introduced names across a series, run retrieval, labels note | [`tests/test_wrong_facts.py`](../../tests/test_wrong_facts.py), [`tests/test_grounding.py`](../../tests/test_grounding.py) |

No new prompts: stage 2 reuses `grounding.v1` (the labels note is input, versioned as `render`), stage 3 reuses `judge.v1` and `grounding.v1`. With defaults the grounding checker is unchanged: the first run replays fully from cache. Results go to `data/ami/results/facts/` (gitignored: they can hold product text).

## 6. Cost

Quoted offline with Haiku 5.5 on 2026-11-05. Only IS1008a was synced, so the full-run figures scale IS1008a's claim counts to all six meetings.

| Run | Estimate |
|---|---|
| Stage 1 | **$0** |
| Stage 2 pilot (IS1008a, both products, 44 claims) | $0.006 – $0.013 |
| Stage 2 full (6 meetings × 2 products, extrapolated) | $0.04 – $0.10 |
| Stage 2 first full run (7 meetings, measured) | **$0.08** |
| Stage 2 re-run with `stage2-input.v2` | $0.059 – $0.130; expect ≈ $0.11 |
| Stage 3 full (136 answers; ASSUMED 80-token answers) | judge $0.012 + notes check ≤ $0.061 = **≤ $0.073** |

Every paid run needs `--max-usd` (pilots default to a $0.05 cap). After a pilot, the command prints measured ÷ quoted cost and re-prices the full run × 1.5.

## 7. Running it

```bash
uv run concentrate facts stage1                                # $0; lists recordings found
uv run concentrate facts stage2 --quote                        # $0
uv run concentrate facts stage2 --pilot                        # IS1008a only, cap $0.05
uv run concentrate facts stage2 --max-usd 0.25                 # after reviewing the pilot
uv run concentrate facts reclassify                            # $0: current class rules on saved verdicts
uv run concentrate facts stage3 --answers <answers.jsonl> --quote
uv run concentrate facts stage3 --answers <answers.jsonl> --pilot   # 3 seeded DEV answers
uv run concentrate facts stage3 --answers <answers.jsonl> --max-usd 0.15
```

## 8. Threats to validity

- **Spelling is not always a wrong fact.** AMI's annotators spelled names as they heard them. A product spelling a name differently counts as a substitution, and as a name insertion, even when it may be right. Read name substitutions with their examples.
- **The name rule is a heuristic.** It catches proper nouns, not only people, and misses names a speaker never says mid-sentence.
- **Alignment can misplace a fact** in dense overlapping speech. `unaligned` is reported apart; a substitution can absorb a neighbouring word.
- **The two stage-2 checks share one checker,** so their errors correlate. AMI's transcript also carries role names as speaker labels and the products' don't, which pushes role claims towards `human_only`. Checker accuracy on AMI: [GROUNDING_CHECKER.md](GROUNDING_CHECKER.md) (89% of planted errors caught; 18% of true sentences flagged).
- **Two checks, one notch apart, is noise.** The first run showed it: 13 of 20 `transcript_propagated` were supported vs inferred. `borderline` holds those; read it as "fine, probably", not as an error class.
- **The labels note trades attribution checking for fewer false alarms.** A claim saying "Speaker 3 said X" is now judged on X; a real mix-up of Wispr's speaker numbers can't be caught. Names AMI establishes (IS1008, ES2006a) can still be checked.
- **Self-introduced names are a narrow rule.** No names for IB4003 or EN2002b, and one ES2006a speaker never says theirs.
- **Stage 3 is approximate.** Transcript presence is lexical. The notes check uses the grounding checker off-label (notes as the transcript). `answering` assumes Chat read the notes or transcript.
- **Small n.** 6 meetings, 2 products, 68 questions (13 DEV). Report rates with CIs; a difference that isn't significant is a tie.
- **Source quirk.** Wispr's MCP transcript starts with a `<<<…>>>` safety banner that our Wispr source stores as an utterance. Stage 1–3 drop it; `ami wer` doesn't yet.
- **Setting.** 2005 role-play audio played in digitally; four speakers; no screen share.
