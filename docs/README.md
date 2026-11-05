# Docs map

Where every doc lives. Start with the two-page summary; every doc follows [`STYLE.md`](STYLE.md).

| Folder | What's in it | Start with |
|---|---|---|
| [`report/`](report/) | The two-page summary and the full technical report: question, method, results, limitations | [`HINDSIGHT_2PAGE.md`](report/HINDSIGHT_2PAGE.md), then [`HINDSIGHT_REPORT.md`](report/HINDSIGHT_REPORT.md) |
| [`benchmark/`](benchmark/) | What the benchmark is: datasets, how they're generated, how realistic they are | [`DESIGN.md`](benchmark/DESIGN.md), [`BLACKBOX.md`](benchmark/BLACKBOX.md) (bring your own meetings), [`NOTES_FAITHFULNESS.md`](benchmark/NOTES_FAITHFULNESS.md) (are notes grounded?), [`GROUNDING_CHECKER.md`](benchmark/GROUNDING_CHECKER.md) (a per-claim grounding checker, validated on AMI), [`PRODUCTS.md`](benchmark/PRODUCTS.md) (Granola vs Wispr Flow on paired meetings) |
| [`systems/`](systems/) | The systems being compared: Concentrate (the reference memory), the Mem0 and Graphiti baselines, the MCP server | [`systems/concentrate/memory.md`](systems/concentrate/memory.md) |
| [`results/`](results/) | Every experiment and what it found, including the red-team audit; synthetic results are diagnostics only | [`REAL_SLICE.md`](results/REAL_SLICE.md), [`SYNTHETIC_DIAGNOSTICS.md`](results/SYNTHETIC_DIAGNOSTICS.md), [`RED_TEAM.md`](results/RED_TEAM.md) |
| [`engineering/`](engineering/) | How it's built: decision log, Granola field provenance, hardening, the web app | [`DESIGN.md`](engineering/DESIGN.md) |
| [`guide/`](guide/) | Learning guide: tour, concepts, interview prep, exercises | [`TOUR.md`](guide/TOUR.md) |
| [`writeup/`](writeup/) | The memo, deck and outreach drafts for Granola | [`MEMO.md`](writeup/MEMO.md) |
| [`archive/`](archive/) | Earlier plans and probes, kept for the record | — |

The current plan is [`PLAN.md`](PLAN.md); the writing and figure rules are [`STYLE.md`](STYLE.md). Prior work and how Hindsight differs: [`RELATED_WORK.md`](RELATED_WORK.md).
