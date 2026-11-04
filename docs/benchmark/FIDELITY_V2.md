# Benchmark v2: realistic wording (generator `northwind-2`, 2026-11-02)

Workstream G. Follows [FIDELITY.md](FIDELITY.md), the audit of Northwind-XL v1 against the author's 4 real
Granola meetings. This doc covers three things: the extra reference data we found, what generator
`northwind-2` changes, and the metrics before and after.

**Where things are.**
- **XL v2:** written to `data/fixtures/large/northwind-xl-v2` (gitignored), with `concentrate synth --seed 42 --scale xl --out data/fixtures/large/northwind-xl-v2`.
- **XL v1:** stays untouched in `northwind-xl` (git tag `northwind-xl-v1`). Built memories and reported results use it. The CLI refuses to overwrite an XL directory that was made by a different generator version.
- **Base set:** the committed `data/fixtures/northwind` was regenerated (a test enforces this).

**Reproduce the metrics.** Run `uv run concentrate fidelity <fixture dir | data/real.db>`. It uses `concentrate.synth.fidelity` and prints aggregate numbers only, never any text. No LLM calls were made for anything in this doc.

## 1. Reference data beyond n = 4

The real data still comes from only 3 remote meetings and 1 in-person meeting, all from one owner. We added
two kinds of public reference. Each answers a different question:

- **Format (how Granola writes it down).** Granola's API docs and help centre, the two reverse-engineering
  write-ups, and **one publicly shared Granola note**. Its owner linked it from the Wonder Tools Granola guide; it summarises a workshop.
  We measured its embedded document:
  - 6 sections, all `###`;
  - 71 bullets, 30 top-level and 41 nested (at most 2 levels);
  - about 600 words;
  - every number written as digits, `$` or `%` (34 digit tokens, 0 number words).

  The API's example `summary_markdown` uses `##`, a line of prose and flat bullets, with money as bold `$` digits.
  We found **no public Granola transcript text**, because web shares hide the transcript.
- **Wording (how people talk in meetings).** We used 30 meetings from each of four sources: AMI and ICSI (via QMSum), QMSum's
  parliamentary committees, and MeetingBank city councils. They are **not Granola's ASR**:
  - AMI and ICSI are hand transcribed, spell every number as a word, and mark disfluencies with `{disfmarker}`-style tokens, which we stripped before measuring.
  - Their passages are speaker turns, not Granola passages.
  - The committee record is edited, so it has no fillers.
  - MeetingBank's text version has no turns, so we measured sentences.

  We use them for conversational vocabulary and variety (type/token ratio, n-gram duplication), not for passage shape or number format.

| Source | Licence | Used for |
|---|---|---|
| [QMSum](https://github.com/Yale-LILY/QMSum) (AMI "Product", ICSI "Academic", "Committee") | Repo MIT. AMI CC BY 4.0. ICSI via LDC (we used only QMSum's copy). Committee from Welsh/Canadian parliamentary records (Open Government Licence) | wording variety, filler ranges |
| [MeetingBank](https://huggingface.co/datasets/huuuyeah/meetingbank) (`test.json`) | CC BY-NC-SA 4.0 | numbers in ASR-style captions |
| [docs.granola.ai](https://docs.granola.ai) (intro, Get Note, Get Transcript, help centre) | public docs | transcript item shape, `summary_markdown` example |
| [Shared Granola note](https://notes.granola.ai/t/13658d33-1107-4bba-a18c-587603717d75-008umkv4) linked from [Wonder Tools](https://wondertools.substack.com/p/granolaguide) | public share by its owner | summary structure, number rendering (n = 1, a workshop, not a business meeting) |
| [getprobo/reverse-engineering-granola-api](https://github.com/getprobo/reverse-engineering-granola-api), [josephthacker.com](https://josephthacker.com/hacking/2025/05/08/reverse-engineering-granola-notes.html) | MIT / blog | notes are ProseMirror; no content |

Downloads were about 23 MB. They stayed in a session scratch directory and were neither committed nor quoted.

## 2. What `northwind-2` changes

| Change | Where | Calibrated on |
|---|---|---|
| **Compositional filler.** v1 drew from 69 fixed sentences. v2 fills about 60 templates from per-meeting vocabulary: 185 meeting-type noun phrases, the project and customer names of the meeting's facts, and states, observations, worries, actions, quantities, openers and tails. Short reactions (43) and meta remarks (27) are drawn from lists. Filler names projects and customers (protected from noise, so "Beacon Freight" is never split), but never people, dates, prices or owner-plus-deadline commitments | `synth/filler.py`, `synth/pad.py` | real dup6 0%, TTR@2k 0.23; AMI/ICSI TTR 0.23 |
| **Monologues.** 16% of non-reaction turns are 3–8 sentences, and turns over 40 words are kept whole 70% of the time | `filler.turn`, `noise.fragment` | real p90 38 words, 10% ≥ 38 |
| **Slot-scaled length.** Filler words = slot minutes × 120 × factor (XL 1.0, so 30 minutes ≈ 3,600 words; base 0.35) | `pad.WORDS_PER_SLOT_MINUTE`, `pipeline.PAD_FACTOR` | real remote median 3,555 words |
| **Noise:**<ul><li>um/uh, "you know" and "I mean" cut about 11×;</li><li>discourse "like" added;</li><li>repeats cut 2.5×;</li><li>**no hyphenated false starts**;</li><li>12% of remote passages lose their final period and 6% trail off with "..."</li></ul> | `noise.NoiseRates`, `noise.end_style` | real |
| **Digits.** For 75% of turns (`digit_share`), numbers are written the way ASR smart formatting writes them: `$25`, `40%`, `312K`, `October 14`, `the 14th`, `PAY 212`, `22 months`. Single words below ten stay words | `core/spoken.written` (idempotent; `spoken_numbers` reads both forms) | real digits 2.4/1k; shared note all digits |
| **Timing.** Speech at 3.0 words/s within a passage. Gaps are heavy-tailed: 55% back-to-back, 36% log-normal (median ~0.7 s), 9% pauses of 3–12 s | `render._gap` | real gaps p10/50/90 0 / 0 / 3.9 s |
| **Summaries.** 4–6 `###` sections. The fact sections are as before (still lossy: ~20% of facts dropped). Discussion sections hold two-level bullets that restate what the filler actually said (`filler.summary_points`), so they are faithful and fact-free | `render._discussion` | real 4.5 sections, 34 bullets (15 nested); shared note 6 × `###` |
| **Validation.** The gold check accepts an evidence key in its spoken or written form | `questions.validate` | — |

The gold invariant still holds: "all gold answers are stated in their gold utterances ✓" on both the base set and XL v2.
Question and fact semantics are unchanged; only wording, length and number format differ.

## 3. Before / after (remote meetings; aggregate only)

Real = the author's 3 remote meetings (702 passages). XL v1 = `northwind-xl`. XL v2 = `northwind-xl-v2` (184
meetings, 64k passages, 0.7M words). The public corpora are shown for wording only (see §1 caveats).
The bold targets are the ones the brief set.

| Metric | Real | XL v1 | **XL v2** | Base v2 | AMI | ICSI | MeetingBank |
|---|---|---|---|---|---|---|---|
| Words/passage p10/25/50/75/90 | 1/1/5/17/38 | 1/2/6/10/13 | **1/2/9/15/26** | 1/2/9/15/26 | 1/1/5/11/22 | 1/2/5/15/32 | 1/3/8/17/28 |
| Passages ≤5 / ≥38 words | .52 / .10 | .43 / .00 | **.42 / .05** | .42 / .05 | .55 / .04 | .51 / .08 | .39 / .05 |
| Words per meeting (median) | 3,555 | 1,339 | **3,778** | 1,343 | 5,192 | 10,213 | 2,042 |
| Transcript span, min (median) | 20 | 11 | **25** | 9 | — | — | — |
| Gap p10/50/90 (s) | 0/0/3.9 | .22/.68/1.14 | **0/.09/3.2** | 0/.09/3.1 | — | — | — |
| **TTR, 2k-token windows (target ≥ .18)** | .228 | .109 | **.210** ✓ | .224 | .232 | .233 | .313 |
| TTR, 5k windows | .156 | .057 | **.109** | .125 | .160 | .164 | .233 |
| Distinct trigrams, 5k windows | .92 | .40 | **.67** | .69 | .93 | .92 | .93 |
| **≥6-word passages exactly duplicated (target < 5%)** | 0% | 78% | **4.9%** ✓ | 2.5% | 0% | 0.1% | 0.5% |
| ≥6-word passages sharing their first 6 words | 0.6% | 91% | **54%** | 24% | 0.3% | 1% | 2% |
| um+uh / 100 words | .32 | 1.72 | **.39** | .46 | 3.97 | 2.73 | .02 |
| "you know" / "I mean" / 100 words | .08 / .10 | .78 / 1.02 | **.18 / .29** | .17 / .30 | .32 / .21 | .55 / .40 | .25 / .05 |
| "like" / "yeah" / "so" / 100 words | 6.0 / 2.9 / 1.5 | .8 / 1.4 / 1.5 | **1.8 / 1.8 / 1.3** | 1.8 / 1.7 / 1.4 | 1.0 / 3.3 / 1.7 | .6 / 1.9 / 1.7 | .3 / .2 / 1.0 |
| Immediate repeats / 100 words | 1.31 | 1.98 | **.80** | .80 | 2.17 | 2.22 | .62 |
| Hyphenated false starts / 100 words | 0 | .55 | **0** | 0 | 0 | 0 | 0 |
| Digits / number words per 1k words | 2.4 / 4.4 | 0 / 1.6 | **1.7 / 4.1** | 1.9 / 4.3 | 0* / 17 | 0* / 13 | 11 / 9.2 |
| Passage endings . / none / ? / , / ... | .75/.17/.05/.04/0 | .76/.03/.15/.06/0 | **.65/.20/.07/.04/.04** | .65/.19/.08/.04/.04 | .67/.20/.07/.07/0 | .66/.19/.09/.06/0 | .95/0/.05/0/0 |
| Passages containing "..." | 8.3% | 0% | **4.1%** | 4.3% | 0 | 0 | 0 |
| Summary words / sections / bullets / nested (median) | 422 / 4.5 / 34 / 15 | 15 / 1 / 2 / 0 | **268 / 5 / 26 / 10** | 200 / 5 / 18 / 4 | — | — | — |

\* AMI and ICSI spell numbers out by transcription convention. "yeah" also counts "yes" and "yep".

## 4. Honest read

**Fixed.**
- Templating, the biggest validity caveat, is gone at the level the brief targeted: exact duplication 78% → 4.9%, and TTR@2k .11 → .21.
  BM25 or embeddings can no longer find the needle simply because every other passage is the same 69 sentences.
- Length now matches real meetings (3.8k vs 3.6k words).
- Filler rates, false starts, digits, gap shape and summary structure are now in the real range.

**Still off. We report these rather than tune them away on n = 3.**
- **Template signatures.** 54% of long passages share their first six words with another passage (real: 0.6%). The
  frames ("I just want to make sure…", "I put some notes on the…") recur across 184 meetings. Distinct trigrams are
  .67 vs ~.92 for every real and public corpus, and TTR@5k is .11 vs .16. Exact-match retrieval is no longer easy, but a
  reader still sees a house style. This is the main argument for `--polish` (§5).
- **Long tail is still short.** p90 is 26 words vs 38 real, and 5% of passages reach 38 words vs 10%.
- **"like" density.** 1.8 vs 6.0 per 100 words. This is one speaker's idiolect: AMI is 1.0 and ICSI 0.6, so we stopped there.
- **Ellipses.** Real ones sit mid-passage; ours end passages.
- **Summaries are thinner.** 268 vs 422 words.
- **Span.** 25 min vs 20 min, because there are more, shorter passages (297 vs 236 per meeting).
- **Discussion bullets are generic.** They restate filler, so they read as plausible but generic ("Rollback plan is a
  little fragile").
- **Numbers in filler.** Filler now contains vague quantities ("like fifteen calls", "40 minutes"). These are realistic
  numeric distractors but carry no gold.

**Consequence for the benchmark.**
- **Not comparable with v1.** v2 is a new benchmark version, so numbers on v1 and v2 must not be compared as if
  they were one dataset.
- **Rebuild needed.** Memories and indexes built on XL v1 must be rebuilt on v2.
- **Cost.** v2 is about 3× more tokens per full-context or ingest run.
- **Numeric questions.** These may get harder, because about 25% of statements stay spelled out while most are digits.

## 5. Polish (LLM rewrite of filler), not run

`concentrate synth --seed 42 --scale xl --out data/fixtures/large/northwind-xl-v2 --estimate`:

| Model | Input tokens | Output tokens (incl. 30% thinking allowance) | Estimate |
|---|---|---|---|
| claude-opus-5-5 (default) | ~1.75M | ~2.40M | **≈ $55** |
| claude-sonnet-5-5 | same | same | ≈ $28 |
| claude-haiku-5-5 | same | same | ≈ $1.4 (at the repo's price table; verify before relying on it) |

Polish would mainly fix the template signatures above. That is over $2, so it is waiting for the user's decision.
Heuristic counts; the cache makes re-runs free.
