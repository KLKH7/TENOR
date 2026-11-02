// Run with `npm test` (node --test; Node strips the TypeScript types).
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyFilters,
  citationScores,
  classifyLines,
  failureTypes,
  filterQuery,
  paretoFront,
  parseFilters,
  type QuestionItem,
  questionFlags,
  sortCategories,
} from "./explore.ts";
import type { ResultRow, Verdict } from "./results.ts";

const row = (system: string, verdict: Verdict) => ({ system, verdict }) as ResultRow;
const item = (qid: string, category: string, v: Record<string, Verdict>): QuestionItem => ({
  qid,
  category,
  question: qid,
  bySystem: new Map(Object.entries(v).map(([s, x]) => [s, row(s, x)])),
});

test("citationScores mirrors scoring.citation_scores", () => {
  const gold = ["not_a:0001", "not_a:0002"];
  assert.deepEqual(citationScores(gold, ["[not_a:0001]", "not_a:0009", "not_a"]), [0.5, 0.5]);
  assert.deepEqual(citationScores(gold, ["not_a"]), [null, 0]); // meeting-level only
  assert.deepEqual(citationScores([], ["not_a:0001"]), [null, null]); // unanswerable
});

test("classifyLines splits cited/gold into correct, extra, missed", () => {
  const m = classifyLines(["n:1", "n:2"], ["n:2", "n:3", "n"]);
  assert.deepEqual(Object.fromEntries(m), { "n:2": "correct", "n:3": "extra", "n:1": "missed" });
});

test("questionFlags: disagree and everyone wrong over the selected systems", () => {
  const q = item("q1", "temporal", { a: "correct", b: "incorrect", c: "partially_correct" });
  assert.deepEqual(questionFlags(q.bySystem, ["a", "b"]), { disagree: true, everyoneWrong: false });
  assert.deepEqual(questionFlags(q.bySystem, ["b", "c"]), { disagree: true, everyoneWrong: true });
  assert.deepEqual(questionFlags(q.bySystem, ["a"]), { disagree: false, everyoneWrong: false });
});

test("filters round-trip through the query string and apply", () => {
  const all = ["a", "b"];
  const f = parseFilters({ cat: "temporal", sys: "b,zzz", wrong: "1" }, all);
  assert.deepEqual(f, { category: "temporal", systems: ["b"], disagree: false, wrong: true, failure: null });
  assert.equal(filterQuery(f), "?cat=temporal&sys=b&wrong=1");
  const items = [
    item("q1", "temporal", { a: "correct", b: "incorrect" }),
    item("q2", "temporal", { a: "correct", b: "correct" }),
    item("q3", "aggregation", { a: "incorrect", b: "incorrect" }),
  ];
  assert.deepEqual(applyFilters(items, f, all).map((i) => i.qid), ["q1"]);
  const g = parseFilters({ disagree: "1" }, all);
  assert.deepEqual(applyFilters(items, g, all).map((i) => i.qid), ["q1"]);
});

test("failure filter keeps questions where a selected system failed that way", () => {
  const all = ["a", "b"];
  const items = [
    item("q1", "temporal", { a: "correct", b: "incorrect" }),
    item("q2", "temporal", { a: "incorrect", b: "incorrect" }),
  ];
  items[0].bySystem.get("b")!.failure = { primary: "stale_value" } as ResultRow["failure"];
  items[1].bySystem.get("a")!.failure = { primary: "retrieval_miss" } as ResultRow["failure"];
  items[1].bySystem.get("b")!.failure = { primary: "stale_value" } as ResultRow["failure"];
  const f = parseFilters({ fail: "stale_value" }, all);
  assert.equal(filterQuery(f), "?fail=stale_value");
  assert.deepEqual(applyFilters(items, f, all).map((i) => i.qid), ["q1", "q2"]);
  const onlyA = parseFilters({ fail: "stale_value", sys: "a" }, all);
  assert.deepEqual(applyFilters(items, onlyA, all).map((i) => i.qid), []);
  const rows = items.flatMap((i) => [...i.bySystem.values()]);
  assert.deepEqual(failureTypes(rows), ["stale_value", "retrieval_miss"]);
});

test("paretoFront keeps only undominated systems, cheapest first", () => {
  const pts = [
    { name: "cheap", cost: 0.03, accuracy: 0.7 },
    { name: "best", cost: 0.09, accuracy: 1.0 },
    { name: "dominated", cost: 0.27, accuracy: 0.9 },
  ];
  assert.deepEqual(paretoFront(pts).map((p) => p.name), ["cheap", "best"]);
});

test("sortCategories follows the generator's order, unknown last", () => {
  assert.deepEqual(sortCategories(["zeta", "unanswerable", "single_hop", "temporal"]), [
    "single_hop",
    "temporal",
    "unanswerable",
    "zeta",
  ]);
});
