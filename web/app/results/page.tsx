import { listReports } from "@/lib/results";
import { redirect } from "next/navigation";

export const dynamic = "force-dynamic";

/** /results → the newest report. */
export default function ResultsIndex() {
  const [latest] = listReports();
  if (!latest) {
    return (
      <main className="mx-auto max-w-xl p-16 text-[15px] text-muted">
        No eval reports found in <code>evals/reports/</code>. Run <code>tenor eval</code> first.
      </main>
    );
  }
  redirect(`/results/${latest.stamp}`);
}
