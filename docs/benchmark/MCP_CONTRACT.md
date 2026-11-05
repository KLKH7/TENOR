# MCP contract check

A small test that tells you when a product's MCP starts returning a different **shape** of data. It exists because one already did, silently, and broke every agent reading it.

## What happened

On **5 Nov 2026**, Granola's `get_meeting_transcript` changed how it returns transcript chunks:

| Until 4 Nov | From 5 Nov |
|---|---|
| `chunks[].passages`: a list of `{speaker, time, text}` | `chunks[].transcript`: one string of `[m:ss] Label: text` lines |
| Absolute timestamps | Offsets from the first timed line |

There was no error and no announcement. Our sync kept running and stored **0 transcript lines** until we noticed. Any outside agent parsing the old shape fails the same way: quietly, with empty results.

## What the check does

```bash
uv run concentrate mcp-contract            # compare with the baseline; exit 1 on drift
uv run concentrate mcp-contract --update   # accept the current shapes as the new baseline
```

1. Calls Granola's four read tools once, on the newest meeting with a finished transcript. Read-only by construction: the caller is allowlisted.
2. Reduces each reply to its **shape**: field names, types, tag and attribute names, and the line format of multi-line text.
3. Diffs it against [`evals/contracts/granola_mcp.json`](../../evals/contracts/granola_mcp.json) and prints one line per change.

Fed the 4 Nov and 5 Nov replies, it prints exactly the change that broke us (test: `tests/test_contract_check.py`):

```
removed  get_meeting_transcript.chunks.json.chunks[].passages
added    get_meeting_transcript.chunks.json.chunks[].transcript: text[[m:ss] Label: text]
```

## Why the baseline can be public

Shapes hold **no values**: no titles, names, IDs or text. The meeting probed is picked at run time, so no meeting ID is committed. A test checks that planted values never reach the shape.

## Limits

- **Shape, not meaning.** A field that keeps its type but changes meaning (e.g. seconds to milliseconds) isn't caught.
- **Optional fields.** A field that is sometimes `null` is treated as compatible. One that is sometimes *absent* can raise a false alarm.
- **Needs sign-in.** It uses the author's Granola login, so it can't run in public CI; run it locally on a schedule.
- **Granola only, for now.** Wispr's MCP has the same risk; adding it takes one probe function.
