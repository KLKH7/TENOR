import { redirect } from "next/navigation";

import { listNotes } from "@/lib/data";

export default function Home() {
  const [latest] = listNotes();
  redirect(`/notes/${latest.id}`);
}
