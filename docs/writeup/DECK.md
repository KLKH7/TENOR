# Deck outline (10 slides, ~10 minutes)

Same claims as [MEMO.md](MEMO.md); sources in [FINDINGS_CHECKLIST.md](FINDINGS_CHECKLIST.md). Generate every chart from a report file. Footer on every slide: *n shown · real slice = 4 meetings, one person · synthetic = Northwind-XL v1*.

## 1. I tested my memory against Granola's agent. It lost on real meetings.
- Real meetings, open-ended questions: Granola's agent 92%, my Concentrate 59% (n = 39, CIs don't overlap).
- On my synthetic benchmark they tie (92% vs 90%), and the gap between those two results is the finding.
- Takeaway: compressing meetings into facts loses the long tail. Keep transcripts; precompute state and change.
- **Chart:** two bars per setting (real vs synthetic), Granola/agent vs Concentrate, with CI whiskers.

## 2. The question Granola is already asking
- Pedregal ([Platformer](https://www.platformer.news/granola-chris-pedregal-interview/), Jul 2026): agents do "a relatively dumb job at extracting information"; send "apple juice from concentrate" instead.
- He also said Granola is looking into "whether we should delete the transcript automatically", but you "might want to still go back and chat with that meeting".
- This project is an independent measurement of those ideas, not a claim to have invented them.
- **Chart:** none. Two columns: "look it up when asked" vs "study ahead of time".

## 3. What I built
- Northwind: 184 synthetic meetings in Granola's API shape, 296 questions with verified, line-cited answers.
- 7 systems, one harness, same answer model and judge; paired McNemar tests with Holm correction, plus a second judge.
- A real slice: Granola's live agent (read-only) vs Concentrate on my own 4 meetings.
- A red-team audit of my own headline, which corrected the cost claim and the Mem0/Graphiti claims.
- **Chart:** pipeline diagram (sources → store → query-time / write-time systems → harness), with the real slice as a side path.

## 4. Result 1: real meetings
- Granola 92% [82, 100]; top-k reading the one right meeting 77%; Concentrate 59% [44, 74]. Paired: 14 questions only Granola got right, 1 only Concentrate.
- Who said X: 88% vs 0%. Which person: 80% vs 40%. What changed: Concentrate 100% vs 83%.
- 10 of Concentrate's 16 misses were "not in the material": the detail was in the transcript, just not in the facts.
- Second judge agrees on 97% (κ 0.93).
- **Chart:** per-category dot plot (Granola vs Concentrate), with n under each category.

## 5. Result 2: synthetic, a tie the setup makes easy
- Concentrate 92%, agent 90%, top-k 88%: tied (Holm p = 1.0); oracle 95%.
- ~3.5× cheaper per question than the agent at equal prices (~2× against a caching agent). Pays off its $5 ingest after ~50 questions per set of meetings.
- Beats summaries (+21) solidly. The Mem0/Graphiti gaps reflect my wiring, not those libraries.
- The directory decided 1/120 questions; gold errors exist.
- **Chart:** accuracy vs $/question (equal prices, log x), with CI whiskers.

## 6. Why the two results disagree
- I wrote both the question generator and the extractor, so synthetic questions ask about the facts the extractor keeps.
- Real questions ask about anything: who proposed X, a passing date, an exact phrase.
- Home advantage that a dev/test split can't remove; only independent questions expose it.
- This is what the real slice was built to catch, and it caught it.
- **Chart:** a Venn diagram of "what questions ask about" vs "what the extractor keeps", with the real misses landing outside.

## 7. So: don't delete transcripts, go hybrid
- Synthetic dev: deleting old transcripts barely moved Concentrate (89% → 88%), but no question there needed exact wording.
- Real: those are the questions people ask, and fact-only memory scores 0% on "who said".
- Precomputation wins on state and change: "what changed" 100% vs 83% (real); 58% vs 5% recall in the brief.
- Proposal: facts as an index for state, change and commitments; transcripts kept and searchable for everything else.
- **Chart:** `docs/results/retirement_curve.svg`, annotated "no exact-wording questions here", next to the real "who said" bar.

## 8. What Granola does well, and where memory still helps
- Live probes (4 meetings): strong, stable commitment tracking; honest abstention on single-mic speech; ~0.3 s retrieval tools.
- Gaps: an attendee known only by display name, and a shared first name (attendee metadata not joined to the text).
- Pre-meeting brief from memory: open commitments 91% vs 30% recall (precision 43% vs 90%); ~6 ms, no LLM call.
- **Chart:** brief precision/recall bars, memory vs baseline.

## 9. Limits
- Real slice: 39 questions, 4 meetings, one person; gold LLM-drafted and bulk-approved; unanswerable questions not run.
- Synthetic: home advantage, unequal tuning, saturation near the oracle, a re-implemented agent, n = 120 (detects only gaps of ~7 points or more).
- Every number on these slides maps to a file in the repo.
- **Chart:** none.

## 10. What I'd run next, and do at Granola
- Next: a hybrid Concentrate (transcript fallback plus Granola's speaker labels) on fresh real questions; v2.1 with exact-quote and who-said questions; the directory ablation; fixing the Mem0/Graphiti wiring and the gold errors.
- At Granola: treat compression as an index over transcripts, not a replacement; test retention policies against open-ended questions.
- Precompute what search does badly (state changes, commitments, briefs), and resolve identity at write time.
- **Chart:** none. Repo link and a "run it yourself" command.
