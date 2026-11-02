# Scale curve

Answer + judge model `claude-haiku-5-5` · k=20 · seed 42 · git 6498f3d · 20261102T223053Z
Questions: dev, eligible at the 50% prefix (21 also eligible at every size).

| system | corpus | meetings | n | accuracy | accuracy on common Qs | $/q | in-tok/q | cite recall |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| b0_topk | 25% | 46 | 21 | 86% | 86% (21) | $0.0079 | 156,908 | 96% |
| b0_topk | 50% | 92 | 45 | 91% | 90% (21) | $0.0081 | 158,750 | 92% |
| b0_topk | 100% | 184 | 45 | 80% | 90% (21) | $0.0081 | 160,097 | 87% |
| b3_agentic | 25% | 46 | 21 | 95% | 95% (21) | $0.0020 | 15,640 | 73% |
| b3_agentic | 50% | 92 | 45 | 91% | 95% (21) | $0.0023 | 18,007 | 65% |
| b3_agentic | 100% | 184 | 45 | 82% | 95% (21) | $0.0027 | 21,516 | 55% |
| concentrate | 25% | 46 | 21 | 71% | 71% (21) | $0.0004 | 6,220 | 65% |
| concentrate | 50% | 92 | 45 | 76% | 76% (21) | $0.0005 | 7,626 | 77% |
| concentrate | 100% | 184 | 45 | 73% | 81% (21) | $0.0005 | 8,426 | 83% |

## Paired flips on shared questions (same question, bigger haystack)

| system | from → to | shared | correct → wrong | wrong → correct |
|---|---|---:|---:|---:|
| b0_topk | 25% → 50% | 21 | 1 | 2 |
| b0_topk | 50% → 100% | 45 | 7 | 2 |
| b3_agentic | 25% → 50% | 21 | 0 | 0 |
| b3_agentic | 50% → 100% | 45 | 5 | 1 |
| concentrate | 25% → 50% | 21 | 0 | 1 |
| concentrate | 50% → 100% | 45 | 4 | 3 |
