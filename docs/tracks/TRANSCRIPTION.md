# Transcription track

This page defines the transcription track: whether a product's transcript has the words, numbers and names that were actually said, measured against AMI's human transcript.

## Question

Every later output (notes, answers, action items) is built on the transcript. A number or name lost here cannot be recovered downstream, so transcription accuracy bounds what the rest of the product can get right.

## How it is measured

| Measure | Definition | Cost |
|---|---|---|
| **WER** (word error rate) | Edits needed to turn the product transcript into AMI's manual transcript, divided by the number of reference words. Both sides are normalised: lower case, no punctuation, AMI vocal-sound and filler tokens removed, numbers spelt out | $0 |
| **Content-word recall** | Share of AMI's content words found in the product transcript, in any order. Unlike WER, it does not depend on word order across overlapping speakers | $0 |
| **Fact-token recall** | Recall of numbers, money and percentages, proper names and role words, with substitutions located by local alignment (stage 1 of [`WRONG_FACTS.md`](../method/WRONG_FACTS.md)) | $0 |
| **Speakers found** | Number of distinct speakers the product's MCP output separates | $0 |

Both products receive the same audio in the same session ([`AMI_REPLAY.md`](../method/AMI_REPLAY.md)). Granola's microphone turns are dropped (on AMI the microphone hears only the room); Wispr Flow has no channel split, so the room is kept silent. Code: `concentrate.ami.wer`, `concentrate.ami.facts`; commands `concentrate ami wer` and `concentrate facts stage1`.

## Current results

**Pilot only.** IS1008a, recorded once with the account holder's personal settings on, before the clean protocol was fixed (n = 1 meeting; not a result):

| Measure | Granola | Wispr Flow |
|---|---|---|
| WER | 10.0% | 9.0% |
| Content-word recall | 94.2% | 95.8% |
| Speakers found | 0 of 4 (no speaker separation in its MCP transcript) | 3 of 4 |

A one-point WER gap on one meeting is a tie, not a ranking. Stage-1 fact recall on the clean recordings has been computed locally but not yet published; the one figure cited elsewhere (money and percentage mentions in IS1008d: Granola 3 of 11, Wispr Flow 8 of 11; plain numbers 75–100% for both) rests on a single meeting and should be read as a lead to check, not as a finding.

## Status and caveats

- **Status: pilot.** The full 7-meeting WER table is next; it costs nothing to compute.
- **Best case, not typical accuracy.** AMI is a standard speech-recognition benchmark, and product speech models may have been trained or tuned on it. WER here is reported as a best case and is not compared with WER on private meetings.
- **Spelling is not always an error.** AMI annotators spelt names as they heard them; a different spelling counts as a substitution.
- **Speaker separation is a product design difference.** Granola separates microphone from system audio rather than diarising speakers; on a single mixed channel it reports no speakers by design.
