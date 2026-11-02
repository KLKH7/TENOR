import Link from "next/link";
import { notFound } from "next/navigation";

import {
  CalendarIcon,
  CloseIcon,
  FolderIcon,
  PersonIcon,
  SendIcon,
  WaveIcon,
} from "@/components/Icons";
import { Sidebar } from "@/components/Sidebar";
import { Markdownish, TypedNotes } from "@/components/Summary";
import { type Line, Transcript } from "@/components/Transcript";
import { AppNav } from "@/components/AppNav";
import { ScrollToLine } from "@/components/results/ScrollToLine";
import { getDirectory, getNote, listNotes, meetingStart, setOf } from "@/lib/data";
import { dayLabel, timeLabel } from "@/lib/format";
import { type Link as EntityLink, linkText } from "@/lib/linker";

const SUGGESTIONS = ["List action items", "Who committed to what?", "What changed since last time?"];

type View = "enhanced" | "mine";
interface Query {
  view?: string;
  transcript?: string;
  raw?: string;
  /** Comma-separated utterance ids to highlight (links from Memory and Brief). */
  line?: string;
  /** Fixture set: "xl" for Northwind-XL (shares note ids with base Northwind). */
  set?: string;
}

function href(
  id: string,
  q: { view: View; transcript: boolean; raw: boolean; line: string[]; set: string },
): string {
  const p = new URLSearchParams();
  if (q.set !== "northwind") p.set("set", q.set);
  if (q.view !== "enhanced") p.set("view", q.view);
  if (q.transcript) p.set("transcript", "1");
  if (q.raw) p.set("raw", "1");
  if (q.transcript && q.line.length) p.set("line", q.line.join(","));
  const s = p.toString();
  return `/notes/${id}${s ? `?${s}` : ""}`;
}

function Chip({ icon, children }: { icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-full border border-line bg-card px-3.5 py-1.5 text-[13.5px] text-ink shadow-[0_1px_1px_rgba(0,0,0,0.03)]">
      {icon && <span className="text-muted">{icon}</span>}
      {children}
    </span>
  );
}

function Segmented({
  items,
}: {
  items: { label: string; href: string; active: boolean }[];
}) {
  return (
    <div className="inline-flex gap-0.5 rounded-full bg-canvas p-0.5">
      {items.map((i) => (
        <Link
          key={i.label}
          href={i.href}
          aria-current={i.active ? "page" : undefined}
          className={`rounded-full px-3 py-1 text-[12.5px] transition-colors ${
            i.active
              ? "bg-card font-medium text-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
              : "text-muted hover:text-ink"
          }`}
        >
          {i.label}
        </Link>
      ))}
    </div>
  );
}

export default async function NotePage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<Query>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const set = setOf(id, query.set);
  const note = set && getNote(id, set.root);
  if (!set || !note) notFound();
  const line = (query.line ?? "").split(",").filter((l) => l.startsWith(`${id}:`));
  const state = {
    view: (query.view === "mine" ? "mine" : "enhanced") as View,
    // Following a cited line opens the transcript on it.
    transcript: query.transcript === "1" || line.length > 0,
    raw: query.raw === "1",
    line,
    set: set.key,
  };
  const notes = listNotes(set.root);
  const dir = getDirectory(set.root);
  const items = note.transcript ?? [];
  const start = meetingStart(note);
  const inPerson = items.length > 0 && items.every((i) => i.speaker.source === "microphone");
  const folder = note.folder_membership[0];
  const others = note.attendees.filter((a) => a.email !== note.owner.email);

  const linked = state.transcript ? items.map((item) => linkText(item.text, note, dir)) : [];
  const lines: Line[] = items.map((item, index) => ({
    index,
    item,
    segments: state.transcript && !state.raw ? linked[index] : null,
  }));
  const mentions = new Map<string, { link: EntityLink; count: number }>();
  let ambiguous = 0;
  for (const seg of linked.flat()) {
    if (!seg.link) continue;
    if (seg.link.status === "ambiguous") {
      ambiguous++;
      continue;
    }
    const key = seg.link.id ?? seg.link.label;
    const prev = mentions.get(key);
    mentions.set(key, { link: prev?.link ?? seg.link, count: (prev?.count ?? 0) + 1 });
  }
  const resolved = [...mentions.values()].sort((a, b) => b.count - a.count);

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 h-screen w-[17.5rem] shrink-0 max-lg:hidden">
        <Sidebar
          notes={notes}
          activeId={note.id}
          setLabel={set.label}
          query={set.key === "northwind" ? "" : `?set=${set.key}`}
        />
      </aside>

      <main className="flex min-w-0 flex-1 gap-3 py-4 pr-4 max-lg:p-2">
        {/* Note sheet */}
        <article className="relative flex min-h-[calc(100vh-2rem)] min-w-0 flex-1 flex-col rounded-[32px] bg-sheet shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
          <div className="mx-auto flex w-full max-w-[40rem] flex-1 flex-col px-8 pt-14 max-lg:pt-6 max-sm:px-5">
            <AppNav current="notes" className="mb-8 self-start lg:hidden" />
            <h1 className="font-serif text-[2.75rem] leading-[1.08] tracking-[-0.01em] text-ink">
              {note.title}
            </h1>
            <div className="mt-5 flex flex-wrap items-center gap-2">
              <Chip icon={<CalendarIcon />}>
                {dayLabel(start)}, {timeLabel(start)}
              </Chip>
              <Chip icon={<PersonIcon />}>
                {others.length === 0
                  ? "No calendar invite"
                  : others.length === 1
                    ? (others[0].name ?? others[0].email)
                    : `${others[0].name?.split(" ")[0]} + ${others.length - 1}`}
              </Chip>
              {folder && <Chip icon={<FolderIcon />}>{folder.name}</Chip>}
              {inPerson && <Chip>In person</Chip>}
            </div>

            <div className="mt-9 flex items-center justify-between">
              <Segmented
                items={[
                  {
                    label: "Enhanced notes",
                    href: href(note.id, { ...state, view: "enhanced" }),
                    active: state.view === "enhanced",
                  },
                  {
                    label: "My notes",
                    href: href(note.id, { ...state, view: "mine" }),
                    active: state.view === "mine",
                  },
                ]}
              />
              <Link
                href={href(note.id, { ...state, transcript: !state.transcript })}
                aria-pressed={state.transcript}
                className={`inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[13px] transition-colors ${
                  state.transcript
                    ? "bg-ink text-card"
                    : "border border-line bg-card text-ink hover:bg-canvas"
                }`}
              >
                <WaveIcon />
                Transcript
              </Link>
            </div>

            <section className="flex-1 pb-48 pt-7">
              {state.view === "enhanced" &&
                (note.summary_markdown ? (
                  <Markdownish source={note.summary_markdown} />
                ) : (
                  <p className="text-muted">No enhanced notes for this meeting.</p>
                ))}
              {state.view === "mine" &&
                (note.private_notes_markdown ? (
                  <TypedNotes source={note.private_notes_markdown} />
                ) : (
                  <p className="text-[15px] text-muted">
                    You didn’t take any notes during this meeting.
                  </p>
                ))}
            </section>
          </div>

          <div className="pointer-events-none sticky bottom-0 z-10 mt-auto rounded-b-[32px] bg-gradient-to-t from-sheet via-sheet/90 to-transparent px-6 pb-5 pt-10">
            <div className="pointer-events-auto mx-auto max-w-[40rem]">
              <div className="mb-3 flex gap-2 overflow-x-auto">
                {SUGGESTIONS.map((s) => (
                  <span
                    key={s}
                    className="shrink-0 rounded-full border border-line bg-card px-3.5 py-1.5 text-[13px] text-ink"
                  >
                    {s}
                  </span>
                ))}
              </div>
              <label className="flex items-center gap-3 rounded-[22px] bg-card px-5 py-3.5 shadow-[0_2px_12px_rgba(31,31,29,0.07)]">
                <span className="sr-only">Chat with this meeting</span>
                <input
                  disabled
                  placeholder="Chat with this meeting"
                  title="Ask arrives in Phase 6"
                  className="flex-1 bg-transparent text-[15px] text-ink placeholder:text-faint disabled:cursor-not-allowed"
                />
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-faint/60 text-white">
                  <SendIcon />
                </span>
              </label>
              <p className="mt-2 text-center text-[11px] text-faint">
                Ask (Concentrate vs top-k baseline) arrives in Phase 6.
              </p>
            </div>
          </div>
        </article>

        {/* Transcript panel — matches Granola's transcript sheet */}
        {state.transcript && (
          <aside
            aria-label="Transcript"
            className="sticky top-4 flex h-[calc(100vh-2rem)] w-[26rem] shrink-0 flex-col overflow-hidden rounded-[28px] bg-panel shadow-[0_1px_3px_rgba(0,0,0,0.05)] max-xl:fixed max-xl:right-4 max-xl:z-50 max-xl:w-[min(26rem,calc(100vw-2rem))] max-xl:shadow-[0_16px_48px_rgba(31,31,29,0.18)]"
          >
            <header className="flex items-center justify-between px-5 pb-2 pt-5">
              <h2 className="text-[17px] font-semibold text-ink">Transcript</h2>
              <div className="flex items-center gap-4 text-[15px] text-ink">
                <Link
                  href={href(note.id, { ...state, raw: !state.raw })}
                  className="hover:opacity-70"
                  title={state.raw ? "Show linked mentions" : "Show the transcript exactly as Granola returned it"}
                >
                  {state.raw ? "Linked" : "Raw"}
                </Link>
                <Link
                  href={href(note.id, { ...state, transcript: false })}
                  aria-label="Close transcript"
                  className="text-ink hover:opacity-70"
                >
                  <CloseIcon />
                </Link>
              </div>
            </header>
            {state.line.length > 0 && (
              <p className="mx-5 mt-2 rounded-xl bg-accent-soft px-3 py-2 text-[12.5px] leading-snug text-accent">
                Highlighted: the {state.line.length === 1 ? "line" : `${state.line.length} lines`} cited by
                the memory item you followed.
              </p>
            )}
            {inPerson && (
              <p className="mx-5 mt-2 rounded-xl bg-warn-soft px-3 py-2 text-[12.5px] leading-snug text-warn">
                In person: one microphone, so all speech is attributed to “Me”.
              </p>
            )}
            <div className="flex-1 overflow-y-auto px-5">
              <Transcript lines={lines} noteId={note.id} highlight={new Set(state.line)} />
              {state.line[0] && <ScrollToLine id={state.line[0]} />}
            </div>
            {!state.raw && (
              <footer className="border-t border-hairline px-5 py-3 text-[11.5px] leading-snug text-faint">
                <span className="text-accent">Green</span> mentions are linked
                {ambiguous > 0 && <> · <span className="text-warn">{ambiguous} unresolved</span></>}
                {resolved.length > 0 && (
                  <> · {resolved.length} {resolved.length === 1 ? "entity" : "entities"}</>
                )}. Prototype linker
                until Phase 4.
              </footer>
            )}
          </aside>
        )}
      </main>
    </div>
  );
}
