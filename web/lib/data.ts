import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import type { Directory, Note } from "./types";

// Reads only what a deployment would have: Granola notes + the org directory/glossary.
// world.json (ground truth) is deliberately never read by the UI.
const ROOT =
  process.env.CONCENTRATE_FIXTURES ??
  path.join(process.cwd(), "..", "data", "fixtures", "northwind");

export function meetingStart(note: Note): string {
  return note.calendar_event?.scheduled_start_time ?? note.created_at;
}

export function listNotes(): Note[] {
  const dir = path.join(ROOT, "notes");
  return readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .map((f) => JSON.parse(readFileSync(path.join(dir, f), "utf8")) as Note)
    .sort((a, b) => meetingStart(b).localeCompare(meetingStart(a)));
}

export function getNote(id: string): Note | undefined {
  if (!/^not_[A-Za-z0-9]{14}$/.test(id)) return undefined;
  try {
    return JSON.parse(readFileSync(path.join(ROOT, "notes", `${id}.json`), "utf8")) as Note;
  } catch {
    return undefined;
  }
}

export function getDirectory(): Directory {
  return JSON.parse(readFileSync(path.join(ROOT, "directory.json"), "utf8")) as Directory;
}
