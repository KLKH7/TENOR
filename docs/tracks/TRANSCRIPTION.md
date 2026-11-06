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

Both products receive the same audio in the same session ([`AMI_REPLAY.md`](../method/AMI_REPLAY.md)). Granola's microphone turns are dropped (on AMI the microphone hears only the room); Wispr Flow has no channel split, so the room is kept silent. Code: `tenor.ami.wer`, `tenor.ami.facts`; commands `tenor ami wer` and `tenor facts stage1`.

## Current results

**All 7 AMI meetings, clean protocol** (5 November 2026, code `39163c4`; numbers: [`evals/reports/ami/wer.json`](../../evals/reports/ami/wer.json)). Preliminary.

| Measure | Granola | Wispr Flow | n |
|---|---|---|---|
| WER, pooled (total edits / total reference words) | 16.6% | 16.0% | 7 meetings, 28,581 reference words |
| WER, per-meeting range | 9.3–28.6% | 8.0–26.4% | 7 meetings |
| Content-word recall, pooled | 87.8% | 90.0% | 7 meetings |
| Content-word recall, per-meeting range | 77.7–94.4% | 82.9–96.9% | 7 meetings |

| Meeting | WER, Granola | WER, Wispr Flow | Content recall, Granola | Content recall, Wispr Flow |
|---|---|---|---|---|
| IS1008a | 9.3% | 8.0% | 94.4% | 96.9% |
| IS1008b | 11.7% | 12.4% | 92.2% | 92.7% |
| IS1008c | 13.6% | 13.6% | 90.1% | 91.0% |
| IS1008d | 15.9% | 15.4% | 87.3% | 89.1% |
| IB4003 | 16.1% | 14.7% | 88.5% | 90.8% |
| EN2002b | 28.6% | 26.4% | 77.7% | 82.9% |
| ES2006a | 10.8% | 12.5% | 91.6% | 92.5% |

- **Pooled WER is within 0.7 points.** Wispr Flow was lower on 4 meetings, Granola on 2, 1 tied. No significance test; read it as a tie.
- **Wispr Flow's content-word recall was higher on all 7 meetings** (by 0.5–5.2 points). Part of this may be scoring: Granola's microphone turns are dropped (4 turns, in IS1008b and IS1008c), while Wispr Flow's whole transcript is kept because it has no channel split.
- **EN2002b, the one naturally occurring meeting, is hardest for both** (26–29% WER).
- **The 1-meeting pilot is superseded.** It was recorded with personal settings on (IS1008a: 10.0% vs 9.0%); the clean recording scores 9.3% vs 8.0%.

**Speakers separated in the MCP transcript** (moved from the README; a design difference, not a quality measure):

| Meeting set | Granola | Wispr Flow |
|---|---|---|
| Distinct speakers in the MCP transcript, per meeting (AMI has 4) | 0 in all 7 | 2–4 (4 in 4 meetings, 3 in 2, 2 in 1) |

Granola separates microphone from system audio ("Me" and "Them") rather than diarising speakers. AMI arrives on one mixed system-audio channel, so Granola reports no named speakers by design. Wispr Flow labels speakers within the mixed channel. This compares a product choice, not transcription quality, so it is not in the README.

Stage-1 fact recall on the clean recordings has been computed locally but not yet published; the one figure cited elsewhere (money and percentage mentions in IS1008d: Granola 3 of 11, Wispr Flow 8 of 11; plain numbers 75–100% for both) rests on a single meeting and should be read as a lead to check, not as a finding.

## Status and caveats

- **Status: preliminary.** 7 meetings, one recording per product per meeting.
- **Best case, not typical accuracy.** AMI is a standard speech-recognition benchmark, and product speech models may have been trained or tuned on it. WER here is reported as a best case and is not compared with WER on private meetings.
- **Spelling is not always an error.** AMI annotators spelt names as they heard them; a different spelling counts as a substitution.
- **Speaker separation is a product design difference.** Granola separates microphone from system audio rather than diarising speakers; on a single mixed channel it reports no speakers by design.
