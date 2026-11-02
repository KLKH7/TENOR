import Link from "next/link";

import { ReportPicker } from "@/components/results/ReportPicker";
import { Chip } from "@/components/results/ui";
import { stampLabel, usd } from "@/lib/explore";
import { listReports, type LoadedReport } from "@/lib/results";

/** Page frame for the explorer: greige canvas, one rounded sheet, provenance chips on top.
 *  Every number below is traceable to the meta shown here (git SHA, model, seed, split). */
export function ResultsShell({
  loaded,
  children,
  aside,
}: {
  loaded: LoadedReport;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  const { meta } = loaded.report;
  const reports = listReports().map((r) => ({ ...r, label: stampLabel(r.stamp) }));
  const prompts = Object.entries(meta.prompts)
    .map(([k, v]) => `${k}: ${v.join(", ")}`)
    .join("\n");
  return (
    <div className="flex min-h-screen gap-3 p-4 max-lg:p-2">
      <article className="min-w-0 flex-1 rounded-[32px] bg-sheet shadow-[0_1px_3px_rgba(0,0,0,0.04)]">
        <div className="mx-auto w-full max-w-[72rem] px-10 pb-20 pt-10 max-sm:px-5">
          <nav className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-4 text-[13px] text-muted">
              <Link href={`/results/${loaded.stamp}`} className="font-medium text-ink">
                Results explorer
              </Link>
              <Link href="/" className="hover:text-ink">
                Notes
              </Link>
            </div>
            <ReportPicker reports={reports} current={loaded.stamp} />
          </nav>
          <div className="mt-4 flex flex-wrap items-center gap-1.5">
            <Chip title="Report stamp (UTC)">{stampLabel(loaded.stamp)}</Chip>
            <Chip title="Question split">
              {meta.split} split · {meta.questions} questions
            </Chip>
            <Chip title="Answer model">{meta.model}</Chip>
            <Chip
              tone={meta.git.dirty ? "warn" : "plain"}
              title={meta.git.dirty ? "Run from a working tree with uncommitted changes" : "Git SHA"}
            >
              git {meta.git.sha}
              {meta.git.dirty && " · dirty"}
            </Chip>
            <Chip title="Seed">seed {meta.seed}</Chip>
            <Chip title={prompts}>prompts</Chip>
            <Chip title={`Budget ${usd(meta.budget_usd)}`}>spent {usd(meta.spent_usd)}</Chip>
            {loaded.fixtures ? (
              <>
                <Chip
                  tone={loaded.fixtures.inferred ? "warn" : "plain"}
                  title={`${
                    loaded.fixtures.inferred
                      ? "The report does not record its fixture dir; matched by question text. "
                      : ""
                  }${
                    loaded.fixtures.set.sha
                      ? "Read from git at the report's commit, so transcripts match what the systems saw."
                      : "Read from the working tree."
                  }`}
                >
                  {loaded.fixtures.label}
                  {loaded.fixtures.inferred && " · inferred"}
                </Chip>
                {loaded.fixtures.drift.size ? (
                  <Chip
                    tone="warn"
                    title="Recomputed citation precision/recall from these gold lines differs from the stored values: the fixtures changed since the run, so highlighted lines may be off."
                  >
                    {loaded.fixtures.drift.size}/{loaded.fixtures.checked} answers don&apos;t reproduce
                  </Chip>
                ) : (
                  <Chip title="Every stored citation precision/recall was recomputed from these gold lines and matches">
                    ✓ citation scores reproduce ({loaded.fixtures.checked}/{loaded.fixtures.checked})
                  </Chip>
                )}
              </>
            ) : (
              <Chip tone="warn">fixtures not found</Chip>
            )}
            {meta.stopped_early && <Chip tone="warn">stopped early: {meta.stopped_early}</Chip>}
          </div>
          {children}
        </div>
      </article>
      {aside}
    </div>
  );
}
