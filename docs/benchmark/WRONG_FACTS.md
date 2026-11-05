# Anatomy of a wrong fact

When a meeting product gets a fact wrong (a name, number, role, commitment or decision), at which stage did it go wrong? This doc says how we answer that on AMI meetings played into Granola and Wispr Flow. For engineers who want to run it or attack it.

**Status: built, not run.** Stage 1 is $0 and runs as soon as the AMI databases are synced. Stages 2 and 3 are priced and sandboxed; nothing paid has run.

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

Each claim in a product's notes (`split_claims`) goes through the [grounding checker](GROUNDING_CHECKER.md) twice, unchanged: against the product's own transcript, and against AMI's.

| Own transcript | AMI transcript | Class | Meaning |
|---|---|---|---|
| supported | supported | `ok` | |
| supported | not supported | `transcript_propagated` | the note faithfully repeats a transcription error |
| unsupported / contradicted | supported | `human_only` | the transcript lost it; the note has it anyway |
| unsupported / contradicted | not supported | `note_error` | the note-writer added it |
| inferred | any | `inferred` | overreach, not a hard fact error |

The full own × AMI verdict matrix is saved too, so other class boundaries can be recomputed.

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

## 3. What's built

| Piece | Where |
|---|---|
| Fact extraction, anchoring, alignment, stage-1 scores | [`ami/facts.py`](../../packages/concentrate/ami/facts.py) |
| Stage 2 classes and runner; stage 3 judge and attribution; quotes | [`evals/wrong_facts.py`](../../packages/concentrate/evals/wrong_facts.py) |
| Recording matcher: `AMI <id>` tags, a title that is just the bare ID, and pins in `data/ami/results/recording_ids.txt` | [`ami/products.py`](../../packages/concentrate/ami/products.py) `match_recordings` |
| Read-only DB reader (never creates or migrates a database) | `core/store.py` `read_notes` |
| CLI `concentrate facts stage1|stage2|stage3` | `cli.py` |
| Tests: planted "Kian" insertion, misheard number, a claim the own transcript doesn't support, a claim propagated from a transcript error | [`tests/test_wrong_facts.py`](../../tests/test_wrong_facts.py) |

No new prompts: stage 2 reuses `grounding.v1`, stage 3 reuses `judge.v1` and `grounding.v1`. Results go to `data/ami/results/facts/` (gitignored: they can hold product text).

## 4. Cost

Quoted offline with Haiku 5.5 on 2026-11-05. Only IS1008a was synced, so the full-run figures scale IS1008a's claim counts to all six meetings.

| Run | Estimate |
|---|---|
| Stage 1 | **$0** |
| Stage 2 pilot (IS1008a, both products, 44 claims) | $0.006 – $0.013 |
| Stage 2 full (6 meetings × 2 products, extrapolated) | $0.04 – $0.10 |
| Stage 3 full (136 answers; ASSUMED 80-token answers) | judge $0.012 + notes check ≤ $0.061 = **≤ $0.073** |

Every paid run needs `--max-usd` (pilots default to a $0.05 cap). After a pilot, the command prints measured ÷ quoted cost and re-prices the full run × 1.5.

## 5. Running it

```bash
uv run concentrate facts stage1                                # $0; lists recordings found
uv run concentrate facts stage2 --quote                        # $0
uv run concentrate facts stage2 --pilot                        # IS1008a only, cap $0.05
uv run concentrate facts stage2 --max-usd 0.25                 # after reviewing the pilot
uv run concentrate facts stage3 --answers <answers.jsonl> --quote
uv run concentrate facts stage3 --answers <answers.jsonl> --pilot   # 3 seeded DEV answers
uv run concentrate facts stage3 --answers <answers.jsonl> --max-usd 0.15
```

## 6. Threats to validity

- **Spelling is not always a wrong fact.** AMI's annotators spelled names as they heard them. A product spelling a name differently counts as a substitution, and as a name insertion, even when it may be right. Read name substitutions with their examples.
- **The name rule is a heuristic.** It catches proper nouns, not only people, and misses names a speaker never says mid-sentence.
- **Alignment can misplace a fact** in dense overlapping speech. `unaligned` is reported apart; a substitution can absorb a neighbouring word.
- **The two stage-2 checks share one checker,** so their errors correlate. AMI's transcript also carries role names as speaker labels and the products' don't, which pushes role claims towards `human_only`. Checker accuracy on AMI: [GROUNDING_CHECKER.md](GROUNDING_CHECKER.md) (89% of planted errors caught; 18% of true sentences flagged).
- **Stage 3 is approximate.** Transcript presence is lexical. The notes check uses the grounding checker off-label (notes as the transcript). `answering` assumes Chat read the notes or transcript.
- **Small n.** 6 meetings, 2 products, 68 questions (13 DEV). Report rates with CIs; a difference that isn't significant is a tie.
- **Source quirk.** Wispr's MCP transcript starts with a `<<<…>>>` safety banner that our Wispr source stores as an utterance. Stage 1–3 drop it; `ami wer` doesn't yet.
- **Setting.** 2005 role-play audio played in digitally; four speakers; no screen share.
