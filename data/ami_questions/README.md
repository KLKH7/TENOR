# AMI questions for the evidence-compression experiment

68 questions about six AMI meetings, for [EVIDENCE_COMPRESSION.md](../../docs/benchmark/EVIDENCE_COMPRESSION.md). Built by fixed templates from AMI's human annotations; no LLM wrote any question or answer.

**Source and licence.** AMI Meeting Corpus, manual annotations v1.6.2 (University of Edinburgh and the AMI Consortium), CC BY 4.0. Gold answers quote the AMI annotators' abstractive summaries; who-said-it questions quote AMI's manual transcript. Regenerate with `uv run concentrate ami questions --seed 42` (byte-identical; a test checks it).

| File | What |
|---|---|
| `questions.jsonl` | One question per line: `type`, `template`, `meetings`, `question`, `gold_answer`, `gold_evidence` (AMI dialogue-act IDs), `evidence_source`, `fact_group`, `notes_can_answer` (null until product notes are synced), `split`, `seed` |
| `closed_book.jsonl` | The same questions exactly as the closed-book control sends them: no meeting at all |

| Type | DEV | TEST | Built from |
|---|---|---|---|
| decision | 1 | 9 | decision sentences, grouped by subject |
| action_who | 4 | 11 | action sentences whose subject is a role |
| action_what | 4 | 12 | action sentences, grouped by subject |
| problem | 1 | 3 | problem sentences, one list per meeting |
| who_said | 3 | 9 | a seeded sample of summary-linked dialogue acts, quoted; gold is the role, looked up by participant code |
| what_changed | 0 | 11 | IS1008k action item → what that role presented in IS1008k+1 (8); a decision and its closest decision in the next meeting (3) |

**Limits, read before using.**
- **DEV = questions about IS1008a only** (13). Everything else is TEST.
- **EN2002b gives no questions**: AMI 1.6.2 has no summary or dialogue acts for it. It still counts for compression sizes.
- **IB4003 has no gold evidence** (no summary links) and no who-said-it questions (no scenario roles).
- **Lists over 5 sentences are dropped** (IB4003's 10 problems): judge.v1 needs every item for "correct".
- **Summary-level questions.** Most gold answers are summary sentences, so any notes resembling that summary can answer them. Only `who_said` is a pure detail question.
- **`action_who` and `action_what` on one sentence share a `fact_group`**: they are not independent.
