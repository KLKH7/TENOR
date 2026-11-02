import { AppNav } from "@/components/AppNav";
import { SidebarList, type SidebarItem } from "@/components/SidebarList";
import { meetingStart } from "@/lib/data";
import type { Note } from "@/lib/types";

/** Granola-style "My notes" list. Server part: shapes the data the client list needs. */
export function Sidebar({
  notes,
  activeId,
  setLabel,
  query,
}: {
  notes: Note[];
  activeId: string;
  /** Which fixture corpus is listed (Northwind or Northwind-XL). */
  setLabel: string;
  /** Appended to note links so they stay in this corpus (e.g. "?set=xl"). */
  query: string;
}) {
  const owner = notes[0]?.owner;
  const items: SidebarItem[] = notes.map((n) => ({
    id: n.id,
    title: n.title ?? "Untitled",
    start: meetingStart(n),
    hasEvent: n.calendar_event !== null,
    inPerson:
      (n.transcript ?? []).length > 0 &&
      (n.transcript ?? []).every((i) => i.speaker.source === "microphone"),
    people: n.attendees.filter((a) => a.email !== n.owner.email).map((a) => a.name ?? a.email),
  }));
  const initials = (owner?.name ?? owner?.email ?? "?")
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <SidebarList
      items={items}
      activeId={activeId}
      initials={initials}
      corpus={setLabel}
      query={query}
      nav={<AppNav current="notes" />}
    />
  );
}
