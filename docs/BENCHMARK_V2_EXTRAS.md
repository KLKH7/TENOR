# Benchmark v2.1: harder phenomena (generator `northwind-2.1`, 2026-11-02)

Workstream P. v2 ([FIDELITY_V2.md](FIDELITY_V2.md)) fixed *wording*. v2.1 adds five *situations* that real
meeting memory has to survive and that v1/v2 under-test: hybrid rooms, identity mess, corrections,
implicit or out-of-band commitments, and long-horizon questions. No LLM calls were made for anything
in this document ($0).

**Where things are.**
- **Generate:** `uv run concentrate synth --seed 42 --extras [--scale xl] --out <dir>`. XL v2.1 is in
  `data/fixtures/large/northwind-xl-v2.1` (gitignored). The CLI refuses to overwrite a directory made by
  another generator version.
- **Code:** `synth/extras.py` holds the story and the questions. `synth/extras_checks.py` holds the free
  probes. Tests are in `tests/test_extras.py`.
- **Opt-in.** Without `--extras` the generator is still `northwind-2`, and its output is byte-identical,
  including the committed base fixture.
- **v2 inside v2.1 is untouched.** With `--extras`, every v2 note, question ID, answer and split is
  byte-identical (tested). v2.1 = v2 + 10 meetings + 26 questions with IDs `x001`–`x026`. How:
  - extras meetings use the key prefix `v21-`;
  - they pad from their own RNG stream;
  - their facts are hidden from the v2 question generators (`extras.base_view`);
  - their questions get their own seeded split and are appended after the stratified XL selection.
- **Comparability.** v2.1 scores on the 296 v2 questions are directly comparable with v2. Report the 26
  extras questions as separate categories. The 10 extra meetings add ~40k words (+5%) of distractor text
  for v2 questions, so expect at most a small shift.

## The phenomena

All ten meetings are about a new project, **Halyard** (the partner API, weeks 1–8), plus Corvid Bank,
which already exists in v1. Every question is computed from the world model and validated: each evidence
key must occur in the gold utterances.

| # | Phenomenon | How it is rendered | Questions (dev/test) | What it tests that v2 does not |
|---|---|---|---|---|
| 1 | **Hybrid room** | Dev, Rosa and Felix sit in "Room 4B", which joins 4 Halyard calls as one `speaker` (system-audio) channel. When Granola names that channel, it says `Room 4B`, never a person. The room is also a calendar resource in `invitees`. Only conversational cues say who spoke: "Dev, what date…?" → answer, or "Felix here". Right after Felix proposes a slip, Jordan (remote and named) says "Yeah, agreed, push it", as a trap | `room_attribution` 6 (2/4) | Attribution from **discourse** rather than channel or label. In v2, "who said" is answered by the label 40% of the time; here, 0% |
| 2a | **Name change** | Rosa Delgado becomes Rosa Whitfield on 2026-09-28: new surname and new email (`rosa.d@` → `rosa.w@`). Invites and labels show the name as of each meeting. The directory shows only "Rosa Whitfield". One beat links the two names | `identity_resolution` 2 | Time-scoped identity: questions use today's name, while the evidence carries the old one |
| 2b | **Nickname** | "AK" is used for Alex Kim, and only as "AK". There are still two Alexes | 2 | Nickname → initials → directory, with a same-first-name distractor |
| 2c | **Contractor** | Jamie Okonkwo has a personal Gmail address. Jamie is not in the directory, has no display name on invites, and so is never named by Granola. Jamie is introduced once in speech | 1 | Entities that exist only in the transcript |
| 2d | **Namesakes** | Corvid Bank has two "Daniel Cho"s, a Compliance Analyst and a Procurement Manager. Both are labelled "Daniel Cho". They are told apart only by "Daniel from compliance / from procurement" | 2 | The same full name for two entities; the label is right but not identifying |
| 3 | **Corrections** | (a) In the same meeting: "twelve endpoints" … later "correction… it's nine". (b) Across meetings: "Ignore what I said last week, the number is actually 340 pilot users" (was 400). (c) By someone else: Jordan says "November fifth"; Felix replies "No, it's still October twenty ninth". Wrong values are recorded as retracted facts in `world.extras.retracted` | `correction` 4 (1/3) | Supersession by **retraction**, not by change. The wrong value was never true, yet it appears in the transcript, and 1–3 of the 3 also appear in AI summaries (§Probes) |
| 4 | **Implicit / silent / out-of-band commitments** | Implicit commitments ("I can look into…", "Let me see if legal can…", "I could probably mock up…"); two are never mentioned again. Three are resolved outside meetings ("as discussed on Slack, it's done", "sent it over email yesterday") and count as **done** | `commitment_tracking` 6 (2/4) | Detecting commitments without "I'll X by Friday". Silence ≠ done. An out-of-band report closes the loop even though the work happened outside the corpus |
| 5 | **Long horizon** | The Halyard launch date moves Oct 1 → Oct 15 (sandbox failed load testing, w4) → Oct 29 (unsigned webhooks, w6) → Nov 12 (docs not ready, w8): **3 moves** in 7 weeks. Jordan's "November 5" in week 7 is a misstatement and **not** a move. Rosa's commitments span w1–w6, across her name change | `long_horizon` 3 (1/2) | Aggregation over 4+ meetings 7 weeks apart, while ignoring a retracted value; it composes 1, 2a and 3 |

## Probes: free and deterministic (`python -m concentrate.synth.extras_checks <dir>`)

These are cheap stand-ins for failure modes, used to check that the extras are hard for the intended
reason before any money is spent. Numbers are on the XL v2.1 set; the base set (`--extras`, 50 meetings)
gives the same picture.

| Probe | Result | Reading |
|---|---|---|
| BM25 meeting recall@5 (all gold meetings in top 5) | room_attribution **1.00**, identity 1.00, correction 1.00, commitment_tracking 0.67, long_horizon 1.00; v2 categories 0.45–0.97 | The extras are **findable**. "Halyard" is a distinctive token, so their difficulty is reasoning, not search. This is a confound to keep in mind (see limitations) |
| "Who said it?" read off the speaker label of the gold passages | room_attribution **0.00** (n = 6), identity namesakes/contractor 0.00 (n = 2); v2 who-said 0.40 (n = 5) | The label is never sufficient in a room. A system that trusts `name` gets "Room 4B" or Jordan |
| Retracted values still visible | each of the 3 misstatements: 1 transcript passage; AI summaries repeat 3 of 3 on XL (2 of 3 on base) | A summary-only system reads the wrong value as a summary bullet ("Partner sandbox has 12 endpoints"). Summaries are lossy and order-blind, which is realistic |
| Names heard on calls that resolve to exactly one directory entry | unique 39, none 2 (`Room 4B`, `Rosa Delgado`), ambiguous 1 (`Daniel Cho`); 2 attendee emails not in the directory (old Rosa email, Jamie's Gmail) | Directory-driven linking breaks exactly where intended |

**Realism did not regress.** `concentrate fidelity`, aggregate only, XL v2 → XL v2.1:
- remote words/passage p10–90: 1/2/9/15/26 → 1/2/9/15/25;
- words/meeting median: 3,778 → 3,781;
- TTR@2k: .209 → .209;
- exact dup6: 4.9% → 5.0%;
- first-6-word dup: 54.2% → 55.0%;
- um+uh: .39 → .39 per 100 words;
- digits: 1.68 → 1.70 per 1k;
- gap p90: 3.22 → 3.21 s;
- summary words: 268 → 267.

Base: TTR@2k .224 → .226, exact dup6 2.5% → 2.1%, first-6 24% → 26%.
The extras' scripted beats are short and conversational, and their filler is the v2 generator, so
style stays in range. The small rise in shared first-6 words comes from more filler drawn from the same frames.

## Which systems should fail what (predictions, not yet measured)

| | Top-k hybrid | Summaries only | Full context | Granola-style agent | Mem0 / Graphiti | Concentrate |
|---|---|---|---|---|---|---|
| Room attribution | ✗ chunks keep "Them (Room 4B)" but the cue is often in the *previous* passage, outside the chunk | ✗ summaries carry no speakers | ✓ if the model reads cues | ~ reads neighbouring chunks | ✗ ingest attributes facts to the channel label | ✗/~ the speaker resolver maps a channel to one person; a room breaks that assumption |
| Name change / nickname | ~ | ~ | ✓ | ~ | ✗ separate entities for Delgado and Whitfield | ✗ directory linking fails on "Rosa Delgado" and "AK" unless the alias beat is extracted |
| Namesakes | ✗ | ✗ | ~ | ~ | ✗ merges the two Daniels | ✗ merges them by name |
| Corrections | ~ latest chunk wins if retrieved | ✗ summary repeats the wrong value | ✓ | ~ | ✗ stores both, often returns the first | ~ the bitemporal store handles supersession, not "never true" |
| Implicit / out-of-band commitments | ✗ "dropped" needs proof of absence | ✗ | ~ | ✗ | ✗ extraction keys on "I'll … by" | ~ depends on the extraction prompt |
| Long horizon (count moves) | ✗ needs 4 meetings and k is small | ✗ lossy, and includes the misstatement | ✓ if it ignores Nov 5 | ~ | ~ | ✓ if retractions are modelled, else 4 (wrong) |

## Limitations (honest read)

- **Small n.** There are 26 questions: 3–7 per category. Category accuracy CIs will be ±20–40 points.
  Use these questions as **diagnostics with examples**, not a leaderboard. Scaling up means turning the
  hand-authored story into a seeded family; that is not done.
- **One storyline.** All phenomena hang off Halyard and Corvid, so a system that does well on Halyard
  is partly being rewarded for one project. Lexical retrieval finds the meetings easily (recall@5 = 1.0
  for most categories): this isolates reasoning, but the real world mixes both difficulties.
- **Room labelling is ASSUMED** (`SCHEMA_NOTES.md`). We don't know whether Granola names a room endpoint
  at all. If it doesn't (bare `System audio`), attribution is equally hard; if it names one in-room
  invitee, it is *harder*, because the label would be confidently wrong.
- **Out-of-band resolution is taken at the speaker's word.** "As discussed on Slack, it's done" counts
  as done. We do not model a false out-of-band claim.
- **Corrections are always explicit** ("sorry", "ignore what I said"). Silent corrections, where a
  different number is simply stated later without comment, are what v1/v2 `latest_state` already tests.
- **Retracted facts appear in summaries.** That's deliberate, but it means summary-only systems lose these
  questions by construction.
- **Not polished.** The scripted lines are template-quality, like v2's fact beats.

## Decisions for the coordinator

1. Should `--extras` be the default for v2.x runs? Recommendation: keep it **opt-in**. It costs nothing to
   keep, and the committed base fixture and every pinned test value stay stable while other workstreams
   merge.
2. Should the extras go in the headline comparison? Recommendation: **no, only as a diagnostic appendix**
   (26 questions; per-category n is too small). Run them only if a v2 run happens anyway: the extra
   ingest is about 5% of corpus tokens.
