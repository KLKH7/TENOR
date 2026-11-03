// Run with `npm test`. Mirrors tests of Fact.live_at / Commitment.status_at in Python.
import assert from "node:assert/strict";
import { test } from "node:test";

import { type CommitmentRow, commitmentStatus, type FactRow, factLines, factState, lineHref, linesByNote, ownerCandidates } from "./asof.ts";

const fact = (p: Partial<FactRow>): FactRow =>
  ({
    valid_from: "2026-09-08",
    valid_to: null,
    recorded_at: "2026-09-08T09:00:00+00:00",
    valid_to_recorded_at: null,
    ...p,
  }) as FactRow;

test("factState is bitemporal: an end only counts once the memory learnt it", () => {
  const f = fact({ valid_to: "2026-09-29", valid_to_recorded_at: "2026-09-29T10:00:00+00:00" });
  assert.equal(factState(f, "2026-09-07"), "unknown"); // not learnt yet
  assert.equal(factState(f, "2026-09-20"), "current");
  assert.equal(factState(f, "2026-09-29"), "superseded");
  // Recorded later that its end: on 09-30 we learn it ended 09-25.
  const g = fact({ valid_to: "2026-09-25", valid_to_recorded_at: "2026-09-30T10:00:00+00:00" });
  assert.equal(factState(g, "2026-09-27"), "current"); // as known on 09-27
  assert.equal(factState(g, "2026-09-30"), "superseded");
  const planned = fact({ valid_from: "2026-10-01" });
  assert.equal(factState(planned, "2026-09-20"), "scheduled");
});

test("commitmentStatus replays events up to the day", () => {
  const c = {
    owner: "sam_p",
    due: "2026-09-03",
    made_on: "2026-08-31",
    events: [
      { kind: "done", day: "2026-09-14", new_due: null, new_owner: null },
      { kind: "slipped", day: "2026-09-07", new_due: "2026-09-10", new_owner: null },
    ],
  } as unknown as CommitmentRow;
  assert.equal(commitmentStatus(c, "2026-08-30").status, "unknown");
  assert.equal(commitmentStatus(c, "2026-08-31").status, "open");
  assert.equal(commitmentStatus(c, "2026-09-01").status, "no_follow_up");
  const s = commitmentStatus(c, "2026-09-08");
  assert.deepEqual([s.status, s.due, s.seen.length], ["slipped", "2026-09-10", 1]);
  assert.equal(commitmentStatus(c, "2026-10-01").status, "done");
});

test("factLines adds restatements only once heard", () => {
  const f = fact({
    source_lines: ["not_a:1"],
    evidence: [{ lines: ["not_b:5", "not_a:1"], recorded_at: "2026-09-15T09:00:00+00:00" }],
  });
  assert.deepEqual(factLines(f, "2026-09-10"), ["not_a:1"]);
  assert.deepEqual(factLines(f, "2026-09-15"), ["not_a:1", "not_b:5"]);
});

test("line helpers", () => {
  assert.deepEqual(ownerCandidates("?a|b"), ["a", "b"]);
  assert.equal(ownerCandidates("a"), null);
  assert.deepEqual(linesByNote(["not_a:1", "not_b:2", "not_a:3", "not_a:1"]), [
    ["not_a", ["not_a:1", "not_a:3"]],
    ["not_b", ["not_b:2"]],
  ]);
  assert.equal(lineHref("not_a", ["not_a:1", "not_a:3"]), "/notes/not_a?set=xl&line=not_a%3A1%2Cnot_a%3A3");
});
