# Design implications for meeting note-takers

This page lists product ideas suggested by TENOR's evidence so far, for anyone who builds or evaluates meeting note-takers. Each item gives the evidence, what a product could do, how TENOR would measure it, and why a product might reasonably not do it.

These are hypotheses, not recommendations. The evidence behind most of them is preliminary (small n, one verifier), and none has been tested as a product feature. None involves storing audio.

## Summary

| Idea | Evidence strength | TENOR measure | Measurable now? |
|---|---|---|---|
| [Capture-health indicator and silent-failure detection](#1-capture-health-indicator-and-silent-failure-detection) | Public user reports; baseline replay found no failures; detector prototype on injected failures | Capture track, stress conditions; capture-health detector | Prototype (injected failures) |
| [Nightly replay regression suite](#2-nightly-replay-regression-suite) | One observed silent interface change | Replay of a frozen meeting set | Partly built |
| [Said, not noted](#3-said-not-noted-completeness) | About a third of Chat answers partial (automated judge) | Gold-item recall in notes and answers | Yes |
| [Inferred ink](#4-inferred-ink) | 14% of note claims inferred (4 private meetings) | Grounding classes per claim | Yes |
| [Margin notes](#5-margin-notes) | Cross-meeting answers often partial | Cross-meeting question templates | Yes |
| [Since last time](#6-since-last-time-in-pre-meeting-briefs) | Indirect only | "What changed" and action-owner questions | Partly |
| [Exact](#7-exact-numbers-codes-and-names-reconciled-across-sources) | Single-meeting lead on money and percentages | Fact-token recall | Yes |
| [Vocabulary from user edits](#8-learning-vocabulary-from-user-edits) | Name substitutions in transcripts | Name recall before and after | Needs edit replay |
| [Meeting-chat capture](#9-meeting-chat-capture) | Demand unproven; feasibility untested | Not yet defined | No |
| [Typed vs silent](#10-typed-vs-silent-notes) | Untested | Paired replay with typed cues | Designed |
| [Live recall](#11-live-recall-opt-in-experiment-only) | None | Not yet defined | No |

## 1. Capture-health indicator and silent-failure detection

- **Evidence.** Silent capture failures (a recording that appeared to run but captured nothing, or notes for only part of a meeting) were the most common theme in a 2026 sample of public user reports about meeting note-takers: about 16 of about 75 items, from one public forum and one product's user community ([`CAPTURE.md`](../tracks/CAPTURE.md)). TENOR's baseline replay found no failures in either reference product on 7 meetings, so the problem, if real at scale, appears under conditions the baseline does not exercise.
- **What a product could do.** Show capture health during the meeting (audio level received, transcript growing) and warn when audio arrives but no text does, or when capture stops while the meeting continues. Mark gaps in the transcript and notes rather than closing over them.
- **How TENOR would measure it.** The capture stress conditions (long silence, back-to-back meetings, background app, sleep, network drop): gap rate, and whether each gap was accompanied by a visible warning.
- **Prototype.** A detector that compares voice activity with transcript growth caught 237 of 237 injected failures on 6 TEST meetings (2.7 h) with no false alarms and a median delay of 11 s, given a perfect VAD and a live word stream; a plain energy VAD on recorded block times caught 234 with 1.1 false alarms per hour. Injected failures only, one product ([`CAPTURE_HEALTH.md`](../tracks/CAPTURE_HEALTH.md)).
- **Why a product might not.** Health indicators add visual noise to a calm interface; false warnings erode trust faster than rare silent failures; some failures (operating-system audio permissions, sleep) are outside the product's control.

## 2. Nightly replay regression suite

- **Evidence.** On 5 November 2026 a transcript tool in one product's MCP changed shape without an error or announcement, and downstream readers silently received empty results ([`AGENT_INTERFACE.md`](../tracks/AGENT_INTERFACE.md#mcp-drift-the-contract-check)).
- **What a product could do.** Replay a frozen set of public meetings every night through the shipped pipeline, and compare capture, transcription, notes and answers against the previous night and against human ground truth.
- **How TENOR would measure it.** TENOR's harness is such a suite from the outside: a frozen, versioned meeting set, free scorers (coverage, WER, fact recall) and an MCP contract check.
- **Why a product might not.** Real-time replay is slow and needs dedicated machines and accounts; public corpora under-represent a product's real users; internal telemetry may already catch most regressions.

## 3. Said, not noted (completeness)

- **Evidence.** About 35% of one product's Chat answers on AMI questions were judged partial by the automated judge (24 of 68; preliminary, judge not yet calibrated) ([`ANSWERS.md`](../tracks/ANSWERS.md)).
- **What a product could do.** Under the notes, a collapsed line such as "3 things said that aren't in your notes", each with its source line, that the user can add with one tap.
- **How TENOR would measure it.** Gold-item recall of AMI's annotated decisions and actions in the notes and answers, and the precision of any suggested additions against the transcript.
- **Why a product might not.** Notes are deliberately selective; surfacing omissions can make good notes look incomplete; low-precision suggestions cost attention.

## 4. Inferred ink

- **Evidence.** On the author's 4 private meetings, 14% of one product's note claims went beyond what was said (an added qualifier, a discussion written up as a decision, an unconfirmed owner), with none invented outright ([`NOTES.md`](../tracks/NOTES.md)).
- **What a product could do.** Render bullets that are inferences, not statements, in a slightly lighter style, with the reason on hover.
- **How TENOR would measure it.** Per-claim grounding classes (supported, inferred, unsupported, contradicted) and whether the product's own marking agrees with them.
- **Why a product might not.** Users may prefer clean, confident notes; a second visual style adds complexity; the boundary between paraphrase and inference is itself contested.

## 5. Margin notes

- **Evidence.** Answers to cross-meeting questions were often partial on AMI, and in one case an answer merged two separate statements ([`ANSWERS.md`](../tracks/ANSWERS.md)).
- **What a product could do.** When a later meeting changes something recorded in an earlier note, mark the earlier note ("updated 5 Nov"), with a link to the source.
- **How TENOR would measure it.** AMI's four-meeting series give "what changed" and "when was it decided" questions with human answers.
- **Why a product might not.** Editing past notes, even with marks, can feel like the record is unstable; cross-meeting linking needs reliable entity matching.

## 6. "Since last time" in pre-meeting briefs

- **Evidence.** Indirect only: cross-meeting answers on AMI were often partial ([`ANSWERS.md`](../tracks/ANSWERS.md)). Whether a brief would help has not been measured.
- **What a product could do.** A short line in the pre-meeting brief listing open actions and decisions that changed since the last meeting in the series.
- **How TENOR would measure it.** Action-owner and "what changed" questions asked before the next meeting in an AMI series.
- **Why a product might not.** Overlaps with existing to-do and brief features; stale or wrong items in a brief are more visible than in an answer.

## 7. Exact: numbers, codes and names reconciled across sources

- **Evidence.** A 2026 public review describes transcripts that miss numbers and codes, with no way to verify them (source to be re-checked before citation). TENOR's single-meeting lead: money and percentage mentions in one AMI meeting were kept at different rates by the two reference products (3 of 11 and 8 of 11), while plain numbers were kept at 75–100% by both ([`TRANSCRIPTION.md`](../tracks/TRANSCRIPTION.md)). This is one meeting and must be replicated.
- **What a product could do.** An "Exact" strip under the notes listing each number, code, date and name with a mark for whether two sources agree. Sources: a number-focused second pass over spans with digits, money or dates, run at capture time on the live stream (no audio stored); values read off the shared screen; the user's typed notes; calendar attendees for name spellings. Tapping an uncertain value shows the transcript sentence and, where relevant, the screen capture.
- **How TENOR would measure it.** Fact-token recall and substitutions against AMI's human transcript, and whether "two sources agree" separates right values from wrong ones. Number-heavy AMI series give a larger sample.
- **Why a product might not.** A second pass costs compute at capture time; showing many uncertain marks undermines confidence; screen capture raises its own privacy questions.

## 8. Learning vocabulary from user edits

- **Evidence.** Name errors occur in AMI replays: in one case a participant's name was transcribed as a different name and the note repeated it; in the pilot, one product wrote the account holder's name for an AMI speaker ([`AMI_REPLAY.md`](../method/AMI_REPLAY.md#9-decisions-kian-2026-11-04), [`WRONG_FACTS.md`](../method/WRONG_FACTS.md)).
- **What a product could do.** When a user corrects a name or term in the notes, add it to a per-workspace vocabulary used by later transcription.
- **How TENOR would measure it.** Name recall on a later meeting of the same series, before and after scripted corrections.
- **Why a product might not.** Learned vocabulary can over-apply (the account-holder case above is a form of it); corrections may be stylistic rather than factual.

## 9. Meeting-chat capture

- **Evidence.** None from TENOR. Links and decisions are sometimes typed into the meeting's text chat rather than spoken. **Demand unproven; feasibility untested.**
- **What a product could do.** Capture the meeting platform's chat alongside the transcript, where the platform allows it.
- **How TENOR would measure it.** Not yet defined; AMI has no meeting chat.
- **Why a product might not.** Platform access varies; chat often holds off-topic or sensitive content; consent norms differ from audio.

## 10. Typed vs silent notes

- **Evidence.** Untested. Notes in some products blend the user's typing with the transcript, and reviews say note quality depends on participation; all of TENOR's AMI replays so far are silent ([`AMI_REPLAY.md`](../method/AMI_REPLAY.md)).
- **What a product could do.** Nothing yet; this is a measurement to make first: how much do a few typed topic cues improve completeness and faithfulness?
- **How TENOR would measure it.** The planned typed-vs-silent test: the same AMI meeting replayed silent and with scripted topic cues that never contain the answer ([`CAPTURE.md`](../tracks/CAPTURE.md#b-typed-vs-silent)).
- **Why a product might not.** If typing helps a lot, the product may prefer to encourage it rather than compensate for its absence.

## 11. Live recall (opt-in experiment only)

- **Evidence.** None. Listed for completeness because it is often proposed.
- **What a product could do.** An opt-in setting, off by default, that shows one card during a meeting only when something said directly conflicts with a past decision.
- **How TENOR would measure it.** Not yet defined; would need precision on planted conflicts in a replayed series.
- **Why a product might not.** Interruptions during a meeting are costly, false alarms are worse live than afterwards, and it changes the product from a note-taker into a participant.

## Shared design rule

Each surface above should show a line only when confident, with its source quote, and its precision should be measured with the calibrated judge ([`CALIBRATED_JUDGING.md`](../method/CALIBRATED_JUDGING.md)) before it is considered for shipping.
