import Link from "next/link";

import { PageShell } from "@/components/AppNav";
import { listNotes, XL_ROOT } from "@/lib/data";
import { stampLabel } from "@/lib/explore";
import { loadMemory } from "@/lib/memory";
import { listReports } from "@/lib/results";

export const dynamic = "force-dynamic";

function Tile({ href, title, children, meta }: { href: string; title: string; children: React.ReactNode; meta: string }) {
  return (
    <Link
      href={href}
      className="group flex flex-col rounded-[22px] border border-line bg-card p-5 shadow-[0_1px_2px_rgba(0,0,0,0.03)] transition-colors hover:border-ink/30"
    >
      <span className="font-serif text-[1.6rem] leading-tight text-ink group-hover:text-accent">{title} →</span>
      <span className="mt-2 flex-1 text-[14px] leading-relaxed text-muted">{children}</span>
      <span className="mt-4 text-[12px] text-faint">{meta}</span>
    </Link>
  );
}

export default function Home() {
  const notes = listNotes();
  let xl = 0;
  try {
    xl = listNotes(XL_ROOT).length;
  } catch {
    xl = 0;
  }
  const reports = listReports();
  const m = loadMemory();
  return (
    <PageShell current="home">
      <h1 className="mt-10 font-serif text-[3rem] leading-[1.05] tracking-[-0.01em] text-ink">Concentrate</h1>
      <div className="mt-4 max-w-[42rem] space-y-1.5 text-[16px] leading-relaxed text-ink">
        <p>A memory for meeting notes, built once at write time: facts, commitments and entities, each citing the transcript lines it came from and stamped with when it was true and when it was learnt.</p>
        <p>An open benchmark that puts it against &ldquo;agent + top-k transcript retrieval&rdquo; and other baselines on the same questions, with every answer, citation and cost inspectable.</p>
        <p className="text-muted">The data is a synthetic company (Northwind) in the exact shape of Granola&apos;s public API; nothing here calls an LLM.</p>
      </div>
      <div className="mt-10 grid gap-3 sm:grid-cols-2">
        <Tile href="/notes" title="Notes" meta={`${notes.length} Northwind meetings${xl ? ` · ${xl} in Northwind-XL` : ""}`}>
          The corpus as a note app: enhanced notes, your notes, and the channel-attributed transcript with
          entity mentions linked.
        </Tile>
        <Tile
          href="/results"
          title="Results"
          meta={reports.length ? `${reports.length} report(s) · newest ${stampLabel(reports[0].stamp)}` : "no reports yet"}
        >
          Leaderboard, cost vs accuracy, paired significance, and every question&apos;s answers side by side
          with failure types and the cited lines.
        </Tile>
        <Tile
          href="/memory"
          title="Memory"
          meta={m ? `${m.facts.length} facts · ${m.commitments.length} commitments · ${m.rejections.length} gate rejections` : "no store built"}
        >
          Browse what the memory knew on any day: an entity&apos;s facts on a timeline with supersession,
          commitments with their history, and what the gate threw out.
        </Tile>
        <Tile href="/brief" title="Brief" meta="as-of SQL over the memory · $0">
          The product feature: before a meeting, open commitments, what changed since last time, and open
          customer requests, each with its source lines.
        </Tile>
      </div>
      <p className="mt-10 text-[12.5px] text-faint">
        Research prototype. Methods and honest caveats: README.md, docs/engineering/DESIGN.md, docs/results/ANALYSIS.md, docs/engineering/WEB_DEMO.md.
      </p>
    </PageShell>
  );
}
