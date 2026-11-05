# Web demo (workstream Q)

The web app (`web/`, Next.js) is the clickable way into the project: a home page, the note
viewer, the results explorer, a browser for the memory store and the pre-meeting brief. Nothing
in it calls an LLM; every number and item is read from files the CLI already wrote.

```bash
uv sync && (cd web && npm install)
# gitignored data, only needed for Memory, Brief and XL notes:
#   data/fixtures/large/northwind-xl  (concentrate synth --xl)
#   data/cache/concentrate/<corpus>/<cfg>/{memory.sqlite,complete.json}  (concentrate eval --xl --systems c)
cd web && npm run dev        # http://localhost:3100
npm test && npm run typecheck
```

## What's where

| Path | What it shows | Reads |
|---|---|---|
| `/` | Three lines on what the project is, and a tile per section with live counts | everything below |
| `/notes/<id>` | Granola-style note view: enhanced notes, my notes, the channel-attributed transcript with linked entities. The sidebar now carries the section nav and says which corpus it lists | `data/fixtures/northwind`, or Northwind-XL with `?set=xl` |
| `/results` | The results explorer ([RESULTS_EXPLORER.md](RESULTS_EXPLORER.md)), plus a **failure-type** filter, the failure type on every wrong answer, and an **"Is the ranking real?"** panel | `evals/reports/<stamp>.{json,jsonl}`, and `<stamp>.failures.jsonl` / `<stamp>.analysis.json` when present |
| `/memory` | The memory store with an **as-of date slider**: facts per entity on a validity timeline, commitments with their event history, and the gate's rejections with reasons | the newest complete `memory.sqlite`, read-only |
| `/brief?meeting=<id>` | `concentrate brief` for any Northwind-XL meeting | runs the CLI (`--json`), memoised |

Every memory and brief item links to `/notes/<id>?set=xl&line=<ids>`: the transcript opens on
the cited lines, highlighted and scrolled into view.

### Results: failure types and significance

`concentrate analyze` ([ANALYSIS.md](../archive/northwind/ANALYSIS.md)) writes two files next to a report. The
explorer joins them in when they exist and says nothing when they don't:

- `failures.jsonl` → each answer not judged correct gets a box with its primary failure type,
  its detail (e.g. "right meeting, no gold line"), the other signals that fired, and the LLM
  classifier's label and reason when `--llm` was used. Tooltips on the verdict dots name the
  type. A **Failure** filter row (`?fail=<type>`) lists questions where any selected system
  failed that way; it combines with the other filters and survives prev/next.
- `analysis.json` → a panel under the category matrix: every overall pair sorted by Holm p,
  with gap, paired-bootstrap CI, the discordant counts (A only · B only), raw and Holm p, and
  how many questions a 10-point gap would need; then the Pareto table at the analysis volume
  (1,000 questions per corpus by default) with who dominates whom. The sentence above the table
  says plainly when no pair differs reliably, which is the case for the pilot (n = 10).

The pilot report's `failures.jsonl`/`analysis.json` are the committed ones (generated with
`--llm`, $0.0003). Re-running `concentrate analyze … --fixtures data/fixtures/northwind`
without `--llm` drops the two LLM labels, so I kept the committed files. The drift chip still
warns for the pilot report, as expected (its fixtures were regenerated after the run).

### Memory: hindsight made visible

The store is bitemporal: each fact has `valid_from`/`valid_to` (when it held in the world) and
`recorded_at`/`valid_to_recorded_at` (when the memory learnt it, and when it learnt the end).
The slider picks a day *D*, and every view shows the memory **as it was on D**, by the same
rules as the MCP server (`Fact.live_at`, `Commitment.status_at`):

- a fact recorded after *D* is hidden (the entity header counts them as "learnt later");
- a fact is **true on this day** if `valid_from ≤ D < valid_to`, where `valid_to` only counts
  if the memory had learnt it by *D*; **superseded** once its end was known; **known, not yet
  valid** if it was recorded but starts later;
- restatements from later meetings join a fact's citations only once heard;
- a commitment shows from the day it was made; its status replays events on or before *D*
  (`no_follow_up` = never mentioned again, which is not done); later events are greyed out
  and labelled "after this date";
- rejections are listed for meetings up to *D*.

The timeline per (entity, predicate) draws one bar per version over the corpus's date range,
with *D* as a vertical rule, so a supersession reads as one bar ending where the next starts.
The date lives in the URL (`?as_of=`), so any state is a link.

**Which store.** The newest build directory with a `complete.json` under
`data/cache/concentrate/*/*/` (mtime of the marker), the same rule `concentrate brief` uses.
`concentrate mcp-serve` adds a filter on the corpus hash of the fixtures it serves; the web
app can't compute that hash, so instead it checks that every meeting in the store exists in
the XL fixtures and shows a warning chip if not. Today the two complete builds are both for
Northwind-XL, and the newest (`6b9c4de7`, prompts `extract.v2`/`entail.v2`) is the one the MCP
server serves. `CONCENTRATE_STORE=<build dir>` overrides. The file is opened with
`node:sqlite` in read-only mode (Node ≥ 22.13); the app never writes to it.

### Brief

`/brief` runs `uv run concentrate brief <id> --json` in the repo root (~2 s the first time per
meeting, then memoised in the server process) and renders the four sections, with the last
meeting's digest citations turned into line links, and a link to the meeting itself so you can
compare the brief with what actually came up. The suggested meetings were picked by briefing
all 184 XL meetings and taking ones where every section has content.

## Findings while building it

- **Northwind-XL reuses the 40 base Northwind note IDs with different content.** A link to
  `not_i0kpeW6NCQ4Jpe:0101` opened the *base* note, where line 101 is a different sentence.
  The viewer now takes `?set=xl`, links from Memory/Brief always pass it, and the sidebar
  keeps it. Anything else that resolves XL citations by note ID alone has the same trap.
- **"What changed since this group last met" is usually empty.** Over the 184 XL meetings the
  brief's changes section has 0 items for 137, 1 for 23, and ≥ 2 for 24 (13 for the all-hands).
  This is consistent with BRIEF.md's definition (only facts learnt between consecutive
  meetings of a series), but it means a random pick looks thin; hence the suggested meetings.
- **`no_follow_up` from day one.** Following `Commitment.status_at`, a commitment with no
  events is `no_follow_up` from the day after it was made, even before it is due. On the slider
  that reads oddly ("Send the order form, due Sep 11" is no-follow-up on Sep 11). The brief
  uses a stricter rule (only after the due date). I kept the MCP semantics so the two
  agree; it may be worth aligning the store's rule with the brief's.
- **A fact's `reason` explains the value, not the change.** The superseded "$20 per seat" fact
  carries the reason for choosing $20; the reason for moving to $25 is on the new fact. The UI
  labels it "Reason given" on each version.

## Checked

Desktop (~1280 px) and phone (375 px): no horizontal page scroll on `/`, `/notes`, `/results`,
a question page, `/memory` (all three tabs) or `/brief`; the results tables scroll inside their
cards, and the report picker now truncates instead of overflowing. On phones the note sidebar is
hidden (as before), so the section nav also appears above the note title.

## Limits

- The brief route needs `uv` on the server's `PATH` (override with `CONCENTRATE_UV`), and the
  XL fixtures with `world.json`; it is a dev-server feature, not a static export.
- The memory view loads the whole store into memory (318 fact rows, 117 commitments); fine at
  this size, not for a real account's store.
- The slider has day resolution, like the MCP server's `as_of`; two meetings on the same day
  can't be separated.
- Line links highlight but don't re-check the citation; the store's citations are positional
  IDs into the XL fixtures it was built from, which are gitignored and not versioned.
