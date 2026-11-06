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
| Scorers | Capture coverage, WER and fact-token recall (all free); a grounding checker and an answer judge (LLM, calibration in progress) |

**All AMI results are preliminary.** The sample is 7 meetings, the human-labelled set is small, and the automated judges have not yet been calibrated against enough human labels to be trusted on their own. No result below is confirmed. Planned next: capture stress tests and a nightly replay suite ([next research step](#next-research-step-capture-reliability-and-regression-replay)), and about 100 human labels. The AMI replication of the retention study ran on 5 November 2026 ([results](#retention-what-deleting-transcripts-costs)).

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

**Contract check (harness maintenance).** Products may change their MCP reply formats without notice, so a contract check diffs each read tool's reply shape against a committed baseline and tells us when the harness's parser needs updating. It was added on 5 November 2026, after the harness detected a change in one product's reply format and was updated ([details](docs/tracks/AGENT_INTERFACE.md#harness-maintenance-the-mcp-contract-check)).

**Free scorers.** Capture coverage aligns AMI's timed human words with the product transcript in 15-second windows. WER (word error rate) and content-word recall compare the full transcript with AMI's. Fact-token recall checks numbers, money, names and roles. None of these uses an LLM, so they cost nothing and are deterministic. Track definitions: [`docs/tracks/`](docs/tracks/README.md).

**Calibrated judging.** Notes and answers need an LLM judge, and on meeting outputs automated judges over-count errors. The causes found so far are each fixable: judges penalise names where the answer key uses roles, product boilerplate is read as content, a small judge's verdict contradicts its own reasoning, and a one-line summary used as the answer key drops nuance the product kept. The calibrated judge adds a speaker roster from the meeting's own introductions, strips product boilerplate, uses a stronger model, scores list answers per item, and is reported with its agreement with human labels. Details: [`docs/method/CALIBRATED_JUDGING.md`](docs/method/CALIBRATED_JUDGING.md).

**Human verification.** A human labels a sample of note claims and answers against the AMI source, on public AMI items so that labels can be released. These labels measure how far each automated judge over- or under-counts. Protocol and status: [`docs/method/HUMAN_VERIFICATION.md`](docs/method/HUMAN_VERIFICATION.md).

## Results

Each table gives the measure, both products side by side, n, an interval or note, and a status. *Preliminary* means small n or a method still changing; read it as a direction, not a ranking. Intervals are 95% Wilson intervals unless stated. Dated log of every result: [`docs/results/INDEX.md`](docs/results/INDEX.md).

![Where do Granola and Wispr Flow differ, and by how much? On every measure both products have: both captured almost all speech (98.5% and 99.4% of 15-second windows); Granola's notes had more claims supported by both transcripts (90% vs 81%, about half of the gap one-notch checker disagreement); notes-only answers were close (50% vs 44% not wrong, 21% vs 12% correct, neither paired difference significant)](docs/figures/results_overview.svg)

Measures only one product has (Granola Chat answers; Wispr Flow's MCP has no question-answering tool) are left out of this chart and given in the tables below.

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
| Note claims supported by both the product's and AMI's transcript | 90% (195 of 216) | 81% (138 of 171) | 387 claims, 7 meetings | Granola [86, 94], Wispr Flow [74, 86]. Corrected automated run (`352698f`); supersedes the first pass (94% / 80%) | Preliminary |
| One-notch checker disagreement (`borderline`) | 12 of 216 (6%) | 19 of 171 (11%) | 387 claims | Mostly checker noise: one check says supported, the other inferred | Preliminary |
| Claims where the note may repeat a transcription error | 4 of 216 | 6 of 171 | 387 claims | Upper bound, not re-read by hand. 4 of the 10 are in the human sample: 2 supported, 1 overreach, 1 could not judge, none wrong | Preliminary |
| Claims the note-writer added (`note_error`) / overreach (`inferred`) | 0 / 4 of 216 | 2 / 5 of 171 | 387 claims | note_error 95% CI: Granola [0, 2%], Wispr Flow [0.3, 4%] | Preliminary |
| Human check of sampled claims | 20 sampled claims (per-product split not reported): 12 judgeable, 0 wrong, 1 overreach | | 20 | The automated checker had flagged most of these | Preliminary |
| *Earlier, 4 private meetings:* claims supported / inferred / unsupported | 84% / 14% / 0% | not measured | 132 claims | Supported [77, 89]; Granola only | Superseded by AMI |

On the corrected run Granola's notes were grounded 9.6 points more often than Wispr Flow's (95% CI 2.5 to 16.7 treating claims as independent; wider in truth, since claims cluster in 7 meetings). Granola was higher in 4 meetings, Wispr Flow in 2, tied in 1. About half the gap is one-notch checker disagreement: counting `borderline` as fine, 95.8% vs 91.8%. Human calibration is still pending. Track: [`docs/tracks/NOTES.md`](docs/tracks/NOTES.md); method: [`docs/method/WRONG_FACTS.md`](docs/method/WRONG_FACTS.md).

### Answers (Chat), and judge over-count

| Measure | Granola | Wispr Flow | n | Interval / notes | Status |
|---|---|---|---|---|---|
| Answers judged wrong, automated judge | 16 (24%) | not applicable | 68 questions | [15, 35]. Wispr Flow's MCP has no question-answering tool | Preliminary |
| Answers judged partial, automated judge | 24 (35%) | not applicable | 68 | [25, 47] | Preliminary |
| Of the 3 strongest automated "confident contradictions", clearly wrong on human check | 0 of 3 | — | 3 | All three were partly right | Preliminary |
| Note claims judged wrong by a human, of those the automated checker could be compared on | 0 of 12 | (pooled) | 12 | Human agreed with the checker on 4 of 12 (first pass, on which the sample was drawn); 7 of 12 on the corrected run | Preliminary |

![When an automated judge says "wrong", does a human agree? Of 23 human labels, none found an output wrong: of 7 note claims the first-pass checker called contradicted, the human found 4 supported and could not judge 3; all 3 Chat answers the judge called confidently wrong were partly right](docs/figures/judge_vs_human.svg)

**Automated judges over-count errors on these outputs.** In both human-checked samples, most automated error verdicts did not survive a human reading. With 23 labels this is a direction, not a rate: the over-count factor will be reported once about 100 labels exist. A comparison of both products through one fixed outside agent is built and not yet run. Track: [`docs/tracks/ANSWERS.md`](docs/tracks/ANSWERS.md).

### Agent interface

| Measure | Granola | Wispr Flow | n | Interval / notes | Status |
|---|---|---|---|---|---|
| *Earlier, 4 private meetings:* answers that point inside a meeting (line, chunk or timestamp) | 0 of 47 | not measured | 47 DEV questions | Meeting-level links only; no abstain or coverage field | Preliminary |
| *Earlier, 4 private meetings:* useful and comprehensive answers | 89% | not measured | 47 | [77, 95] | Preliminary |

Track: [`docs/tracks/AGENT_INTERFACE.md`](docs/tracks/AGENT_INTERFACE.md).

### Retention: what deleting transcripts costs

What does answering from notes alone cost once transcripts are deleted? Five conditions on the same 68 AMI questions (5 meetings), each answered over the question's own meetings. "Not wrong" = judged correct or partially correct; brackets are Wilson 95% CIs.

![What does deleting transcripts cost, and what wins it back? On 68 AMI questions, correct answers fell from 41% (Granola Chat, transcript kept) to 21% from Granola's notes alone (2 gained, 16 lost, p = 0.001); Wispr Flow's notes scored 12% (not significantly different); a fact layer added nothing (2-3); reading the transcript when notes fall short won back part of the loss (32%, 9-0, p = 0.004)](docs/figures/ami_retention.svg)

| Condition | Correct | Not wrong | Paired wins–losses, correct | Status |
|---|---|---|---|---|
| A. Granola Chat, transcript kept | 41% [30, 53] | 76% [65, 85] | — | Preliminary |
| B. Granola notes only (our reader) | 21% [13, 32] | 50% [38, 62] | vs A: 2–16, p = 0.001 | Preliminary |
| B′. Wispr Flow notes only (our reader) | 12% [6, 22] | 44% [33, 56] | vs B: 4–10, p = 0.18 (not significant) | Preliminary |
| C. B plus an extracted fact layer | 19% [12, 30] | 51% [40, 63] | vs B: 2–3, not significant | Preliminary |
| D. C plus the transcript, read only when C abstains or hedges | 32% [22, 44] | 60% [48, 71] | vs C: 9–0, p = 0.004 | Preliminary |

**Deletion costs a lot; the fact layer adds nothing; an on-demand transcript recovers part of the loss, not all.** Granola Chat still beats D: 17–6 on not-wrong answers (p = 0.035, uncorrected; not significant after Holm correction) and 9–3 on correct (p = 0.15). This does not replicate the earlier private study, where D nearly matched Granola. A used Granola's own Chat and B–D our reader, so A − B mixes deletion with a change of model. The automated judge over-counts errors, equally for all conditions; decision questions score 0–20% everywhere because the answer key is strict. Track: [`docs/tracks/RETENTION.md`](docs/tracks/RETENTION.md); numbers: [`evals/reports/ami/deletion_ami.json`](evals/reports/ami/deletion_ami.json).

*Earlier, 4 private meetings (47 DEV questions):* Granola's agent 89%, notes only 57% (2–17, p = 0.001), fact layer 66% (not significant), on-demand transcript 87%; superseded by the AMI run above ([interim report](docs/results/TENOR_TECHNICAL_REPORT.md)).

## Next research step: capture reliability and regression replay

**Why capture first.** Silent capture failure (a recording that appeared to run but captured nothing, or notes covering only part of a meeting) was the most common theme in a 2026 sample of public user reports about meeting note-takers: about 16 of about 75 items. The sample is small, drawn from one public forum and one product's user community, and has not yet been repeated for other products; but a failure the user cannot see undermines every other measure, whichever product it occurs in. TENOR's baseline found no failures in either product, so the question is which conditions produce them.

**Stress-test design.** Each condition replays a known AMI meeting into both products at the same time and is scored by the free coverage scorer; a gap with no visible warning in the app (recorded by screenshot) counts as a silent failure. Full design: [`docs/tracks/CAPTURE.md`](docs/tracks/CAPTURE.md).

| # | Condition | What it probes |
|---|---|---|
| A1 | 17 minutes of silence spliced mid-meeting (12-minute control) | Whether capture resumes when speech returns after a documented 15-minute no-audio timeout, and whether the user is told |
| A2 | Two meetings back to back, one recording | Content from one meeting written into another |
| A3 | Two meetings back to back, second recording started late | Missed openings; which note gets the overlap |
| A4 | App in the background or on another full-screen desktop | Capture when the app is not visible |
| A5 | Display sleep or lid closed | Capture across system sleep |
| A6 | Network drop for 60 seconds | Offline handling |

**Live detection.** A prototype capture-health detector (speech heard, transcript not growing, so alert) caught 237 of 237 injected failures with no false alarms in 2.7 h of TEST meetings when given a perfect VAD; injected failures only ([`docs/tracks/CAPTURE_HEALTH.md`](docs/tracks/CAPTURE_HEALTH.md)).

![Can a product tell when its transcript has stopped? One injected failure on a TEST AMI meeting: speech continues while transcript blocks stop, and each of four detector variants raises an alert 13 to 33 seconds later](docs/figures/capture_health.svg)

Scoring A0–A6 is free apart from cross-meeting bleed (about $0.02 per meeting); each condition takes about 25 minutes of playback.

**Nightly replay suite.** The same harness, run on a frozen, versioned set of public meetings every night, becomes a regression detector: coverage, WER and fact recall per product, compared with the previous run and with human ground truth, plus the MCP contract check. Coverage, WER and fact-recall changes are product regressions; the contract check only keeps the harness itself in step with each product's reply format. This is the centre of TENOR's forward plan, and it applies to any product that can be replayed into.

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
uv run concentrate mcp-contract                                         # reply-format check (harness)
```

**Costs.** Capture, WER, fact recall and the contract check make no LLM calls. Judged steps need `ANTHROPIC_API_KEY` in `.env` (see `.env.example`): notes grounding about $0.10 for all 7 meetings (measured); answer attribution at most about $0.07; the AMI retention replication $5.64 (run; quoted at about $8). Every paid command prints an exact quote first and asks before spending more than $2.

**Product plans.** Reading Granola transcripts through its MCP requires a paid plan (a free trial covers it), and Granola accepts work accounts only. Wispr Flow needs its Notetaker, MCP access and cloud sync enabled; plan requirements beyond that have not been verified.

**Products excluded for now.** Notion, tl;dv and products reached through Zoom's API have terms that require consent before comparative results are published, so they are not included.

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
