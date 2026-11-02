import { EntityToken } from "@/components/EntityToken";
import { offsetLabel } from "@/lib/format";
import type { Segment } from "@/lib/linker";
import type { Speaker, TranscriptItem } from "@/lib/types";

export interface Line {
  index: number;
  item: TranscriptItem;
  segments: Segment[] | null; // null = raw (no linking)
}

// Granola's speaker circles: orange for the first voice, magenta for the second, then others.
const AVATAR = ["bg-speaker-a", "bg-speaker-b", "bg-speaker-c", "bg-speaker-d"];

function who(s: Speaker): { label: string; initial: string; slot: number } {
  if (s.name) return { label: s.name, initial: s.name[0], slot: s.source === "microphone" ? 0 : 1 };
  if (s.diarization_label) {
    const letter = s.diarization_label.slice(-1);
    return {
      label: s.diarization_label,
      initial: letter,
      slot: Math.max(0, letter.charCodeAt(0) - 65) % AVATAR.length,
    };
  }
  return s.source === "microphone"
    ? { label: "Me", initial: "M", slot: 0 }
    : { label: "Them", initial: "T", slot: 1 };
}

/** Speaker rows as in Granola's transcript sheet: initial circle, bold name, grey text,
 *  hairline dividers, no timestamps (the start offset is in the row's tooltip). */
export function Transcript({
  lines,
  noteId,
  highlight,
}: {
  lines: Line[];
  noteId: string;
  /** Utterance ids to mark (a citation being followed). */
  highlight?: Set<string>;
}) {
  if (!lines.length) return <p className="py-6 text-[15px] text-muted">No transcript.</p>;
  const start = lines[0].item.start_time;
  const blocks: Line[][] = [];
  for (const l of lines) {
    const prev = blocks[blocks.length - 1];
    if (prev && who(prev[0].item.speaker).label === who(l.item.speaker).label) prev.push(l);
    else blocks.push([l]);
  }
  return (
    <ol className="divide-y divide-hairline">
      {blocks.map((block) => {
        const s = who(block[0].item.speaker);
        return (
          <li
            key={block[0].index}
            className="flex gap-3 py-4"
            title={`at ${offsetLabel(block[0].item.start_time, start)}`}
          >
            <span
              aria-hidden
              className={`mt-px flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[12px] font-semibold text-white ${AVATAR[s.slot]}`}
            >
              {s.initial}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[15px] font-semibold leading-6 text-ink">{s.label}</div>
              <p className="text-[15px] leading-[1.45] text-transcript">
                {block.map((l) => {
                  const uid = `${noteId}:${String(l.index).padStart(4, "0")}`;
                  return (
                    <span
                      key={l.index}
                      id={uid}
                      data-utterance={uid}
                      className={highlight?.has(uid) ? "rounded bg-lime/40 text-ink" : undefined}
                    >
                      {l.segments
                        ? l.segments.map((seg, i) =>
                            seg.link ? (
                              <EntityToken key={i} text={seg.text} link={seg.link} />
                            ) : (
                              seg.text
                            ),
                          )
                        : l.item.text}{" "}
                    </span>
                  );
                })}
              </p>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
