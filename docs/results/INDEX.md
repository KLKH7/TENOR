# Results index

One line per experiment: what it asks, its headline figure and the page that explains it.
Every figure is generated from saved data by `uv run concentrate figures 20261103T002456Z` and is
checked in CI (see [docs/figures/README.md](../figures/README.md) for exact inputs).

**Read the labels.** **TEST** means the held-out synthetic split (never tuned on). **DEV** means
questions that were seen while building or tuning something, so DEV results are evidence for
where to look, not claims. Dollar figures use one equal price list ($4/M input, $20/M output
tokens), not anyone's bill.

| Experiment | Question | Split | Figure | Page |
|---|---|---|---|---|
| Synthetic vs real | Why did a tie on synthetic data become a loss on real meetings? | TEST vs real DEV | <img src="../figures/hero_synthetic_vs_real.svg" width="240" alt="hero"> | [REAL_SLICE.md](REAL_SLICE.md) |
| Main comparison | Who's ahead, and is it real? | TEST, n=120 | <img src="../figures/leaderboard.svg" width="240" alt="leaderboard"> | [ANALYSIS.md](ANALYSIS.md) |
| Paired significance | Which gaps are real? | TEST, n=120 | <img src="../figures/pairwise.svg" width="240" alt="pairwise matrix"> | [ANALYSIS.md](ANALYSIS.md) |
| By question type | Where does each system break? | TEST, n=120 | <img src="../figures/by_category.svg" width="240" alt="heatmap"> | [ANALYSIS.md](ANALYSIS.md) |
| Failure taxonomy | Why is it wrong? | TEST, n=120 | <img src="../figures/failures.svg" width="240" alt="failure types"> | [ANALYSIS.md](ANALYSIS.md) |
| Judge reliability | Can we trust the grader? | TEST, 840 answers | <img src="../figures/judge_agreement.svg" width="240" alt="judge agreement"> | [ANALYSIS.md](ANALYSIS.md) |
| Cost frontier | What's the best system for a given budget? | TEST, n=120 | <img src="../figures/cost_frontier.svg" width="240" alt="cost frontier"> | [ECONOMICS.md](ECONOMICS.md) |
| Cost breakdown | Where does the money go? | TEST, n=120 | <img src="../figures/cost_breakdown.svg" width="240" alt="cost breakdown"> | [ECONOMICS.md](ECONOMICS.md) |
| Scale curve | Does retrieval degrade as meetings pile up? | DEV, n=21, Haiku | <img src="../figures/scale.svg" width="240" alt="scale curve"> | [ECONOMICS.md](ECONOMICS.md) |
| Transcript retirement | What does deleting transcripts cost? | DEV, n=90 | <img src="../figures/retirement.svg" width="240" alt="retirement curve"> | [RETIREMENT.md](RETIREMENT.md) |
| Transcript fallback | Did the hybrid help, and where? | real DEV, n=22 | <img src="../figures/fallback_dev.svg" width="240" alt="fallback"> | [fallback.md](../systems/concentrate/fallback.md) |
| Pre-meeting brief | Can memory write a useful brief? | DEV / TEST | (no figure yet) | [BRIEF.md](BRIEF.md) |
| Red team | Does the headline survive attack? | all | (no figure) | [RED_TEAM.md](RED_TEAM.md) |
