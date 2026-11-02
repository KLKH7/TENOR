import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";

import type { CommitmentEvent, CommitmentRow, FactRow } from "./asof";
import { getDirectory, XL_ROOT } from "./data";

// Read-only view of a built Concentrate memory (memory.sqlite). Store choice follows
// `concentrate brief` / `mcp-serve`: the newest build whose complete.json exists, under
// data/cache/concentrate/<corpus hash>/<config>/. CONCENTRATE_STORE (a build dir) overrides.
const REPO = process.env.CONCENTRATE_ROOT ?? path.join(process.cwd(), "..");
const CACHE = path.join(REPO, "data", "cache", "concentrate");

export interface Rejection {
  note_id: string;
  item: string;
  reason: string;
  detail: string;
  payload: Record<string, unknown> & { source_lines?: string[] };
}

export interface MeetingRow {
  note_id: string;
  title: string;
  day: string;
  recorded_at: string;
}

export interface Memory {
  store: string;
  complete: { cost_usd: number; detail: { config?: Record<string, unknown>; kept?: Record<string, number>; rejected?: Record<string, number> } };
  facts: FactRow[];
  commitments: CommitmentRow[];
  rejections: Rejection[];
  meetings: Map<string, MeetingRow>;
  /** Entity id → display name (directory), falling back to the id. */
  names: Map<string, string>;
  /** Entity id → kind (person / org / project / other). */
  kinds: Map<string, string>;
  /** Store meetings not found in the XL fixtures (should be 0: the store matches the corpus). */
  missingNotes: number;
}

/** Newest complete build directory, or null. */
export function findStore(): string | null {
  if (process.env.CONCENTRATE_STORE) return process.env.CONCENTRATE_STORE;
  if (!existsSync(CACHE)) return null;
  const done: { dir: string; mtime: number }[] = [];
  for (const corpus of readdirSync(CACHE)) {
    const cdir = path.join(CACHE, corpus);
    if (!statSync(cdir).isDirectory()) continue;
    for (const cfg of readdirSync(cdir)) {
      const dir = path.join(cdir, cfg);
      const marker = path.join(dir, "complete.json");
      if (existsSync(marker) && existsSync(path.join(dir, "memory.sqlite"))) {
        done.push({ dir, mtime: statSync(marker).mtimeMs });
      }
    }
  }
  done.sort((a, b) => b.mtime - a.mtime);
  return done[0]?.dir ?? null;
}

const lines = (s: unknown): string[] => (typeof s === "string" && s ? (JSON.parse(s) as string[]) : []);

let cached: { dir: string; mtime: number; value: Memory } | null = null;

/** Load the whole store (a few hundred rows) read-only; memoised on the db's mtime. */
export function loadMemory(): Memory | null {
  const dir = findStore();
  if (!dir) return null;
  const file = path.join(dir, "memory.sqlite");
  const mtime = statSync(file).mtimeMs;
  if (cached && cached.dir === dir && cached.mtime === mtime) return cached.value;

  const db = new DatabaseSync(file, { readOnly: true });
  try {
    const evidence = new Map<number, FactRow["evidence"]>();
    for (const r of db.prepare("SELECT fact_id, source_lines, recorded_at FROM evidence ORDER BY recorded_at").all()) {
      const id = Number(r.fact_id);
      evidence.set(id, [...(evidence.get(id) ?? []), { lines: lines(r.source_lines), recorded_at: String(r.recorded_at) }]);
    }
    const facts = db
      .prepare("SELECT * FROM facts ORDER BY subject, predicate, valid_from, id")
      .all()
      .map((r) => ({
        ...(r as unknown as FactRow),
        source_lines: lines(r.source_lines),
        evidence: evidence.get(Number(r.id)) ?? [],
      }));
    const events = new Map<number, CommitmentEvent[]>();
    for (const r of db.prepare("SELECT * FROM commitment_events ORDER BY day, recorded_at").all()) {
      const id = Number(r.commitment_id);
      events.set(id, [
        ...(events.get(id) ?? []),
        { ...(r as unknown as CommitmentEvent), source_lines: lines(r.source_lines) },
      ]);
    }
    const commitments = db
      .prepare("SELECT * FROM commitments ORDER BY made_on, id")
      .all()
      .map((r) => ({
        ...(r as unknown as CommitmentRow),
        source_lines: lines(r.source_lines),
        events: events.get(Number(r.id)) ?? [],
      }));
    const rejections = db
      .prepare("SELECT * FROM rejections")
      .all()
      .map((r) => ({ ...(r as unknown as Rejection), payload: JSON.parse(String(r.payload)) }));
    const meetings = new Map(
      db
        .prepare("SELECT note_id, title, day, recorded_at FROM meetings ORDER BY recorded_at")
        .all()
        .map((r) => [String(r.note_id), r as unknown as MeetingRow]),
    );

    const names = new Map<string, string>();
    const kinds = new Map<string, string>();
    let missingNotes = meetings.size;
    if (existsSync(path.join(XL_ROOT, "directory.json"))) {
      const d = getDirectory(XL_ROOT);
      for (const p of d.people) (names.set(p.id, p.name), kinds.set(p.id, "person"));
      for (const o of d.orgs) (names.set(o.id, o.name), kinds.set(o.id, "org"));
      for (const p of d.projects) (names.set(p.id, p.name), kinds.set(p.id, "project"));
      missingNotes = [...meetings.keys()].filter((id) => !existsSync(path.join(XL_ROOT, "notes", `${id}.json`))).length;
    }

    const value: Memory = {
      store: path.relative(REPO, dir),
      complete: JSON.parse(readFileSync(path.join(dir, "complete.json"), "utf8")),
      facts,
      commitments,
      rejections,
      meetings,
      names,
      kinds,
      missingNotes,
    };
    cached = { dir, mtime, value };
    return value;
  } finally {
    db.close();
  }
}

/** "sam_p" → "Sam Patel"; "?sam_p|kofi" → "Sam Patel or Kofi Mensah?"; unknown ids pretty-printed. */
export function nameOf(m: Memory, id: string): string {
  if (id.startsWith("?")) return `${id.slice(1).split("|").map((x) => nameOf(m, x)).join(" or ")}?`;
  return m.names.get(id) ?? id.replace(/_/g, " ");
}
