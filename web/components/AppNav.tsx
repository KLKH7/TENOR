import Link from "next/link";

export type Section = "home" | "notes" | "results" | "memory" | "brief";

const ITEMS: { key: Section; label: string; href: string }[] = [
  { key: "home", label: "Overview", href: "/" },
  { key: "notes", label: "Notes", href: "/notes" },
  { key: "results", label: "Results", href: "/results" },
  { key: "memory", label: "Memory", href: "/memory" },
  { key: "brief", label: "Brief", href: "/brief" },
];

/** The demo's top-level sections as a pill row (same segmented style as the note view). */
export function AppNav({ current, className = "" }: { current: Section; className?: string }) {
  return (
    <nav aria-label="Sections" className={`flex flex-wrap gap-0.5 rounded-full bg-canvas p-0.5 ${className}`}>
      {ITEMS.map((i) => (
        <Link
          key={i.key}
          href={i.href}
          aria-current={i.key === current ? "page" : undefined}
          className={`rounded-full px-3 py-1 text-[12.5px] transition-colors ${
            i.key === current
              ? "bg-card font-medium text-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]"
              : "text-muted hover:text-ink"
          }`}
        >
          {i.label}
        </Link>
      ))}
    </nav>
  );
}

/** Frame for the non-note views: greige canvas, one rounded sheet, the section nav on top. */
export function PageShell({
  current,
  children,
  right,
  wide = false,
}: {
  current: Section;
  children: React.ReactNode;
  right?: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <div className="min-h-screen p-4 max-lg:p-2">
      <article className="min-h-[calc(100vh-2rem)] rounded-[32px] bg-sheet shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
        <div className={`mx-auto w-full ${wide ? "max-w-[72rem]" : "max-w-[52rem]"} px-10 pb-20 pt-8 max-sm:px-4`}>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <AppNav current={current} />
            {right}
          </div>
          {children}
        </div>
      </article>
    </div>
  );
}
