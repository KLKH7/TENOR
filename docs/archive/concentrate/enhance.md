# Stage 1, Enhance: speaker attribution and entity linking (no LLM)

Status: **built and evaluated** (2026-11-02). Code: `packages/concentrate/enhance/`. Tests:
`tests/test_enhance.py`. Evaluation: `uv run python -m concentrate.enhance.evaluate <fixture dir>`.
No LLM calls and no API spend: everything below is deterministic string and probability work.

## What it does

`enhance(note, directory, history)` turns one `MeetingNote` into an `EnhancedNote`:

```python
class SpeakerResolution(BaseModel):   # frozen; one per utterance
    person_id: str | None   # directory id; None when below the confidence threshold
    confidence: float       # 0..1 (the winning candidate's probability)
    method: str             # owner_mic | platform_name | addressed | turn_continuity |
                            # self_reference | diarization_propagation | attendee_prior | unresolved
    candidates: list[str]   # ids considered, best first (always filled when guessing)

class MentionLink(BaseModel):         # frozen
    utterance_id: str; start: int; end: int; text: str
    kind: Literal["person", "org", "project"]
    entity_id: str | None; confidence: float; reason: str

class EnhancedNote(BaseModel):
    note_id: str
    speakers: dict[str, SpeakerResolution]   # keyed by utterance_id
    mentions: list[MentionLink]
    attendee_ids: list[str]                  # owner + attendees, incl. email-only attendees
    labels: dict[str, str] = {}              # additive: id -> "Sam Patel (Backend Engineer)"

enhanced_line(u, e)  # "[not_x:0012] Sam Patel (Backend Engineer): ..."
                     # "[not_x:0013] Them (? Sam Patel|Sam Okafor): ..."
```

`labels` is the one addition to the agreed shape: it lets `enhanced_line(u, e)` print names and
roles without a directory argument. It defaults to `{}`, so code built against the original shape
still works. `enhance_all(notes, directory)` processes notes chronologically and feeds each one the
earlier notes as history (incrementally, so XL's 184 meetings take about 3 s).

**Boundary.** `enhance` reads only the note, `directory.json` (people, orgs, projects with aliases)
and earlier notes. It never imports `concentrate.synth`, never reads `world.json`, and never sees
`asr_variants`. A test parses the package's source to enforce this. Only `evaluate.py` touches
ground truth.

## Method

### Who is in the room
- **Attendees → directory ids** by email first, so `{"name": null, "email": "kofi.m@…"}` resolves
  to Kofi: that's the live cross-meeting miss from the probe. Then exact or near-exact display
  names. The owner is always included.
- **Platform names** (`System audio (Sam Patel)`) map to the directory by folded exact full name,
  or Jaro-Winkler ≥ 0.9 with a clear margin.
- **No calendar event** (one base meeting, "Sam / Priya"): people named in the title become
  candidates. An ambiguous title name nominates every match, so both Sams. After that come people
  who attended earlier meetings of the same series.

### Speaker attribution
Every candidate gets a log-linear score: a prior plus cue weights in log-odds. A softmax turns the
scores into probabilities, and the winner's probability is the confidence. Below
`MIN_CONFIDENCE = 0.6` the line stays unresolved and keeps a best-first candidate list.

| Signal | Where | How |
|---|---|---|
| Owner on the microphone | remote | every `microphone` line is the owner (0.97) |
| Platform display name | remote | `speaker_name` → directory (0.99) |
| Attendee prior | remote, unnamed lines | each identified speaker is expected to leave ~12% of their lines unnamed; the remaining unnamed lines go to attendees Granola *never* named, weighted by a gentle talkativeness prior from history (shrunk to the population mean, square-rooted) |
| Turn continuity | remote | consecutive system-audio lines with no real owner turn between them form a chain. Adjacent lines prefer the same speaker with odds measured on base: a continued sentence (no final punctuation, or lowercase start) 6.5:1, a one-word lead-in ("So.") 25:1, otherwise 1:1, scaled by the effective number of speakers. Solved exactly by forward-backward |
| Crosstalk | remote | a ≤3-word system-audio line that overlaps the owner's ongoing passage ("Yeah.") isn't chained: anyone can say it |
| Addressing | both | "Kofi, what's on your plate?" (a vocative: the name at the start of a sentence followed by a comma, or ", Kofi?" at the end) adds +2.5 for Kofi on the next system-audio turn (decaying), and −1.5 for Kofi on the addressing line itself |
| Third-person mention | both | naming X ("Ruth is ready to own…") is weak evidence the speaker isn't X (−0.5) |
| Self-reference | both | "this is Owen", "Owen here", "I'm Owen" (+4) |
| Owner prior | in person | +0.4 for the owner: it's the owner's device |
| Diarization propagation | in person + iOS labels | one person per `Speaker A/B` cluster, chosen as the best *joint* assignment (distinct people) from the cues summed over the cluster; re-scored until stable; confidence from the margin over the runner-up assignment |
| Co-occurrence prior | mentions, title candidates | how often a candidate attended with this meeting's attendees |

Weights are hand-set or measured on the **base** set only. XL is the generalization check, though it
comes from the same generator (see Risks).

### Mention linking
1. **Glossary phrases**, longest match first: full names, org names, project names and aliases with
   a leading "the" made optional. Single-word names must be capitalized ("Falcon", not "falcon").
   Unambiguous short org names ("Corvid") are added automatically. Colliding ones ("Beacon" vs
   project Beacon, "Harbor" vs project Harbor in XL) are not.
2. **Spoken ticket codes**: an alias like `PAY-212` compiles to a pattern for the prefix followed
   by digits or number words. "PAY two twelve", "pay 212" and "PAY-212" all parse, via
   `core.spoken.spoken_numbers`, to 212 → Falcon.
3. **First names**, exact or fuzzy. A fuzzy match needs `name_similarity ≥ 0.82` (Jaro-Winkler,
   lifted when the words also sound alike under Double Metaphone). It also needs either a word this
   note never uses in lowercase or a name-like context: a vocative, a possessive, or a following
   verb ("Joseph found…"). With a name-like context, sounding alike at ≥ 0.70 is enough. Never
   matched:
   - discourse words ("Sorry", "Right");
   - contractions ("We'll");
   - words of org/project names ("Teams");
   - a first name followed by an unknown surname ("Lucy Park" is a hiring candidate, not a
     misheard Luis).
4. **Shared first names** ("Sam") are ranked by meeting attendance (×8) and co-attendance history.
   Below 0.55 the link keeps `entity_id=None` and lists the candidates in `reason`.
5. **Overlaps:** the longest span wins, so "Beacon Freight" (customer) beats "Beacon" (project).

## The CS ideas, in plain terms

**Jaro-Winkler similarity.** Two strings are compared by counting the characters they share
*near the same position* (within half the longer word's length), minus half the shared characters
that appear in a different order (transpositions). That gives the Jaro score, between 0 and 1.
Winkler's tweak adds a bonus for up to four identical leading characters, because people (and ASR)
get the start of a name right more often than the end. "Meghan" vs "Megan" scores 0.96. "Coffee" vs
"Kofi" scores only 0.61: the spelling is too different, even though it *sounds* the same.

**Double Metaphone.** Instead of comparing letters, reduce each word to a rough code of its
consonant *sounds*. Vowels are dropped (except a leading one, which becomes "A"). Doubled letters
collapse. Silent letters vanish ("KN" → N). Spellings that sound alike merge: "PH" → F, soft "C" →
S, "CK" → K. "Coffee" and "Kofi" both become **KF**. "Double" means a word can have a second code
for an alternative pronunciation: "Thomas" is `0MS` ("th" as in *thin*) *or* `TMS`, and the second
matches "Tomás". "Joseph" can start with a vowel sound, which matches "Yusuf". We implemented the
subset of the rules that matters for names (~100 lines) instead of adding an untyped dependency.

**Label propagation, and why we use forward-backward.** Some lines are known (named, or the
owner's microphone) and some aren't. Propagation spreads labels from known lines to unknown
neighbours: a fragment that continues Sam's sentence is probably Sam's.

The naive version, "relabel every line from its neighbours' current guesses, repeat", has a
known flaw. Two unknown neighbours can convince each other: A copies B, B copies A, and both end up
at 99% confidence for the wrong person. We saw this in the first version, with 0.99-confidence
errors on runs of "Um." and "So."

Treating each run of system-audio lines as a **chain** fixes it. Each line has a prior over
speakers, and each adjacent pair has a "same speaker" bonus. The **forward-backward algorithm**
(the HMM smoothing algorithm) then computes every line's exact probability of being each person:
- one pass left to right collects the evidence from everything before the line;
- one pass right to left collects the evidence from everything after it;
- their product is the answer.

No line's own guess is ever fed back to it. That's why confidences became meaningful: on XL,
lines at ≥ 0.9 confidence are 96% right.

For in-person meetings with iOS diarization, propagation works over **clusters** instead of a
chain. All lines labelled "Speaker A" are the same unknown person. So we add up the cues across
the cluster and choose the assignment of clusters to people that explains them best.

## Evaluation

**How gold is built.** `evaluate.py` reads the fixture's `manifest.json` (seed, scale, noise
rates) and rebuilds the world with `synth.pipeline.build`. It then re-renders every meeting with
`render_meeting(..., gold_out=...)`, a new optional output that records each utterance's true
speaker(s) and source beats. Recording gold draws no random numbers, and the fixture
byte-identity test still passes. The regenerated transcripts are checked text-for-text against the
fixture before scoring. In-person blocks merge several turns, so their gold is the speaker of the
majority of words. 15 of 27 base blocks and 141 of 302 XL blocks are mixed.

Gold person mentions come from the **clean** beat text, before ASR noise, so a heard "Coffee" is
still a gold Kofi mention. A shared first name in gold is assigned by the meeting's true attendees,
or by a commitment evidenced in that beat. Otherwise it's left out of scoring. Mention P/R is
scored at beat level: a link counts if the beat it came from mentions that person.

Columns: *coverage* is the share of lines given a `person_id` at the default threshold (0.6).
*Precision* is accuracy among those lines. *Accuracy (all)* counts unresolved lines as wrong.

### Speaker attribution

| Slice | Base lines | Base coverage | Base precision | Base acc. (all) | XL lines | XL coverage | XL precision | XL acc. (all) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **overall** | 2,868 | 95.0% | 98.8% | 93.8% | 32,832 | 93.9% | 99.4% | 93.4% |
| remote | 2,841 | 95.6% | 98.8% | 94.4% | 32,530 | 94.7% | 99.5% | 94.2% |
| remote / Me (microphone) | 1,139 | 100% | 100% | 100% | 15,140 | 100% | 100% | 100% |
| remote / Them, named | 1,359 | 100% | 100% | 100% | 13,669 | 100% | 100% | 100% |
| **remote / Them, unnamed** | 343 | 63.3% | 84.8% | 53.6% | 3,721 | 53.9% | 91.6% | 49.4% |
| **in person** | 27 | 33.3% | 88.9% | 29.6% | 302 | 4.6% | 50.0% | 2.3% |
| in person, diarized | 9 | 0% | – | 0% | 23 | 0% | – | 0% |

The "named vs unnamed" split is the one that matters. Named lines and the owner's microphone are
solved by the signals Granola already provides. All the difficulty is in the **unnamed
system-audio** lines (11% of XL) and the **in-person** lines (1%).

**Coverage / accuracy at confidence thresholds.** Each cell is coverage / accuracy of covered
lines. An unresolved line is scored with its best candidate.

| Threshold | Base: unnamed Them | Base: in person | XL: all lines | XL: unnamed Them | XL: in person |
|---:|---:|---:|---:|---:|---:|
| 0.00 | 100% / 67.6% | 100% / 77.8% | 100% / 96.1% | 100% / 67.8% | 100% / 70.9% |
| 0.40 | 90.7% / 68.5% | 100% / 77.8% | 97.9% / 97.4% | 81.3% / 75.5% | 100% / 70.9% |
| 0.50 | 69.4% / 81.9% | 100% / 77.8% | 96.8% / 98.2% | 71.8% / 82.5% | 100% / 70.9% |
| **0.60** (default) | 63.3% / 84.8% | 33.3% / 88.9% | 93.9% / 99.4% | 53.9% / 91.6% | 4.6% / 50.0% |
| 0.70 | 56.0% / 88.0% | 0% / – | 93.5% / 99.5% | 50.1% / 91.5% | 4.6% / 50.0% |
| 0.80 | 45.8% / 92.4% | 0% / – | 92.6% / 99.8% | 43.2% / 96.0% | 0% / – |
| 0.90 | 35.0% / 90.8% | 0% / – | 91.4% / 99.9% | 32.1% / 96.4% | 0% / – |

**Baselines.** For in-person lines, "always the note owner" scores 74.1% (base) and 70.5% (XL).
Our in-person best guess scores 77.8% and 70.9%. **In-person attribution without diarization is
essentially at the owner baseline.** We say so rather than dress it up. The model knows it's
guessing: in-person confidences sit at ~0.6, so by default these lines stay unresolved with both
candidates listed.

For unnamed system-audio lines, uniform guessing among non-owner attendees would score about
1/(attendees − 1), i.e. 15–35% in these meetings. The best guess scores 68%.

**Where the unnamed-line errors are** (XL, best guess at threshold 0):

| Unnamed system-audio line | Lines | Correct |
|---|---:|---:|
| speaker is never named in the meeting (prior points to them) | 945 | **94.6%** |
| speaker is named on other lines in the same meeting (Granola dropped the label on this one) | 1,320 | 65.8% |
| meeting with no platform names at all | 1,065 | 48.4% |
| crosstalk backchannel ("Yeah." overlapping the owner) | 391 | 62.7% |

### Planted traps

| Trap | Base | XL |
|---|---|---|
| Both Sams, speaker lines (named / unnamed) | 100% / 42.9% acc. (75.0% precision, 57% coverage) | 100% / 46.7% acc. (87.0% precision, 54% coverage) |
| Both Alexes, speaker lines (named / unnamed) | 100% / 73.3% acc. (92.5% precision) | 100% / 35.5% acc. (91.1% precision, 39% coverage) |
| "Sam" mentions → right Sam | 11/12 (the miss is the ASR-mangled token "Samlike") | 16/16 |
| "Alex" mentions → right Alex | 5/5 | 10/10 |
| Megan heard as "Meghan" | 3/3 | 4/4 |
| Kofi heard as "Coffee" | 2/2 | 2/2 |
| Tomás heard as "Thomas" | 4/4 | 3/3 |
| Inês as "Ines"/"Inez", Lena as "Lina" | 5/5 | 4/4 |
| Yusuf heard as "Joseph" | 1/1 | 1/1 |
| "Beacon Freight" → customer org, not project | 1/1 | 1/1 |
| "Falcon" / "PAY two twelve" / "the payments migration" → falcon | 8/8, 1/1, 1/1 | 8/8, 1/1, 1/1 |

Speakers are always fine for both Sams and both Alexes when Granola names them, because the
platform name is a full name. They are hard when it doesn't. That's the same unnamed-line problem
as above, not a name-collision problem.

### Mention linking (persons the roster knows)

| Set | Gold mentions | Resolved links | Precision | Recall |
|---|---:|---:|---:|---:|
| base | 67 | 67 | **100%** | **98.5%** |
| XL | 164 | 167 | **100%** | **100%** |

These sets are **small**. The generator says names rarely (standup prompts, reassignments,
asides), and each ASR trap appears only 1–4 times, so a single error moves recall by 25 points.
The org and project traps are tiny too (Beacon Freight appears once; "Beacon" alone never appears
in clean beat text, so that case is covered only by a unit test). Treat 100% as "no errors found
on what the generator plants", not as a population estimate.

During development, XL precision started at 84.7%. The false positives were the guards' origin:
- "We'll" fuzzily matched Joel;
- "Teams" matched Tomás;
- "Lucy" (a hiring candidate, "Lucy Park") matched Luis.

## Failure analysis: the 10 highest-confidence XL errors

All ten are unnamed system-audio lines in remote meetings. The planted name traps produced no
high-confidence errors.

| # | Line (gold → predicted, conf.) | Root cause |
|---|---|---|
| 1–2 | `F5KJ3…:0030/0031` "I mean." / "Right, right." hana → carla (0.97) | Hana's turn opens with two unlabeled passages before her named "Keep going.". Carla is never named in this meeting, so the attendee prior gives her nearly all unnamed lines, and the 1:1 same-turn chain link isn't strong enough to overturn it. **Prior too sharp for the "named elsewhere" case.** |
| 3 | `k808c…:0059` "Let's make sure sales and marketing hear this…" elena → jordan (0.97) | A one-passage turn by Elena (named elsewhere, unlabeled here) sits between two passages by never-named Jordan. The text has no continuity or addressing cue, and chain plus prior both point to Jordan. **Irreducible from text alone.** |
| 4 | `FIWlI…:0136` same sentence, hana → elena (0.96) | Same pattern: a single unlabeled passage of an identified speaker inside a never-named speaker's run. |
| 5 | `Bg70E…:0057` "Yeah, that makes sense to me." theo → ines (0.96) | Same pattern (Ines never named; Theo named elsewhere). |
| 6 | `rwo3g…:0083` "I'm worried about how this lands…" marcus → olivia (0.96) | Same pattern in an investor update. Olivia is never named, Marcus is. |
| 7 | `FIWlI…:0134` "The board will ask about this…" theo → elena (0.95) | Same pattern. This sentence is a role cue (a growth lead or CEO talking about the board), which a role prior could use. Not implemented. |
| 8 | `SSAdG…:0113` "Right, right. Keep going." ruth → carla (0.94) | Ruth's one-line interjection sits between an unlabeled fragment of Carla's ("…this to work?") and Carla's next named line. The chain's same-speaker preference on both sides outvotes the prior. **A turn boundary with no textual signal.** |
| 9 | `I81os…:0020` "I think the question is really about scope." alex_k → ben (0.94) | Same "identified speaker, unlabeled once, inside a never-named run" pattern. |
| 10 | `Zue7w…:0083` "I think that's mostly right, but there's a nuance there." sam_p → alex_k (0.93) | Same pattern: Sam is named elsewhere, Alex never is. This line directly follows two (correctly attributed) Alex lines, so prior and chain agree on Alex. |

Two root causes cover all ten:
1. **(Nine of ten.)** A speaker Granola *identified* has one passage that lost its label, inside
   a run of a speaker Granola never named. The attendee prior is right on average (94.6% on lines
   of never-named speakers) but overconfident on exactly these lines. The text of the synthetic
   filler carries no speaker signal: anyone can say "Right, right". That puts a ceiling on any
   text-only method for this slice.
2. **(One of ten.)** A turn boundary with no textual signal, where the chain's same-speaker
   preference carries a neighbour's label across it.

The first version's worst errors were 0.99-confidence runs of "Um."/"So." caused by naive
iterative relabelling, plus crosstalk backchannels chained to the wrong turn. Forward-backward
and the crosstalk rule removed those.

**Where it's weak, summarized:**
- **In-person attribution** is at the owner baseline (70.9% vs 70.5% XL). The 1:1s have two
  people, little addressing, and merged multi-speaker blocks. In the one diarized meeting,
  propagation assigns both clusters to the right people (best guess right on 8/9 base and 19/23
  XL lines; the misses are mixed blocks). But confidence is only ~0.6, so these lines stay
  unresolved by default: no cue separates Speaker A from B beyond the owner prior on the larger
  cluster.
- **Meetings with no platform names at all** (48% best-guess accuracy). With two unnamed speakers
  (salesperson + customer) there's little to go on.
- **Both Alexes, unnamed, XL** (35.5% accuracy, 39% coverage): Alex Rivera leads many customer
  calls without platform names.

## Open risks
- **Same generator, both sets.** XL isn't independent of base: it shares templates, filler and
  noise. The measured odds (6.5:1, 25:1, 12% unlabeled) come from the generator's own rates and
  may differ on real transcripts. The real MCP probe showed 82% of "Them" passages named, matching
  the synth's ~80%. Unlabeled-passage rates and fragmenting on real data are unmeasured.
- **The `UNLABELED_RATE` assumption** (12% of an identified speaker's lines unnamed) drives the
  attendee prior. It's the single most influential constant.
- **Hybrid rooms** (a conference room joining as one participant) aren't modeled. The owner-mic
  rule would mislabel everyone on the owner's laptop microphone in a hybrid meeting.
- **Gold for in-person lines** is the majority speaker of a merged block. A line-level answer is
  ill-posed there. A future version should split blocks into sentences and attribute those.
- **Mention gold** is small, and its shared-first-name rule skips undecidable beats, which can
  flatter precision slightly.
- **`render_meeting` gained an optional `gold_out` parameter** (no behaviour change, fixtures
  byte-identical). `_merge_in_person` now also returns its index remap.

## Appendix: raw evaluation output

Regenerate with `uv run python -m concentrate.enhance.evaluate data/fixtures/northwind
data/fixtures/large/northwind-xl` (create XL first with `uv run concentrate synth --seed 42
--scale xl --out data/fixtures/large/northwind-xl`; it is free and takes ~2 s).

#### data/fixtures/northwind: 40 meetings, 2868 utterances

| Slice | Lines | Coverage | Precision (resolved) | Accuracy (all) |
|---|---:|---:|---:|---:|
| in_person | 27 | 33.3% | 88.9% | 29.6% |
| overall | 2868 | 95.0% | 98.8% | 93.8% |
| remote | 2841 | 95.6% | 98.8% | 94.4% |
| in_person/diarized | 9 | 0.0% | 0.0% | 0.0% |
| in_person/me | 27 | 33.3% | 88.9% | 29.6% |
| in_person/mixed_block | 15 | 33.3% | 80.0% | 26.7% |
| in_person/no_diarization | 18 | 50.0% | 88.9% | 44.4% |
| in_person/single_speaker | 12 | 33.3% | 100.0% | 33.3% |
| remote/me | 1139 | 100.0% | 100.0% | 100.0% |
| remote/them | 1702 | 92.6% | 97.9% | 90.7% |
| in_person/me/unnamed | 27 | 33.3% | 88.9% | 29.6% |
| remote/me/unnamed | 1139 | 100.0% | 100.0% | 100.0% |
| remote/them/named | 1359 | 100.0% | 100.0% | 100.0% |
| remote/them/unnamed | 343 | 63.3% | 84.8% | 53.6% |

In-person blocks with more than one true speaker: 15

| Method | Lines | Precision |
|---|---:|---:|
| addressed | 7 | 85.7% |
| attendee_prior | 137 | 85.4% |
| owner_mic | 1148 | 99.9% |
| platform_name | 1359 | 100.0% |
| turn_continuity | 73 | 83.6% |
| unresolved | 144 | - |

Coverage / accuracy-of-covered at confidence thresholds (unresolved lines count with their best candidate)

| Threshold | all lines | hard lines (unnamed system audio + in-person) | unnamed system audio (remote) | in-person |
|---:|---:|---:|---:|---:|
| 0.00 | 100.0% / 95.9% | 100.0% / 68.4% | 100.0% / 67.6% | 100.0% / 77.8% |
| 0.30 | 99.9% / 96.0% | 99.5% / 68.5% | 99.4% / 67.7% | 100.0% / 77.8% |
| 0.40 | 98.6% / 96.6% | 89.2% / 70.9% | 88.3% / 70.3% | 100.0% / 77.8% |
| 0.50 | 96.3% / 98.2% | 71.6% / 81.5% | 69.4% / 81.9% | 100.0% / 77.8% |
| 0.60 | 95.0% / 98.8% | 61.1% / 85.0% | 63.3% / 84.8% | 33.3% / 88.9% |
| 0.70 | 93.8% / 99.3% | 51.6% / 89.5% | 55.7% / 89.5% | 0.0% / 0.0% |
| 0.80 | 92.6% / 99.5% | 42.4% / 92.4% | 45.8% / 92.4% | 0.0% / 0.0% |
| 0.90 | 91.1% / 99.8% | 30.8% / 95.6% | 33.2% / 95.6% | 0.0% / 0.0% |
| 0.95 | 90.3% / 100.0% | 25.1% / 98.9% | 27.1% / 98.9% | 0.0% / 0.0% |

Baseline, in-person: always the note owner: accuracy 74.1% on 27 lines

| Speaker trap | Lines | Coverage | Precision | Accuracy |
|---|---:|---:|---:|---:|
| both Alexes (all) | 351 | 94.0% | 98.2% | 92.3% |
| both Alexes (named) | 250 | 100.0% | 100.0% | 100.0% |
| both Alexes (unnamed) | 101 | 79.2% | 92.5% | 73.3% |
| both Sams (all) | 154 | 94.2% | 97.9% | 92.2% |
| both Sams (named) | 133 | 100.0% | 100.0% | 100.0% |
| both Sams (unnamed) | 21 | 57.1% | 75.0% | 42.9% |

| Person mention linking | Gold | Predicted | Precision | Recall |
|---|---:|---:|---:|---:|
| all roster persons | 67 | 67 | 100.0% | 98.5% |
| alex_k | 5 | 5 | 100.0% | 100.0% |
| dana | 1 | 1 | 100.0% | 100.0% |
| dev | 1 | 1 | 100.0% | 100.0% |
| grace | 1 | 1 | 100.0% | 100.0% |
| ines | 5 | 5 | 100.0% | 100.0% |
| jordan | 1 | 1 | 100.0% | 100.0% |
| kofi | 6 | 6 | 100.0% | 100.0% |
| lena | 3 | 3 | 100.0% | 100.0% |
| luis | 1 | 1 | 100.0% | 100.0% |
| marcus | 1 | 1 | 100.0% | 100.0% |
| megan | 6 | 6 | 100.0% | 100.0% |
| nadia | 1 | 1 | 100.0% | 100.0% |
| priya | 1 | 1 | 100.0% | 100.0% |
| ruth | 10 | 10 | 100.0% | 100.0% |
| sam_o | 2 | 2 | 100.0% | 100.0% |
| sam_p | 10 | 9 | 100.0% | 90.0% |
| tomas | 9 | 10 | 100.0% | 100.0% |
| yusuf | 3 | 3 | 100.0% | 100.0% |

Ambiguous links (no id) on a gold mention: 0

| Mention trap | Gold | Recall |
|---|---:|---:|
| Alex (first name shared) | 5 | 100.0% |
| Inês heard as 'Ines' | 2 | 100.0% |
| Kofi heard as 'Coffee' | 2 | 100.0% |
| Lena heard as 'Lina' | 3 | 100.0% |
| Megan heard as 'Meghan' | 3 | 100.0% |
| Sam (first name shared) | 12 | 91.7% |
| Tomás heard as 'Thomas' | 4 | 100.0% |
| Yusuf heard as 'Joseph' | 1 | 100.0% |

| Entity trap (beat-level) | Occurrences | Linked to right kind | Correct |
|---|---:|---:|---:|
| 'Beacon Freight' -> customer org | 1 | 100.0% | 100.0% |
| 'Falcon' -> project falcon | 8 | 100.0% | 100.0% |
| 'PAY two twelve' / PAY-212 -> falcon | 1 | 100.0% | 100.0% |
| 'the payments migration' -> falcon | 1 | 100.0% | 100.0% |

Highest-confidence speaker errors:

- `not_Zue7w068bRonJB:0024` (remote) gold **tomas**, predicted **alex_k** via turn_continuity @ 0.97: "Yeah, the alerting for failed charges is done, uh, that went out last week."
- `not_i0kpeW6NCQ4Jpe:0045` (remote) gold **alex_r**, predicted **olivia** via attendee_prior @ 0.94: "I mean."
- `not_i0kpeW6NCQ4Jpe:0046` (remote) gold **alex_r**, predicted **olivia** via attendee_prior @ 0.94: "Hold on, my laptop is is about too die, let me grab a charger."
- `not_JQEktc0eauNYt8:0031` (remote) gold **sam_p**, predicted **ines** via attendee_prior @ 0.92: "The logs are super noisy right now, it's you know, hard to find anything."
- `not_JQEktc0eauNYt8:0032` (remote) gold **ruth**, predicted **ines** via attendee_prior @ 0.91: "uh, Sorry, I uh, was on mute. I was saying I agree."
- `not_rwo3gvYvcMvUIm:0018` (remote) gold **simone**, predicted **olivia** via attendee_prior @ 0.88: "Yeah, that makes sense to me."
- `not_aRQDbsQzkAnEJP:0079` (remote) gold **megan**, predicted **lena** via turn_continuity @ 0.86: "I'm not sure that's the right framing, honestly."
- `not_vvivyeMzZ3WLw0:0048` (remote) gold **dana**, predicted **alex_r** via turn_continuity @ 0.85: "Hold on, my laptop is about die, let I mean, me grab a charger."
- `not_CWIevsIiHzj048:0017` (remote) gold **alex_r**, predicted **felix** via attendee_prior @ 0.84: "Okay."
- `not_CWIevsIiHzj048:0018` (remote) gold **owen**, predicted **felix** via attendee_prior @ 0.84: "Non-negotiable for uh, us is SSO. Our IT policy doesn't allow separate passwords."

#### data/fixtures/large/northwind-xl: 184 meetings, 32832 utterances

| Slice | Lines | Coverage | Precision (resolved) | Accuracy (all) |
|---|---:|---:|---:|---:|
| in_person | 302 | 4.6% | 50.0% | 2.3% |
| overall | 32832 | 93.9% | 99.4% | 93.4% |
| remote | 32530 | 94.7% | 99.5% | 94.2% |
| in_person/diarized | 23 | 0.0% | 0.0% | 0.0% |
| in_person/me | 302 | 4.6% | 50.0% | 2.3% |
| in_person/mixed_block | 141 | 7.1% | 70.0% | 5.0% |
| in_person/no_diarization | 279 | 5.0% | 50.0% | 2.5% |
| in_person/single_speaker | 161 | 2.5% | 0.0% | 0.0% |
| remote/me | 15140 | 100.0% | 100.0% | 100.0% |
| remote/them | 17390 | 90.1% | 98.9% | 89.2% |
| in_person/me/unnamed | 302 | 4.6% | 50.0% | 2.3% |
| remote/me/unnamed | 15140 | 100.0% | 100.0% | 100.0% |
| remote/them/named | 13669 | 100.0% | 100.0% | 100.0% |
| remote/them/unnamed | 3721 | 53.9% | 91.6% | 49.4% |

In-person blocks with more than one true speaker: 141

| Method | Lines | Precision |
|---|---:|---:|
| addressed | 17 | 94.1% |
| attendee_prior | 1334 | 91.2% |
| owner_mic | 15154 | 100.0% |
| platform_name | 13669 | 100.0% |
| turn_continuity | 655 | 92.4% |
| unresolved | 2003 | - |

Coverage / accuracy-of-covered at confidence thresholds (unresolved lines count with their best candidate)

| Threshold | all lines | hard lines (unnamed system audio + in-person) | unnamed system audio (remote) | in-person |
|---:|---:|---:|---:|---:|
| 0.00 | 100.0% / 96.1% | 100.0% / 68.0% | 100.0% / 67.8% | 100.0% / 70.9% |
| 0.30 | 99.6% / 96.4% | 97.0% / 69.4% | 96.8% / 69.3% | 100.0% / 70.9% |
| 0.40 | 97.7% / 97.7% | 81.4% / 77.1% | 79.9% / 77.7% | 100.0% / 70.9% |
| 0.50 | 96.8% / 98.2% | 73.9% / 81.3% | 71.8% / 82.5% | 100.0% / 70.9% |
| 0.60 | 93.9% / 99.4% | 50.2% / 91.3% | 53.9% / 91.6% | 4.6% / 50.0% |
| 0.70 | 93.4% / 99.6% | 46.0% / 93.7% | 49.4% / 94.1% | 4.6% / 50.0% |
| 0.80 | 92.6% / 99.8% | 39.9% / 96.0% | 43.2% / 96.0% | 0.0% / 0.0% |
| 0.90 | 91.3% / 99.9% | 28.7% / 98.6% | 31.0% / 98.6% | 0.0% / 0.0% |
| 0.95 | 90.5% / 100.0% | 22.8% / 99.3% | 24.6% / 99.3% | 0.0% / 0.0% |

Baseline, in-person: always the note owner: accuracy 70.5% on 302 lines

| Speaker trap | Lines | Coverage | Precision | Accuracy |
|---|---:|---:|---:|---:|
| both Alexes (all) | 2179 | 85.5% | 99.0% | 84.6% |
| both Alexes (named) | 1660 | 100.0% | 100.0% | 100.0% |
| both Alexes (unnamed) | 519 | 38.9% | 91.1% | 35.5% |
| both Sams (all) | 1435 | 92.6% | 98.8% | 91.5% |
| both Sams (named) | 1206 | 100.0% | 100.0% | 100.0% |
| both Sams (unnamed) | 229 | 53.7% | 87.0% | 46.7% |

| Person mention linking | Gold | Predicted | Precision | Recall |
|---|---:|---:|---:|---:|
| all roster persons | 164 | 167 | 100.0% | 100.0% |
| alex_k | 10 | 11 | 100.0% | 100.0% |
| ava | 1 | 1 | 100.0% | 100.0% |
| carla | 1 | 1 | 100.0% | 100.0% |
| dana | 1 | 1 | 100.0% | 100.0% |
| dev | 2 | 2 | 100.0% | 100.0% |
| elena | 7 | 7 | 100.0% | 100.0% |
| felix | 1 | 1 | 100.0% | 100.0% |
| grace | 3 | 3 | 100.0% | 100.0% |
| hana | 8 | 8 | 100.0% | 100.0% |
| hugo | 1 | 1 | 100.0% | 100.0% |
| ines | 13 | 13 | 100.0% | 100.0% |
| ivan | 1 | 1 | 100.0% | 100.0% |
| joel | 1 | 1 | 100.0% | 100.0% |
| jordan | 1 | 1 | 100.0% | 100.0% |
| kofi | 13 | 13 | 100.0% | 100.0% |
| lena | 9 | 9 | 100.0% | 100.0% |
| luis | 1 | 1 | 100.0% | 100.0% |
| marcus | 1 | 1 | 100.0% | 100.0% |
| maya | 1 | 1 | 100.0% | 100.0% |
| megan | 16 | 17 | 100.0% | 100.0% |
| nadia | 9 | 9 | 100.0% | 100.0% |
| noor | 1 | 1 | 100.0% | 100.0% |
| priya | 1 | 1 | 100.0% | 100.0% |
| ruth | 18 | 18 | 100.0% | 100.0% |
| sam_o | 3 | 3 | 100.0% | 100.0% |
| sam_p | 12 | 12 | 100.0% | 100.0% |
| sam_r | 1 | 1 | 100.0% | 100.0% |
| tess | 1 | 1 | 100.0% | 100.0% |
| theo | 6 | 6 | 100.0% | 100.0% |
| tomas | 16 | 17 | 100.0% | 100.0% |
| yusuf | 4 | 4 | 100.0% | 100.0% |

Ambiguous links (no id) on a gold mention: 0

| Mention trap | Gold | Recall |
|---|---:|---:|
| Alex (first name shared) | 10 | 100.0% |
| Inês heard as 'Inez' | 2 | 100.0% |
| Kofi heard as 'Coffee' | 2 | 100.0% |
| Lena heard as 'Lina' | 2 | 100.0% |
| Megan heard as 'Meghan' | 4 | 100.0% |
| Sam (first name shared) | 16 | 100.0% |
| Tomás heard as 'Thomas' | 3 | 100.0% |
| Yusuf heard as 'Joseph' | 1 | 100.0% |

| Entity trap (beat-level) | Occurrences | Linked to right kind | Correct |
|---|---:|---:|---:|
| 'Beacon Freight' -> customer org | 1 | 100.0% | 100.0% |
| 'Falcon' -> project falcon | 8 | 100.0% | 100.0% |
| 'PAY two twelve' / PAY-212 -> falcon | 1 | 100.0% | 100.0% |
| 'the payments migration' -> falcon | 1 | 100.0% | 100.0% |

Highest-confidence speaker errors:

- `not_F5KJ3CtprwkUUi:0030` (remote) gold **hana**, predicted **carla** via attendee_prior @ 0.97: "I mean."
- `not_F5KJ3CtprwkUUi:0031` (remote) gold **hana**, predicted **carla** via attendee_prior @ 0.97: "Right, right."
- `not_k808cU76BMWV5G:0059` (remote) gold **elena**, predicted **jordan** via attendee_prior @ 0.97: "Let's make sure sales and marketing hear this at the same time."
- `not_FIWlIaLukWliRj:0136` (remote) gold **hana**, predicted **elena** via attendee_prior @ 0.96: "Let's make sure sales sales and marketing hear this at the same time."
- `not_Bg70E4tj6oBvbK:0057` (remote) gold **theo**, predicted **ines** via attendee_prior @ 0.96: "Yeah, that makes makes sense to me."
- `not_rwo3gvYvcMvUIm:0083` (remote) gold **marcus**, predicted **olivia** via attendee_prior @ 0.96: "I'm worried about how um, this lands with uh, existing customers."
- `not_FIWlIaLukWliRj:0134` (remote) gold **theo**, predicted **elena** via attendee_prior @ 0.95: "The board will ask about this, so let's have a clear story."
- `not_SSAdGyQAhLN2Mv:0113` (remote) gold **ruth**, predicted **carla** via turn_continuity @ 0.94: "Right, right. Keep going."
- `not_I81oszk09c8znc:0020` (remote) gold **alex_k**, predicted **ben** via attendee_prior @ 0.94: "I think the question is really about scope."
- `not_Zue7w068bRonJB:0083` (remote) gold **sam_p**, predicted **alex_k** via attendee_prior @ 0.93: "I think that's mostly right, but there's a nuance there."

