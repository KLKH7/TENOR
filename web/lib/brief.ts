import { execFile } from "node:child_process";
import path from "node:path";
import { promisify } from "node:util";

// Runs `concentrate brief <note> --json` (memory/brief.py: SQL reads over the newest complete
// store, no LLM, $0) and memoises the result per meeting for the life of the server.
const REPO = process.env.CONCENTRATE_ROOT ?? path.join(process.cwd(), "..");
const UV = process.env.CONCENTRATE_UV ?? "uv";
const run = promisify(execFile);

export interface Brief {
  meeting: { title: string; start: string; attendees: string[]; customer: string | null; note_id: string | null };
  as_of: string;
  last_meeting: { note_id: string; title: string; day: string; digest: string } | null;
  commitments: {
    owner: string;
    task: string;
    status: string;
    due: string | null;
    original_due: string | null;
    overdue: boolean;
    made_in: string;
    made_on: string;
    lines: string[];
  }[];
  changes: {
    subject: string;
    predicate: string;
    kind: string;
    value: string;
    previous: string | null;
    learnt_in: string;
    learnt_on: string;
    lines: string[];
  }[];
  requests: { customer: string; request: string; since: string; note_id: string; lines: string[] }[];
}

const cache = new Map<string, Promise<Brief>>();

export function briefCommand(noteId: string): string {
  return `uv run concentrate brief ${noteId}`;
}

/** The brief for a meeting, as of its start. Throws with the CLI's stderr on failure. */
export function getBrief(noteId: string): Promise<Brief> {
  if (!/^not_[A-Za-z0-9]{14}$/.test(noteId)) return Promise.reject(new Error("bad note id"));
  let p = cache.get(noteId);
  if (!p) {
    p = run(UV, ["run", "concentrate", "brief", noteId, "--json"], {
      cwd: REPO,
      timeout: 120_000,
      maxBuffer: 8 * 1024 * 1024,
    }).then(({ stdout }) => JSON.parse(stdout) as Brief);
    p.catch(() => cache.delete(noteId));
    cache.set(noteId, p);
  }
  return p;
}
