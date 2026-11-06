# Docs map

Where every TENOR (Transcripts, Evidence, Notes, Outcomes, Recall) document lives. Start with the [repository README](../README.md); every doc follows [`STYLE.md`](STYLE.md).

| Folder | What it holds | Start with |
|---|---|---|
| [`method/`](method/) | How products are measured: the outside-in harness, the AMI replay protocol, calibrated judging, human verification, and the scorers they use | [`HARNESS.md`](method/HARNESS.md), [`AMI_REPLAY.md`](method/AMI_REPLAY.md) |
| [`tracks/`](tracks/) | One page per track: the question, how it is measured, current results and status | [`tracks/README.md`](tracks/README.md) |
| [`results/`](results/) | Dated results, the interim technical report, and the earlier study on the author's own meetings | [`INDEX.md`](results/INDEX.md) |
| [`product/`](product/) | Design implications for meeting note-takers, each tied to evidence and to a TENOR measurement | [`IMPLICATIONS.md`](product/IMPLICATIONS.md) |
| [`engineering/`](engineering/) | How the code is built: architecture, decision log, field provenance (VERIFIED / ASSUMED), hardening, the web app | [`ARCHITECTURE.md`](engineering/ARCHITECTURE.md), [`DESIGN.md`](engineering/DESIGN.md) |
| [`archive/`](archive/) | Superseded material kept for the record: the synthetic Northwind benchmark, the archived Concentrate memory system, old plans and probes | [`archive/README.md`](archive/README.md) |
| [`figures/`](figures/) | Generated SVG figures and the exact inputs they were built from (checked in CI) | [`figures/README.md`](figures/README.md) |

## Method pages

| Page | What it covers |
|---|---|
| [`HARNESS.md`](method/HARNESS.md) | How a product plugs in through read-only MCP tools, how one meeting is paired across products, what is and is not comparable |
| [`AMI_REPLAY.md`](method/AMI_REPLAY.md) | Replaying public AMI meetings into products: corpus facts, licence, protocol, meeting selection, risks |
| [`CALIBRATED_JUDGING.md`](method/CALIBRATED_JUDGING.md) | Why automated judges over-count errors on meeting AI, and the calibrated judge that corrects for it |
| [`HUMAN_VERIFICATION.md`](method/HUMAN_VERIFICATION.md) | How human labels are collected and used to calibrate the judges |
| [`GROUNDING_CHECKER.md`](method/GROUNDING_CHECKER.md) | The per-claim grounding checker and its validation on AMI |
| [`WRONG_FACTS.md`](method/WRONG_FACTS.md) | Attributing a wrong fact to transcription, note-writing or answering |
| [`METRICS.md`](method/METRICS.md) | Definitions of every metric and statistical test |
| [`BLACKBOX.md`](method/BLACKBOX.md) | The kit for running the question tracks on your own meetings, locally |
| [`EVIDENCE_COMPRESSION.md`](method/EVIDENCE_COMPRESSION.md) | Pre-registered plan: how much of a meeting an agent needs to keep |

Other top-level pages: [`STYLE.md`](STYLE.md) (writing and figure rules) and [`RELATED_WORK.md`](RELATED_WORK.md) (prior benchmarks and how TENOR differs).
