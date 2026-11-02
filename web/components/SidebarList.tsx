"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { CloseIcon, MicIcon, NoteIcon, SearchIcon } from "@/components/Icons";

export interface SidebarItem {
  id: string;
  title: string;
  start: string;
  hasEvent: boolean;
  inPerson: boolean;
  people: string[];
}

const UTC = { timeZone: "UTC" } as const;

function sectionLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    ...UTC,
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

function time(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", { ...UTC, hour: "numeric", minute: "2-digit" });
}

/** Calendar meetings get Granola's date tile; ad-hoc notes get the lime icon tile. */
function Tile({ item }: { item: SidebarItem }) {
  if (!item.hasEvent) {
    return (
      <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[10px] bg-tile-lime text-tile-lime-ink">
        {item.inPerson ? <MicIcon /> : <NoteIcon />}
      </span>
    );
  }
  const d = new Date(item.start);
  return (
    <span className="flex h-9 w-9 shrink-0 flex-col items-center justify-center rounded-[10px] border border-line bg-white leading-none">
      <span className="text-[8.5px] font-semibold uppercase tracking-[0.06em] text-date-red">
        {d.toLocaleDateString("en-US", { ...UTC, month: "short" })}
      </span>
      <span className="mt-[2px] font-serif text-[17px] leading-none text-ink">
        {d.getUTCDate()}
      </span>
    </span>
  );
}

function RoundButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      className="flex h-9 w-9 items-center justify-center rounded-full bg-round text-ink transition-colors hover:bg-line"
    >
      {children}
    </button>
  );
}

export function SidebarList({
  items,
  activeId,
  initials,
  corpus,
  query: linkQuery,
  nav,
}: {
  items: SidebarItem[];
  activeId: string;
  initials: string;
  corpus: string;
  query: string;
  /** Section links (Overview, Results, Memory, Brief), rendered server-side. */
  nav: React.ReactNode;
}) {
  const [searching, setSearching] = useState(false);
  const [query, setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (searching) input.current?.focus();
  }, [searching]);

  const q = query.trim().toLowerCase();
  const shown = q
    ? items.filter(
        (i) => i.title.toLowerCase().includes(q) || i.people.some((p) => p.toLowerCase().includes(q)),
      )
    : items;
  const sections = new Map<string, SidebarItem[]>();
  for (const item of shown) {
    const key = sectionLabel(item.start);
    sections.set(key, [...(sections.get(key) ?? []), item]);
  }

  return (
    <nav aria-label="Notes" className="flex h-full flex-col">
      <div className="flex items-center justify-between px-5 pt-5">
        {searching ? (
          <label className="flex h-9 flex-1 items-center gap-2 rounded-full bg-round px-3.5">
            <span className="text-muted">
              <SearchIcon />
            </span>
            <span className="sr-only">Search notes</span>
            <input
              ref={input}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Escape") {
                  setQuery("");
                  setSearching(false);
                }
              }}
              placeholder="Search notes or people"
              className="min-w-0 flex-1 bg-transparent text-[14px] text-ink placeholder:text-faint focus:outline-none"
            />
            <button
              type="button"
              aria-label="Close search"
              onClick={() => {
                setQuery("");
                setSearching(false);
              }}
              className="text-muted hover:text-ink"
            >
              <CloseIcon />
            </button>
          </label>
        ) : (
          <>
            <span />
            <div className="flex items-center gap-2">
              <RoundButton label="Search notes" onClick={() => setSearching(true)}>
                <SearchIcon />
              </RoundButton>
              <span
                aria-hidden
                className="flex h-9 w-9 items-center justify-center rounded-full bg-ink text-[12px] font-semibold text-white"
              >
                {initials}
              </span>
            </div>
          </>
        )}
      </div>

      <div className="px-4 pt-4">{nav}</div>

      <h1 className="px-5 pb-1 pt-5 font-serif text-[2.1rem] leading-none text-ink">My notes</h1>
      <p className="px-5 text-[12.5px] text-muted">
        {corpus} · {items.length} meetings
      </p>

      <div className="flex-1 overflow-y-auto px-3 pb-8">
        {shown.length === 0 && (
          <p className="px-2 pt-6 text-[14px] text-muted">No notes match “{query}”.</p>
        )}
        {[...sections].map(([label, list]) => (
          <section key={label} className="mt-5">
            <h2 className="px-2 pb-2 text-[13.5px] text-muted">{label}</h2>
            <ul className="space-y-2">
              {list.map((item) => {
                const active = item.id === activeId;
                return (
                  <li key={item.id}>
                    <Link
                      href={`/notes/${item.id}${linkQuery}`}
                      aria-current={active ? "page" : undefined}
                      className={`flex items-center gap-3 rounded-[18px] border px-3 py-2.5 transition-colors ${
                        active
                          ? "border-line bg-white shadow-[0_1px_3px_rgba(0,0,0,0.05)]"
                          : "border-transparent bg-sheet hover:border-line"
                      }`}
                    >
                      <Tile item={item} />
                      <span className="min-w-0">
                        <span className="block truncate text-[14.5px] font-semibold text-ink">
                          {item.title}
                        </span>
                        <span className="block text-[12.5px] text-muted">{time(item.start)}</span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
    </nav>
  );
}
