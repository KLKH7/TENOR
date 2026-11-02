# Benchmark card: Northwind meeting memory

An open benchmark for **agent memory over meeting notes**: can a system answer an agent's questions about months of meetings correctly, cheaply and quickly, citing the transcript line it relied on?

Existing agent-memory benchmarks (LongMemEval, LoCoMo, BEAM, RealMem) are built on chat or dialogue logs. In a survey on 2026-11-02 we found none built on meeting transcripts. Meetings differ from chat in ways that matter for memory:
- the speaker is often unknown;
- the same person shows up under different names (display name, email handle, a misheard first name);
- facts change across weeks;
- commitments are made in one meeting and resolved, slipped or silently dropped in later ones.

## At a glance

| | Northwind (base) | Northwind-XL |
|---|---|---|
| Meetings | 40 | 184 |
| Time span | 8 weeks | 2026-08-31 → 2026-10-23 |
| Transcript words | ~20k | ~228k |
| Directory: people / orgs / projects | 30 / 5 / 5 | 38 / 13 / 11 |
| Questions (dev / test) | 230 | 296 (90 / 206), selected from 867 |
| Committed to git | yes (`data/fixtures/northwind`) | no; regenerate it (13 MB) |
| Purpose | smoke tests, fast iteration | the headline comparison |

Both are fully deterministic: `uv run concentrate synth --seed 42 [--scale xl]` reproduces them byte for byte, and costs nothing (no LLM).

## How it's built

1. **The world first.** A generator plans a fictional startup (30 people in the base directory, 38 in XL, counting customers): people, customers, projects, prices, launch dates, owners, and commitments with outcomes (kept, slipped, reassigned, dropped, never followed up). XL adds procedural families on top of a hand-authored plot:
   - project status that evolves as a Markov chain;
   - near-miss customer names;
   - a third "Sam".
2. **Meetings are rendered from the world.** Standups, 1:1s, customer calls, design reviews, all-hands and an investor update, both remote and in person. Each line records which world fact it expresses.
3. **Questions are derived from the world**, so each has a verified answer plus its gold transcript lines and the evidence keys it depends on.

**Concept: generate the ground truth first.** Hand-labelling answers over 228k words of transcript is slow and error-prone. Planting the facts first and rendering meetings from them means every answer is known by construction, and the generator validates that each question's evidence actually appears in the transcript.

## Fidelity to Granola

Notes use the exact JSON shape of Granola's public API and are validated by the same Pydantic models as real data (`tests/test_contract.py`). Transcripts follow the format observed live through Granola's MCP on 2026-11-02 ([`PROBE_2026-11-02.md`](PROBE_2026-11-02.md)):
- the owner is on `Microphone`; other speakers are on system audio, and only some of them are named;
- in-person meetings are all `Microphone`;
- utterances are short and fragmentary;
- some attendees are known only by email handle;
- ASR noise includes misheard names ("Coffee" for Kofi).

Noise rates were **calibrated on the author's real synced meetings**. For example:
- 82% of remote "Them" lines carried a name;
- remote utterances have a median length of 5 words (p25 1, p90 38);
- in-person utterances have a median of 25 words.

The rates are recorded in each dataset's `manifest.json`.

## Question categories

| Category | XL dev+test | What it tests |
|---|---|---|
| temporal | 50 | "what was X as of date D": valid time, not just the latest mention |
| single_hop | 40 | one fact from one meeting |
| latest_state | 40 | the current value after changes |
| commitment_status | 35 | was a promise kept, slipped, reassigned, dropped, or never followed up? |
| entity_disambiguation | 34 | which Sam, Beacon the project or Beacon Freight the customer, codename = ticket |
| decision_change | 30 | what changed and why |
| unanswerable | 25 | the system should abstain |
| multi_hop | 22 | combine facts across meetings |
| aggregation | 20 | count or list across meetings |

Categories are stratified across the dev/test split. **Tuning uses dev only; test is run once.**

## Metrics

Every run records the git SHA, models, prompt versions, seed and date.

- **Accuracy:** an LLM judge (`prompts/judge.v1.md`) is primary, with a 95% bootstrap CI. Exact match against the normalised answer is reported alongside, so judge bias is visible.
- **Abstention** precision and recall (on unanswerable and answerable questions).
- **Citation** precision and recall against gold transcript line IDs.
- **Cost:** input tokens per question and $ per question. For write-time systems, the one-off **ingest cost** is reported separately.
- **Latency:** p50 / p95 seconds per question, and tool calls per question.

## Systems compared

All systems use the same answer model (Opus 5.5), the same answer prompt where applicable, and the same judge, so differences come from the memory and retrieval, not the prompting.

| ID | System | When the work happens |
|---|---|---|
| b0 | Top-k hybrid retrieval (BM25 + embeddings, RRF) over transcript chunks | query time |
| b1 | Meeting summaries only | query time |
| b2 / b2o | Full transcripts in context (base only) / oracle: only the gold meetings (XL) | query time, ceiling |
| b3 | Agent with search → summaries → transcript-chunk tools, modelled on Granola's MCP | query time |
| b4 | Mem0 (fact extraction and update) | ingest |
| b5 | Graphiti (temporal knowledge graph) | ingest |
| c | Concentrate (speaker/entity enhancement, grounded facts, bitemporal store) | ingest |

**b3 is a re-implementation, not Granola's agent.** Granola's MCP agent only reads the connected user's own account, so it can't be pointed at synthetic data. b3 copies its observed tool shapes. Findings about the real agent come from the live probe, not from this benchmark.

## Run it

Generate XL (free):

```bash
uv run concentrate synth --seed 42 --scale xl
```

Print the estimated cost of building a memory (free):

```bash
uv run concentrate ingest --xl --systems b4,b5,c --estimate
```

Build the memories (paid). Estimates on XL: Mem0 ~$16, Graphiti ~$62, Concentrate ~$6.

```bash
uv run concentrate ingest --xl --systems b4,b5,c
```

Print the estimated eval cost (free):

```bash
uv run concentrate eval --xl --split dev --systems b0,b1,b2o,b3,b4,b5,c --estimate
```

Run the eval itself (paid). Batch mode is 50% cheaper:

```bash
uv run concentrate eval --xl --split dev --systems b0,b1,b2o,b3,b4,b5,c --batch
```

Reports land in `evals/reports/<timestamp>.{md,json,jsonl}`.

**Adding a system:** implement the `System` protocol in `packages/concentrate/agents/` (`answer(question) -> Answer`), plus `WriteTimeSystem.ingest(notes)` if it builds memory ahead of time, and register it in `evals/runner.py`.

## Known limitations

- **Synthetic.** This is the biggest weakness. Wording is template-generated, and real meetings are messier, more ambiguous and more off-topic. An LLM-polished variant is built and validated but not yet used. Calibration came from a handful of real meetings, not thousands.
- **One company, one seed** for the headline numbers.
- **No hybrid rooms yet**: a conference room joining a call as one participant carrying several voices.
- **The judge is an LLM.** The exact-match column and citation scores are there to check it, not replace it.
- **The pilot on the base set saturated** (90–100% on 10 dev questions), which is why XL exists. XL results are pending.
