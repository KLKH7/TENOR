# The real-data slice: Granola's real agent vs Concentrate on real meetings

Every other number in this project is either synthetic (Northwind) or comes from our *re-implementation* of Granola's agent (b3). This slice is the only comparison against **Granola's production agent** (`query_granola_meetings`), on **real meetings**, with gold answers written and checked by the person who was in those meetings.

**Privacy.** Questions, gold answers, every system's answers, the LLM cache and the memory store all stay in `data/real/`, which is gitignored. This file and the committed code contain only the method and aggregate scores.

## Method

1. **Corpus.** The author's own Granola account, synced through `GranolaMCPSource` into `data/real.db`: **4 meetings** (Oct 16–30, 2026; 3 remote, 1 in person; 881 transcript passages). This is small: see *Limits*.
2. **Questions** (`concentrate real-kit draft | review | add | status`).
   - An LLM (`prompts/real_draft.v1.md`, Opus 5.5, $0.57) drafted 47 candidates over the whole corpus, stratified into 7 categories with a target mix of ~30:

     | Category | Target |
     |---|---|
     | who said X | 5 |
     | what was decided | 5 |
     | what changed | 4 |
     | commitment status | 5 |
     | which person (disambiguation) | 3 |
     | temporal / as-of | 4 |
     | unanswerable | 4 |

   - Each draft carries its evidence (line IDs and a quote) for the reviewer.
   - **The author reviewed every draft** in an interactive terminal tool: accept, edit (question, gold answer, key terms) or reject. Only `verified` questions are scored. Self-written questions (`real-kit add`) count as verified.
   - *Drafting bias:* LLM drafting may favour questions that an LLM reading full transcripts finds easy, which plausibly favours transcript-reading systems. Rejected drafts and the reasons for rejection are kept, so this can be audited.
3. **Systems.** Each answers every verified question.
   - **Granola's agent:** one `query_granola_meetings` call per question.
     - Called from Python through `ReadOnlyCaller`, an allowlist of read tools; a test checks that every write tool is refused.
     - The raw response, wall-clock latency and timestamp are recorded once in `data/real/granola/`, and re-runs replay them.
     - The standing `<transcript_note>` disclaimer is stripped. Citations are parsed from its `notes.granola.ai/d/<id>` links.
     - It runs on Granola's side, so its API cost to us is $0. It has no abstain flag, so the judge reads its prose.
   - **Concentrate (`c`):** the full pipeline (Enhance → extract.v2 → grounding gate → bitemporal store), built over the 4 meetings.
     - The directory is built from attendee metadata only (`real-kit directory`), so no Northwind people leak in.
     - Build: 8 LLM calls, $0.23; 43 facts and 11 commitments kept, 2 facts rejected as not entailed.
   - **b0 top-k (k=2):** hybrid retrieval of the 2 most relevant meetings, with full transcripts in context. (With 4 meetings, k=20 would be full context.)
   - **b3** (our re-implementation of Granola's agent) was **not run**: the user chose to keep this run under the $5 cap, and the real agent is the better reference.
4. **Scoring.** All systems use the benchmark's judge, `judge.v1` (Opus 5.5), unchanged, against the author's gold answer.
   - Also reported: key-term exact match, and **meeting-level** citation precision/recall (cited meetings vs gold evidence meetings; the real data has no gold lines).
   - Second judge: `concentrate judge-check` (Sonnet 5.5) on the same report: `CONCENTRATE_CACHE=data/real/cache/llm uv run concentrate judge-check data/real/reports/<stamp>.jsonl`.
   - 95% bootstrap CIs (seed 42).
5. **Reproduce:**
   ```bash
   uv run concentrate real-slice --systems granola,c,b0 --estimate
   ```
   Then run it without `--estimate`. The aggregate (content-free) result lands in `data/real/reports/<stamp>.aggregate.json`, and only that is copied below.

## Results

_Pending: the author is verifying the questions. This section will hold only aggregate scores (accuracy with CI per system, per category, latency, citations, judge agreement)._

## Ask both MCPs the same question about the same real meetings

`concentrate real-kit serve` serves Concentrate's memory of **your real meetings** through WS-D's read-only MCP server ([`MCP_SERVER.md`](MCP_SERVER.md)). It offers the same six tools as `mcp-serve`:
- the store is opened SQLite `mode=ro`;
- the corpus comes from `data/real.db` and the directory from `data/real/directory.json`;
- `ask_memory(answer: true)` caches in `data/real/cache/llm`;
- it starts in about 1 s without embeddings and about 6 s with them.

Unlike the Northwind demo in `MCP_SERVER.md`, Granola's MCP and this server now hold **the same meetings**, so you can compare two answers to one fact.

**Claude Desktop.** Open `~/Library/Application Support/Claude/claude_desktop_config.json` (Settings → Developer → Edit Config). Inside `"mcpServers"`, add this entry and keep any entries already there. `/Users/kianlakhani/Code/concentrate` is the main checkout; `data/real/` physically lives there (this worktree's `data/real` is a symlink to it). **Until this branch is merged into the branch checked out there, use the worktree path** `/Users/kianlakhani/Code/concentrate/.claude/worktrees/ws-b-real-slice-9332a8` instead.

```json
{
  "mcpServers": {
    "concentrate-real": {
      "command": "/Users/kianlakhani/.local/bin/uv",
      "args": [
        "run",
        "--directory",
        "/Users/kianlakhani/Code/concentrate",
        "concentrate",
        "real-kit",
        "serve"
      ]
    }
  }
}
```

Restart Claude Desktop. Granola's MCP is the custom connector `https://mcp.granola.ai/mcp` (Settings → Connectors). Prefix each prompt with "Use only the concentrate-real tools" or "Use only the granola tools".

**Claude Code** (alternative):

```bash
claude mcp add --scope user concentrate-real -- /Users/kianlakhani/.local/bin/uv run --directory /Users/kianlakhani/Code/concentrate concentrate real-kit serve
```

**Good same-data prompts** are the probe's misses ([`PROBE_V2.md`](PROBE_V2.md)):
- "who is <full name of an attendee>, and which meetings were they in?";
- "which meetings did <shared first name> appear in, and is it the same person?";
- "when is the next <team> meeting?";
- one commitment from the in-person meeting, asked narrowly and then broadly.

**Known Concentrate gap, measured on the real store:** its directory comes from attendee metadata only, so a person who is only *mentioned* (never an attendee) isn't an entity. `resolve_entity` on a shared first name returns just the attendee. Concentrate can therefore miss the same shared-name case Granola missed. Ask it anyway.

**Privacy:** the server prints nothing to stdout except the protocol. The answers Claude shows you are your private data; don't paste them into anything committed.

## Limits (read before quoting any number)
- **n ≈ 30 questions over 4 meetings.** A 95% CI on accuracy is roughly ±15–18 points. Only large differences are meaningful; per-category rows (n = 3–5) are anecdotes.
- **4 meetings don't test retrieval at scale.** Every system can effectively see everything, so this slice measures *reading and reasoning over real speech*, not finding the needle. The synthetic scale curve covers scale.
- **One user, one account.** One person's meeting style, mostly student and startup meetings; no external validity across users.
- **Time.** Granola answers "today"; Concentrate and b0 are told the question's `as_of` date (the day it was written). Questions are about past events, so this rarely matters.
- **The author writes and checks gold answers, and the author built Concentrate.** Mitigations: an LLM drafts the questions blind to systems; review happens before any system answers; the judge is automated and second-judged.
