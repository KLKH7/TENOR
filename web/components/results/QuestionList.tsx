import Link from "next/link";

import { Card, categoryLabel, systemLabel, VerdictDot } from "@/components/results/ui";
import {
  applyFilters,
  failureLabel,
  type Filters,
  filterQuery,
  type QuestionItem,
} from "@/lib/explore";

function Pill({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      scroll={false}
      aria-pressed={active}
      className={`rounded-full border px-3 py-1 text-[12.5px] transition-colors ${
        active
          ? "border-ink bg-ink text-white"
          : "border-line bg-card text-ink hover:border-ink/40"
      }`}
    >
      {children}
    </Link>
  );
}

export function QuestionList({
  items,
  filters,
  systems,
  categories,
  base,
  failureTypes = [],
}: {
  items: QuestionItem[];
  filters: Filters;
  systems: string[];
  categories: string[];
  base: string;
  /** Primary failure types from `concentrate analyze`; empty when the report has none. */
  failureTypes?: string[];
}) {
  const shown = applyFilters(items, filters, systems);
  const cols = filters.systems.length ? filters.systems : systems;
  const link = (f: Partial<Filters>) => `${base}${filterQuery({ ...filters, ...f })}#questions`;
  const toggleSys = (s: string) => {
    const on = filters.systems.includes(s);
    return link({ systems: on ? filters.systems.filter((x) => x !== s) : [...filters.systems, s] });
  };
  const q = filterQuery(filters);

  return (
    <div>
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 text-[12px] text-muted">Category</span>
          <Pill href={link({ category: null })} active={!filters.category}>
            All
          </Pill>
          {categories.map((c) => (
            <Pill key={c} href={link({ category: c })} active={filters.category === c}>
              {categoryLabel(c)}
            </Pill>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 text-[12px] text-muted">Systems</span>
          <Pill href={link({ systems: [] })} active={!filters.systems.length}>
            All
          </Pill>
          {systems.map((s) => (
            <Pill key={s} href={toggleSys(s)} active={filters.systems.includes(s)}>
              {systemLabel(s)}
            </Pill>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="w-20 text-[12px] text-muted">Show</span>
          <Pill href={link({ disagree: !filters.disagree })} active={filters.disagree}>
            Systems disagree
          </Pill>
          <Pill href={link({ wrong: !filters.wrong })} active={filters.wrong}>
            Everyone wrong
          </Pill>
        </div>
        {failureTypes.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="w-20 text-[12px] text-muted">Failure</span>
            <Pill href={link({ failure: null })} active={!filters.failure}>
              Any
            </Pill>
            {failureTypes.map((t) => (
              <Pill key={t} href={link({ failure: t })} active={filters.failure === t}>
                {failureLabel(t)}
              </Pill>
            ))}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[12.5px] text-muted">
            {shown.length} of {items.length} questions
          </span>
        </div>
      </div>

      <Card className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[44rem] border-collapse">
          <thead className="border-b border-hairline">
            <tr>
              <th className="px-3 py-2.5 text-left text-[11.5px] font-medium uppercase tracking-[0.04em] text-muted">
                Question
              </th>
              {cols.map((s) => (
                <th
                  key={s}
                  title={systemLabel(s)}
                  className="w-16 px-1 py-2.5 text-center text-[11.5px] font-medium text-muted"
                >
                  {systemLabel(s).split(" · ")[0]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {shown.map((item) => (
              <tr key={item.qid} className="group hover:bg-sheet/60">
                <td className="px-3 py-2.5">
                  <Link href={`${base}/q/${item.qid}${q}`} className="block">
                    <span className="mr-2 text-[11.5px] tabular-nums text-faint">{item.qid}</span>
                    <span className="mr-2 rounded-full bg-canvas px-2 py-0.5 text-[11px] text-muted">
                      {categoryLabel(item.category)}
                    </span>
                    <span className="text-[13.5px] text-ink group-hover:text-accent">{item.question}</span>
                  </Link>
                </td>
                {cols.map((s) => {
                  const r = item.bySystem.get(s);
                  return (
                    <td key={s} className="px-1 py-2.5 text-center">
                      <VerdictDot
                        verdict={r?.verdict}
                        title={
                          r
                            ? `${systemLabel(s)}: ${r.verdict}${r.failure ? ` (${failureLabel(r.failure.primary).toLowerCase()})` : ""}`
                            : undefined
                        }
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
            {!shown.length && (
              <tr>
                <td colSpan={cols.length + 1} className="px-3 py-8 text-center text-[13.5px] text-muted">
                  No questions match these filters.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
