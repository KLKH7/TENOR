import { notFound } from "next/navigation";

import { CategoryMatrix, CostScatter, Leaderboard } from "@/components/results/Overview";
import { QuestionList } from "@/components/results/QuestionList";
import { ResultsShell } from "@/components/results/Shell";
import { Significance } from "@/components/results/Significance";
import { Card, Section } from "@/components/results/ui";
import { failureTypes, parseFilters, type QuestionItem, sortCategories } from "@/lib/explore";
import { loadReport, rowsByQuestion, systemsOf } from "@/lib/results";

export const dynamic = "force-dynamic";

type Params = Record<string, string | string[] | undefined>;

export default async function ReportPage({
  params,
  searchParams,
}: {
  params: Promise<{ stamp: string }>;
  searchParams: Promise<Params>;
}) {
  const { stamp } = await params;
  const loaded = loadReport(stamp);
  if (!loaded) notFound();
  const { metrics, meta } = loaded.report;
  const systems = systemsOf(loaded);
  const filters = parseFilters(await searchParams, systems);
  const base = `/results/${stamp}`;

  const grouped = rowsByQuestion(loaded.rows);
  const items: QuestionItem[] = [...grouped.entries()]
    .map(([qid, bySystem]) => {
      const any = [...bySystem.values()][0];
      return { qid, category: any.category, question: any.question, bySystem };
    })
    .sort((a, b) => a.qid.localeCompare(b.qid));
  const categories = sortCategories(items.map((i) => i.category));
  const minN = Math.min(...systems.map((s) => metrics[s].n));

  return (
    <ResultsShell loaded={loaded}>
      <h1 className="mt-8 font-serif text-[2.75rem] leading-[1.08] tracking-[-0.01em] text-ink">
        {systems.length} systems, {meta.questions} questions
      </h1>
      <p className="mt-3 max-w-[44rem] text-[15px] leading-relaxed text-muted">
        Every number below links to the questions behind it, and every question shows each
        system&apos;s answer, the judge&apos;s verdict and the transcript lines it cited.
      </p>
      {minN < 30 && (
        <div className="mt-5 max-w-[44rem] rounded-2xl bg-warn-soft px-4 py-3 text-[13.5px] leading-relaxed text-warn">
          <strong className="font-semibold">Small sample (n = {minN}).</strong> One question is{" "}
          {Math.round(100 / minN)} points of accuracy, and the 95% intervals overlap. Read this as a
          smoke test, not a ranking.
        </div>
      )}

      <Section
        title="Leaderboard"
        hint="Accuracy counts only fully correct verdicts; the whisker is a seeded bootstrap 95% CI. Citation precision/recall are over transcript lines vs the gold evidence lines. Click a system to see its questions."
      >
        <Leaderboard metrics={metrics} ingest={meta.ingest} base={base} />
      </Section>

      <Section title="Cost vs accuracy">
        <CostScatter metrics={metrics} />
      </Section>

      <Section
        title="By category"
        hint="Accuracy per question category. Cells show correct / n; with one or two questions per cell, a single answer flips the colour. Click a cell to list those questions."
      >
        <CategoryMatrix metrics={metrics} categories={categories} base={base} />
      </Section>

      {loaded.analysis && (
        <Section
          id="significance"
          title="Is the ranking real?"
          hint={
            <>
              From <code>concentrate analyze</code> ({stamp}.analysis.json): paired tests, power and the
              cost–accuracy frontier. See docs/results/ANALYSIS.md.
            </>
          }
        >
          <Significance analysis={loaded.analysis} />
        </Section>
      )}

      <Section
        id="questions"
        title="Questions"
        hint="Dots are verdicts: green correct, orange partial, red wrong. Open a question to compare answers and follow citations into the transcript. Failure types come from concentrate analyze."
      >
        <QuestionList
          items={items}
          filters={filters}
          systems={systems}
          categories={categories}
          base={base}
          failureTypes={failureTypes(loaded.rows)}
        />
      </Section>

      {loaded.judgechecks.length > 0 && (
        <Section
          title="Judge checks"
          hint="The same answers re-judged by a second model. Low agreement would mean the leaderboard measures the judge as much as the systems."
        >
          {loaded.judgechecks.map((j) => (
            <Card key={j.model} className="mb-3 p-5">
              <details>
                <summary className="cursor-pointer text-[13.5px] font-medium text-ink">
                  Second judge: {j.model}
                  <span className="ml-2 font-normal text-muted">
                    {j.markdown.match(/\*\*Agreement on correct \/ not correct:\*\* ([^\n]+)/)?.[1]}
                  </span>
                </summary>
                <pre className="mt-4 whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-ink">
                  {j.markdown}
                </pre>
              </details>
            </Card>
          ))}
        </Section>
      )}
    </ResultsShell>
  );
}
