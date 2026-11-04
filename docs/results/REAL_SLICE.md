# The real-data slice: Granola's real agent vs Concentrate on real meetings

Every other number in this project is either synthetic (Northwind) or comes from our *re-implementation* of Granola's agent (b3). This slice is the only comparison against **Granola's production agent** (`query_granola_meetings`), on **real meetings**, with gold answers **drafted by an LLM and approved in bulk by the meeting owner** (see *How the gold was verified*).

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
   - The kit offers per-question review (`real-kit review`: accept, edit or reject) and self-written questions (`real-kit add`). Only `verified` questions are scored.
   - **How the gold was verified in this run (read this):** the meeting owner read the list of all 47 questions and gold answers in chat and approved them **in one step** ("all good"). **0 were edited and 0 rejected; 0 are self-written.** So these are *LLM-drafted gold answers, approved in bulk by the meeting owner*, **not** answers hand-verified one by one. The drafting LLM read the full transcripts, so a wrong gold answer is possible. To find any, `real-kit spotcheck` lists every question where a system disagrees with the gold (private file), so the owner can re-check exactly those. Scores are reported both before and after that spot-check, if one is done.
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
   - **b0 top-k (k=1):** hybrid retrieval of the single most relevant meeting, with its full transcript in context. (With 4 meetings, k=20 would be full context. k=1 was chosen to fit the budget: one real meeting is ~18k tokens.)
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

## Results (2026-11-02)

Model `claude-opus-5-5` (answers and judge); judge `judge.v1`; seed 42. The memory build was reused from the cache. Runs:
- `20261103T004019Z`: Granola + Concentrate. It started from `c9caf49` and recorded `0cad9c5` (dirty) at the end; the answering and scoring code didn't change during the run.
- `20261103T013240Z`: b0 on all 47. This is a cache-only replay of runs `…004102Z` and `…013132Z`, at git `9490a7f`.

**Coverage.**
- **Granola and Concentrate: 39 of 47.** That run hit its budget cap. The 8 it missed were the last in the file: **all 6 unanswerable** questions and 2 temporal. So abstention is **not measured** for Granola and Concentrate. Finishing them (~$0.50) wasn't approved.
- **b0: all 47**, after an approved top-up.
- The runner now interleaves categories (`interleave`), so a future budget stop drops questions evenly.

| System | n | Accuracy [95% CI] | Second judge (Sonnet 5.5) | Key-term match | Meeting cite P / R | Latency p50 / p95 | $/question |
|---|---|---|---|---|---|---|---|
| **Granola agent** (`query_granola_meetings`) | 39 | **92%** [82, 100] | 90% | 87% | 99% / 94% | 7.9 / 10.7 s | $0 to us (runs on Granola) |
| **Concentrate** | 39 | **59%** [44, 74] | 56% | 53% | 100% / 73% | 4.2 / 8.5 s | $0.056 + $0.23 one-off build |
| **b0 top-k, k=1** (same 39) | 39 | **77%** [64, 90] | (see below) | | | | |
| b0 top-k, k=1 (all 47, incl. 6 unanswerable) | 47 | 81% [68, 91] | 83% | 78% | 100% / 80% | 3.3 / 7.2 s | $0.078 |

- **Judges agree.** Primary and second judge agree on correct vs not-correct for **97%** of answers (Cohen's κ 0.93 for both reports), and the system ranking doesn't change.
- **The gap is large.** Granola's lead over Concentrate (+33 points) is larger than the noise: the 95% intervals don't overlap. Paired by question, Granola was right where Concentrate was wrong 14 times, and the reverse happened once (sign test p ≈ 0.001). On the same 39 questions, Granola vs b0 (+15) and b0 vs Concentrate (+18) are suggestive only, since their intervals overlap.
- **Latency isn't like-for-like.** Granola's latency is one remote MCP call, including its agent loop. Ours is one local answer call; retrieval takes milliseconds.

**Accuracy by category** (n per cell is tiny: read these as anecdotes):

| Category | n | Granola | Concentrate | b0 (all 47) |
|---|---|---|---|---|
| who said X | 8 / 8 | 88% | **0%** | 88% |
| what was decided | 8 / 8 | 100% | 88% | 100% |
| what changed | 6 / 6 | 83% | **100%** | 50% |
| commitment status | 8 / 8 | 100% | 75% | 62% |
| which person | 5 / 5 | 80% | 40% | 60% |
| temporal / as-of | 4 / 6 | 100% | 50% | 100% |
| unanswerable | 0 / 6 | not run | not run | 100% (6/6) |

### What this says (honestly)
1. **On real meetings, Granola's production agent is clearly better than Concentrate today.** This contradicts the direction of the synthetic results, and this slice exists to catch exactly that.
2. **Why Concentrate loses: the write-time memory is lossy for long-tail details.**
   - It kept 43 facts and 11 commitments for 4 meetings, and it answers only from those facts plus the transcript lines they cite.
   - The real questions were drafted from full transcripts and ask about arbitrary details: who proposed X, a passing mention of a date or a name.
   - Of Concentrate's 16 non-correct answers, **10 were abstentions** of the kind "the material doesn't mention it".
   - Granola's agent reads summaries and then transcripts at question time, so nothing is lost before the question arrives.
   - Our synthetic questions are generated from the very facts the generator planted, so they reward extraction. **This is the external-validity gap the slice was meant to find.**
3. **Speaker attribution in remote meetings:** where Concentrate did answer a "who said" question, it twice named the wrong person (the same person both times). Granola reads the named system-audio labels directly.
4. **Where Concentrate's design does show up:** *what changed* (100% vs Granola 83% and b0 50%). This is the category its bitemporal store is built for, though n=6.
5. **b0, which just reads one whole retrieved meeting, beats Concentrate.** At this corpus size, "read the right transcript" beats "read the extracted notebook".
6. **What it would take for Concentrate to compete on real data** (a hypothesis for a *new* dev set, not tuned here):
   - fall back to raw transcript retrieval when the facts don't cover a question (the L0 tier is already stored);
   - use the named system-audio speaker labels in Enhance;
   - extract many more, finer-grained facts.
   Any such change must be evaluated on fresh real questions, because these 47 have now been seen.

### Gold quality
The gold answers were LLM-drafted and approved in bulk (0 edits). On at least one "who said" question, Granola and Concentrate both disagree with the gold, so the gold may be wrong there. `data/real/reports/spotcheck.md` (private) lists, per question, every system's verdict and answer: 22 of 47 questions have at least one non-correct verdict. A spot-check by the meeting owner could move every system's score; we'll report numbers both before and after any correction.

### Spend (an honest overrun)

**Approved:** ~$4.95 for the run and the judge (~$4.20 at first, then a ~$0.80 top-up to finish b0), plus ~$0.80 already spent on drafting and the memory build.

**Actual: ~$7.50 in total**, ~$6.70 of it after the approvals.

| Item | Cost | Note |
|---|---|---|
| Question drafting | $0.57 | |
| Memory build | $0.23 | |
| Granola + Concentrate run | $2.54 | estimate was $1.75 |
| b0, first run | $3.01 | stopped at 37 questions |
| b0, approved top-up | $0.87 | brought b0 to 47 |
| Second judge | $0.28 | |

**Why it went over:**
1. Answers that read real transcripts cost more than estimated, because one real meeting is ~18k tokens.
2. I set the per-run budget caps ($2.50, $3.00, $0.85) **above** the approved amounts, instead of at them, so nothing stopped the overrun.

**The lesson:** caps must equal the approval, not exceed it.

Granola's agent costs us nothing: it runs on Granola's side.

**Final scope:** the user chose to stop at 39 questions for Granola and Concentrate. b0's 37-question run was completed to 47 under the earlier top-up approval. The b0 rows on the same 39 questions and on all 47 are both shown above.

## Ask both MCPs the same question about the same real meetings

`concentrate real-kit serve` serves Concentrate's memory of **your real meetings** through WS-D's read-only MCP server ([`MCP_SERVER.md`](../systems/MCP_SERVER.md)). It offers the same six tools as `mcp-serve`:
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

**Good same-data prompts** are the probe's misses ([`PROBE_V2.md`](../archive/PROBE_V2.md)):
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
