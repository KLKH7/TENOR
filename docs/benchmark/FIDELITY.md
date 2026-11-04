# Synthetic-transcript fidelity audit (2026-11-02)

**What was compared.** The real corpus is the author's **4 Granola meetings** (`data/real.db`, synced via
`GranolaMCPSource`; 881 passages: 3 remote meetings with 702 passages, 1 in-person meeting with 179).
The synthetic corpus is **Northwind-XL** (184 meetings, 32,832 passages: 173 remote, 11 in-person;
`polish` disabled). The 40-meeting base corpus behaves like XL on every per-utterance metric
(see the notes below the table). A meeting counts as in-person when every passage is on the microphone channel.

**Privacy rule.** Real notes were read only to compute aggregate numbers. This file contains no real text,
names, titles, emails or quotes. All quoted text below is synthetic. No LLM or network calls were made.

**Caveat.** n = 4 real meetings from a single owner, and only one of them is in-person. Real percentiles are
pooled over 702 (remote) and 179 (in-person) passages, so they are indicative, not population estimates.
The in-person row is effectively n = 1. Speaker mix, filler habits and summary style may reflect this one
person and template, not Granola in general.

## Metrics: real vs XL

| Metric | Real | XL synthetic | Verdict |
|---|---|---|---|
| Remote words/passage p10/p25/med/p75/p90 | 1 / 1 / 5 / 17 / 38 | 1 / 2 / 6 / 10 / 13 | **off** (no long tail) |
| Remote passages ≤5 words / ≥38 words | 52% / 10.1% | 43% / 0.0% | **off** |
| Remote 1-word passages | 27% | 20% | close-ish |
| In-person words/passage p10/p25/med/p75/p90 | 4 / 10 / 25 / 69 / 139 | 8 / 10 / 18 / 52 / 91 | close-ish (n = 1) |
| Passages per meeting (remote, median) | 236 | 192 | close |
| Passages per meeting (in-person) | 179 | 28 (median) | **off** (n = 1) |
| Words per meeting (remote, median) | 3,544 | 1,336 | **off** (≈2.7× short) |
| Words per meeting (in-person) | 8,836 | 944 | **off** (n = 1) |
| Transcript span (remote, median) | 20 min | 10.7 min (calendar slot 30) | **off** |
| Speech rate, words per transcript-minute | ~165 | ~124 | off |
| Within-passage rate (≥5-word passages), words/s | 3.0 | 2.6 | close-ish |
| Inter-passage gap p10/p50/p90 (s) | 0.0 / 0.07 / 4.0 | 0.22 / 0.68 / 1.14 | off (too regular) |
| Share of remote passages that are "Me" | 0.61 | 0.47 | can't tell (owner-dependent) |
| "Them" passages carrying a name | 0.82 | 0.79 | close |
| Distinct speaker names per remote meeting (median) | 3 | 3 | close |
| Transcript speaker names absent from attendee names | 10 (across 4 meetings) | 123 (across 184) | close in kind |
| Fillers per 100 words: um+uh | 0.21 | 1.71 | **off** (8× too many) |
| "you know" / "I mean" per 100 words | 0.15 / 0.11 | 0.79 / 1.00 | **off** (5–9×) |
| "like" / "yeah" / "so" per 100 words (all senses) | 5.6 / 2.6 / 1.2 | 0.8 / 1.2 / 1.5 | **off** (real is far chattier) |
| Immediate word repetitions ("the the") per 100 words | 0.33 | 1.05 | off (3×) |
| False starts ("wo- word") per 100 words | 0.00 | 0.55 | **off** (never seen in real) |
| Type/token ratio, 2,000-token windows | 0.231 | 0.110 | **off** |
| Type/token ratio, 5,000-token windows | 0.160 | 0.059 | **off** |
| Distinct-trigram ratio, 5,000-token windows | 0.92 | 0.40 | **off** |
| Passages of ≥6 words that exactly duplicate another passage | 0.0% | 77.6% | **off** (templating) |
| Passages of ≥6 words sharing their first 6 words with another | 0.4% | 90.8% | **off** |
| Passage endings: . / none / ? / , | .74 / .14 / .08 / .03 | .76 / .03 / .15 / .06 | off on "none" (remote real: 17%) |
| Passages containing "..." | 6.6% | 0% | off |
| Numbers per 1k words: digits / number words | 4.2 / 4.4 | 0.00 / 1.5 | **off** (real ASR writes digits; also % and $) |
| Summary length (words, median) | 422 (331–513) | 14.5 (6–110) | **off** (≈30×) |
| Summary sections / bullets (median) | 4.5 / 34, with nested bullets | 1 / 2, flat; 24 notes use the fallback "General discussion" | **off** |
| Summary heading level | `#` | `###` | can't tell (real came via MCP, see SCHEMA_NOTES) |
| Attendees per meeting (median) | 3 (1–5) | 4 (0–25) | close |
| Attendees without a real display name | 5/12 show an email handle as the name | 155/773 `name: null` | close in kind (different surface form) |
| Diarization labels | 0/4 | 1/184 | close |

**Notes.** The base-40 corpus matches XL per utterance (the same p90 of 13, fillers 6.9/100 words, digits 0). Its templating is
milder (≥6-word exact duplicates 50%) and its meetings are shorter (median 531 words).
The "like/so/yeah" counts include non-filler uses in both corpora, so compare their magnitudes, not their exact values.

## Root cause of the biggest gap: XL is ~96% template filler

`world.json` holds 21,944 beats, of which 21,046 are pad filler (97% of words). They are drawn from **69 fixed
sentences** (`pad.py:25-109`), and XL pads at `PAD_FACTOR["xl"] = 2.5` (`pipeline.py:78`). The median meeting has
**5 non-filler beats** out of 125. Only 588 distinct beat texts exist. The top 5-grams each occur about 650–700 times:

> "i hadn't thought about it" (699) · "so where did we land" (691) · "sorry can you say that" (680) ·
> "let me grab a charger" (675) · "i'm not sure that's the" (670) · "can you share your screen" (651)

**Consequence for the benchmark.** Fact-bearing passages are lexically unique, set against hay that is identical in every
meeting. Top-k retrieval (BM25 or embeddings) therefore finds the needle far more easily than on real transcripts, which
have no exact duplicates of 6+ words and a 2× higher TTR. This *favours the retrieval baseline*. PROBE_2026-11-02 claimed
that synthetic data makes any Concentrate advantage conservative, and that holds for transcript length but not for distractor realism.

## Artifacts (generator bugs). In each case, 0 occurrences in real

| # | Artifact | XL | Base-40 | Code | Minimal fix |
|---|---|---|---|---|---|
| a | Filler glued to the preceding word: "Realistic Falcon cutover is October eighthI mean, , after…", "Yep, Meridian is mineum, ." | **11 passages** (11 meetings); glued after Friday ×3, Thursday, eighth, Atlas, risk, late… | 2 | `noise.py:72-73` with `noise.py:86` | A protected span is followed by an unprotected chunk that starts with punctuation (`". Next"`). `chunk.split(" ")` yields `"."` as a word, a filler is inserted before it, and `"".join` glues the chunk to the span. Fix: change `if w and rng.random()…` to `if core and …` (no fillers before punctuation-only tokens). |
| b | Filler before bare punctuation ("like, .", "I mean, , right?") | 9 (all also glued) | 2 | same | Same one-line fix. |
| c | Self-referential tasks ("I'll take the growth plan draft for Elena", said by Elena; "The onboarding doc review for Hana slipped", said by Hana) | **61 beats in 40 meetings**; 40/108 commitments; 30 summary bullets of the form "Elena to … for Elena" | 0 | `extend.py:679` (task = `f"{t} for {s.first(pid)}"`), spoken via `extend.py:66,71,100` | Keep the "for X" suffix in `task` (for uniqueness in gold/questions) but pass a separate `task_spoken` without it. Or, when the speaker is the beneficiary, say "my growth plan draft". |
| d | The standup runner addresses himself: Alex Kim asks "Alex, anything to report?" / "Alex, what's on your plate?" and then answers; he also reassigns his own task in the third person ("Alex is heads down…, so Kofi…") | 3 beats | 3 | `scenario.py:128`, `:180` (prompt), `:158-162` (reassign) | Skip the prompt when `runner == c.owner` / `runner == owner`; use a first-person line for a self-reassign. |
| e | Summary fallback "General discussion; no decisions recorded." | 24 notes | — | `render.py:150-151` | Generate topic bullets from the meeting's filler or beat themes instead of a constant. |
| f | Transcript covers ~⅓ of the calendar slot (10.7 min of 30) | all | all | `render.py:260,287` (2.6 words/s plus gaps) with low turn counts | Scale the turn count to `meeting.minutes`, or stretch timestamps. |

Other oddities: hyphenated false starts ("wo- word", `noise.py:87-90`) never appear in real Granola output. All numbers are
spelled out (`core/spoken.py`), but real Granola emits digits, %, $ and ellipses. Backchannels are 13.7% of XL passages, drawn from 8 strings
(`noise.py:142`).

## What would make it closer to Granola (cheapest high-impact first)

**Fix now, in a v2 corpus.** Regenerating XL is free (`polish` is off), but it **invalidates any built Mem0/Graphiti/Concentrate
indexes and cached LLM answers over XL**. Batch all of these fixes into one `northwind-xl-v2` and keep v1 for results already reported.
Base-40 regeneration also changes a test-enforced fixture.

1. **Artifacts a–d.** These are four one-to-three-line fixes and change no gold semantics except in (c). They are a visible
   credibility cost if a reviewer reads a transcript.
2. **Noise rates:** cut um/uh, "you know" and "I mean" ~5–8× (`noise.py:22,30`), drop hyphen false starts (`false_start_per_turn=0`), and halve
   repeats. Optionally add "like"/"yeah" as fillers at real rates. This is cheap and changes no gold.
3. **Digits in ASR output.** Render a configurable share of numbers, money and percentages as digits ("$25", "38%"). Validators already
   normalise spoken numbers (`core/spoken.py`); they would need to accept both forms. This changes question difficulty for numeric facts.
4. **Break the templating.** Grow the filler pool from 69 to many hundreds of sentences, compose filler from slot-filled fragments (project,
   customer and meeting-type vocabulary), or turn `polish` on for XL (this costs LLM money: estimate first, per CLAUDE.md). Target ≥6-word
   exact duplicates under 5% and TTR@2k above 0.18. **This has the highest impact on retrieval realism.**
5. **Length and long tail.** Draw a "monologue" turn type (40–150 words, multi-sentence) for ~10% of remote passages. Scale the beat count to
   the slot length so that remote meetings reach ~3–4k words and ~20+ minutes. The cost is ~2.5× more tokens per build/eval.
6. **Timing.** Use heavier-tailed gaps (most ~0 s, some 4 s+) and ~3 words/s.
7. **Summaries.** Make them richer (4–6 sections, ~30 bullets, nested, ~400 words). Only do this after confirming the REST API
   `summary_markdown` shape matches the MCP one (add an ASSUMED row to SCHEMA_NOTES first).

**Document as known limitations (don't chase them with n = 4).** These are the Me/Them mix (owner-dependent), the in-person shape
(n = 1), "like/yeah" density (a speaker idiolect), the summary heading level, and any metric where a real 95% interval would span
the synthetic value. Re-run this audit (`uv run python` over `NoteStore("data/real.db")`, aggregate-only) once ≥20 real meetings exist.
