// Small presentational pieces shared by the results explorer, in the note view's visual
// language (pill chips, soft cards, serif titles). See docs/engineering/RESULTS_EXPLORER.md.

import type { Verdict } from "@/lib/results";

export function Chip({
  children,
  tone = "plain",
  title,
}: {
  children: React.ReactNode;
  tone?: "plain" | "warn";
  title?: string;
}) {
  const cls =
    tone === "warn"
      ? "border-warn/30 bg-warn-soft text-warn"
      : "border-line bg-card text-ink shadow-[0_1px_1px_rgba(0,0,0,0.03)]";
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[12.5px] ${cls}`}
    >
      {children}
    </span>
  );
}

export function Section({
  title,
  hint,
  children,
  id,
}: {
  title: string;
  hint?: React.ReactNode;
  children: React.ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className="mt-12 scroll-mt-6">
      <h2 className="text-[17px] font-semibold text-ink">{title}</h2>
      {hint && <p className="mt-1 max-w-[46rem] text-[13.5px] leading-relaxed text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

export function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={`rounded-[20px] border border-line bg-card shadow-[0_1px_2px_rgba(0,0,0,0.03)] ${className}`}
    >
      {children}
    </div>
  );
}

const VERDICT: Record<Verdict, { label: string; dot: string; badge: string }> = {
  correct: { label: "Correct", dot: "bg-accent", badge: "bg-accent-soft text-accent" },
  partially_correct: {
    label: "Partial",
    dot: "bg-them",
    badge: "bg-warn-soft text-warn",
  },
  incorrect: { label: "Wrong", dot: "bg-date-red", badge: "bg-date-red/10 text-date-red" },
};

export function VerdictDot({ verdict, title }: { verdict: Verdict | undefined; title?: string }) {
  if (!verdict) return <span title="no answer" className="inline-block h-3 w-3 rounded-full border border-line" />;
  return (
    <span
      title={title ?? VERDICT[verdict].label}
      className={`inline-block h-3 w-3 rounded-full ${VERDICT[verdict].dot}`}
    />
  );
}

export function VerdictBadge({ verdict }: { verdict: Verdict }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[12px] font-medium ${VERDICT[verdict].badge}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${VERDICT[verdict].dot}`} />
      {VERDICT[verdict].label}
    </span>
  );
}

/** "b0_topk" → "B0 · top-k chunks"; unknown names pass through with underscores as spaces. */
export function systemLabel(name: string): string {
  const known: Record<string, string> = {
    b0_topk: "B0 · top-k chunks",
    b1_summaries: "B1 · summaries",
    b2_full: "B2 · full context",
    b2o_oracle: "B2o · oracle meetings",
    b3_agentic: "B3 · agentic search",
    b4_mem0: "B4 · Mem0",
    b5_graphiti: "B5 · Graphiti",
    concentrate: "Concentrate",
  };
  return known[name] ?? name.replace(/_/g, " ");
}

export function categoryLabel(c: string): string {
  const t = c.replace(/_/g, " ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}
