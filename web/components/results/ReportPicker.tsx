"use client";

import { useRouter } from "next/navigation";

import type { ReportSummary } from "@/lib/results";

/** Choose which eval report to explore; keeps the current sub-path (overview or question). */
export function ReportPicker({
  reports,
  current,
}: {
  reports: (ReportSummary & { label: string })[];
  current: string;
}) {
  const router = useRouter();
  return (
    <label className="inline-flex items-center gap-2 rounded-full border border-line bg-card py-1 pl-3.5 pr-2 text-[12.5px] text-muted shadow-[0_1px_1px_rgba(0,0,0,0.03)]">
      Report
      <select
        value={current}
        onChange={(e) => router.push(`/results/${e.target.value}`)}
        className="cursor-pointer rounded-full bg-transparent pr-1 text-[12.5px] font-medium text-ink outline-none"
      >
        {reports.map((r) => (
          <option key={r.stamp} value={r.stamp}>
            {r.label} · {r.split} · {r.questions} q · {r.systems.length} systems
          </option>
        ))}
      </select>
    </label>
  );
}
