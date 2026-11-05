# Answers (Chat) track

This page defines the answers track: whether a product's answers to questions about a meeting are right, complete and honest about what they do not know.

## Question

Users ask their note-taker about past meetings ("what did we decide about the price?", "who owns the follow-up?"). An answer that is wrong but confident is worse than one that says it does not know, and an answer that is right but incomplete is common and easy to miss.

## How it is measured

| Step | What happens |
|---|---|
| **Questions** | Built by fixed templates from AMI's human annotations (decisions, actions, problems, who proposed what, when it was decided, what changed, unanswerable controls); no LLM writes a question or a gold answer ([`AMI_REPLAY.md`](../method/AMI_REPLAY.md#6-question-construction-from-annotations-no-llm-drafting), [`data/ami_questions/`](../../data/ami_questions/README.md)) |
| **Asking** | Each product's own question tool, scoped to the question's meetings, through a read-only allowlist ([`HARNESS.md`](../method/HARNESS.md)) |
| **Judging** | First an automated LLM judge (`judge.v1`); then the calibrated judge, with a speaker roster, product boilerplate stripped, a stronger model and per-item scoring for list answers ([`CALIBRATED_JUDGING.md`](../method/CALIBRATED_JUDGING.md)) |
| **Answer types** | Correct; partial; incomplete and clearly flagged; wrong but confident ([`AGENT_INTERFACE.md`](AGENT_INTERFACE.md)) |
| **Human check** | A sample of judge verdicts is labelled by a human to measure how far the judge over- or under-counts ([`HUMAN_VERIFICATION.md`](../method/HUMAN_VERIFICATION.md)) |

## Current results

| Measure | Granola | Wispr Flow | n | Notes | Status |
|---|---|---|---|---|---|
| Judged wrong, automated judge | 16 (24%, 95% CI 15–35%) | not applicable | 68 questions | Of the 3 strongest "confident contradictions", the human verifier found 0 clearly wrong (all partly right) | Preliminary; over-counts errors |
| Judged partial, automated judge | 24 (35%, 95% CI 25–47%) | not applicable | 68 | Partial = some gold items missing or imprecise | Preliminary |

- **Wispr Flow has no question-answering tool in its MCP,** so there is no Wispr Flow row for the product's own answers. A comparison through a fixed outside agent over each product's read tools is built and not yet run ([`HARNESS.md`](../method/HARNESS.md#the-standard-outside-agent-agent-interface-track)).
- **The automated numbers are an upper bound on errors.** The causes of the over-count (names vs roles, product boilerplate, a weak judge, a summary-level answer key) are listed in [`CALIBRATED_JUDGING.md`](../method/CALIBRATED_JUDGING.md). Calibrated figures will replace these once the judge is checked against at least 100 human labels.
- **Cross-meeting questions were often partial,** and in one case an answer merged two separate statements (first reading of the answers; not yet quantified).
- **Earlier result, superseded:** on the author's 4 private meetings (39 DEV questions, LLM-drafted gold approved by the owner), Granola's agent scored 92% [82, 100] ([`results/REAL_SLICE.md`](../results/REAL_SLICE.md)).

## Status and caveats

- **Preliminary.** One run, one automated judge, 3 human-checked answers.
- **Summary-level answer key.** Most gold answers are AMI summary sentences, which drop nuance a product may keep; a product can be "partial" against the key and right against the transcript.
- **Contamination.** AMI has been public for about 20 years; a closed-book control (the same questions with no meeting access) is part of the protocol and has not run yet.
