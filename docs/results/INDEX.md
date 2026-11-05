# Results index

A dated log of TENOR results, newest first: what was measured, on what, with what n, and how far to trust it. Track pages hold the method and full tables; this page only points to them.

**Labels.** *Preliminary*: small n or a method still changing; read as direction, not as a ranking. *Confirmed*: method frozen and human-checked (none yet). *Superseded*: replaced by a later result. *DEV*: questions seen during development.

| Date | Result | Data, n | Status | Where |
|---|---|---|---|---|
| 2026-11-05 | Capture coverage, baseline (no stress): both products captured 94–100% of each meeting; no silent failures | 7 AMI meetings × 2 products, 665 windows each | Preliminary | [`tracks/CAPTURE.md`](../tracks/CAPTURE.md) |
| 2026-11-05 | Automated judges over-count errors: of 20 sampled note claims, the human could judge 12 and found 0 wrong; of the 3 strongest "confident contradictions" in Chat answers, 0 clearly wrong | 23 human labels | Preliminary | [`method/CALIBRATED_JUDGING.md`](../method/CALIBRATED_JUDGING.md) |
| 2026-11-05 | Note claims, first automated grounding pass (before input fixes): most of the apparent transcription-propagated errors were measurement noise | 387 claims, 7 AMI meetings × 2 products | Preliminary; re-run quoted | [`method/WRONG_FACTS.md`](../method/WRONG_FACTS.md) |
| 2026-11-05 | Granola Chat on AMI questions, automated judge: 16 wrong, 24 partial | 68 questions | Preliminary; over-counts errors | [`tracks/ANSWERS.md`](../tracks/ANSWERS.md) |
| 2026-11-05 | MCP drift: Granola's transcript tool changed shape with no error; detected and turned into a contract check | 1 event | Observed | [`tracks/AGENT_INTERFACE.md`](../tracks/AGENT_INTERFACE.md#mcp-drift-the-contract-check) |
| 2026-11-05 | Transcription pilot: WER 10.0% (Granola), 9.0% (Wispr Flow); a tie | 1 AMI meeting, pilot settings | Pilot, not a result | [`tracks/TRANSCRIPTION.md`](../tracks/TRANSCRIPTION.md) |
| 2026-11-05 | Grounding checker validated on AMI: 89% of planted errors flagged; 18% of human summary sentences flagged | AMI TEST meetings, 188 items | Preliminary | [`method/GROUNDING_CHECKER.md`](../method/GROUNDING_CHECKER.md) |
| 2026-11-05 | Notes faithfulness (Granola): 84% of claims supported, 14% inferred, 0% unsupported | 4 private meetings, 132 claims | Preliminary | [`tracks/NOTES.md`](../tracks/NOTES.md) |
| 2026-11-04 | Deletion simulation: 89% with transcripts, 57% from notes only | Author's 4 private Granola meetings, 47 DEV questions | Superseded by the AMI replication in progress | [`tracks/RETENTION.md`](../tracks/RETENTION.md), [`TENOR_TECHNICAL_REPORT.md`](TENOR_TECHNICAL_REPORT.md) |
| 2026-11-04 | Agent interface (Granola): accurate, but meeting-level citations only and no abstain field | 4 private meetings, 47 DEV questions | Preliminary | [`AGENT_INTERFACE.md`](AGENT_INTERFACE.md) |
| 2026-11-03 | Synthetic Northwind diagnostics | 184 synthetic meetings | Archived; not evidence | [`archive/northwind/`](../archive/northwind/SYNTHETIC_DIAGNOSTICS.md) |
| 2026-11-02 | Question answering on real speech: Granola's agent 92%, a fact-memory reference system 59% | 4 private meetings, 39 DEV questions | Preliminary | [`REAL_SLICE.md`](REAL_SLICE.md) |

The interim technical report on the earlier private-meeting study is [`TENOR_TECHNICAL_REPORT.md`](TENOR_TECHNICAL_REPORT.md). Figures are generated from saved data by `uv run concentrate figures 20261103T002456Z` and checked in CI ([`figures/README.md`](../figures/README.md)).
