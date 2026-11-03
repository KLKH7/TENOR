import Link from "next/link";

import { lineHref, linesByNote } from "@/lib/asof";
import type { Memory } from "@/lib/memory";

export function dayLabel(day: string): string {
  return new Date(`${day.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-US", {
    timeZone: "UTC",
    month: "short",
    day: "numeric",
  });
}

/** Cited transcript lines, grouped by meeting, each linking into the note viewer with the
 *  lines highlighted. */
export function Lines({ m, lines }: { m: Memory; lines: string[] }) {
  if (!lines.length) return null;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {linesByNote(lines).map(([note, ls]) => (
        <Link
          key={note}
          href={lineHref(note, ls)}
          title={ls.join("\n")}
          className="rounded-full border border-line bg-card px-2 py-0.5 text-[11.5px] text-muted hover:border-ink/40 hover:text-ink"
        >
          {m.meetings.get(note)?.title ?? note} · {dayLabel(m.meetings.get(note)?.day ?? note)} ·{" "}
          {ls.length === 1 ? `line ${Number(ls[0].split(":")[1])}` : `${ls.length} lines`}
        </Link>
      ))}
    </span>
  );
}
