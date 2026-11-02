// Prototype entity linker ("linker v0") for the note view.
//
// This is a stand-in so the UI can be built before Phase 4. It uses only what a deployment
// has: the note's attendees, the org directory and the glossary. Rules:
//   - full names resolve directly;
//   - a first name resolves when exactly one *attendee* has it, else when exactly one person
//     in the directory has it; otherwise it stays explicitly ambiguous (shown as "?");
//   - misspelled names (ASR) match the directory within edit distance 1, at lower confidence;
//   - project codenames, aliases and spoken ticket codes ("PAY two twelve") expand via the
//     glossary; the longest match wins, so "Beacon Freight" (customer) beats "Beacon" (project).
// It does NOT use co-occurrence priors, channel cues or conversational context — Phase 4 does.

import type { Directory, DirectoryPerson, Note } from "./types";

export type EntityKind = "person" | "org" | "project";

export interface Link {
  kind: EntityKind;
  status: "resolved" | "ambiguous";
  confidence: number;
  reason: string;
  label: string; // what the entity resolved to (or "Sam?" when ambiguous)
  detail?: string; // role · email, org domain, project expansion
  id?: string;
  candidates?: DirectoryPerson[];
}

export type Segment = { text: string; link?: Link };

interface Match {
  start: number;
  end: number;
  link: Link;
}

const NUM: Record<string, number> = {
  zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9,
  ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16,
  seventeen: 17, eighteen: 18, nineteen: 19, twenty: 20, thirty: 30, forty: 40, fifty: 50,
  sixty: 60, seventy: 70, eighty: 80, ninety: 90,
};
// Capitalised words that are close to a first name but are ordinary English.
const STOP = new Set(["Then", "Them", "Than", "Sure", "Same", "Seen", "Dear", "Mean"]);

function fold(s: string): string {
  return s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

function editDistance(a: string, b: string): number {
  const dp = Array.from({ length: a.length + 1 }, (_, i) => [i, ...Array(b.length).fill(0)]);
  for (let j = 1; j <= b.length; j++) dp[0][j] = j;
  for (let i = 1; i <= a.length; i++)
    for (let j = 1; j <= b.length; j++)
      dp[i][j] = Math.min(
        dp[i - 1][j] + 1,
        dp[i][j - 1] + 1,
        dp[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1),
      );
  return dp[a.length][b.length];
}

/** "two twelve" -> "212", "twenty one" -> "21". Concatenates spoken digit groups. */
function spokenCode(words: string[]): string | null {
  let out = "";
  for (let i = 0; i < words.length; i++) {
    const v = NUM[words[i]];
    if (v === undefined) return null;
    const next = NUM[words[i + 1]];
    if (v >= 20 && next !== undefined && next < 10) {
      out += String(v + next);
      i++;
    } else out += String(v);
  }
  return out || null;
}

function personLink(
  p: DirectoryPerson,
  confidence: number,
  reason: string,
): Link {
  return {
    kind: "person",
    status: "resolved",
    confidence,
    reason,
    label: p.name,
    detail: `${p.role} · ${p.org} · ${p.email}`,
    id: p.id,
  };
}

export function linkText(text: string, note: Note, dir: Directory): Segment[] {
  const attendeeEmails = new Set([note.owner.email, ...note.attendees.map((a) => a.email)]);
  const firstName = (p: DirectoryPerson) => p.name.split(" ")[0];
  const matches: Match[] = [];

  // People: full names.
  for (const p of dir.people) {
    for (const m of text.matchAll(new RegExp(`\\b${p.name}\\b`, "g"))) {
      matches.push({
        start: m.index!,
        end: m.index! + p.name.length,
        link: personLink(p, 0.98, "Full name matches the directory."),
      });
    }
  }

  // People: capitalised single tokens that match (or nearly match) a first name.
  for (const m of text.matchAll(/\b\p{Lu}[\p{L}]+\b/gu)) {
    const token = m[0];
    if (STOP.has(token)) continue;
    const exact = dir.people.filter((p) => fold(firstName(p)) === fold(token));
    const fuzzy =
      exact.length || token.length < 4
        ? []
        : dir.people.filter((p) => editDistance(fold(firstName(p)), fold(token)) === 1);
    const cands = exact.length ? exact : fuzzy;
    if (!cands.length) continue;
    const spelled = exact.length ? "" : ` Heard as "${token}" — closest directory name.`;
    const penalty = exact.length ? 1 : 0.8;
    const attending = cands.filter((p) => attendeeEmails.has(p.email));
    let link: Link;
    if (attending.length === 1) {
      link = personLink(
        attending[0],
        0.9 * penalty,
        `Only attendee called ${firstName(attending[0])}.${spelled}`,
      );
    } else if (attending.length === 0 && cands.length === 1) {
      link = personLink(
        cands[0],
        0.6 * penalty,
        `Not an attendee; only ${firstName(cands[0])} in the directory.${spelled}`,
      );
    } else {
      const pool = attending.length ? attending : cands;
      link = {
        kind: "person",
        status: "ambiguous",
        confidence: 1 / pool.length,
        reason: attending.length
          ? `${pool.length} attendees share this name; context needed.`
          : `${pool.length} people in the directory share this name and none attended.`,
        label: `${token}?`,
        candidates: pool,
      };
    }
    matches.push({ start: m.index!, end: m.index! + token.length, link });
  }

  // Organisations.
  for (const o of dir.orgs) {
    for (const m of text.matchAll(new RegExp(`\\b${o.name}\\b`, "g"))) {
      matches.push({
        start: m.index!,
        end: m.index! + o.name.length,
        link: {
          kind: "org",
          status: "resolved",
          confidence: 0.95,
          reason: o.kind === "customer" ? "Customer in the CRM." : "Investor in the CRM.",
          label: o.name,
          detail: `${o.kind} · ${o.domain}`,
          id: o.id,
        },
      });
    }
  }

  // Projects: names, aliases, written and spoken ticket codes.
  for (const pr of dir.projects) {
    const expansion = [pr.name, ...pr.aliases].join(" = ");
    const surfaces = [pr.name, ...pr.aliases.filter((a) => !/^[A-Z]+-\d+$/.test(a))];
    for (const s of surfaces) {
      for (const m of text.matchAll(new RegExp(`\\b${s}\\b`, "gi"))) {
        const shadowed = pr.name === "Beacon" ? " (Not the customer Beacon Freight.)" : "";
        matches.push({
          start: m.index!,
          end: m.index! + s.length,
          link: {
            kind: "project",
            status: "resolved",
            confidence: shadowed ? 0.8 : 0.95,
            reason: `Glossary: ${pr.description}.${shadowed}`,
            label: pr.name,
            detail: expansion,
            id: pr.id,
          },
        });
      }
    }
    for (const code of pr.aliases.filter((a) => /^[A-Z]+-\d+$/.test(a))) {
      const [prefix, digits] = code.split("-");
      for (const m of text.matchAll(new RegExp(`\\b${prefix}[ -]((?:[a-z]+ ?){1,3})`, "g"))) {
        const words = m[1].trim().split(" ");
        for (let n = words.length; n > 0; n--) {
          if (spokenCode(words.slice(0, n)) === digits) {
            const len = prefix.length + 1 + words.slice(0, n).join(" ").length;
            matches.push({
              start: m.index!,
              end: m.index! + len,
              link: {
                kind: "project",
                status: "resolved",
                confidence: 0.9,
                reason: `Spoken ticket code normalised to ${code}.`,
                label: code,
                detail: expansion,
                id: pr.id,
              },
            });
            break;
          }
        }
      }
    }
  }

  // Longest match wins; drop overlaps.
  matches.sort((a, b) => a.start - b.start || b.end - b.start - (a.end - a.start));
  const chosen: Match[] = [];
  for (const m of matches) {
    const last = chosen[chosen.length - 1];
    if (last && m.start < last.end) continue;
    chosen.push(m);
  }
  const out: Segment[] = [];
  let pos = 0;
  for (const m of chosen) {
    if (m.start > pos) out.push({ text: text.slice(pos, m.start) });
    out.push({ text: text.slice(m.start, m.end), link: m.link });
    pos = m.end;
  }
  if (pos < text.length) out.push({ text: text.slice(pos) });
  return out;
}
