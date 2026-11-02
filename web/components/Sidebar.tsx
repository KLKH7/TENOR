import { SidebarList, type SidebarItem } from "@/components/SidebarList";
import { meetingStart } from "@/lib/data";
import type { Note } from "@/lib/types";

/** Granola-style "My notes" list. Server part: shapes the data the client list needs. */
export function Sidebar({ notes, activeId }: { notes: Note[]; activeId: string }) {
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
  return <SidebarList items={items} activeId={activeId} initials={initials} />;
}
