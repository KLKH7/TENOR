# Exercises: learn it by touching it

Eight drills, each under 10 minutes, all **free** (no API calls). Do them in order; each builds on
[TOUR.md](TOUR.md) and [CONCEPTS.md](CONCEPTS.md).

**The rule:** before running anything, **write your prediction down**. Then run it and compare. The
gap between prediction and output is where the learning happens.

Setup once, from the repo root:

```bash
uv sync
mkdir -p /tmp/cx                         # scratch space for these exercises
```

Every expected output below was produced on `HEAD 50fefb4`. If yours differs, find out why: that's
the exercise too. Any time you edit code, undo it after with `git checkout <file>`.

---

## 1. Determinism: regenerate the benchmark and diff it (5 min)

**Learn:** "ground truth first", seeds, and why a test can demand byte-identical fixtures.

**Predict:** how many meetings, questions, and dev/test questions will the base set have? Will the
diff against the committed fixtures be empty?

```bash
uv run concentrate synth --seed 42 --out /tmp/cx/nw
diff -r /tmp/cx/nw data/fixtures/northwind && echo IDENTICAL
```

**Expected:**
```
wrote 40 notes, 4,327 utterances, 57,553 words, 230 questions → /tmp/cx/nw
questions: aggregation=10, commitment_status=22, decision_change=10, entity_disambiguation=25, latest_state=17, multi_hop=12, single_hop=63, temporal=46, unanswerable=25
split: dev=71, test=159
validation: all gold answers are stated in their gold utterances ✓
IDENTICAL
```

**Then:** rerun with `--seed 43` into `/tmp/cx/nw43`, and diff `questions.jsonl` heads.
- Which things changed? The seeded parts: commitment outcomes, filler, the split.
- Which didn't? The hand-authored plot: the $20 → $25 price change and the launch slips.

Read `tests/test_synth.py:37-45` to see the two tests that enforce this.

---

## 2. Break the rate limiter on purpose (5 min)

**Learn:** token bucket vs sliding window ([CONCEPTS §1](CONCEPTS.md#token-bucket)), and that tests
encode the API contract.

**Predict:** in `packages/concentrate/sources/ratelimit.py`, line 76, change `burst: int = 25,` to
`burst: int = 40,`. Which of the four tests in `tests/test_ratelimit.py` fail, and with what numbers?

```bash
uv run pytest -q tests/test_ratelimit.py
git checkout packages/concentrate/sources/ratelimit.py
```

**Expected:** 2 failures.
```
E   assert 26 <= 25                                   (test_never_more_than_25_in_any_5s_window)
E   assert 36.99999999999991 >= (40.0 - 1e-09)        (test_sustained_rate_is_5_per_second)
```

**Why 37?** 225 requests, the first 40 free, then 185 at 5/s → 37 s. Granola's contract needs ≥ 40 s.
`test_burst_of_25_is_immediate` still passes. Why? Because 25 ≤ 40.

---

## 3. Names: Jaro–Winkler, Double Metaphone, and a docstring that's wrong (7 min)

**Learn:** how "Coffee" gets linked to Kofi ([CONCEPTS §5](CONCEPTS.md#5-who-is-who-enhance)).

**Predict** each score (0–1), and whether each pair "sounds alike": meghan/megan, coffee/kofi, thomas/tomas, dana/dan.

```bash
uv run python -c "
from concentrate.enhance.strings import jaro_winkler, double_metaphone, sounds_alike, name_similarity
for a, b in [('meghan','megan'), ('coffee','kofi'), ('thomas','tomas'), ('dana','dan')]:
    print(f'{a:7}{b:6} jw={jaro_winkler(a,b):.3f} codes={double_metaphone(a)}/{double_metaphone(b)} alike={sounds_alike(a,b)} sim={name_similarity(a,b):.3f}')
"
```

**Expected:**
```
meghan megan  jw=0.961 codes=('MKN', 'MKN')/('MKN', 'MKN') alike=True sim=0.983
coffee kofi   jw=0.611 codes=('KF', 'KF')/('KF', 'KF') alike=True sim=0.825
thomas tomas  jw=0.950 codes=('0MS', 'TMS')/('TMS', 'TMS') alike=True sim=0.978
dana   dan    jw=0.942 codes=('TN', 'TN')/('TN', 'TN') alike=True sim=0.974
```

**Then:** read the `name_similarity` docstring (`packages/concentrate/enhance/strings.py:188`). It
claims look-alikes that sound different, like "Dana" vs "Dan", "stay lower". The output says
otherwise: they sound alike and score 0.974. That's a real (small) inaccuracy you found yourself,
and a good interview story about checking claims against code. Also notice "thomas" has two codes
(`0MS` = "th" as in *think*, `TMS`), and the alternate code is what matches "tomas".

---

## 4. Read one bitemporal query (8 min)

**Learn:** valid time vs transaction time, and supersession ([CONCEPTS §6](CONCEPTS.md#bitemporal-data)).

Save this as `/tmp/cx/bt.py`:

```python
from concentrate.memory.store import MemoryStore

s = MemoryStore(":memory:")
common = dict(subject="teams_plan", predicate="price_per_seat", kind="metric_value",
              quote="", reason=None, confidence=0.8, privacy="none")
print(s.add_fact(value="$20", value_norm="20", valid_from="2026-09-01",
                 recorded_at="2026-09-01T10:00", note_id="m1", source_lines=["m1:0004"], **common))
print(s.add_fact(value="$25", value_norm="25", valid_from="2026-09-29",
                 recorded_at="2026-09-29T10:00", note_id="m2", source_lines=["m2:0010"], **common))
print(s.add_fact(value="25 dollars", value_norm="25", valid_from="2026-10-06",
                 recorded_at="2026-10-06T10:00", note_id="m3", source_lines=["m3:0002"], **common))
for f in s.versions("teams_plan", "price_per_seat"):
    print(f.id, f.value, f.valid_from, "->", f.valid_to, "conf", round(f.confidence, 2), "lines", f.all_lines())
for day in ["2026-09-15", "2026-10-01"]:
    print(day, [f.value for f in s.as_of("teams_plan", "price_per_seat", day)])
print("as of 10-01, known on 09-20:",
      [f.value for f in s.as_of("teams_plan", "price_per_seat", "2026-10-01", known_at="2026-09-20")])
```

**Predict:**
- the action returned by each of the three `add_fact` calls;
- how many rows exist at the end;
- the final confidence of the $25 row;
- the three as-of answers.

```bash
uv run python /tmp/cx/bt.py
```

**Expected:**
```
('inserted', 1)
('superseded', 2)
('restated', 2)
1 $20 2026-09-01 -> 2026-09-29 conf 0.8 lines ['m1:0004']
2 $25 2026-09-29 -> None conf 0.96 lines ['m2:0010', 'm3:0002']
2026-09-15 ['$20']
2026-10-01 ['$25']
as of 10-01, known on 09-20: ['$20']
```

**Explain out loud:**
- Why "25 dollars" didn't make a new row: `norm_value` reduces `value_norm` "25" to "25".
- Why confidence is 0.96: 1 − (1−0.8)(1−0.8).
- Why the last line says $20: on Sept 20 we hadn't recorded the change yet.

Then find each rule in `Fact.live_at` and `MemoryStore.add_fact` (`packages/concentrate/memory/store.py:92`, `:246`).

**Stretch:** add a fourth fact `$30` with `valid_from="2026-10-06"`. Predict the action. (It's
`contradiction`: a different value on the same day as an existing live one.)

---

## 5. Be the grounding gate (6 min)

**Learn:** how a fact gets rejected without any LLM ([CONCEPTS §6](CONCEPTS.md#grounding--entailment)).

**Predict** `(ok, detail, relative)` for each call:

```bash
uv run python -c "
from concentrate.memory.verify import check_value, quote_supported
from concentrate.core.spoken import spoken_numbers, written
print(spoken_numbers('twenty five dollars per seat'), written('it is twenty five dollars'))
print(check_value('25', 'we moved to twenty five dollars a seat'))
print(check_value('30', 'we moved to twenty five dollars a seat'))
print(check_value('2026-10-28', 'slipping to October twenty eighth'))
print(check_value('2026-10-30', 'I can get it done by Friday'))
print(quote_supported('twenty five dollars ... SSO included', 'Twenty-five dollars a seat. And SSO included now.'))
"
```

**Expected:**
```
[25] it is $25
(True, '', False)
(False, 'number 30 not said in cited lines', False)
(True, '', False)
(True, 'relative date', True)
True
```

The fourth line is the interesting one. "By Friday" can't be checked by string rules, so it passes
**but is flagged** `relative=True`, and `check_item` routes it to the LLM entailment check.
Find where that happens: the `needs = relative or item.confidence < low_confidence` line in
`check_item` (`packages/concentrate/memory/verify.py:142`).

---

## 6. Re-analyse the pilot and read the statistics (8 min)

**Learn:** McNemar, Holm, power, Pareto ([CONCEPTS §4](CONCEPTS.md#4-measuring-the-eval-harness)).

`analyze` writes its output next to the report, so work on a copy:

```bash
cp evals/reports/20261102T100403Z.* /tmp/cx/
uv run python -c "
from concentrate.evals.stats import holm, mcnemar_exact, mcnemar_sample_size, mcnemar_mde
print(holm([0.01, 0.04, 0.03]))
print(mcnemar_exact([True]*3 + [True]*7, [False]*3 + [True]*7))
print(mcnemar_sample_size(0.10, 0.30), round(mcnemar_mde(206, 0.30), 3))
"
uv run concentrate analyze /tmp/cx/20261102T100403Z.json
```

**Predict** first:
- the Holm-adjusted values;
- the McNemar p when A is right on 3 questions B got wrong and they agree on the other 7;
- how many questions a 10-point gap needs at 30% disagreement.

**Expected:**
```
[0.03, 0.06, 0.06]
0.25
234 0.106
written: /tmp/cx/20261102T100403Z.analysis.md, …
```

Open `/tmp/cx/20261102T100403Z.analysis.md` and find:
1. the sentence saying **none of the 6 pairwise gaps is reliable**;
2. "a typical pair needs a gap of about 62 points" at n = 10;
3. the row `b2_full | b1_summaries | … | 3 / 0 | 0.250 | 1.000`.

Explain why p = 0.25 becomes 1.000 after Holm. (Six pairs; it isn't the smallest p.)

Your copy will lack the "LLM refinement" table that the committed analysis has, because that needed
`--llm` (Haiku, paid). That's the only difference.

---

## 7. Price an eval before running it (5 min)

**Learn:** prompt caching and the Batch API in money ([CONCEPTS §3](CONCEPTS.md#3-calling-llms-cheaply)).

**Predict:** the four query-time systems on the base dev split (71 questions). Which has the most
input tokens per question? Which is cheapest in total? Is the most context also the most expensive?

```bash
uv run concentrate eval --split dev --estimate
```

(The first run downloads the free local embedding model. Nothing is sent to Anthropic.)

**Expected (roughly):**
```
data/fixtures/northwind: 40 meetings · 71 questions (dev) · 4 systems · claude-opus-5-5
  b0_topk       ~  76,841 in-tok/q … ≈ $ 12.89
  b1_summaries  ~   9,123 in-tok/q … ≈ $  1.92
  b2_full       ~ 151,636 in-tok/q … ≈ $  2.40
  b3_agentic    ~  32,000 in-tok/q (est. ) ≈ $ 10.70
  estimated total ≈ $27.92 (before cache reuse) · cap $25.00
```

The exact/est. labels and the last digits can differ on your machine. Explain:
- b2 sends the **most** tokens but is the second-**cheapest**, because the same 150k-token prefix is
  a cache read (~$0.03) after the first call;
- b0 can't cache, because its top-20 meetings differ per question;
- b3 is "est." because an agent's number of turns isn't known in advance.

Note the total is over the $25 default cap and over the $2 "ask first" rule. Don't run it.

---

## 8. Trace one question through the explorer (10 min)

**Learn:** the whole pipeline end to end, and why positional line IDs need pinning.

```bash
cd web && npm install && npm run dev      # port 3100
```

Open `http://localhost:3100/results/20261102T100403Z/q/q194`.

**The question (multi-hop, dev):** "Who owned the project that the Northwind Teams launch depended
on at the time the launch was first delayed?" **Gold:** Tomás Herrera. It needs three hops: Teams
depends on Falcon; the first delay was on Sep 22; Falcon was handed from Sam Patel to Tomás on Sep 21.

**Predict** each system's verdict before looking. Then check:

| System | Verdict | Cite P / R |
|---|---|---|
| b0_topk | partially_correct ("No Falcon owner is explicitly…") | 0.75 / 0.60 |
| b1_summaries | incorrect (abstained) | – / 0.00 |
| b2_full | correct | 1.00 / 1.00 |
| b3_agentic | correct | 0.80 / 0.80 |

Now:
1. Click one of b0's citations. The transcript opens at that line, and the gold line it *missed* is
   marked red-dashed. That is the `retrieval_miss` the analysis assigned to `b0_topk/q194`.
2. Find the "reproduces" chip at the top. It reads fixtures at the report's SHA (`e04f133`), not
   today's. Explain why: fixtures were regenerated after the run, and line `…:0058` is now a
   different sentence.
3. Trace it back through the code using [TOUR.md](TOUR.md):
   - `web/lib/results.ts` `loadReport`;
   - the report `.jsonl`;
   - `evals/scoring.py` `citation_scores`;
   - `agents/baselines.py` `TopKSystem`;
   - `agents/retrieval.py` `rrf`.

No Node? Use `grep '"q194"' evals/reports/20261102T100403Z.jsonl` and
`git show e04f133:data/fixtures/northwind/questions.jsonl | grep '"q194"'` instead.

---

## Bonus: measure the enhancer's honest weak spot (3 min)

```bash
uv run python -m concentrate.enhance.evaluate data/fixtures/northwind
```

**Predict** accuracy on remote vs in-person lines. **Expected:** remote 94.9%, in-person **21.0%**.
The last line reads "Baseline, in-person: always the note owner: accuracy 64.2%". So the clever
method loses to the dumb baseline in person. Report it as a limitation of the method.

## When you've done all eight

You should be able to answer, without notes:
1. Why are there two rate limiters?
2. What makes a fact "superseded" rather than "restated"?
3. What does McNemar ignore, and why is that right?
4. Why is b2 cheap despite reading everything?
5. Why can't you trust citation colours from an old report without its git SHA?
