import Link from "next/link";

import { CloseIcon } from "@/components/Icons";
import { ScrollToLine } from "@/components/results/ScrollToLine";
import { systemLabel } from "@/components/results/ui";
import { dayLabel, offsetLabel, timeLabel } from "@/lib/format";
import { meetingStart } from "@/lib/data";
import type { LineMark } from "@/lib/explore";
import type { Note, Speaker } from "@/lib/types";

export type PanelMark = LineMark | "gold";

/** Line styles. Gold evidence the system cited = green; cited but not gold = amber;
 *  gold the system missed = red dashed; gold with no system selected = green outline. */
export const MARK_STYLE: Record<PanelMark, { row: string; label: string; chip: string }> = {
  correct: {
    row: "bg-accent-soft",
    label: "cited · gold",
    chip: "border-accent/40 bg-accent-soft text-accent",
  },
  extra: {
    row: "bg-warn-soft",
    label: "cited · not gold",
    chip: "border-warn/30 bg-warn-soft text-warn",
  },
  missed: {
    row: "outline-dashed outline-1 -outline-offset-1 outline-date-red/60 bg-date-red/5",
    label: "gold · not cited",
    chip: "border-dashed border-date-red/60 bg-date-red/5 text-date-red",
  },
  gold: {
    row: "outline outline-1 -outline-offset-1 outline-accent/60 bg-accent-soft/50",
    label: "gold evidence",
    chip: "border-accent/40 bg-card text-accent",
  },
};

export function speakerOf(s: Speaker): { label: string; me: boolean } {
  const me = s.source === "microphone";
  return { label: s.name ?? (me ? "Me" : "Them"), me };
}

export function lineId(noteId: string, index: number): string {
  return `${noteId}:${String(index).padStart(4, "0")}`;
}

/** Granola-style white transcript sheet, one row per utterance so single lines can be marked.
 *  Line numbers are shown because citations are line ids (`not_…:0025`). */
export function EvidencePanel({
  note,
  marks,
  target,
  systems,
  activeSystem,
  hrefFor,
  closeHref,
  meetingLevel = false,
}: {
  note: Note | undefined;
  marks: Map<string, PanelMark>;
  target: string | null;
  systems: string[];
  activeSystem: string | null;
  hrefFor: (sys: string | null) => string;
  closeHref: string;
  /** The citation named the whole meeting, not a line: nothing to point at. */
  meetingLevel?: boolean;
}) {
  const lines = note?.transcript ?? [];
  const start = lines[0]?.start_time;
  const used = new Set(marks.values());
  return (
    <aside className="sticky top-4 flex h-[calc(100vh-2rem)] w-[32rem] shrink-0 flex-col rounded-[24px] bg-panel shadow-[0_1px_3px_rgba(0,0,0,0.06)] max-xl:fixed max-xl:inset-y-2 max-xl:right-2 max-xl:z-20 max-xl:h-auto max-xl:w-[min(32rem,calc(100vw-1rem))]">
      <header className="border-b border-hairline px-6 pb-4 pt-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 className="text-[17px] font-semibold text-ink">Transcript</h2>
            {note && (
              <p className="mt-0.5 truncate text-[13px] text-muted">
                {note.title ?? "Untitled"} · {dayLabel(meetingStart(note))}, {timeLabel(meetingStart(note))}
              </p>
            )}
          </div>
          <Link
            href={closeHref}
            scroll={false}
            aria-label="Close transcript"
            className="rounded-full p-1.5 text-muted hover:bg-sheet hover:text-ink"
          >
            <CloseIcon />
          </Link>
        </div>
        <div className="mt-3 flex flex-wrap gap-1">
          <PanelTab href={hrefFor(null)} active={activeSystem === null}>
            Gold only
          </PanelTab>
          {systems.map((s) => (
            <PanelTab key={s} href={hrefFor(s)} active={activeSystem === s}>
              {systemLabel(s).split(" · ")[0]}
            </PanelTab>
          ))}
        </div>
        {meetingLevel && (
          <p className="mt-3 text-[12px] text-muted">
            Whole-meeting citation: no line was cited, so it can&apos;t be checked line by line.
          </p>
        )}
        {used.size > 0 && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {(["correct", "extra", "missed", "gold"] as const)
              .filter((m) => used.has(m))
              .map((m) => (
                <span key={m} className={`rounded-full border px-2 py-0.5 text-[11px] ${MARK_STYLE[m].chip}`}>
                  {MARK_STYLE[m].label}
                </span>
              ))}
          </div>
        )}
      </header>
      <ol className="flex-1 overflow-y-auto px-3 py-2">
        {!note && <li className="px-3 py-6 text-[14px] text-muted">Note not found in the fixtures.</li>}
        {note && !lines.length && <li className="px-3 py-6 text-[14px] text-muted">No transcript.</li>}
        {lines.map((item, i) => {
          const id = lineId(note!.id, i);
          const mark = marks.get(id);
          const who = speakerOf(item.speaker);
          return (
            <li
              key={id}
              id={`line-${id}`}
              title={start ? `at ${offsetLabel(item.start_time, start)}` : undefined}
              className={`my-0.5 flex gap-2.5 rounded-xl px-3 py-2 ${mark ? MARK_STYLE[mark].row : ""} ${
                target === id ? "ring-2 ring-ink/70" : ""
              }`}
            >
              <span className="w-9 shrink-0 pt-[3px] text-right text-[11px] tabular-nums text-faint">
                {String(i).padStart(4, "0")}
              </span>
              <span
                aria-hidden
                className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10.5px] font-semibold text-white ${
                  who.me ? "bg-speaker-a" : "bg-speaker-b"
                }`}
              >
                {who.label[0]}
              </span>
              <p className="min-w-0 text-[14px] leading-[1.45]">
                <span className="mr-1.5 font-semibold text-ink">{who.label}</span>
                <span className={mark ? "text-ink" : "text-transcript"}>{item.text}</span>
              </p>
            </li>
          );
        })}
      </ol>
      <footer className="border-t border-hairline px-6 py-2.5 text-[11.5px] text-faint">
        Me = microphone channel, Them = system audio (names only when the note carries them).
      </footer>
      {target && <ScrollToLine id={`line-${target}`} />}
    </aside>
  );
}

function PanelTab({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      scroll={false}
      aria-current={active ? "true" : undefined}
      className={`rounded-full px-2.5 py-1 text-[12px] ${
        active ? "bg-ink text-white" : "bg-sheet text-muted hover:text-ink"
      }`}
    >
      {children}
    </Link>
  );
}
