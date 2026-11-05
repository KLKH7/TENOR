# Agent interface track

This page defines the agent-interface track: what an outside agent receives through a product's MCP. It also documents the MCP contract check, a piece of harness maintenance that tells TENOR when a product's reply format has changed so the harness can be updated.

| | |
|---|---|
| **Question** | When an outside agent asks a meeting product through its MCP, does the payload say when it is unsure, point to evidence inside the meeting, and stay stable across repeats? |
| **How measured** | Deterministic classifiers over each answer payload (answer type, evidence granularity, abstention, stability, latency) |
| **Current results** | Granola, author's 4 private meetings (47 DEV questions): 89% useful and comprehensive [77, 95]; 0 of 47 answers point inside a meeting; no abstain or coverage field ([`results/AGENT_INTERFACE.md`](../results/AGENT_INTERFACE.md)). Wispr Flow: not yet scored |
| **Status** | Preliminary |

**Task.** An external agent (Claude, ChatGPT, Cursor) reaches a meeting product only through its MCP. This track scores **what that agent receives**: not only whether the answer is right, but whether the payload says when it is unsure, how fine-grained its evidence is, whether the same question gets the same answer twice, and how long it takes. It is product-neutral: every product is asked through its [black-box adapter](../method/BLACKBOX.md) and scored by the same deterministic classifiers. No LLM is used to score (verdicts are reused from the black-box judge, `judge.v1`).

Results: [docs/results/AGENT_INTERFACE.md](../results/AGENT_INTERFACE.md). Code: [`evals/agent_interface.py`](../../packages/concentrate/evals/agent_interface.py). Tests: [`tests/test_agent_interface.py`](../../tests/test_agent_interface.py).

```bash
uv run concentrate agent-interface                      # $0: recorded payloads + recorded repeats
uv run concentrate agent-interface --live granola       # also records missing repeats (<= 24 read-only calls)
```

## Why this track

Granola's own agent-design post names four answer types ("Useful and comprehensive", "Useful but incomplete", "Incomplete, and clearly so", "Wrong, but confident"; re-verified at source 2026-11-05) and prizes transparency about coverage. An answer-accuracy score alone can't see the difference between the last two, and that difference is a property of the **interface**: whether the payload carries an abstain flag, a caveat or a pointer to evidence the agent can check. So the track measures the interface directly.

## Metrics

All computed by regex and set arithmetic over the adapter's parsed `ProductAnswer` (`text`, `cited_ids`, `raw`, `latency_s`, `abstained`). Rates carry 95% Wilson intervals.

| Metric | Definition |
|---|---|
| **Four answer types** | `bucket(verdict, flagged, unanswerable)`, applied in order: judged correct → *useful and comprehensive*; partial or incorrect and **flagged** → *incomplete and clearly so*; partial, unflagged, answerable → *useful but incomplete*; otherwise → *wrong but confident*. Reported with judge 1, with judge 2 where the run had one, and with **strict** flags (gap statements excluded). The taxonomy is Granola's; this mapping is ours. |
| **Flagged** | Any of: hedge (*may, might, appears, likely, unclear*...), coverage caveat (*based on the available meetings*...), gap statement (*the meeting did not name...*, *no later confirmation...*), or abstention. |
| **Abstention** | The payload's machine-readable abstain flag when it has one; otherwise a prose heuristic (*I couldn't find any...* at the start). `abstain_field` records which. |
| **Evidence granularity** | *line*: the payload carries a pointer inside a meeting (a line/utterance ID such as `not_x:0042`, a transcript chunk ID, or a timestamp); *meeting*: only whole-meeting citations; *none*: no citation. |
| **Instructs the caller** | Outside the answer text, the payload tells the agent to call another tool (e.g. a standing "call `search_transcripts`" note). |
| **Quotes / verbatim** | Quoted spans of 3+ words, and how many appear word-for-word in the cited meetings' notes or transcript (local mirror). |
| **Stability** | A stratified sample (every non-correct answer first, then round-robin by category, unanswerables first; seeded) is re-asked `--repeats` more times. Per question: same key-term pattern (the gold answer's key terms present or absent), same flag status, same cited meetings (and mean pairwise Jaccard), latency SD and CV. Ambiguous cases go to a private human-check list. |
| **Latency** | p50 / p90 of end-to-end seconds as recorded, plus the repeat asks' p50 / p90. |

Everything is cross-tabulated by question category and by the notes-sufficient / transcript-needed / unanswerable tag.

**What it can't see.** Regexes read English prose; key-term presence is a proxy for "same fact" (a name can appear in the wrong role). Classifiers are tested on synthetic text only, and were checked against masked fragments of real answers, not by a second annotator.

## Add a product

1. Make it askable on the [black-box track](../method/BLACKBOX.md#adding-a-product): a JSON spec (or a built-in adapter) naming its one read-only question tool and how to read its answer, citations and, if it has one, abstain flag.
2. Record and judge its answers: `uv run concentrate real-kit run --product <name> --split dev` (this writes `data/real/answers/<name>/` and a report `.jsonl`).
3. Score it: `uv run concentrate agent-interface --products granola,<name> --report data/real/reports/<that report>.jsonl`.
4. Stability: `--live <name>` re-asks the stratified sample through the same allowlisted adapter, at most `--max-calls` calls. If the product spends money per call (an LLM answer), price it first: the repo's budget rule applies.

A product scores well on this track by **saying what it doesn't know** (abstain field, coverage flag), **pointing inside meetings** (line or span IDs), and **answering the same way twice**, not only by being right.

## Rules

- Read-only: each adapter allowlists exactly its one question tool; nothing else can be called ([BLACKBOX.md](../method/BLACKBOX.md)).
- Privacy: per-question rows, repeats and the human-check list are written only under `data/real/agent_interface/<product>/` (gitignored). The committed aggregate is checked by `check_committable` against every question, gold answer and answer text before it is written.
- Determinism: same recordings and seed → same aggregate (apart from the date and git SHA).

## Harness maintenance: the MCP contract check

Products may change their MCP reply formats at any time; that is their right, and it is not a product flaw. TENOR's harness parses those replies, so it needs to know when a format changes. The contract check is a small test that tells the harness when a product's MCP starts returning a different **shape** of data, so the parser can be updated before results are affected. It is not a scored track.

### Why it was added

On 5 Nov 2026 the harness detected a change in one product's reply format and was updated. Granola's `get_meeting_transcript` now returns transcript chunks differently:

| Until 4 Nov | From 5 Nov |
|---|---|
| `chunks[].passages`: a list of `{speaker, time, text}` | `chunks[].transcript`: one string of `[m:ss] Label: text` lines |
| Absolute timestamps | Offsets from the first timed line |

TENOR's sync still parsed the old shape, so it stored 0 transcript lines until the harness was updated. The contract check now catches a change like this before a sync.

### What the check does

```bash
uv run concentrate mcp-contract            # compare with the baseline; exit 1 on drift
uv run concentrate mcp-contract --update   # accept the current shapes as the new baseline
```

1. Calls Granola's four read tools once, on the newest meeting with a finished transcript. Read-only by construction: the caller is allowlisted.
2. Reduces each reply to its **shape**: field names, types, tag and attribute names, and the line format of multi-line text.
3. Diffs it against [`evals/contracts/granola_mcp.json`](../../evals/contracts/granola_mcp.json) and prints one line per change.

Fed the 4 Nov and 5 Nov replies, it prints exactly that change (test: `tests/test_contract_check.py`):

```
removed  get_meeting_transcript.chunks.json.chunks[].passages
added    get_meeting_transcript.chunks.json.chunks[].transcript: text[[m:ss] Label: text]
```

### Why the baseline can be public

Shapes hold **no values**: no titles, names, IDs or text. The meeting probed is picked at run time, so no meeting ID is committed. A test checks that planted values never reach the shape.

### Limits

- **Shape, not meaning.** A field that keeps its type but changes meaning (e.g. seconds to milliseconds) isn't caught.
- **Optional fields.** A field that is sometimes `null` is treated as compatible. One that is sometimes *absent* can raise a false alarm.
- **Needs sign-in.** It uses the author's Granola login, so it can't run in public CI; run it locally on a schedule.
- **Granola only, for now.** Wispr's MCP has the same risk; adding it takes one probe function.
