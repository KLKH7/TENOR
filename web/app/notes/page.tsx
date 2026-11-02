import { redirect } from "next/navigation";

import { listNotes } from "@/lib/data";

export const dynamic = "force-dynamic";

export default function NotesIndex() {
  const [latest] = listNotes();
  redirect(`/notes/${latest.id}`);
}
