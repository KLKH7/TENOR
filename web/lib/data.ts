import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import type { Directory, Note } from "./types";

// Reads only what a deployment would have: Granola notes + the org directory/glossary.
// world.json (ground truth) is deliberately never read by the UI.
const FIXTURES = path.join(process.cwd(), "..", "data", "fixtures");
const ROOT = process.env.CONCENTRATE_FIXTURES ?? path.join(FIXTURES, "northwind");
/** Northwind-XL (gitignored): the corpus the memory store and briefs are built from. */
export const XL_ROOT = process.env.CONCENTRATE_XL_FIXTURES ?? path.join(FIXTURES, "large", "northwind-xl");

export interface FixtureSet {
  key: "northwind" | "xl";
  root: string;
  label: string;
}

/** Fixture sets the note viewer can open, in lookup order. Northwind-XL reuses the 40 base
 *  note ids with different content, so links into XL must say `?set=xl`. */
export function fixtureSets(): FixtureSet[] {
  const sets: FixtureSet[] = [
    { key: "northwind", root: ROOT, label: "Northwind" },
    { key: "xl", root: XL_ROOT, label: "Northwind-XL" },
  ];
  return sets.filter((s) => existsSync(path.join(s.root, "notes")));
}

const NOTE_ID = /^not_[A-Za-z0-9]{14}$/;

export function meetingStart(note: Note): string {
  return note.calendar_event?.scheduled_start_time ?? note.created_at;
}

export function listNotes(root: string = ROOT): Note[] {
  const dir = path.join(root, "notes");
  return readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => JSON.parse(readFileSync(path.join(dir, f), "utf8")) as Note)
    .sort((a, b) => meetingStart(b).localeCompare(meetingStart(a)));
}

/** The fixture set holding this note: ``prefer`` if it has it, else base Northwind first. */
export function setOf(id: string, prefer?: string | null): FixtureSet | undefined {
  if (!NOTE_ID.test(id)) return undefined;
  const has = (s: FixtureSet) => existsSync(path.join(s.root, "notes", `${id}.json`));
  const sets = fixtureSets();
  return sets.find((s) => s.key === prefer && has(s)) ?? sets.find(has);
}

export function getNote(id: string, root?: string): Note | undefined {
  const r = root ?? setOf(id)?.root;
  if (!r || !NOTE_ID.test(id)) return undefined;
  try {
    return JSON.parse(readFileSync(path.join(r, "notes", `${id}.json`), "utf8")) as Note;
  } catch {
    return undefined;
  }
}

export function getDirectory(root: string = ROOT): Directory {
  return JSON.parse(readFileSync(path.join(root, "directory.json"), "utf8")) as Directory;
}
