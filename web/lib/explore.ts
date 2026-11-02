// Pure helpers for the results explorer: filters, question flags, citation classification.
// No I/O here, so the rules are easy to read and check.

import type { ResultRow, Verdict } from "./results";

export type LineMark = "correct" | "extra" | "missed";

/** Same normalisation as concentrate.evals.scoring.citation_scores: strip [ ] brackets. */
export function cleanCitation(c: string): string {
  return c.replace(/^\[+|\]+$/g, "");
}

/** Mirror of concentrate.evals.scoring.citation_scores: (precision, recall) of cited line ids
 *  vs gold. Precision is null with no line citations; both are null with no gold lines. */
export function citationScores(gold: string[], cited: string[]): [number | null, number | null] {
  const g = new Set(gold);
  if (!g.size) return [null, null];
  const lines = new Set(cited.filter((c) => c.includes(":")).map(cleanCitation));
  const hit = [...lines].filter((l) => g.has(l)).length;
  return [lines.size ? hit / lines.size : null, hit / g.size];
}

export function scoresMatch(a: [number | null, number | null], b: [number | null, number | null]): boolean {
  const eq = (x: number | null, y: number | null) =>
    x === null || y === null ? x === y : Math.abs(x - y) < 1e-9;
  return eq(a[0], b[0]) && eq(a[1], b[1]);
}

/** Classify transcript lines against the gold evidence, the way cite precision/recall do:
 *  cited ∩ gold = correct, cited − gold = extra, gold − cited = missed. Bare note ids
 *  (note-level citations, e.g. B1 citing a summary) carry no line and are ignored here. */
export function classifyLines(gold: string[], cited: string[]): Map<string, LineMark> {
  const g = new Set(gold);
  const c = new Set(cited.filter((x) => x.includes(":")).map(cleanCitation));
  const out = new Map<string, LineMark>();
  for (const id of c) out.set(id, g.has(id) ? "correct" : "extra");
  for (const id of g) if (!c.has(id)) out.set(id, "missed");
  return out;
}

export interface QuestionFlags {
  /** Selected systems did not all get the same verdict. */
  disagree: boolean;
  /** No selected system was judged fully correct. */
  everyoneWrong: boolean;
}

export function questionFlags(bySystem: Map<string, ResultRow>, systems: string[]): QuestionFlags {
  const verdicts = systems.map((s) => bySystem.get(s)?.verdict).filter(Boolean) as Verdict[];
  return {
    disagree: new Set(verdicts).size > 1,
    everyoneWrong: verdicts.length > 0 && verdicts.every((v) => v !== "correct"),
  };
}

export interface Filters {
  category: string | null;
  systems: string[]; // empty = all systems
  disagree: boolean;
  wrong: boolean;
  /** Primary failure type (`concentrate analyze`): some selected system failed this way. */
  failure: string | null;
}

type Params = Record<string, string | string[] | undefined>;

const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? null;

export function parseFilters(p: Params, allSystems: string[]): Filters {
  const sys = (one(p.sys) ?? "").split(",").filter((s) => allSystems.includes(s));
  return {
    category: one(p.cat),
    systems: sys,
    disagree: one(p.disagree) === "1",
    wrong: one(p.wrong) === "1",
    failure: one(p.fail),
  };
}

export interface QuestionItem {
  qid: string;
  category: string;
  question: string;
  bySystem: Map<string, ResultRow>;
}

/** Apply the filters; the system filter narrows both the columns and the flag logic. */
export function applyFilters(items: QuestionItem[], f: Filters, allSystems: string[]): QuestionItem[] {
  const systems = f.systems.length ? f.systems : allSystems;
  return items.filter((q) => {
    if (f.category && q.category !== f.category) return false;
    const flags = questionFlags(q.bySystem, systems);
    if (f.disagree && !flags.disagree) return false;
    if (f.wrong && !flags.everyoneWrong) return false;
    if (f.failure && !systems.some((s) => q.bySystem.get(s)?.failure?.primary === f.failure)) return false;
    return true;
  });
}

/** Query string for a filter state (stable key order so links are canonical). */
export function filterQuery(f: Filters): string {
  const q = new URLSearchParams();
  if (f.category) q.set("cat", f.category);
  if (f.systems.length) q.set("sys", f.systems.join(","));
  if (f.disagree) q.set("disagree", "1");
  if (f.wrong) q.set("wrong", "1");
  if (f.failure) q.set("fail", f.failure);
  const s = q.toString();
  return s ? `?${s}` : "";
}

/** Failure types present in the rows, in docs/ANALYSIS.md's precedence order. */
export const FAILURE_ORDER = [
  "abstained_wrongly",
  "should_have_abstained",
  "stale_value",
  "wrong_entity",
  "partial_list",
  "retrieval_miss",
  "reasoning_or_reading",
  "unclassified",
];

export function failureTypes(rows: Iterable<{ failure?: { primary: string } }>): string[] {
  const seen = new Set<string>();
  for (const r of rows) if (r.failure) seen.add(r.failure.primary);
  const rank = (t: string) => (FAILURE_ORDER.includes(t) ? FAILURE_ORDER.indexOf(t) : 99);
  return [...seen].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

export function failureLabel(t: string): string {
  const s = t.replace(/_/g, " ");
  return s[0].toUpperCase() + s.slice(1);
}

/** Display helper: 0.9 → "90%"; null → "–". */
export function pct(x: number | null | undefined, digits = 0): string {
  return x === null || x === undefined ? "–" : `${(x * 100).toFixed(digits)}%`;
}

export function usd(x: number | null | undefined): string {
  if (x === null || x === undefined) return "–";
  if (x === 0) return "$0";
  return x < 0.01 ? `$${x.toFixed(4)}` : x < 1 ? `$${x.toFixed(3)}` : `$${x.toFixed(2)}`;
}

export function secs(x: number | null | undefined): string {
  return x === null || x === undefined ? "–" : `${x.toFixed(1)}s`;
}

export function tokens(x: number | null | undefined): string {
  if (x === null || x === undefined) return "–";
  return x >= 1000 ? `${(x / 1000).toFixed(x >= 10000 ? 0 : 1)}k` : `${Math.round(x)}`;
}

export function stampLabel(stamp: string): string {
  // 20261102T100403Z → 2026-11-02 10:04 UTC
  return `${stamp.slice(0, 4)}-${stamp.slice(4, 6)}-${stamp.slice(6, 8)} ${stamp.slice(9, 11)}:${stamp.slice(11, 13)} UTC`;
}

/** Systems on the cost/accuracy Pareto front: no other system is at least as cheap and at
 *  least as accurate while being strictly better on one. Returned cheapest first. */
export function paretoFront<T extends { cost: number; accuracy: number }>(pts: T[]): T[] {
  return pts
    .filter(
      (p) =>
        !pts.some(
          (o) =>
            o !== p &&
            o.cost <= p.cost &&
            o.accuracy >= p.accuracy &&
            (o.cost < p.cost || o.accuracy > p.accuracy),
        ),
    )
    .sort((a, b) => a.cost - b.cost);
}

/** Order categories as concentrate.synth.questions.Category lists them, unknown ones last. */
const CATEGORY_ORDER = [
  "single_hop",
  "multi_hop",
  "temporal",
  "latest_state",
  "entity_disambiguation",
  "commitment_status",
  "decision_change",
  "aggregation",
  "unanswerable",
];

export function sortCategories(cats: Iterable<string>): string[] {
  const rank = (c: string) => {
    const i = CATEGORY_ORDER.indexOf(c);
    return i === -1 ? CATEGORY_ORDER.length : i;
  };
  return [...new Set(cats)].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}
