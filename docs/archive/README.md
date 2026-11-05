# Archive

Superseded material, kept unedited (apart from link fixes and wording noted below) so that every earlier claim can still be traced. Nothing here is a current TENOR result.

Until 5 November 2026 the project was called Hindsight, and several pages below use that name. Its early phases built a synthetic benchmark and a reference memory system, both now archived because results on real speech did not reproduce what the synthetic data showed ([`northwind/SYNTHETIC_DIAGNOSTICS.md`](northwind/SYNTHETIC_DIAGNOSTICS.md)).

| Folder or file | What it is | Why archived |
|---|---|---|
| [`northwind/`](northwind/) | The synthetic Northwind benchmark: benchmark card, fidelity audits, generator extras, synthetic results (analysis, economics, retirement curve, notes vs transcript) and the red-team audit of its headline | Synthetic results pointed the wrong way on real meetings; kept as diagnostics |
| [`concentrate/`](concentrate/) | Concentrate, the fact-memory reference system: memory engine, enhancer, transcript fallback, MCP server, pre-meeting brief, the Mem0 and Graphiti baselines, and a learning guide to the code | TENOR now measures shipped products from the outside; the code still runs (`packages/concentrate`) |
| [`PLAN_V2.md`](PLAN_V2.md) | The Hindsight v2 plan (2 November 2026) | Superseded by the roadmap in the [README](../../README.md#roadmap-to-v1) |
| [`SCOPE.md`](SCOPE.md), [`PHASE4_DESIGN.md`](PHASE4_DESIGN.md), [`CONCENTRATE_TUNING.md`](CONCENTRATE_TUNING.md) | Early scope, the memory-engine design and its tuning log | Concentrate-era planning |
| [`PROBE_2026-11-02.md`](PROBE_2026-11-02.md), [`PROBE_V2.md`](PROBE_V2.md) | Read-only probes of Granola's MCP | Point-in-time; the MCP has changed since ([`AGENT_INTERFACE.md`](../tracks/AGENT_INTERFACE.md#mcp-drift-the-contract-check)) |
| [`SHARING_LEAKS_DESIGN.md`](SHARING_LEAKS_DESIGN.md) | A contextual-integrity test design | Designed, judged not worth building yet; not scheduled |
| [`UI_REFERENCES.md`](UI_REFERENCES.md) | Interface references for the web app | Reference for the archived web views |
