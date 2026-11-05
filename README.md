# TENOR

**Transcripts, Evidence, Notes, Outcomes, Recall: an open benchmark for meeting note-takers.**

TENOR replays public meeting recordings into real meeting products and measures, from the outside, what their users depend on: whether the meeting was captured, whether the notes are faithful and complete, whether answers about the meeting are right, and what is lost when transcripts are deleted. Granola and Wispr Flow are the reference products.

*Independent research prototype; [not affiliated with Granola or Wispr](#not-affiliated-with-granola-or-wispr).*

## Status

As of 5 November 2026:

| What exists | Size |
|---|---|
| Public meetings replayed into products | 7 AMI meetings (about 3 hours of audio), each replayed into 2 products: Granola and Wispr Flow |
| Questions about those meetings | 68, built by fixed templates from AMI's human annotations (13 DEV) |
| Human labels on product outputs | 23 (20 note claims, 3 Chat answers), from 1 verifier |
| Scorers | Capture coverage, WER, fact-token recall and an MCP contract check (all free); a grounding checker and an answer judge (LLM, calibration in progress) |

**All AMI results are preliminary.** The sample is 7 meetings, the human-labelled set is small, and the automated judges have not yet been calibrated against enough human labels to be trusted on their own. No result below is confirmed. Planned next: capture stress tests and a nightly replay suite ([next research step](#next-research-step-capture-reliability-and-regression-replay)), about 100 human labels, and the AMI replication of the retention study.

An earlier study on the author's own 4 private Granola meetings (October–November 2026) is reported below where relevant and labelled as such; it is superseded by the AMI work as each replication lands.

## Motivation

Meeting note-takers are now ordinary work tools. People rely on them to record what was said, write it up, and answer questions about it later, often through an AI agent that reads the product's data via MCP (Model Context Protocol, the interface agents use to call a product's tools). When one of these steps fails, the user usually cannot tell: a missed half-hour, a misheard number or an over-confident answer looks the same as a correct one.

**Existing benchmarks test parts, not shipped products.** Speech-recognition benchmarks score transcription models; agent benchmarks score agents on fixed tasks. Meeting benchmarks such as QMSum, MeetingQA and ELITR-Bench give a model a transcript and grade the model; memory benchmarks such as LongMemEval and LoCoMo use chat or generated dialogue. None that we found tests a shipped meeting product end to end, from audio in to answers out, through the interfaces its users and their agents actually use ([`docs/RELATED_WORK.md`](docs/RELATED_WORK.md)). The closest comparison, a vendor-run test of four note-takers on five AMI meetings, scores notes only.

**Outside-in evaluation fills that gap.** TENOR plays the same public audio into each product, reads the results back only through each product's public, read-only MCP tools, and scores them against human ground truth. It makes no claims about product internals; everything reported is observable by any user.

**Why these two reference products.** Granola and Wispr Flow are the two leading bot-free meeting note-takers (they capture audio on the user's device rather than sending a bot into the call) with public MCP access. Both can be measured the same way, on the same meetings, through the same kind of interface. More products are planned ([roadmap](#roadmap-to-v1)).

## Method

![How does the outside-in evaluation harness work? Public AMI audio is played into Granola and Wispr Flow, read back through read-only MCP tools into a private store, and scored by free scorers and a calibrated judge against the AMI answer key and human labels](docs/figures/harness_flow.svg)

**AMI replay protocol.** Meetings come from the AMI Meeting Corpus: real recorded speech with human transcripts, word timings and annotations (decisions, actions, problems), licensed CC BY 4.0. The set is the four-meeting IS1008 design-team series, ES2006a (another design-team role-play), IB4003 (a staged meeting) and EN2002b (a naturally occurring meeting). Each meeting's headset mix is played in real time as system audio into both products at once. The protocol for scored runs clears personal dictionaries, mutes the speakers so the room stays silent, and drops and counts any microphone turns. Details, licence analysis and risks: [`docs/method/AMI_REPLAY.md`](docs/method/AMI_REPLAY.md).

**Read-only MCP collection.** Each product's notes, transcripts and answers are read through its official MCP server. Every call goes through an allowlist that refuses any tool not on it before anything is sent, so no write tool can be called. Product outputs are stored locally under gitignored paths and are never committed; only scores, labels and short quoted examples beside the AMI source are published. Harness and pairing: [`docs/method/HARNESS.md`](docs/method/HARNESS.md).

**Free scorers.** Capture coverage aligns AMI's timed human words with the product transcript in 15-second windows. WER (word error rate) and content-word recall compare the full transcript with AMI's. Fact-token recall checks numbers, money, names and roles. None of these uses an LLM, so they cost nothing and are deterministic. Track definitions: [`docs/tracks/`](docs/tracks/README.md).

**Calibrated judging.** Notes and answers need an LLM judge, and on meeting outputs automated judges over-count errors. The causes found so far are each fixable: judges penalise names where the answer key uses roles, product boilerplate is read as content, a small judge's verdict contradicts its own reasoning, and a one-line summary used as the answer key drops nuance the product kept. The calibrated judge adds a speaker roster from the meeting's own introductions, strips product boilerplate, uses a stronger model, scores list answers per item, and is reported with its agreement with human labels. Details: [`docs/method/CALIBRATED_JUDGING.md`](docs/method/CALIBRATED_JUDGING.md).

**Human verification.** A human labels a sample of note claims and answers against the AMI source, on public AMI items so that labels can be released. These labels measure how far each automated judge over- or under-counts. Protocol and status: [`docs/method/HUMAN_VERIFICATION.md`](docs/method/HUMAN_VERIFICATION.md).

## Results

Each table gives the measure, both products side by side, n, an interval or note, and a status. *Preliminary* means small n or a method still changing; read it as a direction, not a ranking. Intervals are 95% Wilson intervals unless stated. Dated log of every result: [`docs/results/INDEX.md`](docs/results/INDEX.md).

### Capture

![How much of what was said did each app catch? Coverage timeline for one AMI meeting for Granola and Wispr Flow, every 15 seconds, with the 40% threshold and a wrong-meeting control](docs/figures/capture_coverage.svg)

| Measure | Granola | Wispr Flow | n | Interval / notes | Status |
|---|---|---|---|---|---|
| 15 s windows of AMI speech captured | 98.5% | 99.4% | 665 windows per product, 7 meetings | Per meeting: Granola 93.6–100%, Wispr Flow 96.4–100%. Windows cluster by meeting, so no pooled interval is given | Preliminary |
| Gaps of ≥ 30 s | 1 (51 s, EN2002b) | 0 | 7 meetings | The Granola gap falls where Wispr Flow's coverage also dips (overlapping speech) | Preliminary |
| Silent capture failures | 0 | 0 | 7 meetings | Baseline conditions only; no stress applied | Preliminary |
| Wrong-meeting control | — | — | 7 meetings | 0–2% of windows pass, so the 40% threshold separates capture from chance | — |

Both products captured almost all of every meeting under normal conditions. The capture question that matters to users, silent failure under stress, is not yet tested ([next research step](#next-research-step-capture-reliability-and-regression-replay)). Track: [`docs/tracks/CAPTURE.md`](docs/tracks/CAPTURE.md).

### Transcription

| Measure | Granola | Wispr Flow | n | Interval / notes | Status |
|---|---|---|---|---|---|
| WER against AMI's manual transcript | 10.0% | 9.0% | 1 meeting (IS1008a) | Pilot recording with personal settings on; a one-point gap on one meeting is a tie | Pilot, not a result |
| Content-word recall | 94.2% | 95.8% | 1 meeting | As above | Pilot |
| Speakers separated in MCP output | 0 of 4 | 3 of 4 | 1 meeting | Granola separates microphone from system audio rather than diarising; on one mixed channel it reports no speakers by design | Pilot |

The 7-meeting WER table is next and costs nothing to compute. Because AMI is a standard speech benchmark that product models may have seen, WER here is a best case, not a claim about everyday accuracy. Track: [`docs/tracks/TRANSCRIPTION.md`](docs/tracks/TRANSCRIPTION.md).

### Notes

| Measure | Granola | Wispr Flow | n | Interval / notes | Status |
|---|---|---|---|---|---|
| Note claims supported by both the product's and AMI's transcript (first automated pass) | 94% (202 of 216) | 80% (136 of 171) | 387 claims, 7 meetings | Granola [89, 96], Wispr Flow [73, 85]. Much of the Wispr Flow gap is checker noise (one-notch disagreements, generic "Speaker N" labels); a re-run with corrected input is quoted, not run | Preliminary |
| Claims where the note repeats a transcription error | 1 of 216 | 6 of 171 | 387 claims | Of these 7, on manual reading 4 are speaker-label noise, 2 ambiguous and 1 clearly real | Preliminary |
| Human check of sampled claims | 20 sampled claims (per-product split not reported): 12 judgeable, 0 wrong, 1 overreach | | 20 | The automated checker had flagged most of these | Preliminary |
| *Earlier, 4 private meetings:* claims supported / inferred / unsupported | 84% / 14% / 0% | not measured | 132 claims | Supported [77, 89]; Granola only | Superseded by AMI |

On the first pass Granola's notes were grounded more often than Wispr Flow's; whether that gap survives the corrected re-run and human calibration is open. Track: [`docs/tracks/NOTES.md`](docs/tracks/NOTES.md); method: [`docs/method/WRONG_FACTS.md`](docs/method/WRONG_FACTS.md).

### Answers (Chat), and judge over-count

| Measure | Granola | Wispr Flow | n | Interval / notes | Status |
|---|---|---|---|---|---|
| Answers judged wrong, automated judge | 16 (24%) | not applicable | 68 questions | [15, 35]. Wispr Flow's MCP has no question-answering tool | Preliminary |
| Answers judged partial, automated judge | 24 (35%) | not applicable | 68 | [25, 47] | Preliminary |
| Of the 3 strongest automated "confident contradictions", clearly wrong on human check | 0 of 3 | — | 3 | All three were partly right | Preliminary |
| Note claims judged wrong by a human, of those the automated checker could be compared on | 0 of 12 | (pooled) | 12 | Human agreed with the checker on 4 of 12 | Preliminary |

**Automated judges over-count errors on these outputs.** In both human-checked samples, most automated error verdicts did not survive a human reading. With 23 labels this is a direction, not a rate: the over-count factor will be reported once about 100 labels exist. A comparison of both products through one fixed outside agent is built and not yet run. Track: [`docs/tracks/ANSWERS.md`](docs/tracks/ANSWERS.md).

### Agent interface and MCP drift

| Measure | Granola | Wispr Flow | n | Interval / notes | Status |
|---|---|---|---|---|---|
| Unannounced MCP shape change detected | 1 (5 Nov 2026: transcript chunks changed from structured passages to one text block; a sync stored 0 transcript lines without an error) | none observed; check not yet run | 1 event | Now caught by a contract check that diffs each tool's reply shape against a committed baseline | Observed |
| *Earlier, 4 private meetings:* answers that point inside a meeting (line, chunk or timestamp) | 0 of 47 | not measured | 47 DEV questions | Meeting-level links only; no abstain or coverage field | Preliminary |
| *Earlier, 4 private meetings:* useful and comprehensive answers | 89% | not measured | 47 | [77, 95] | Preliminary |

Track: [`docs/tracks/AGENT_INTERFACE.md`](docs/tracks/AGENT_INTERFACE.md).

### Retention (earlier, 4 private meetings, superseded by the AMI replication in progress)

What does answering from notes alone cost once transcripts are deleted? Four conditions on the same 47 DEV questions about the author's own Granola meetings, each adding one layer:

| Condition | Accuracy [95% bootstrap CI] | vs row above (paired wins–losses) |
|---|---|---|
| A. Granola's agent, transcripts kept | 89% [79, 98] | — |
| B. Notes only (our answer model over Granola's notes) | 57% [43, 70] | 2–17, p = 0.001 |
| C. B plus an extracted fact layer | 66% [51, 79] | 4–0, p = 0.13 (not significant) |
| D. C plus a kept transcript read only when needed | 87% [77, 96] | 10–0, p = 0.002 |

The loss fell on detail: on questions the notes could not answer, accuracy went from 94% to 44%. Without transcripts the answer model declined rather than answering wrongly with confidence. Condition B used our answer model, not the product's own post-deletion chat, so A − B mixes deletion with a change of model. The AMI replication adds Wispr Flow's notes as a second notes-only row and is built and quoted, not yet run. Track: [`docs/tracks/RETENTION.md`](docs/tracks/RETENTION.md); interim report: [`docs/results/TENOR_TECHNICAL_REPORT.md`](docs/results/TENOR_TECHNICAL_REPORT.md).

## Next research step: capture reliability and regression replay

**Why capture first.** Silent capture failure (a recording that appeared to run but captured nothing, or notes covering only part of a meeting) was the most common theme in a 2026 sample of public user reports about meeting note-takers: about 16 of about 75 items. The sample is small, drawn from one public forum and one product's user community, and has not yet been repeated for other products; but a failure the user cannot see undermines every other measure, whichever product it occurs in. TENOR's baseline found no failures in either product, so the question is which conditions produce them.

**Stress-test design.** Each condition replays a known AMI meeting into both products at the same time and is scored by the free coverage scorer; a gap with no visible warning in the app (recorded by screenshot) counts as a silent failure. Full design: [`docs/tracks/CAPTURE.md`](docs/tracks/CAPTURE.md).

| # | Condition | What it probes |
|---|---|---|
| A1 | 12 minutes of silence spliced mid-meeting | Whether capture resumes when speech returns after an inactivity timeout, and whether the user is told |
| A2 | Two meetings back to back, one recording | Content from one meeting written into another |
| A3 | Two meetings back to back, second recording started late | Missed openings; which note gets the overlap |
| A4 | App in the background or on another full-screen desktop | Capture when the app is not visible |
| A5 | Display sleep or lid closed | Capture across system sleep |
| A6 | Network drop for 60 seconds | Offline handling |

Scoring A0–A6 is free apart from cross-meeting bleed (about $0.02 per meeting); each condition takes about 25 minutes of playback.

**Nightly replay suite.** The same harness, run on a frozen, versioned set of public meetings every night, becomes a regression detector: coverage, WER and fact recall per product, compared with the previous run and with human ground truth, plus the MCP contract check. The 5 November MCP shape change is the kind of silent regression it would catch the same day. This is the centre of TENOR's forward plan, and it applies to any product that can be replayed into.

## Design implications for note-takers

What TENOR's evidence could mean for product design (capture-health indicators, completeness prompts, marking inferences, reconciling numbers and names across sources without storing audio, and more) is discussed product-neutrally in [`docs/product/IMPLICATIONS.md`](docs/product/IMPLICATIONS.md). Each item gives the evidence, what a product could do, how TENOR would measure it, and why a product might reasonably not do it.

## Limitations

- **Mostly role-play meetings.** Six of the seven AMI meetings are role-play recorded around 2005 (five design-team scenario meetings and one staged meeting), mostly by non-native English speakers; one (EN2002b) is a naturally occurring meeting. Results describe this material, not typical work calls.
- **Replay is not a live call.** Audio enters digitally as clean 16 kHz system audio, with no calendar invite, attendee names, call compression or meeting-app context. The comparison between products is fair (same input, same time); absolute numbers describe this condition only. A call-path condition is planned.
- **Silent replay, no typed notes.** Nobody typed during the replays. Products that blend the user's typing into their notes are measured in their least favourable mode, so notes results are a lower bound for an engaged user. A typed-vs-silent test is designed.
- **One human verifier,** who also designed the study. Agreement between humans is unmeasured until a second verifier labels a shared subset.
- **Summary-level answer key.** Most gold answers are AMI annotators' summary sentences, which can drop nuance a product keeps; this inflates "partial" verdicts. Scoring against the transcript as well is part of the calibrated judge.
- **Small n.** 7 meetings, 68 questions, 23 human labels. Most differences between products are within their intervals; where a difference is not significant, it is reported as a tie.
- **Contamination.** AMI has been public for about 20 years and is a standard speech benchmark; product and judge models may have seen it. WER is reported as a best case, and a closed-book control for questions is part of the protocol (not yet run).
- **Account and settings effects.** Both products ran on the author's main accounts (one product accepts only work accounts); personal dictionaries are cleared for scored runs, but voice profiles and account names could not be removed.
- **Terms-of-service constraints.** Some products' terms restrict publishing comparative results without consent; those products are excluded for now (see [Reproducing](#reproducing)), which limits how general the comparison can be.

## Reproducing

**Setup.** Python 3.11 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                                                   # install (locked)
uv run pytest                                             # offline tests, no network, no API spend
uv run concentrate figures 20261103T002456Z --check       # regenerate every figure and compare
```

**Replaying AMI and scoring it.** Download the AMI files listed in [`docs/method/AMI_REPLAY.md`](docs/method/AMI_REPLAY.md#8-exact-files-to-download) into `data/ami/` (checksums in [`data/ami/MANIFEST.json`](data/ami/MANIFEST.json)), play each into the products as described there, then sync and score. Synced products stay in local, gitignored SQLite files.

```bash
uv run concentrate sync --source granola-mcp --db data/ami_granola.db   # read-only, browser sign-in
uv run concentrate sync --source wispr-mcp --db data/ami_wispr.db
uv run concentrate ami status                                           # ground truth and recordings found
uv run concentrate ami coverage                                         # capture track, $0
uv run concentrate ami wer --db data/ami_granola.db --product granola   # transcription track, $0
uv run concentrate facts stage1                                         # fact-token recall, $0
uv run concentrate facts stage2 --quote                                 # notes grounding: price first
uv run concentrate mcp-contract                                         # MCP drift check
```

**Costs.** Capture, WER, fact recall and the contract check make no LLM calls. Judged steps need `ANTHROPIC_API_KEY` in `.env` (see `.env.example`): notes grounding about $0.11 for all 7 meetings; answer attribution at most about $0.07; the AMI retention replication about $8 (quoted, not run). Every paid command prints an exact quote first and asks before spending more than $2.

**Product plans.** Reading Granola transcripts through its MCP requires a paid plan (a free trial covers it), and Granola accepts work accounts only. Wispr Flow needs its Notetaker, MCP access and cloud sync enabled; plan requirements beyond that have not been verified.

**Products excluded for now.** Notion, tl;dv and products reached through Zoom's API have terms that require consent before comparative results are published, so they are not included. Granola's organisation Platform Terms contain a benchmarking clause; how it applies to this work is under review.

**Your own meetings.** The black-box kit runs the question tracks on your own account, locally, with only content-free scores leaving your machine: [`docs/method/BLACKBOX.md`](docs/method/BLACKBOX.md).

## Roadmap to v1

1. **More products,** starting with Fireflies, Circleback and Otter, subject to each product's terms.
2. **A frozen, versioned meeting set,** so that results from different dates and products are comparable, and nightly replay can detect regressions.
3. **A public leaderboard,** per track, with intervals and status labels, never a single combined score.
4. **A vendor response and dispute process:** products are shown their results before publication, can point out errors in method or data, and their responses are published alongside.
5. **A technical report** covering the AMI results once the calibrated judge, about 100 human labels and the capture stress tests are complete.

## Repository guide

| Path | What it holds |
|---|---|
| [`docs/`](docs/README.md) | Method, tracks, dated results, product implications, engineering notes, archive ([map](docs/README.md)) |
| [`packages/concentrate/`](packages/concentrate/) | The Python package and `concentrate` CLI: sources (read-only MCP clients), AMI loaders and scorers, evals, figures. The package keeps its earlier name |
| [`evals/reports/`](evals/reports/) | Committed result files: numbers and IDs only. Older files use the earlier project name |
| [`data/`](data/README.md) | AMI manifest and AMI-derived questions (committed); synced products and private data (gitignored) |
| [`prompts/`](prompts/) | Versioned judge and checker prompts |
| [`tests/`](tests/) | Offline tests; HTTP and MCP are faked |
| [`web/`](web/) | A local web viewer for results (archived Concentrate-era views) |

Contributing a product or system: [`CONTRIBUTING.md`](CONTRIBUTING.md). Reporting a problem: [`SECURITY.md`](SECURITY.md).

## Related work

TENOR sits between model benchmarks for meetings (QMSum, MeetingQA, ELITR-Bench, MeetBench-XL, MeetingProbe), memory benchmarks over chat (LongMemEval, LoCoMo, BEAM) and claim-level faithfulness checking (FActScore, MiniCheck). What it adds is measurement of shipped products, end to end, on the same public meetings, through the interfaces their users and agents use. Table, gap and references: [`docs/RELATED_WORK.md`](docs/RELATED_WORK.md).

## Licence

Code is [MIT](LICENSE). The synthetic Northwind data under `data/fixtures/` is CC BY 4.0 ([`data/README.md`](data/README.md#license)). AMI-derived files (questions, labels, scores) are adaptations of the AMI Meeting Corpus (CC BY 4.0) and carry the attribution below. Product outputs are not redistributed beyond short quoted examples beside the AMI source.

## Data attribution

> Uses the AMI Meeting Corpus (University of Edinburgh and AMI consortium partners), licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), https://groups.inf.ed.ac.uk/ami/corpus/. Changes: the audio was played in real time into third-party meeting products, and questions were derived mechanically from the manual annotations. Citation: J. Carletta et al., "The AMI Meeting Corpus: A Pre-announcement", MLMI 2005, LNCS 3869, pp. 28–39.

## Not affiliated with Granola or Wispr

TENOR is independent research. It is not affiliated with, endorsed by or funded by Granola or Wispr. Everything reported about either product is observable through its public interfaces, using the author's own accounts; no claim is made about either product's internals. Product names are used only to identify what was measured.

## Citation

```bibtex
@software{lakhani2026tenor,
  author = {Lakhani, Kian},
  title  = {TENOR: an open benchmark for meeting note-takers (Transcripts, Evidence, Notes, Outcomes, Recall)},
  year   = {2026},
  url    = {https://github.com/KLKH7/TENOR},
  note   = {Research prototype}
}
```

See also [`CITATION.cff`](CITATION.cff).
