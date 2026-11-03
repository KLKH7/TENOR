// Bitemporal "as of" logic for the Memory view. Pure, so it is unit-tested
// (asof.test.mts) and mirrors concentrate.memory.store: Fact.live_at and Commitment.status_at,
// as used by the MCP server's get_facts / get_commitments.

export interface FactRow {
  id: number;
  subject: string;
  predicate: string;
  kind: string;
  value: string;
  value_norm: string;
  valid_from: string;
  valid_to: string | null;
  recorded_at: string;
  valid_to_recorded_at: string | null;
  note_id: string;
  source_lines: string[];
  quote: string;
  reason: string | null;
  confidence: number;
  superseded_by: number | null;
  contradiction: number;
  gate: string | null;
  /** Later meetings that restated this fact (evidence table). */
  evidence: { lines: string[]; recorded_at: string }[];
}

export interface CommitmentEvent {
  kind: string; // slipped | reassigned | done | dropped
  day: string;
  recorded_at: string;
  note_id: string;
  source_lines: string[];
  quote: string;
  new_due: string | null;
  new_owner: string | null;
}

export interface CommitmentRow {
  id: number;
  owner: string;
  task: string;
  due: string | null;
  note_id: string;
  made_on: string;
  recorded_at: string;
  source_lines: string[];
  quote: string;
  confidence: number;
  events: CommitmentEvent[];
}

/** current = true on the day as known that day; superseded = its end was known by then;
 *  scheduled = known, but its valid_from is later; unknown = the memory hadn't learnt it yet. */
export type FactState = "current" | "superseded" | "scheduled" | "unknown";

const d10 = (s: string | null | undefined) => (s ?? "").slice(0, 10);

export function factState(f: FactRow, day: string): FactState {
  if (d10(f.recorded_at) > day) return "unknown";
  const endKnown = f.valid_to !== null && d10(f.valid_to_recorded_at ?? f.recorded_at) <= day;
  const end = endKnown ? f.valid_to : null;
  if (f.valid_from <= day && (end === null || day < end)) return "current";
  if (end !== null && end <= day) return "superseded";
  return "scheduled";
}

export interface CommitmentStatus {
  status: string; // open | no_follow_up | slipped | reassigned | done | dropped | unknown
  owner: string;
  due: string | null;
  /** Events on or before the day (the rest are "in the future" of the slider). */
  seen: CommitmentEvent[];
}

/** Lifecycle on ``day``: the last event on or before it, else open / no follow-up. */
export function commitmentStatus(c: CommitmentRow, day: string): CommitmentStatus {
  if (c.made_on > day) return { status: "unknown", owner: c.owner, due: c.due, seen: [] };
  let owner = c.owner;
  let due = c.due;
  let status = "open";
  const seen: CommitmentEvent[] = [];
  for (const ev of [...c.events].sort((a, b) => a.day.localeCompare(b.day))) {
    if (ev.day > day) break;
    seen.push(ev);
    status = ev.kind;
    owner = ev.new_owner ?? owner;
    due = ev.new_due ?? due;
  }
  if (!seen.length) status = c.made_on < day ? "no_follow_up" : "open";
  return { status, owner, due, seen };
}

/** The fact's own lines plus restatements the memory had heard by ``day``. */
export function factLines(f: FactRow, day: string): string[] {
  const out = [...f.source_lines];
  for (const e of f.evidence) {
    if (d10(e.recorded_at) <= day) out.push(...e.lines.filter((l) => !out.includes(l)));
  }
  return out;
}

/** "?sam_p|kofi" → ["sam_p", "kofi"]: an owner the transcript couldn't pin down. */
export function ownerCandidates(owner: string): string[] | null {
  return owner.startsWith("?") ? owner.slice(1).split("|") : null;
}

/** Group lines by meeting for linking: ["not_a:0001","not_a:0003"] → { not_a: [...] }. */
export function linesByNote(lines: string[]): [string, string[]][] {
  const out = new Map<string, string[]>();
  for (const l of lines) {
    const note = l.split(":")[0];
    if (!out.has(note)) out.set(note, []);
    if (!out.get(note)!.includes(l)) out.get(note)!.push(l);
  }
  return [...out];
}

/** Link into the note viewer (Northwind-XL, which the memory is built from) with the cited
 *  lines highlighted. */
export function lineHref(note: string, lines: string[]): string {
  return `/notes/${note}?set=xl&line=${encodeURIComponent(lines.join(","))}`;
}
