# TENOR (Transcripts, Evidence, Notes, Outcomes, Recall)

**An open benchmark for meeting note-takers.**

TENOR replays public meeting recordings into real meeting products and measures, from the outside, what their users depend on: whether the meeting was captured, whether the notes are faithful and complete, whether answers about the meeting are right, and what is lost when transcripts are deleted. Granola and Wispr Flow are the reference products.

*Independent research prototype; [not affiliated with Granola or Wispr](#not-affiliated-with-granola-or-wispr).*

## Key findings (preliminary)

All findings come from 7 public AMI meetings and are preliminary; read them as directions, not rankings.

- **Capture works under normal use.** Both products captured almost all speech: 98.5% (Granola) and 99.4% (Wispr Flow) of 15-second windows, across 7 meetings ([Capture](#capture)).
- **Note claims are mostly grounded.** 90% of Granola's and 81% of Wispr Flow's note claims were supported by both the product's and AMI's transcript (387 claims; corrected automated run, not yet human-calibrated) ([Notes](#notes)).
- **List answers are precise but incomplete.** On 30 list questions, 86% of Granola Chat's items were true but it found 74% of the annotated items; listing every item from the full transcript found 90% but only 61% of its items were true, through over-inclusion rather than invention ([Answers](#answers)).
- **Answering from notes alone roughly halves correct answers.** Correct answers fell from 41% with transcripts to 21% from notes alone (68 questions, p = 0.001) ([Retention](#retention-what-deleting-transcripts-costs)).
- **Automated judges over-counted errors in every human-checked sample.** None of the 3 strongest automated "confident contradictions" was wrong on human reading (23 human labels in total) ([Judges](#how-far-to-trust-the-automated-judges)).
- **Long silences did not cause silent failures; an app update did.** In two runs with 17–19 minutes of silence mid-meeting, both products warned the user and resumed when speech returned; in one run an automatic application update ended an active recording without a visible warning ([Capture under stress](#capture-under-stress)).

## Status

What has been built and measured so far, as of 5 November 2026.

| What exists | Size |
|---|---|
| Public meetings replayed into products | 7 AMI meetings (about 3 hours of audio), each replayed into 2 products: Granola and Wispr Flow |
| Questions about those meetings | 68, built by fixed templates from AMI's human annotations (13 used during development) |
| Human labels on product outputs | 23 (20 note claims, 3 Chat answers), from 1 verifier |

**All AMI results are preliminary.** The sample is 7 meetings, the human-labelled set is small, and the automated judges have not yet been calibrated against enough human labels to be trusted on their own. No result below is confirmed. Planned next: capture stress tests and a nightly replay suite ([next research step](#capture-under-stress)), and about 100 human labels.

Results from an earlier study on the author's own private meetings are collected in one place, [Earlier private study](#earlier-private-study).

## Motivation

Why measure shipped meeting products end to end, and why these two.

Meeting note-takers are now ordinary work tools. People rely on them to record what was said, write it up, and answer questions about it later, often through an AI agent that reads the product's data via MCP (Model Context Protocol, the interface agents use to call a product's tools). When one of these steps fails, the user usually cannot tell: a missed half-hour, a misheard number or an over-confident answer looks the same as a correct one.

**Existing benchmarks test parts, not shipped products.** Speech-recognition benchmarks score transcription models; agent benchmarks score agents on fixed tasks. Meeting benchmarks such as QMSum, MeetingQA and ELITR-Bench give a model a transcript and grade the model; memory benchmarks such as LongMemEval and LoCoMo use chat or generated dialogue. None that we found tests a shipped meeting product end to end, from audio in to answers out, through the interfaces its users and their agents actually use ([`docs/RELATED_WORK.md`](docs/RELATED_WORK.md)). The closest comparison, a vendor-run test of four note-takers on five AMI meetings, scores notes only.

**Outside-in evaluation fills that gap.** TENOR plays the same public audio into each product, reads the results back only through each product's public, read-only MCP tools, and scores them against human ground truth. It makes no claims about product internals; everything reported is observable by any user.

**Why these two reference products.** Granola and Wispr Flow are the two leading bot-free meeting note-takers (they capture audio on the user's device rather than sending a bot into the call) with public MCP access. Both can be measured the same way, on the same meetings, through the same kind of interface. More products are planned ([roadmap](#roadmap-to-v1)).

## Method

How audio goes in, how outputs come back, and how they are scored.

![How does the outside-in evaluation harness work? Public AMI meeting audio is played into Granola and Wispr Flow at once; transcripts, notes and answers are read back through each product's read-only MCP tools into a private local store, then scored by scorers that use no LLM and by an LLM judge against AMI's human transcripts, annotations and human labels](docs/figures/harness_flow.svg)

*Figure 1. How does the evaluation harness work? Boxes are stages; solid arrows show what passes from one stage to the next, and dashed arrows show the answer key each scorer uses. Granola is black and Wispr Flow teal, as in every figure. The 7 AMI meetings total 3 hours of audio. Only scores leave the private store.*

**AMI replay protocol.** Meetings come from the AMI Meeting Corpus: real recorded speech with human transcripts, word timings and annotations (decisions, actions, problems), licensed CC BY 4.0. The set is the four-meeting IS1008 design-team series, ES2006a (another design-team role-play), IB4003 (a staged meeting) and EN2002b (a naturally occurring meeting). Each meeting's headset mix is played in real time as system audio into both products at once. The protocol for scored runs clears personal dictionaries, mutes the speakers so the room stays silent, and drops and counts any microphone turns. Details, licence analysis and risks: [`docs/method/AMI_REPLAY.md`](docs/method/AMI_REPLAY.md).

**Read-only MCP collection.** Each product's notes, transcripts and answers are read through its official MCP server. Every call goes through an allowlist that refuses any tool not on it before anything is sent, so no write tool can be called. Product outputs are stored locally under gitignored paths and are never committed; only scores, labels and short quoted examples beside the AMI source are published. Harness and pairing: [`docs/method/HARNESS.md`](docs/method/HARNESS.md).

**Contract check (harness maintenance).** Products may change their MCP reply formats without notice, so a contract check compares each read tool's reply shape with a committed baseline and tells us when the harness's parser needs updating. It was added on 5 November 2026, after the harness detected a change in one product's reply format and was updated ([details](docs/tracks/AGENT_INTERFACE.md#harness-maintenance-the-mcp-contract-check)).

**Free scorers.** Capture coverage aligns AMI's timed human words with the product transcript in 15-second windows. Word error rate (WER) and content-word recall compare the full transcript with AMI's. Fact-token recall checks numbers, money, names and roles. None of these uses an LLM, so they cost nothing and are deterministic. Track definitions: [`docs/tracks/`](docs/tracks/README.md).

**Calibrated judging.** Notes and answers need an LLM judge, and on meeting outputs automated judges over-count errors. The causes found so far are each fixable: judges penalise names where the answer key uses roles, product boilerplate is read as content, a small judge's verdict contradicts its own reasoning, and a one-line summary used as the answer key drops nuance the product kept. The calibrated judge adds a speaker roster from the meeting's own introductions, strips product boilerplate, uses a stronger model, scores list answers per item, and is reported with its agreement with human labels. Details: [`docs/method/CALIBRATED_JUDGING.md`](docs/method/CALIBRATED_JUDGING.md).

**Human verification.** A human labels a sample of note claims and answers against the AMI source, on public AMI items so that labels can be released. These labels measure how far each automated judge over- or under-counts. Protocol and status: [`docs/method/HUMAN_VERIFICATION.md`](docs/method/HUMAN_VERIFICATION.md).

## Results

What each product did on the 7 AMI meetings, one measure per row, Granola first. *Preliminary* means small n or a method still changing; read it as a direction, not a ranking. Intervals in brackets are 95% confidence intervals (Wilson score intervals unless stated). Dated log of every result: [`docs/results/INDEX.md`](docs/results/INDEX.md).

![Where do Granola and Wispr Flow differ, and by how much? Dot plot of five measures both products have. Speech captured: Granola 98.5%, Wispr Flow 99.4% of 15-second windows. Note claims supported by both transcripts: 90% vs 81%. Answers from notes, correct or partly correct: 50% vs 44%; correct: 21% vs 12%. Word error rate: 16.6% vs 16.0%](docs/figures/results_overview.svg)

*Figure 2. Where do Granola and Wispr Flow differ, and by how much? Each dot is one product's value on a measure both products have (Granola black, Wispr Flow teal); higher is better in the top panel and lower is better in the bottom one. Solid whiskers are 95% confidence intervals (Wilson score). Dotted whiskers are the lowest and highest meeting, used for speech captured and word error rate because windows and words cluster within meetings. n: speech captured, 665 15-second windows per product over 7 meetings; note claims, 216 (Granola) and 171 (Wispr Flow) over 7 meetings; answers from notes, the same 68 questions over 5 meetings, answered by our reader from each product's notes; word error rate, 28,581 reference words over 7 meetings, pooled. Paired sign tests on the answers: Wispr Flow was better on 7 questions and worse on 11 for correct or partly correct (p = 0.48), and better on 4 and worse on 10 for correct (p = 0.18); neither difference is significant. Note claims differ between the products, so they have no paired test. Data: AMI Meeting Corpus.*

Measures only one product has (Granola Chat answers; Wispr Flow's MCP has no question-answering tool) are left out of Figure 2 and given in the tables below.

### Capture

Did each product record the whole meeting? We split AMI's human transcript into 15-second windows and count a window as captured when at least 40% of its distinctive word pairs appear in the product's transcript.

![How much of what was said did each product capture? Line chart over one 30-minute AMI meeting: the share of speech captured in each 15-second window by Granola and Wispr Flow stays mostly above the 40% threshold and dips at the same moments, while a different meeting's transcript, used as a control, stays below it](docs/figures/capture_coverage.svg)

*Figure 3. How much of what was said did each product capture? Share of AMI's human-transcribed speech found in each product's transcript, per 15-second window, over one meeting (EN2002b, the one naturally occurring meeting, 30 minutes). A window counts as captured above the dashed 40% line. The dotted grey line scores an unrelated meeting's human transcript (IB4003) as if it were a product's, as a control: 0.9% of its windows pass. In this meeting Granola captured 93.6% of windows and Wispr Flow 96.4%; over all 7 meetings, 98.5% and 99.4% of 665 windows each. Data: [`evals/reports/ami/coverage.json`](evals/reports/ami/coverage.json).*

| Measure | Granola | Wispr Flow | n | Status |
|---|---|---|---|---|
| 15-second windows of speech captured (%) | 98.5% | 99.4% | 665 windows per product, 7 meetings | Preliminary |
| Lowest and highest meeting (%) | 93.6–100% | 96.4–100% | 7 meetings | Preliminary |
| Gaps of 30 seconds or more (count) | 1 (51 s) | 0 | 7 meetings | Preliminary |
| Silent capture failures (count) | 0 | 0 | 7 meetings | Preliminary |

Windows cluster within meetings, so no pooled interval is given. Granola's one gap falls where Wispr Flow's coverage also dips, during overlapping speech. As a control, an unrelated meeting's transcript passes only 0–2% of windows, so the 40% threshold separates capture from chance.

Both products captured almost all of every meeting under normal conditions. Silent failure under stress, the capture question that matters most to users, is not yet tested ([next research step](#capture-under-stress)). Track: [`docs/tracks/CAPTURE.md`](docs/tracks/CAPTURE.md).

### Transcription

How accurate is each product's transcript? We compare it with AMI's manual transcript using word error rate (WER: word edits needed to turn one into the other, divided by the number of reference words) and content-word recall (the share of AMI's content words that appear anywhere in the product transcript).

| Measure | Granola | Wispr Flow | n | Status |
|---|---|---|---|---|
| WER, all meetings pooled (%, lower is better) | 16.6% | 16.0% | 7 meetings, 28,581 reference words | Preliminary |
| WER, lowest and highest meeting (%) | 9.3–28.6% | 8.0–26.4% | 7 meetings | Preliminary |
| Content-word recall, pooled (%, higher is better) | 87.8% | 90.0% | 7 meetings | Preliminary |
| Content-word recall, lowest and highest meeting (%) | 77.7–94.4% | 82.9–96.9% | 7 meetings | Preliminary |

Pooled values divide total errors by total words, so longer meetings weigh more. Granola's microphone turns are dropped before scoring (4 turns in 2 meetings); Wispr Flow has no separate microphone channel, so its whole transcript is scored. No significance test was run.

The two products are within 0.7 points on pooled WER: Wispr Flow was lower on 4 meetings, Granola on 2, and 1 was tied. Wispr Flow's content-word recall was higher on all 7 meetings, by 0.5 to 5.2 points. Both did worst on EN2002b, the one naturally occurring meeting. Because AMI is a standard speech benchmark that product models may have seen, these figures are a best case, not a claim about everyday accuracy. Track: [`docs/tracks/TRANSCRIPTION.md`](docs/tracks/TRANSCRIPTION.md); numbers: [`evals/reports/ami/wer.json`](evals/reports/ami/wer.json).

### Notes

Are the claims in each product's notes backed by what was said? We split the notes into single claims and check each one against both the product's own transcript and AMI's human transcript with an automated checker; a human checks a sample.

| Measure | Granola | Wispr Flow | n | Status |
|---|---|---|---|---|
| Note claims supported by both transcripts (%) | 90% [86, 94] (195 of 216) | 81% [74, 86] (138 of 171) | 387 claims, 7 meetings | Preliminary |
| Human check of sampled claims | 12 of 20 could be judged; 0 wrong, 1 went beyond what was said | (pooled with Granola) | 20 claims | Preliminary |

The first row is from the corrected automated run, which replaces a first pass (94% and 80%). The human sample pools both products and was drawn mostly from claims the checker had flagged.

Granola's notes were supported 9.6 points more often (95% CI 2.5 to 16.7 if claims were independent; the true interval is wider, since claims cluster in 7 meetings). Granola was higher in 4 meetings, Wispr Flow in 2, and 1 was tied. About half the gap comes from claims on which the two checks differed by one step (one said supported, the other said inferred); counting those as supported gives 95.8% and 91.8%. Few claims were clearly added by the note-writer: 0 of 216 for Granola and 2 of 171 for Wispr Flow. Human calibration is still pending. Full breakdown by claim type: [`docs/tracks/NOTES.md`](docs/tracks/NOTES.md#ami-claim-types); method: [`docs/method/WRONG_FACTS.md`](docs/method/WRONG_FACTS.md).

### Answers

When asked to list a meeting's decisions, actions or problems, how many of the right items does an answer contain, and how many of its items are true? We score each listed item against AMI's annotated items and its transcript. Wispr Flow's MCP has no question-answering tool, so this section covers Granola only.

| How the answer was produced | Annotated items found (recall) | Listed items that are true (precision) | Items per answer | n | Status |
|---|---|---|---|---|---|
| Granola Chat, transcripts kept | 0.74 [0.61, 0.87] | 0.86 [0.77, 0.93] | 3.2 | 30 questions | Preliminary |
| Our reader on Granola's notes only | 0.73 [0.58, 0.86] | 0.72 [0.61, 0.83] | 3.1 | 30 questions | Preliminary |
| Our reader listing every item from Granola's full transcript | 0.90 [0.79, 0.98] | 0.61 [0.51, 0.71] | 11.9 | 30 questions | Preliminary |

Values are means per question with 95% bootstrap intervals. An item counts as true if it matches an annotated item or is confirmed by AMI's transcript, so true items the annotators left out are not counted wrong. Paired sign tests per question (n = 30): listing every item found more annotated items than Chat on 8 questions and fewer on 0 (p = 0.008), but had a lower share of true items on 23 and a higher share on 3 (p < 0.001). Against notes only, it found more items on 8 questions and fewer on 0 (p = 0.008); the share of true items did not differ significantly (12 higher, 16 lower, p = 0.57). Chat and notes only were not compared directly. One automated judge scored all items, with no human check yet.

**Chat is precise but incomplete; listing everything is complete but imprecise.** The extra items from the full listing were over-inclusion, not invention: every one of its 357 quotes appears in the transcript, and in a sample of 8 items marked wrong the reasons were "proposed but not decided", "not assigned" or "another topic". In a hand reading of Chat's imperfect answers by one coder, most real shortfalls were missing items and none was invented content. Track: [`docs/tracks/ANSWERS.md`](docs/tracks/ANSWERS.md#e1-results-preliminary); numbers: [`evals/reports/ami/list_check.json`](evals/reports/ami/list_check.json).

#### How far to trust the automated judges

Do automated "wrong" verdicts survive a human reading? A human labelled a sample of outputs the automated judges had flagged, against the AMI source.

![When an automated judge says "wrong", does a human agree? Stacked bars of human verdicts on 23 flagged outputs: of 20 note claims, the human found 11 supported, 1 going beyond what was said and none wrong, and could not judge 8; all 3 Chat answers the judge called confidently wrong were partly right](docs/figures/judge_vs_human.svg)

*Figure 4. When an automated judge says "wrong", does a human agree? Each bar groups outputs by the automated verdict; its segments show what one human verifier decided, with counts. n = 23 human labels: 20 note claims sampled from both products' notes, mostly from claims the checker had flagged, and the 3 Chat answers the automated judge called most confidently wrong. Note-claim verdicts are from the first checking pass, on which the sample was drawn. No interval is shown: counts this small give a direction, not a rate. The verifier also designed the study.*

| Measure | Granola | Wispr Flow | n | Status |
|---|---|---|---|---|
| Of the 3 strongest automated "confident contradictions" in Chat answers, wrong on human check | 0 of 3 | no question tool | 3 answers | Preliminary |
| Note claims a human found wrong, of those comparable with the checker | 0 of 12 | (pooled with Granola) | 12 claims | Preliminary |

All 3 Chat answers were partly right. On the note claims, the human agreed with the checker on 4 of 12 for the first pass (on which the sample was drawn) and 7 of 12 for the corrected run.

**Automated judges over-count errors on these outputs.** In both human-checked samples, most automated error verdicts did not survive a human reading. With 23 labels this is a direction, not a rate; the over-count factor will be reported once about 100 labels exist. Track: [`docs/tracks/ANSWERS.md`](docs/tracks/ANSWERS.md).

### Retention: what deleting transcripts costs

If a product keeps only its notes and deletes transcripts, how many questions can still be answered? We answer the same 68 AMI questions (5 meetings) five ways, each over the question's own meetings, and score each answer correct, partially correct or wrong with an automated judge.

![What does deleting transcripts cost, and what wins it back? Dot plot of 68 AMI questions answered five ways. Correct: Granola Chat with transcripts 41%, from Granola's notes 21%, from Wispr Flow's notes 12%, notes plus extracted facts 19%, notes plus the transcript when needed 32%](docs/figures/ami_retention.svg)

*Figure 5. What does deleting transcripts cost, and what wins it back? Share of 68 AMI questions (5 meetings) answered correctly (filled dots) and correctly or partly correctly (open dots), five ways. Whiskers are 95% confidence intervals (Wilson score). Granola Chat answers with Granola's own model and its transcripts. Every other row uses our reader: over Granola's notes (black) or Wispr Flow's notes (teal), then Granola's notes plus extracted facts, then those plus the transcript, read only when the answer abstains or hedges. An automated judge scored every answer against AMI's human summaries. Paired tests are given below the table.*

| How the answer was produced | Correct (%) | Correct or partly correct (%) | n | Status |
|---|---|---|---|---|
| Granola Chat, transcripts kept | 41% [30, 53] | 76% [65, 85] | 68 questions | Preliminary |
| Our reader on Granola's notes only | 21% [13, 32] | 50% [38, 62] | 68 questions | Preliminary |
| Our reader on Wispr Flow's notes only | 12% [6, 22] | 44% [33, 56] | 68 questions | Preliminary |
| Granola's notes plus an extracted fact layer | 19% [12, 30] | 51% [40, 63] | 68 questions | Preliminary |
| Notes plus facts, reading the transcript only when the answer abstains or hedges | 32% [22, 44] | 60% [48, 71] | 68 questions | Preliminary |

Paired sign tests on correct answers, as questions gained–lost: Granola's notes only vs Chat, 2–16 (p = 0.001); Wispr Flow's notes vs Granola's notes, 4–10 (p = 0.18, not significant); adding facts to Granola's notes, 2–3 (not significant); reading the transcript when needed vs notes plus facts, 9–0 (p = 0.004). Chat vs reading the transcript when needed: 17–6 on correct or partly correct (p = 0.035 uncorrected; not significant after Holm correction for multiple tests) and 9–3 on correct (p = 0.15).

**Deletion costs a lot; the fact layer adds nothing; reading the transcript on demand recovers part of the loss, not all.** Chat used Granola's own model and the other rows used our reader, so the first drop mixes deletion with a change of model. The automated judge over-counts errors, equally for all rows; decision questions score 0–20% everywhere because the answer key is strict. This does not replicate the earlier private study, where on-demand transcripts nearly matched Granola. Track: [`docs/tracks/RETENTION.md`](docs/tracks/RETENTION.md); numbers: [`evals/reports/ami/deletion_ami.json`](evals/reports/ami/deletion_ami.json).

### Earlier private study

Before the AMI work, the same measures were run on the author's own 4 private Granola meetings (October–November 2026). These numbers are **not reproducible from public data**, cover Granola only, and are superseded wherever an AMI replication exists.

| Measure | Granola | n | AMI replication |
|---|---|---|---|
| Note claims supported / inferred / unsupported (%) | 84% [77, 89] / 14% / 0% | 132 claims | Superseded by [Notes](#notes) |
| Answers judged useful and comprehensive (%) | 89% [77, 95] | 47 questions | None yet |
| Answers that point inside a meeting (line, chunk or timestamp) | 0 of 47 | 47 questions | None yet |
| Correct answers: Granola's agent / notes only / plus facts / plus on-demand transcript (%) | 89% / 57% / 66% / 87% | 47 questions | Superseded by [Retention](#retention-what-deleting-transcripts-costs) |

All questions were used during development. Notes only vs Granola's agent: 2 gained, 17 lost (p = 0.001); the fact layer's gain was not significant. Granola's answers linked to whole meetings only and carried no abstain or coverage field. Details: [`docs/results/TENOR_TECHNICAL_REPORT.md`](docs/results/TENOR_TECHNICAL_REPORT.md), [`docs/tracks/NOTES.md`](docs/tracks/NOTES.md), [`docs/tracks/AGENT_INTERFACE.md`](docs/tracks/AGENT_INTERFACE.md).

## Capture under stress

*Does a product keep recording through conditions that break capture in real use, and does it tell the user when it stops?* Silent capture failure (a recording that appears to run but misses part of the meeting) was the most common theme in a 2026 sample of public user reports about meeting note-takers (about 16 of 75 items, from one forum and mostly one product's users). Normal replays showed no failures in either product ([Capture](#capture)), so each stress condition replays a known AMI meeting into both products at once and scores capture before and after the stressor with the free coverage scorer. Full design and run sheet: [`docs/tracks/CAPTURE.md`](docs/tracks/CAPTURE.md).

### Long silence (A1)

Meeting IS1008d with digital silence inserted at 12 minutes: 17 minutes of silence in run 1, 19 minutes in run 2. Microphone input was held at zero; neither product transcribed any microphone speech in either run.

| Run | Product | Warning during the silence | Speech captured after the silence (15-second windows) | What happened |
|---|---|---|---|---|
| 1 (17 min) | Granola | Yes: an "are you still there?" prompt with a 3-minute countdown, about 14½ minutes into the silence | Resumed immediately | Not answered; speech returned before the countdown ended. Later windows are missing only because the run was stopped early |
| 1 (17 min) | Wispr Flow | None seen | 0% | The app installed an automatic update and restarted about 10 minutes into the silence, which ended the recording; nothing after it was captured |
| 2 (19 min) | Granola | Yes, same prompt | 100% of 46 windows | The prompt was answered |
| 2 (19 min) | Wispr Flow | Yes, a prompt | 100% of 46 windows | The prompt was answered |

*Note.* Two runs on one meeting; read as observations, not rates. In run 2 both prompts were answered by the person running the test, so run 2 shows the behaviour when a user is present. The restart in run 1 is confirmed by the update installer's own log; whether it was triggered by the silence or would have happened at any time is unknown. Not yet tested: what a product does when its countdown runs out with nobody present (planned: 20 or more minutes of silence, nobody at the machine). Numbers: [`evals/reports/ami/stress_A1_IS1008d_run1.json`](evals/reports/ami/stress_A1_IS1008d_run1.json), [`run2`](evals/reports/ami/stress_A1_IS1008d_run2.json).

**What this shows so far.** Both products warn during a long silence and resume when speech returns, so silence alone did not cause a silent failure. The one loss came from outside the meeting: an application update ended an active recording without a visible warning. Conditions of that kind (another app in front, system sleep, network loss) are the next ones to test.

### Planned conditions

| Condition | What it tests |
|---|---|
| A1, long silence with nobody present | Whether capture ends for good when a prompt goes unanswered |
| A2, two meetings in one recording | Whether content from one meeting is written into the other |
| A3, second recording started late | Whether the opening is missed, and which note gets the overlap |
| A4, app in the background or on another desktop | Capture when the app is not visible |
| A5, display sleep or lid closed | Capture across system sleep |
| A6, network drop for 60 seconds | Behaviour while offline |

### Detecting a stopped transcript

*Could a product tell, while recording, that its transcript has stopped growing?* A prototype detector compares two signals the product already has, speech in the incoming audio and new transcript text, and alerts when there is speech but no new text. On Granola transcripts with failures inserted artificially (237 failures across 6 held-out meetings, 2.7 hours), it caught every failure with no false alarms when given an exact speech signal, and 234 of 237 with about one false alarm per hour using a simple loudness-based speech signal. It cannot detect a failure in the audio input itself, because it listens to the same input. Details: [`docs/tracks/CAPTURE_HEALTH.md`](docs/tracks/CAPTURE_HEALTH.md).

![Can a product tell when its transcript has stopped? Timeline of one simulated failure: speech continues while new transcript text stops for 118 seconds, and four detector variants raise an alert 13 to 33 seconds after it starts](docs/figures/capture_health.svg)

*Figure 6. Can a product tell when its transcript has stopped? One simulated failure in a held-out AMI meeting (IS1008b, minutes 20.5–25). The top two rows show when speech is present, from AMI's word timings (exact) and from audio loudness. The third row shows when transcript text arrives; the text that arrived in the shaded 118 seconds was removed (grey dashed marks) to simulate the failure. The bottom four rows show when each detector variant raises its alert, labelled with the seconds since the failure began. Variants differ in the speech signal they use and in whether they watch for new transcript blocks or for estimated live words. Totals over 237 simulated failures are given in the text above.*

### Nightly replay

The same harness, run every night on a fixed, versioned set of public meetings, becomes a regression test: capture coverage, word error rate and fact recall per product, compared with the previous night and with human ground truth. A separate contract check keeps the harness in step with each product's MCP reply format; it maintains the harness and does not measure the product.

## Design implications for note-takers

What TENOR's evidence could mean for product design (capture-health indicators, completeness prompts, marking inferences, reconciling numbers and names across sources without storing audio, and more) is discussed product-neutrally in [`docs/product/IMPLICATIONS.md`](docs/product/IMPLICATIONS.md). Each item gives the evidence, what a product could do, how TENOR would measure it, and why a product might reasonably not do it.

## Limitations

What these results do and do not show.

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

How to rerun every free measure, and what the paid ones cost.

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
uv run concentrate ami wer --db data/ami_wispr.db --product wispr --keep-mic  # Wispr Flow has no mic channel
uv run concentrate facts stage1                                         # fact-token recall, $0
uv run concentrate facts stage2 --quote                                 # notes grounding: price first
uv run concentrate mcp-contract                                         # reply-format check (harness)
```

**Costs.** Capture, WER, fact recall and the contract check make no LLM calls. Judged steps need `ANTHROPIC_API_KEY` in `.env` (see `.env.example`): notes grounding about $0.10 for all 7 meetings (measured); answer attribution at most about $0.07; the AMI retention replication $5.64 (run; quoted at about $8). Every paid command prints an exact quote first and asks before spending more than $2.

**Product plans.** Reading Granola transcripts through its MCP requires a paid plan (a free trial covers it), and Granola accepts work accounts only. Wispr Flow needs its Notetaker, MCP access and cloud sync enabled; plan requirements beyond that have not been verified.

**Products excluded for now.** Notion, tl;dv and products reached through Zoom's API have terms that require consent before comparative results are published, so they are not included.

**Your own meetings.** The black-box kit runs the question tracks on your own account, locally, with only content-free scores leaving your machine: [`docs/method/BLACKBOX.md`](docs/method/BLACKBOX.md).

## Roadmap to v1

What is needed before TENOR results can be compared across products and dates.

1. **More products,** starting with Fireflies, Circleback and Otter, subject to each product's terms.
2. **A frozen, versioned meeting set,** so that results from different dates and products are comparable, and nightly replay can detect regressions.
3. **A public leaderboard,** per track, with intervals and status labels, never a single combined score.
4. **A vendor response and dispute process:** products are shown their results before publication, can point out errors in method or data, and their responses are published alongside.
5. **A technical report** covering the AMI results once the calibrated judge, about 100 human labels and the capture stress tests are complete.

## Repository guide

Where each part of the project lives.

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

How the code, data and derived files may be reused.

Code is [MIT](LICENSE). The synthetic Northwind data under `data/fixtures/` is CC BY 4.0 ([`data/README.md`](data/README.md#license)). AMI-derived files (questions, labels, scores) are adaptations of the AMI Meeting Corpus (CC BY 4.0) and carry the attribution below. Product outputs are not redistributed beyond short quoted examples beside the AMI source.

## Data attribution

The required attribution for the AMI Meeting Corpus.

> Uses the AMI Meeting Corpus (University of Edinburgh and AMI consortium partners), licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/), https://groups.inf.ed.ac.uk/ami/corpus/. Changes: the audio was played in real time into third-party meeting products, and questions were derived mechanically from the manual annotations. Citation: J. Carletta et al., "The AMI Meeting Corpus: A Pre-announcement", MLMI 2005, LNCS 3869, pp. 28–39.

## Not affiliated with Granola or Wispr

TENOR is independent research. It is not affiliated with, endorsed by or funded by Granola or Wispr. Everything reported about either product is observable through its public interfaces, using the author's own accounts; no claim is made about either product's internals. Product names are used only to identify what was measured.

## Citation

How to cite TENOR.

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
