# Notes-sufficient vs transcript-needed: generated tables

Regenerate: `uv run concentrate notes-vs-transcript`. Write-up and caveats: `docs/results/NOTES_VS_TRANSCRIPT.md`.

## Tag counts

| Set | Split | notes-sufficient | transcript-needed | unanswerable | rule vs generator (test): agreement · κ |
|---|---|---|---|---|---|
| northwind-xl | dev | 67 | 15 | 8 |  |
| northwind-xl | test | 164 | 25 | 17 | 90% · 0.63 |
| northwind-xl-v2 | dev | 68 | 14 | 8 |  |
| northwind-xl-v2 | test | 163 | 26 | 17 | 85% · 0.48 |
| northwind-xl-v2.1 | dev | 76 | 22 | 8 |  |
| northwind-xl-v2.1 | test | 183 | 41 | 17 | 86% · 0.59 |
| real (portable rule) | dev | 23 | 18 | 6 | n/a |

Hand check (20 held-out test questions): generator reference agrees on 19, portable rule on 16.


## Synthetic test report 20261103T002456Z (generator tags)

| System | all: n · acc · conf. err | notes-sufficient: acc · conf. err | transcript-needed: acc · conf. err | declined an answerable q |
|---|---|---|---|---|
| b0_topk | n=120 · 88% [82, 93] · 2% [0, 4] | n=90 · 88% [81, 94] · 2% [0, 6] | n=17 · 82% [65, 100] · 0% [0, 0] | 2 of 107 |
| b1_summaries | n=120 · 71% [62, 78] · 8% [4, 13] | n=90 · 80% [71, 88] · 3% [0, 8] | n=17 · 0% [0, 0] · 41% [18, 65] | 10 of 107 |
| b2o_oracle | n=120 · 95% [91, 98] · 1% [0, 2] | n=90 · 96% [91, 99] · 1% [0, 3] | n=17 · 88% [71, 100] · 0% [0, 0] | 1 of 107 |
| b3_agentic | n=120 · 90% [84, 95] · 4% [1, 8] | n=90 · 92% [87, 97] · 3% [0, 8] | n=17 · 76% [53, 94] · 6% [0, 18] | 0 of 107 |
| b4_mem0 | n=120 · 81% [73, 88] · 4% [1, 8] | n=90 · 80% [71, 88] · 3% [0, 8] | n=17 · 71% [47, 88] · 12% [0, 29] | 4 of 107 |
| b5_graphiti | n=120 · 72% [63, 80] · 8% [4, 13] | n=90 · 73% [63, 82] · 9% [3, 16] | n=17 · 47% [24, 71] · 6% [0, 18] | 11 of 107 |
| concentrate | n=120 · 92% [86, 96] · 2% [0, 6] | n=90 · 92% [87, 98] · 2% [0, 6] | n=17 · 82% [65, 100] · 6% [0, 18] | 1 of 107 |

| A - B | Tag | n | A | B | Gap (pts) [95% CI] | A only / B only | McNemar p |
|---|---|---|---|---|---|---|---|
| b0_topk - b1_summaries | all | 120 | 88% | 71% | +18 [+10, +26] | 24 / 3 | 0.000 |
| b0_topk - b1_summaries | notes-sufficient | 90 | 88% | 80% | +8 [+0, +16] | 10 / 3 | 0.092 |
| b0_topk - b1_summaries | transcript-needed | 17 | 82% | 0% | +82 [+65, +100] | 14 / 0 | 0.000 |
| b0_topk - b1_summaries | unanswerable | 13 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |
| b3_agentic - b1_summaries | all | 120 | 90% | 71% | +19 [+11, +28] | 27 / 4 | 0.000 |
| b3_agentic - b1_summaries | notes-sufficient | 90 | 92% | 80% | +12 [+3, +21] | 14 / 3 | 0.013 |
| b3_agentic - b1_summaries | transcript-needed | 17 | 76% | 0% | +76 [+53, +94] | 13 / 0 | 0.000 |
| b3_agentic - b1_summaries | unanswerable | 13 | 92% | 100% | -8 [-23, +0] | 0 / 1 | 1.000 |
| concentrate - b1_summaries | all | 120 | 92% | 71% | +21 [+13, +29] | 27 / 2 | 0.000 |
| concentrate - b1_summaries | notes-sufficient | 90 | 92% | 80% | +12 [+4, +20] | 13 / 2 | 0.007 |
| concentrate - b1_summaries | transcript-needed | 17 | 82% | 0% | +82 [+65, +100] | 14 / 0 | 0.000 |
| concentrate - b1_summaries | unanswerable | 13 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |
| b2o_oracle - b1_summaries | all | 120 | 95% | 71% | +24 [+17, +32] | 30 / 1 | 0.000 |
| b2o_oracle - b1_summaries | notes-sufficient | 90 | 96% | 80% | +16 [+8, +24] | 15 / 1 | 0.001 |
| b2o_oracle - b1_summaries | transcript-needed | 17 | 88% | 0% | +88 [+71, +100] | 15 / 0 | 0.000 |
| b2o_oracle - b1_summaries | unanswerable | 13 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |

## Synthetic test report 20261103T002456Z (portable-rule tags)

| System | all: n · acc · conf. err | notes-sufficient: acc · conf. err | transcript-needed: acc · conf. err | declined an answerable q |
|---|---|---|---|---|
| b0_topk | n=120 · 88% [82, 93] · 2% [0, 4] | n=85 · 89% [82, 95] · 1% [0, 4] | n=22 · 77% [59, 95] · 5% [0, 14] | 2 of 107 |
| b1_summaries | n=120 · 71% [62, 78] · 8% [4, 13] | n=85 · 78% [68, 86] · 5% [1, 9] | n=22 · 27% [9, 45] · 27% [9, 45] | 10 of 107 |
| b2o_oracle | n=120 · 95% [91, 98] · 1% [0, 2] | n=85 · 98% [94, 100] · 0% [0, 0] | n=22 · 82% [64, 95] · 5% [0, 14] | 1 of 107 |
| b3_agentic | n=120 · 90% [84, 95] · 4% [1, 8] | n=85 · 94% [89, 99] · 2% [0, 6] | n=22 · 73% [55, 91] · 9% [0, 23] | 0 of 107 |
| b4_mem0 | n=120 · 81% [73, 88] · 4% [1, 8] | n=85 · 82% [74, 89] · 1% [0, 4] | n=22 · 64% [41, 82] · 18% [5, 36] | 4 of 107 |
| b5_graphiti | n=120 · 72% [63, 80] · 8% [4, 13] | n=85 · 74% [65, 84] · 8% [2, 14] | n=22 · 50% [27, 68] · 9% [0, 23] | 11 of 107 |
| concentrate | n=120 · 92% [86, 96] · 2% [0, 6] | n=85 · 93% [87, 98] · 1% [0, 4] | n=22 · 82% [64, 95] · 9% [0, 23] | 1 of 107 |

| A - B | Tag | n | A | B | Gap (pts) [95% CI] | A only / B only | McNemar p |
|---|---|---|---|---|---|---|---|
| b0_topk - b1_summaries | all | 120 | 88% | 71% | +18 [+10, +26] | 24 / 3 | 0.000 |
| b0_topk - b1_summaries | notes-sufficient | 85 | 89% | 78% | +12 [+4, +20] | 12 / 2 | 0.013 |
| b0_topk - b1_summaries | transcript-needed | 22 | 77% | 27% | +50 [+23, +73] | 12 / 1 | 0.003 |
| b0_topk - b1_summaries | unanswerable | 13 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |
| b3_agentic - b1_summaries | all | 120 | 90% | 71% | +19 [+11, +28] | 27 / 4 | 0.000 |
| b3_agentic - b1_summaries | notes-sufficient | 85 | 94% | 78% | +16 [+7, +26] | 16 / 2 | 0.001 |
| b3_agentic - b1_summaries | transcript-needed | 22 | 73% | 27% | +45 [+18, +68] | 11 / 1 | 0.006 |
| b3_agentic - b1_summaries | unanswerable | 13 | 92% | 100% | -8 [-23, +0] | 0 / 1 | 1.000 |
| concentrate - b1_summaries | all | 120 | 92% | 71% | +21 [+13, +29] | 27 / 2 | 0.000 |
| concentrate - b1_summaries | notes-sufficient | 85 | 93% | 78% | +15 [+7, +24] | 14 / 1 | 0.001 |
| concentrate - b1_summaries | transcript-needed | 22 | 82% | 27% | +55 [+27, +77] | 13 / 1 | 0.002 |
| concentrate - b1_summaries | unanswerable | 13 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |
| b2o_oracle - b1_summaries | all | 120 | 95% | 71% | +24 [+17, +32] | 30 / 1 | 0.000 |
| b2o_oracle - b1_summaries | notes-sufficient | 85 | 98% | 78% | +20 [+12, +29] | 17 / 0 | 0.000 |
| b2o_oracle - b1_summaries | transcript-needed | 22 | 82% | 27% | +55 [+32, +77] | 13 / 1 | 0.002 |
| b2o_oracle - b1_summaries | unanswerable | 13 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |

## Retirement curve run 20261102T222859Z (dev, generator tags)

| System | all: n · acc · conf. err | notes-sufficient: acc · conf. err | transcript-needed: acc · conf. err | declined an answerable q |
|---|---|---|---|---|
| b1_summaries | n=90 · 64% [54, 74] · 8% [2, 14] | n=67 · 73% [63, 84] · 4% [0, 10] | n=15 · 7% [0, 20] · 27% [7, 53] | 7 of 82 |
| concentrate@0 | n=90 · 88% [81, 94] · 3% [0, 8] | n=67 · 88% [81, 96] · 4% [0, 10] | n=15 · 80% [60, 100] · 0% [0, 0] | 3 of 82 |
| concentrate@14 | n=90 · 88% [81, 94] · 3% [0, 8] | n=67 · 87% [78, 94] · 4% [0, 10] | n=15 · 87% [67, 100] · 0% [0, 0] | 3 of 82 |
| concentrate@30 | n=90 · 88% [81, 93] · 3% [0, 8] | n=67 · 87% [78, 94] · 4% [0, 10] | n=15 · 87% [67, 100] · 0% [0, 0] | 3 of 82 |
| concentrate@7 | n=90 · 88% [81, 94] · 3% [0, 8] | n=67 · 87% [78, 94] · 4% [0, 10] | n=15 · 87% [67, 100] · 0% [0, 0] | 3 of 82 |
| concentrate@inf | n=90 · 89% [82, 94] · 3% [0, 8] | n=67 · 88% [81, 96] · 4% [0, 10] | n=15 · 87% [67, 100] · 0% [0, 0] | 3 of 82 |

| A - B | Tag | n | A | B | Gap (pts) [95% CI] | A only / B only | McNemar p |
|---|---|---|---|---|---|---|---|
| concentrate@inf - concentrate@0 | all | 90 | 89% | 88% | +1 [+0, +3] | 1 / 0 | 1.000 |
| concentrate@inf - concentrate@0 | notes-sufficient | 67 | 88% | 88% | +0 [+0, +0] | 0 / 0 | 1.000 |
| concentrate@inf - concentrate@0 | transcript-needed | 15 | 87% | 80% | +7 [+0, +20] | 1 / 0 | 1.000 |
| concentrate@inf - concentrate@0 | unanswerable | 8 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |
| concentrate@inf - b1_summaries | all | 90 | 89% | 64% | +24 [+14, +34] | 24 / 2 | 0.000 |
| concentrate@inf - b1_summaries | notes-sufficient | 67 | 88% | 73% | +15 [+4, +25] | 12 / 2 | 0.013 |
| concentrate@inf - b1_summaries | transcript-needed | 15 | 87% | 7% | +80 [+60, +100] | 12 / 0 | 0.000 |
| concentrate@inf - b1_summaries | unanswerable | 8 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |
| concentrate@0 - b1_summaries | all | 90 | 88% | 64% | +23 [+13, +33] | 23 / 2 | 0.000 |
| concentrate@0 - b1_summaries | notes-sufficient | 67 | 88% | 73% | +15 [+4, +25] | 12 / 2 | 0.013 |
| concentrate@0 - b1_summaries | transcript-needed | 15 | 80% | 7% | +73 [+53, +93] | 11 / 0 | 0.001 |
| concentrate@0 - b1_summaries | unanswerable | 8 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |

## Retirement curve run 20261102T224523Z (dev, generator tags)

| System | all: n · acc · conf. err | notes-sufficient: acc · conf. err | transcript-needed: acc · conf. err | declined an answerable q |
|---|---|---|---|---|
| b1_summaries | n=90 · 64% [54, 74] · 8% [2, 14] | n=67 · 73% [63, 84] · 4% [0, 10] | n=15 · 7% [0, 20] · 27% [7, 53] | 7 of 82 |
| concentrate@0 | n=90 · 88% [81, 94] · 3% [0, 8] | n=67 · 88% [81, 96] · 4% [0, 10] | n=15 · 80% [60, 100] · 0% [0, 0] | 3 of 82 |
| concentrate@14 | n=90 · 88% [81, 94] · 3% [0, 8] | n=67 · 87% [78, 94] · 4% [0, 10] | n=15 · 87% [67, 100] · 0% [0, 0] | 3 of 82 |
| concentrate@30 | n=90 · 88% [81, 93] · 3% [0, 8] | n=67 · 87% [78, 94] · 4% [0, 10] | n=15 · 87% [67, 100] · 0% [0, 0] | 3 of 82 |
| concentrate@7 | n=90 · 88% [81, 94] · 3% [0, 8] | n=67 · 87% [78, 94] · 4% [0, 10] | n=15 · 87% [67, 100] · 0% [0, 0] | 3 of 82 |
| concentrate@inf | n=90 · 89% [82, 94] · 3% [0, 8] | n=67 · 88% [81, 96] · 4% [0, 10] | n=15 · 87% [67, 100] · 0% [0, 0] | 3 of 82 |

| A - B | Tag | n | A | B | Gap (pts) [95% CI] | A only / B only | McNemar p |
|---|---|---|---|---|---|---|---|
| concentrate@inf - concentrate@0 | all | 90 | 89% | 88% | +1 [+0, +3] | 1 / 0 | 1.000 |
| concentrate@inf - concentrate@0 | notes-sufficient | 67 | 88% | 88% | +0 [+0, +0] | 0 / 0 | 1.000 |
| concentrate@inf - concentrate@0 | transcript-needed | 15 | 87% | 80% | +7 [+0, +20] | 1 / 0 | 1.000 |
| concentrate@inf - concentrate@0 | unanswerable | 8 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |
| concentrate@inf - b1_summaries | all | 90 | 89% | 64% | +24 [+14, +34] | 24 / 2 | 0.000 |
| concentrate@inf - b1_summaries | notes-sufficient | 67 | 88% | 73% | +15 [+4, +25] | 12 / 2 | 0.013 |
| concentrate@inf - b1_summaries | transcript-needed | 15 | 87% | 7% | +80 [+60, +100] | 12 / 0 | 0.000 |
| concentrate@inf - b1_summaries | unanswerable | 8 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |
| concentrate@0 - b1_summaries | all | 90 | 88% | 64% | +23 [+13, +33] | 23 / 2 | 0.000 |
| concentrate@0 - b1_summaries | notes-sufficient | 67 | 88% | 73% | +15 [+4, +25] | 12 / 2 | 0.013 |
| concentrate@0 - b1_summaries | transcript-needed | 15 | 80% | 7% | +73 [+53, +93] | 11 / 0 | 0.001 |
| concentrate@0 - b1_summaries | unanswerable | 8 | 100% | 100% | +0 [+0, +0] | 0 / 0 | 1.000 |

## Real slice 20261103T004019Z (aggregates only, portable-rule tags)

| System | all: n · acc · conf. err | notes-sufficient: acc · conf. err | transcript-needed: acc · conf. err | declined an answerable q |
|---|---|---|---|---|
| concentrate | n=39 · 59% [44, 74] · 8% [0, 18] | n=23 · 61% [39, 78] · 9% [0, 22] | n=16 · 56% [31, 81] · 6% [0, 19] | 10 of 39 |
| granola_agent | n=39 · 92% [82, 100] · ≤ 3% [0, 8] | n=23 · 91% [78, 100] · ≤ 0% [0, 0] | n=16 · 94% [81, 100] · ≤ 6% [0, 19] | 0 of 39 |

| A - B | Tag | n | A | B | Gap (pts) [95% CI] | A only / B only | McNemar p |
|---|---|---|---|---|---|---|---|
| granola_agent - concentrate | all | 39 | 92% | 59% | +33 [+15, +49] | 14 / 1 | 0.001 |
| granola_agent - concentrate | notes-sufficient | 23 | 91% | 61% | +30 [+9, +52] | 8 / 1 | 0.039 |
| granola_agent - concentrate | transcript-needed | 16 | 94% | 56% | +38 [+12, +62] | 6 / 0 | 0.031 |

## Real slice 20261103T013240Z (aggregates only, portable-rule tags)

| System | all: n · acc · conf. err | notes-sufficient: acc · conf. err | transcript-needed: acc · conf. err | declined an answerable q |
|---|---|---|---|---|
| b0_topk | n=47 · 81% [68, 91] · 0% [0, 0] | n=23 · 83% [65, 96] · 0% [0, 0] | n=18 · 72% [50, 89] · 0% [0, 0] | 6 of 41 |

| A - B | Tag | n | A | B | Gap (pts) [95% CI] | A only / B only | McNemar p |
|---|---|---|---|---|---|---|---|
