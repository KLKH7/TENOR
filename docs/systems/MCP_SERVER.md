# Concentrate as an MCP server

`concentrate mcp-serve` serves a built Concentrate memory to any MCP client (Claude Desktop, Claude Code) over **stdio**. Add it next to Granola's official MCP and you can put the same kind of question to both: Granola's agent searches summaries and transcripts when you ask, while Concentrate answers from facts, commitments and entities it extracted ahead of time, each one citing transcript line IDs.

Code: `packages/concentrate/mcp_server/` (`service.py` holds the logic with no MCP in it; `server.py` is the thin MCP layer). Tests: `tests/test_mcp_server.py` (in-process MCP client, tiny store, no network).

## Tools

All six tools are read-only (`readOnlyHint: true`, `destructiveHint: false`) and the store is opened with SQLite `mode=ro`, so a write would fail even if the code tried one. Every tool takes an optional `as_of` (YYYY-MM-DD). The default is the day of the last meeting in the memory, never the wall clock, so the same call always gives the same answer.

| Tool | What it returns | Closest Granola MCP tool |
|---|---|---|
| `ask_memory(question, as_of?, answer?)` | The exact context Concentrate's eval answers from: facts about the entities in the question (with validity dates and status), related commitments, the closest facts and digests, and the transcript lines they cite. With `answer: true` it also returns Concentrate's own cited answer (same `answer.v1` prompt and model as the eval; one LLM call, about $0.03–0.05). | `query_granola_meetings` |
| `get_facts(entity, predicate?, as_of?)` | Every version of an entity's facts: value, `valid_from`/`valid_to` (when it held), `recorded_at` (when the memory learnt it), which meeting changed it, why, and the lines that say so. Facts learnt after `as_of` are hidden. | none (Granola has no fact store) |
| `get_commitments(person?, status?, as_of?)` | Who promised what, by when, and what happened next: `done`, `slipped`, `reassigned`, `dropped`, or `no_follow_up` (never mentioned again, which is not the same as done). `status: "unfinished"` = anything not done or dropped. Owners the transcript couldn't pin down are kept as candidates, not guessed. | part of `query_granola_meetings` answers |
| `resolve_entity(name)` | Every person/org/project a name could mean, with role, email, meetings attended (linked by email even when the invite has no name), and what the memory knows about each. | none |
| `get_meeting(meeting_id)` | Title, date, attendees mapped to entities, Granola's AI summary, Concentrate's digest, and the facts and commitments taken from the meeting. Accepts a `not_…` id or a notes.granola.ai URL. | `get_meetings` |
| `get_transcript_lines(ids, context?)` | The lines behind a citation: text, capture channel (`me` = microphone, `them` = system audio), Granola's speaker label if any, timestamp, and the speaker Concentrate attributed (`? A\|B` = unsure between A and B). | `get_meeting_transcript(chunk_ids=…)` |

There is also one **resource**, `concentrate://memory`: which store is being served, whether its build is complete, the build config, counts, and the default as-of date.

Ambiguity is reported, not guessed. `get_facts(entity="Sam")` returns an error that lists `sam_p`, `sam_o` and `sam_r` and asks the agent to pick one or call `resolve_entity`.

## Run it

```bash
uv run concentrate mcp-serve --xl
```

- `--xl` serves Northwind-XL (184 meetings). Without it, the server serves base Northwind, which needs a Concentrate build of the 40-meeting corpus first (none exists today).
- The server finds the build by corpus hash under `data/cache/concentrate/<hash>/` and picks the newest **complete** build. `--store DIR` overrides that, and `--allow-partial` serves an unfinished build.
- `--no-embeddings` starts faster but retrieves with BM25 only. The default (embeddings on) matches the eval.
- Start-up takes about 6 s warm. The first launch after a `uv sync` can take about 35 s (package build and torch import).
- It prints nothing to stdout, because on stdio, stdout *is* the protocol. The one start-up line ("serving … (184 meetings)") goes to stderr, which Claude shows in its MCP logs.

## Add it to Claude

Below, `/Users/kianlakhani/Code/concentrate` is the main checkout. **Until this branch is merged there, use this worktree's path instead:** `/Users/kianlakhani/Code/concentrate/.claude/worktrees/ws-d-mcp-server-2e61f4`. Its `data/` entries are symlinks to the main checkout's data. Use the absolute path to `uv`: Claude Desktop doesn't inherit your shell's `PATH`.

### Claude Desktop

Open `~/Library/Application Support/Claude/claude_desktop_config.json` (Settings → Developer → Edit Config) and add a `concentrate` entry inside `"mcpServers"`, keeping any entries already there:

```json
{
  "mcpServers": {
    "concentrate": {
      "command": "/Users/kianlakhani/.local/bin/uv",
      "args": [
        "run",
        "--directory",
        "/Users/kianlakhani/Code/concentrate",
        "concentrate",
        "mcp-serve",
        "--xl"
      ]
    }
  }
}
```

Restart Claude Desktop. Granola's official MCP is a remote server with OAuth: add it under Settings → Connectors as a custom connector with the URL `https://mcp.granola.ai/mcp`.

### Claude Code

```bash
claude mcp add --scope user concentrate -- /Users/kianlakhani/.local/bin/uv run --directory /Users/kianlakhani/Code/concentrate concentrate mcp-serve --xl
```

```bash
claude mcp add --scope user --transport http granola https://mcp.granola.ai/mcp
```

Or as project config in `.mcp.json`:

```json
{
  "mcpServers": {
    "concentrate": {
      "command": "/Users/kianlakhani/.local/bin/uv",
      "args": ["run", "--directory", "/Users/kianlakhani/Code/concentrate", "concentrate", "mcp-serve", "--xl"]
    },
    "granola": { "type": "http", "url": "https://mcp.granola.ai/mcp" }
  }
}
```

`ask_memory(answer: true)` reads `ANTHROPIC_API_KEY` from the repo's `.env` (`uv run --directory` runs from the repo). Without a key, every tool still works: `ask_memory` returns the context plus an `answer_error`, and the calling agent answers from the context itself.

## Demo script: ask both

**Important caveat.** The two servers don't hold the same meetings. Granola's MCP reads **your real account**, and Concentrate serves the **synthetic Northwind** company. So this demo compares *how each one handles a kind of question*, not two answers to one fact. For each question, ask Concentrate the Northwind version and ask Granola the same *kind* of question about your own meetings. Questions 1 and 2 are the two failures we saw live in [`PROBE_2026-11-02.md`](../archive/PROBE_2026-11-02.md) §3. A true same-data comparison would need Concentrate to serve a memory built from your real meetings (see "Open question" below).

Prefix each prompt with "Use only the concentrate tools" or "Use only the granola tools", so the client doesn't mix the two.

1. **Cross-meeting identity.** *"Which meetings did Megan Liu attend? Include ones where the invite lists only an email."*
   - **Granola (probe §3.1):** asked "who is <teammate> and which meetings were they in?", it found one meeting. It missed an earlier one where the invite showed only an email handle and the name appeared only in the transcript and summary.
   - **Concentrate:** `resolve_entity` links invitees by email, not display name. Megan Liu attended 39 Northwind meetings, and 12 of those invites carry only `megan.l@…` with no name.
2. **In-person attribution.** *"In the in-person 1:1 between Priya and Tomás on 2026-10-07, who committed to what? Quote the line."*
   - **Granola (probe §3.2):** in an in-person meeting everyone shares one microphone, and owners came back as "Unidentified speaker".
   - **Concentrate:** `get_meeting` and `get_transcript_lines` show the commitment ("take the skills matrix for Tomás") owned by Priya Raman, and cite line `not_WzcSce6AYbjoMf:0030`, which is on the `me` channel with no Granola speaker label. Where the memory can't tell who spoke, it says so instead of guessing: `get_commitments` marks those owners `owner_unresolved` and lists the candidates (4 such commitments in XL).
3. **Which Sam?** *"What did Sam commit to, and did it happen?"*
   - **Concentrate:** the tools refuse to guess between Sam Patel (Backend Engineer), Sam Okafor (Product Designer) and Sam Rivera (Orchard Labs, external). `resolve_entity("Sam")` shows the evidence for each, and the agent should ask you which one, or answer for each.
4. **What was true then vs now.** *"What was the Teams price per seat as of 2026-09-20, what is it now, and why did it change?"*
   - **Concentrate:** `get_facts` shows `$20` (valid 09-08 → 09-29, superseded) and the later value with its reason, each with lines. Asked with `as_of: "2026-09-20"`, the smoke test answered "$20 per seat", citing four lines from the 09-08 pricing review. The later change is hidden because the memory hadn't learnt it yet.
   - **Granola:** answers from notes and summaries, and its own tool notes say "dated plans do not establish current progress".
5. **Dropped vs never discussed.** *"Which commitments made in September never got a follow-up?"*
   - **Concentrate:** `get_commitments(status="no_follow_up", as_of="2026-09-30")` lists 34 of them, which is distinct from `done`.
   - **Granola (probe §3.3):** most items came back "no later evidence", which is honest, but from summaries it can't separate dropped from not discussed.

Follow any answer with *"show me the exact transcript lines behind that"*. Concentrate's citations are line IDs, which `get_transcript_lines` resolves directly. Granola needs `search_transcripts` and then `get_meeting_transcript`.

## Smoke test (2026-11-02)

The server was run on the XL build `78a4767dabf454ae/6b9c4de7` (184 meetings, complete). Two `ask_memory(answer: true)` calls on `claude-opus-5-5` cost **$0.086** in total:
- *"Which Sam owns the Falcon cutover script, and is it done?"* got: Sam Patel, committed 08-31, slipped 09-07, done 09-14, with 5 line citations.
- *"What was the Teams plan price per seat?"* as of 2026-09-20 got: $20, decided at the 09-08 pricing review, with 4 citations.

These are two hand-checked answers, not an evaluation.

## Open question (for the author)

To make "ask both" an apples-to-apples comparison, Concentrate would serve a memory built from your real meetings (`GranolaMCPSource` → build → `mcp-serve --store …`). That needs about $0.10 of ingest for 4 meetings, and it keeps real data local: nothing is committed and the server is read-only. It isn't built in this workstream.
