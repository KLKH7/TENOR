# Results explorer (workstream E)

A view in the existing Next.js app (`web/`, port 3100) that makes every benchmark number
inspectable. Open `http://localhost:3100/results`; it redirects to the newest report.

```bash
cd web && npm install && npm run dev     # then open /results
npm test                                 # unit tests for the pure explorer logic (node --test)
```

## What it shows

| View | Where | What you can do |
|---|---|---|
| **Provenance chips** | top of every page | report stamp, split and n, answer model, git SHA (amber if the tree was dirty), seed, prompt versions (hover), spend, fixture set, and whether the stored citation scores reproduce |
| **Leaderboard** | `/results/<stamp>` | systems × accuracy (bar + 95% CI whisker), exact match, citation precision/recall, $/question, input tokens/question, latency p50/p95, tool calls, ingest $ (write-time systems). Click a system to list its questions |
| **Cost vs accuracy** | same page | log-x scatter with CI bars; the Pareto front (nothing is both cheaper and better) is dashed and green |
| **Category matrix** | same page | accuracy by category × system as a heatmap with `correct/n` in each cell. Click a cell to filter the question list to that category and system |
| **Question list + filters** | `#questions` | category, systems (multi-select), *systems disagree*, *everyone wrong*. Filters live in the URL, so a filtered view is a shareable link |
| **Question drill-down** | `/results/<stamp>/q/<qid>` | question, gold answer, exact-match keys, as-of date, the gold evidence lines (quoted), then every system's answer side by side with verdict, cost, tokens in/out, latency, tool calls, EM, cite P/R and "gold meeting seen" (was the meeting holding the evidence in what the system retrieved?). Prev/next walks the current filter |
| **Transcript panel** | `?cite=<line id>&sys_panel=<system>` | clicking a citation opens the meeting's transcript scrolled to that line. Lines are marked for the chosen system: **green** = cited and gold, **amber** = cited but not gold, **red dashed** = gold but not cited. "Gold only" shows just the evidence. Tabs switch system without losing your place |

## Design choices

- **Files, not a database.** Server components read `evals/reports/<stamp>.{json,jsonl}` and
  `*.judgecheck.<model>.md` directly (`web/lib/results.ts`), memoised on file mtime. A new
  report appears in the picker as soon as the runner writes it; the XL report needs no code
  change. `CONCENTRATE_REPORTS` and `CONCENTRATE_ROOT` override the paths.
- **All state in the URL.** Filters, the open citation and the highlighted system are query
  params, so there is almost no client JS (a report `<select>` and a scroll-into-view) and any
  view can be pasted into a review comment.
- **Reproduce before you highlight.** Citation ids are positional (`not_…:0025` is the 26th
  utterance), so if the fixtures are regenerated after a run, line 25 is a different sentence
  and the green/amber/red marks would silently lie. The loader therefore:
  1. takes the fixture dir from `meta.fixtures` (newer reports) or, for older ones, the fixture
     dir whose questions match every `(qid, question)` in the jsonl (flagged *inferred*);
  2. tries it **at the report's git SHA** (`git show <sha>:<dir>/…`; the fixtures are committed)
     and as it is on disk now;
  3. recomputes every row's citation precision/recall with the same rule as
     `concentrate.evals.scoring.citation_scores` and keeps the version that reproduces the
     stored values best (ties go to the SHA).
  The chip says "✓ citation scores reproduce (36/36)" or "N/M answers don't reproduce", and an
  affected answer card carries its own warning.
- **Small-n warning.** When any system has n < 30, the overview says how many accuracy points
  one question is worth and that the CIs overlap.
- **Visual language** follows the note view (docs/UI_REFERENCES.md "Eval dashboard"): Gumloop's
  tiles → charts → records table order, Adaline's per-question rows, Granola's pill chips, serif
  titles, greige canvas and white transcript sheet. No Granola logo, name or fonts.

## What we found while building it (pilot report 20261102T100403Z)

- **The pilot report's citations only line up with the fixtures at e04f133.** Against today's
  `data/fixtures/northwind` (regenerated in 03ab479 → 2c96ac1), 18 of 36 scored answers no
  longer reproduce their stored citation precision/recall. At e04f133 all 36 reproduce. The
  explorer handles this automatically, but anything else that joins old reports to current
  fixtures (e.g. a write-up quoting evidence lines) has the same trap.
- **The judge's reason is not in the jsonl.** `runner.py` writes `{**details, **asdict(row)}`;
  both have a `verdict` key, so the row's string overwrites the judge's `{verdict, reason}`
  object. The explorer already displays `verdict.reason` or `judge_reason` when present; the fix
  belongs in the runner (outside this workstream).
- **B1 cites whole meetings**, so its citation recall is 0 by construction. The drill-down shows
  these as grey "(meeting)" chips and the transcript panel says it can't check them line by line.
- **The pilot report has no `meta.fixtures` or `meta.ingest`** (the runner added those later):
  fixtures are inferred and the Ingest $ column shows "none".

## Limits and open questions

- No judge rationale for existing reports (see above); re-judging from the LLM cache would be
  $0 but needs the runner fix first.
- Reading fixtures at a SHA only works for committed fixtures. `data/fixtures/large/` is
  gitignored, so for XL reports the check can only *detect* drift, not undo it.
- Latency shows "batched" for rows served from a Message Batch (`latency_measured: false`);
  older reports lack the field and show the raw value.
- The heatmap shades by accuracy only; it does not show CIs per cell (most cells have n ≤ 2 in
  the pilot, ~30 in XL).

## Files

- `web/lib/results.ts`: report/fixture loading, fixture resolution and the reproduction check.
- `web/lib/explore.ts`: pure logic (filters, flags, citation scoring/classification, Pareto
  front); tested in `web/lib/explore.test.mts`.
- `web/app/results/…`: the overview and question pages.
- `web/components/results/…`: leaderboard, scatter, heatmap, question list, transcript panel.
