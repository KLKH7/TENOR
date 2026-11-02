import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";

import { citationScores, scoresMatch } from "./explore";
import type { Note } from "./types";

// Reads eval reports written by concentrate.evals.runner (evals/reports/<stamp>.{json,jsonl,md})
// and the fixture set they were run on (questions.jsonl + notes/*.json). Files only, no DB.
const REPO = process.env.CONCENTRATE_ROOT ?? path.join(process.cwd(), "..");
const REPORTS = process.env.CONCENTRATE_REPORTS ?? path.join(REPO, "evals", "reports");
const STAMP = /^\d{8}T\d{6}Z$/;

export type Verdict = "correct" | "partially_correct" | "incorrect";

/** One system's metrics as computed by concentrate.evals.metrics.summarize. */
export interface SystemMetrics {
  n: number;
  accuracy: number;
  accuracy_ci95: [number, number];
  exact_match: number | null;
  abstain_precision: number | null;
  abstain_recall: number | null;
  cite_precision: number | null;
  cite_recall: number | null;
  input_tokens_per_q: number;
  output_tokens_per_q: number;
  cost_per_q: number;
  cost_total: number;
  latency_p50: number | null;
  latency_p95: number | null;
  tool_calls_per_q: number;
  by_category: Record<string, { n: number; accuracy: number }>;
}

export interface IngestStats {
  llm_calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  seconds: number;
  cached: boolean;
  detail: Record<string, unknown>;
}

export interface ReportMeta {
  date: string;
  git: { sha: string; dirty: boolean };
  model: string;
  seed: number;
  split: string;
  questions: number;
  prompts: Record<string, string[]>;
  budget_usd: number;
  spent_usd: number;
  stopped_early: string | null;
  ingest?: Record<string, IngestStats>;
  fixtures?: string;
  k?: number;
  embedder?: string | null;
  batch?: Record<string, unknown>;
}

export interface Report {
  meta: ReportMeta;
  metrics: Record<string, SystemMetrics>;
  ledger_by_stage?: Record<string, { calls: number; usd: number }>;
}

/** One line of <stamp>.jsonl: the runner's details dict merged with its Row. */
export interface ResultRow {
  system: string;
  qid: string;
  question: string;
  gold: string;
  answer: { answer: string; abstain: boolean; citations: string[] };
  verdict: Verdict;
  retrieved: string[];
  batched?: boolean;
  category: string;
  correct: boolean;
  exact_match: number;
  abstained: boolean;
  unanswerable: boolean;
  cite_precision: number | null;
  cite_recall: number | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  latency_s: number;
  tool_calls: number;
  latency_measured?: boolean;
  /** The judge's one-line reason, when the report carries it (see normalizeRow). */
  judge_reason?: string;
}

/** A gold question from <fixtures>/questions.jsonl. */
export interface Question {
  id: string;
  category: string;
  question: string;
  answer: string;
  answer_norm: string[];
  evidence_keys: string[];
  gold_utterance_ids: string[];
  as_of: string;
  sources: string[];
  split: string;
}

export interface ReportSummary {
  stamp: string;
  split: string;
  questions: number;
  model: string;
  systems: string[];
}

export interface LoadedReport {
  stamp: string;
  report: Report;
  rows: ResultRow[];
  questions: Map<string, Question>;
  fixtures: ResolvedFixtures | null;
  judgechecks: { model: string; markdown: string }[];
}

/** The runner merges `{**details, **asdict(row)}`, so the row's verdict string overwrites the
 *  judge's {verdict, reason} object and the reason is lost. Accept either shape (and a
 *  separate `judge_reason`) so reports written after that is fixed show the reason. */
function normalizeRow(raw: Record<string, unknown>): ResultRow {
  const v = raw.verdict as Verdict | { verdict: Verdict; reason?: string };
  if (typeof v === "object" && v !== null) {
    return { ...(raw as unknown as ResultRow), verdict: v.verdict, judge_reason: v.reason };
  }
  return raw as unknown as ResultRow;
}

export function isStamp(s: string): boolean {
  return STAMP.test(s);
}

/** Every report on disk, newest first. */
export function listReports(): ReportSummary[] {
  if (!existsSync(REPORTS)) return [];
  return readdirSync(REPORTS)
    .filter((f) => f.endsWith(".json") && isStamp(f.slice(0, -5)))
    .map((f) => {
      const r = JSON.parse(readFileSync(path.join(REPORTS, f), "utf8")) as Report;
      return {
        stamp: f.slice(0, -5),
        split: r.meta.split,
        questions: r.meta.questions,
        model: r.meta.model,
        systems: Object.keys(r.metrics),
      };
    })
    .sort((a, b) => b.stamp.localeCompare(a.stamp));
}

/** A fixture set as of some version: the working tree, or a commit (fixtures are in git). */
export interface FixtureSet {
  /** Path relative to the repo root, e.g. data/fixtures/northwind. */
  rel: string;
  /** Commit the files are read at; null = the files on disk now. */
  sha: string | null;
  read(file: string): string | null;
}

function diskSet(rel: string): FixtureSet {
  return {
    rel,
    sha: null,
    read: (file) => {
      try {
        return readFileSync(path.join(REPO, rel, file), "utf8");
      } catch {
        return null;
      }
    },
  };
}

function gitSet(sha: string, rel: string): FixtureSet {
  return {
    rel,
    sha,
    read: (file) => {
      try {
        return execFileSync("git", ["-C", REPO, "show", `${sha}:${rel}/${file}`], {
          encoding: "utf8",
          maxBuffer: 256 * 1024 * 1024,
          stdio: ["ignore", "pipe", "ignore"],
        });
      } catch {
        return null;
      }
    },
  };
}

function parseQuestions(text: string | null): Map<string, Question> {
  const out = new Map<string, Question>();
  for (const line of (text ?? "").split("\n")) {
    if (!line.trim()) continue;
    const q = JSON.parse(line) as Question;
    out.set(q.id, q);
  }
  return out;
}

/** "…/data/fixtures/northwind" (absolute, possibly another checkout) → "data/fixtures/northwind". */
function toRel(p: string): string {
  const i = p.replace(/\\/g, "/").lastIndexOf("data/fixtures/");
  return i >= 0 ? p.slice(i).replace(/\/+$/, "") : path.relative(REPO, path.resolve(REPO, p));
}

/** Fixture dirs on disk: data/fixtures/<name> and data/fixtures/large/<name>. */
function diskCandidates(): string[] {
  const roots = ["data/fixtures", "data/fixtures/large"];
  return roots.flatMap((r) =>
    existsSync(path.join(REPO, r))
      ? readdirSync(path.join(REPO, r))
          .map((n) => `${r}/${n}`)
          .filter((d) => existsSync(path.join(REPO, d, "questions.jsonl")))
      : [],
  );
}

export interface ResolvedFixtures {
  set: FixtureSet;
  label: string;
  /** True when the report did not record its fixture dir and we matched it by question text. */
  inferred: boolean;
  /** Rows whose stored citation scores we could recompute, and those that did not reproduce. */
  checked: number;
  drift: Set<string>;
}

/** Find the fixtures a report was run on, preferring a version that reproduces its scores.
 *
 *  Candidates are the recorded dir (meta.fixtures) or, for older reports, every fixture dir;
 *  each is tried at the report's git SHA and as it is on disk now. A candidate must contain
 *  every (qid, question text) in the jsonl; among those we keep the one whose gold lines
 *  reproduce the most stored cite precision/recall values (ties → the SHA, the run's own
 *  provenance). Line ids are positional, so a regenerated transcript silently shifts them. */
function resolveFixtures(
  meta: ReportMeta,
  rows: ResultRow[],
): (ResolvedFixtures & { questions: Map<string, Question> }) | null {
  const rels = meta.fixtures ? [toRel(meta.fixtures)] : diskCandidates();
  const sha = /^[0-9a-f]{7,40}$/.test(meta.git?.sha ?? "") ? meta.git.sha : null;
  const sets = rels.flatMap((rel) => [...(sha ? [gitSet(sha, rel)] : []), diskSet(rel)]);
  let best: (ResolvedFixtures & { questions: Map<string, Question> }) | null = null;
  for (const set of sets) {
    const qs = parseQuestions(set.read("questions.jsonl"));
    if (!rows.every((r) => qs.get(r.qid)?.question === r.question)) continue;
    let checked = 0;
    const drift = new Set<string>();
    for (const r of rows) {
      const gold = qs.get(r.qid)!.gold_utterance_ids;
      if (!gold.length) continue;
      checked++;
      if (!scoresMatch(citationScores(gold, r.answer.citations), [r.cite_precision, r.cite_recall])) {
        drift.add(`${r.system}/${r.qid}`);
      }
    }
    if (!best || drift.size < best.drift.size) {
      best = {
        set,
        label: set.sha ? `${set.rel} @ ${set.sha}` : set.rel,
        inferred: !meta.fixtures,
        checked,
        drift,
        questions: qs,
      };
    }
  }
  return best;
}

const cache = new Map<string, { mtime: number; value: LoadedReport }>();

/** Load a report with its rows, gold questions and judge checks (memoised on file mtime). */
export function loadReport(stamp: string): LoadedReport | undefined {
  if (!isStamp(stamp)) return undefined;
  const base = path.join(REPORTS, stamp);
  if (!existsSync(`${base}.json`) || !existsSync(`${base}.jsonl`)) return undefined;
  const mtime = Math.max(statSync(`${base}.json`).mtimeMs, statSync(`${base}.jsonl`).mtimeMs);
  const hit = cache.get(stamp);
  if (hit && hit.mtime === mtime) return hit.value;

  const report = JSON.parse(readFileSync(`${base}.json`, "utf8")) as Report;
  const rows = readFileSync(`${base}.jsonl`, "utf8")
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => normalizeRow(JSON.parse(l)));
  const fx = resolveFixtures(report.meta, rows);
  const judgechecks = readdirSync(REPORTS)
    .filter((f) => f.startsWith(`${stamp}.judgecheck.`) && f.endsWith(".md"))
    .map((f) => ({
      model: f.slice(`${stamp}.judgecheck.`.length, -3),
      markdown: readFileSync(path.join(REPORTS, f), "utf8"),
    }));
  const value: LoadedReport = {
    stamp,
    report,
    rows,
    questions: fx?.questions ?? new Map(),
    fixtures: fx ? { set: fx.set, label: fx.label, inferred: fx.inferred, checked: fx.checked, drift: fx.drift } : null,
    judgechecks,
  };
  cache.set(stamp, { mtime, value });
  return value;
}

/** Read one fixture note from the report's resolved fixture set (Granola-shaped ids only). */
export function loadFixtureNote(fx: ResolvedFixtures | null, id: string): Note | undefined {
  if (!fx || !/^not_[A-Za-z0-9]{14}$/.test(id)) return undefined;
  const text = fx.set.read(`notes/${id}.json`);
  return text ? (JSON.parse(text) as Note) : undefined;
}

/** "not_x:0025" → { note: "not_x", line: 25 }; a bare note id has line null. */
export function parseCitation(c: string): { note: string; line: number | null } {
  const m = /^\[?(not_[A-Za-z0-9]+):(\d+)\]?$/.exec(c);
  return m ? { note: m[1], line: Number(m[2]) } : { note: c, line: null };
}

export function systemsOf(r: LoadedReport): string[] {
  return Object.keys(r.report.metrics);
}

/** Rows grouped by question id, keyed by system. */
export function rowsByQuestion(rows: ResultRow[]): Map<string, Map<string, ResultRow>> {
  const out = new Map<string, Map<string, ResultRow>>();
  for (const r of rows) {
    if (!out.has(r.qid)) out.set(r.qid, new Map());
    out.get(r.qid)!.set(r.system, r);
  }
  return out;
}
