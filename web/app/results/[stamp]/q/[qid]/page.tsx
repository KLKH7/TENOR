import Link from "next/link";
import { notFound } from "next/navigation";

import { EvidencePanel, lineId, MARK_STYLE, type PanelMark, speakerOf } from "@/components/results/EvidencePanel";
import { ResultsShell } from "@/components/results/Shell";
import { Card, categoryLabel, Chip, systemLabel, VerdictBadge } from "@/components/results/ui";
import {
  applyFilters,
  classifyLines,
  cleanCitation,
  failureLabel,
  filterQuery,
  parseFilters,
  pct,
  type QuestionItem,
  secs,
  tokens,
  usd,
} from "@/lib/explore";
import {
  loadFixtureNote,
  loadReport,
  parseCitation,
  type ResultRow,
  rowsByQuestion,
  systemsOf,
} from "@/lib/results";
import type { Note } from "@/lib/types";

export const dynamic = "force-dynamic";

type Params = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? null;

export default async function QuestionPage({
  params,
  searchParams,
}: {
  params: Promise<{ stamp: string; qid: string }>;
  searchParams: Promise<Params>;
}) {
  const { stamp, qid } = await params;
  const loaded = loadReport(stamp);
  if (!loaded) notFound();
  const sp = await searchParams;
  const systems = systemsOf(loaded);
  const filters = parseFilters(sp, systems);
  const grouped = rowsByQuestion(loaded.rows);
  const bySystem = grouped.get(qid);
  if (!bySystem) notFound();
  const first = [...bySystem.values()][0];
  const q = loaded.questions.get(qid);
  const gold = q?.gold_utterance_ids ?? [];
  const goldNotes = [...new Set(gold.map((g) => parseCitation(g).note))];
  const fq = filterQuery(filters);
  const base = `/results/${stamp}`;
  const here = `${base}/q/${qid}`;

  // Prev / next within the current filter, so you can walk e.g. every "everyone wrong" case.
  const items: QuestionItem[] = [...grouped.entries()]
    .map(([id, m]) => ({ qid: id, category: [...m.values()][0].category, question: "", bySystem: m }))
    .sort((a, b) => a.qid.localeCompare(b.qid));
  const walk = applyFilters(items, filters, systems).map((i) => i.qid);
  const pos = walk.indexOf(qid);
  const prev = pos > 0 ? walk[pos - 1] : null;
  const next = pos >= 0 && pos < walk.length - 1 ? walk[pos + 1] : null;

  // Transcript panel state lives in the URL: ?cite=<line or note id>&sys=<system>.
  const cite = one(sp.cite);
  const activeSys = one(sp.sys_panel);
  const activeRow = activeSys ? bySystem.get(activeSys) : undefined;
  const withPanel = (c: string | null, s: string | null) => {
    const p = new URLSearchParams(fq.slice(1));
    if (c) p.set("cite", c);
    if (c && s) p.set("sys_panel", s);
    const str = p.toString();
    return `${here}${str ? `?${str}` : ""}`;
  };
  const notes = new Map<string, Note | undefined>();
  const note = (id: string) => {
    if (!notes.has(id)) notes.set(id, loadFixtureNote(loaded.fixtures, id));
    return notes.get(id);
  };
  const lineText = (id: string) => {
    const { note: n, line } = parseCitation(id);
    const item = line === null ? undefined : note(n)?.transcript?.[line];
    return item ? { text: item.text, who: speakerOf(item.speaker).label } : null;
  };

  let panel = null;
  if (cite) {
    const { note: noteId, line } = parseCitation(cleanCitation(cite));
    const marks = new Map<string, PanelMark>();
    if (activeRow) for (const [k, v] of classifyLines(gold, activeRow.answer.citations)) marks.set(k, v);
    else for (const g of gold) marks.set(g, "gold");
    panel = (
      <EvidencePanel
        note={note(noteId)}
        marks={marks}
        target={line === null ? (gold.find((g) => g.startsWith(`${noteId}:`)) ?? null) : lineId(noteId, line)}
        meetingLevel={line === null}
        systems={systems.filter((s) => bySystem.has(s))}
        activeSystem={activeRow ? activeSys : null}
        hrefFor={(s) => withPanel(cite, s)}
        closeHref={withPanel(null, null)}
      />
    );
  }

  return (
    <ResultsShell loaded={loaded} aside={panel}>
      <div className="mt-8 flex flex-wrap items-center justify-between gap-2 text-[13px]">
        <Link href={`${base}${fq}#questions`} className="text-muted hover:text-ink">
          ← All questions{fq && " (filtered)"}
        </Link>
        <div className="flex items-center gap-3 text-muted">
          {pos >= 0 && (
            <span className="tabular-nums">
              {pos + 1} / {walk.length}
            </span>
          )}
          {prev ? (
            <Link href={`${base}/q/${prev}${fq}`} className="hover:text-ink">
              ← {prev}
            </Link>
          ) : (
            <span className="text-faint">← prev</span>
          )}
          {next ? (
            <Link href={`${base}/q/${next}${fq}`} className="hover:text-ink">
              {next} →
            </Link>
          ) : (
            <span className="text-faint">next →</span>
          )}
        </div>
      </div>

      <h1 className="mt-5 max-w-[48rem] font-serif text-[2.1rem] leading-[1.15] tracking-[-0.01em] text-ink">
        {first.question}
      </h1>
      <div className="mt-4 flex flex-wrap gap-1.5">
        <Chip>{qid}</Chip>
        <Chip>
          {categoryLabel(first.category)}
        </Chip>
        {q && <Chip title="The question is asked as of this date">as of {q.as_of}</Chip>}
        {q && <Chip>{q.split} split</Chip>}
        {first.unanswerable && <Chip tone="warn">unanswerable: abstaining is correct</Chip>}
        {!q && <Chip tone="warn">gold question not found in fixtures</Chip>}
      </div>

      <Card className="mt-6 p-5">
        <div className="text-[11.5px] font-medium uppercase tracking-[0.04em] text-muted">Gold answer</div>
        <p className="mt-1.5 text-[16px] font-medium text-ink">{first.gold}</p>
        {q && q.answer_norm.length > 0 && (
          <p className="mt-1 text-[12px] text-muted">
            exact-match keys: {q.answer_norm.map((k) => `“${k}”`).join(", ")}
          </p>
        )}
        {gold.length > 0 && (
          <div className="mt-4">
            <div className="text-[11.5px] font-medium uppercase tracking-[0.04em] text-muted">
              Gold evidence · {gold.length} line{gold.length > 1 ? "s" : ""} in {goldNotes.length} meeting
              {goldNotes.length > 1 ? "s" : ""}
            </div>
            <ul className="mt-2 space-y-1.5">
              {gold.map((g) => {
                const t = lineText(g);
                const n = note(parseCitation(g).note);
                return (
                  <li key={g}>
                    <Link
                      href={withPanel(g, null)}
                      scroll={false}
                      className="group flex gap-3 rounded-xl px-2 py-1.5 hover:bg-sheet"
                    >
                      <span className="shrink-0 pt-px font-mono text-[11.5px] text-faint">{g.split(":")[1]}</span>
                      <span className="text-[14px] leading-snug">
                        {t ? (
                          <>
                            <span className="font-semibold text-ink">{t.who}: </span>
                            <span className="text-ink/80">{t.text}</span>
                          </>
                        ) : (
                          <span className="text-muted">{g}</span>
                        )}
                        <span className="ml-2 text-[12px] text-muted group-hover:text-accent">
                          {n?.title ?? parseCitation(g).note} →
                        </span>
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </Card>

      <div className={`mt-6 grid gap-4 ${panel ? "2xl:grid-cols-2" : "lg:grid-cols-2"}`}>
        {systems
          .filter((s) => bySystem.has(s))
          .map((s) => (
            <AnswerCard
              key={s}
              row={bySystem.get(s)!}
              gold={gold}
              goldNotes={goldNotes}
              citeHref={(c) => withPanel(c, s)}
              activeCite={activeSys === s ? cite : null}
              drift={loaded.fixtures?.drift.has(`${s}/${qid}`) ?? false}
            />
          ))}
      </div>
    </ResultsShell>
  );
}

function Stat({ label, value, title }: { label: string; value: string; title?: string }) {
  return (
    <div title={title}>
      <div className="text-[10.5px] uppercase tracking-[0.05em] text-faint">{label}</div>
      <div className="text-[13px] tabular-nums text-ink">{value}</div>
    </div>
  );
}

function AnswerCard({
  row,
  gold,
  goldNotes,
  citeHref,
  activeCite,
  drift,
}: {
  row: ResultRow;
  drift: boolean;
  gold: string[];
  goldNotes: string[];
  citeHref: (c: string) => string;
  activeCite: string | null;
}) {
  const marks = classifyLines(gold, row.answer.citations);
  const missed = gold.filter((g) => marks.get(g) === "missed");
  const retrievedNotes = new Set(row.retrieved);
  const goldRetrieved = goldNotes.filter((n) => retrievedNotes.has(n)).length;
  return (
    <Card className="flex flex-col p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[14.5px] font-semibold text-ink">{systemLabel(row.system)}</div>
          <div className="text-[11.5px] text-faint">{row.system}</div>
        </div>
        <div className="flex items-center gap-2">
          {row.abstained && (
            <span className="rounded-full bg-canvas px-2 py-0.5 text-[11.5px] text-muted">abstained</span>
          )}
          <VerdictBadge verdict={row.verdict} />
        </div>
      </div>

      <p className="mt-3 flex-1 whitespace-pre-wrap text-[14.5px] leading-[1.6] text-ink">{row.answer.answer}</p>
      {row.failure && (
        <div className="mt-3 rounded-xl border border-date-red/20 bg-date-red/5 px-3 py-2 text-[12.5px] leading-relaxed">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[10.5px] uppercase tracking-[0.05em] text-faint">Failure type</span>
            <span className="rounded-full bg-date-red/10 px-2 py-0.5 font-medium text-date-red">
              {failureLabel(row.failure.primary)}
            </span>
            {row.failure.tags
              .filter((t) => t.type !== row.failure!.primary)
              .map((t) => (
                <span key={t.type} className="rounded-full bg-canvas px-2 py-0.5 text-muted" title={t.detail}>
                  also {failureLabel(t.type).toLowerCase()}
                </span>
              ))}
          </div>
          {row.failure.tags.find((t) => t.type === row.failure!.primary)?.detail && (
            <p className="mt-1 text-muted">{row.failure.tags.find((t) => t.type === row.failure!.primary)!.detail}</p>
          )}
          {row.failure.llm_type && (
            <p className="mt-1 text-muted">
              <span className="font-medium text-ink">LLM classifier: {failureLabel(row.failure.llm_type)}. </span>
              {row.failure.llm_reason}
            </p>
          )}
        </div>
      )}
      {row.judge_reason && (
        <p className="mt-3 rounded-xl bg-sheet px-3 py-2 text-[12.5px] leading-relaxed text-muted">
          <span className="font-medium text-ink">Judge: </span>
          {row.judge_reason}
        </p>
      )}

      <div className="mt-4">
        {drift && (
          <p className="mb-2 rounded-xl bg-warn-soft px-3 py-2 text-[12px] text-warn">
            These gold lines don&apos;t reproduce this answer&apos;s stored citation scores: the
            fixtures changed after the run, so the colours below may be wrong.
          </p>
        )}
        <div className="text-[10.5px] uppercase tracking-[0.05em] text-faint">
          Citations · {row.answer.citations.length}
        </div>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {row.answer.citations.map((c) => {
            const clean = cleanCitation(c);
            const line = parseCitation(clean).line;
            const mark = marks.get(clean);
            const style = mark
              ? MARK_STYLE[mark].chip
              : "border-line bg-card text-muted"; /* note-level citation: no line to score */
            return (
              <Link
                key={c}
                href={citeHref(clean)}
                scroll={false}
                title={mark ? MARK_STYLE[mark].label : "whole-meeting citation (not line-level, scores 0 recall)"}
                className={`rounded-full border px-2 py-0.5 font-mono text-[11px] hover:brightness-95 ${style} ${
                  activeCite === clean ? "ring-2 ring-ink/60" : ""
                }`}
              >
                {line === null ? `${clean.slice(0, 10)}… (meeting)` : `${clean.slice(4, 9)}…:${clean.split(":")[1]}`}
              </Link>
            );
          })}
          {missed.map((g) => (
            <Link
              key={`m-${g}`}
              href={citeHref(g)}
              scroll={false}
              title="gold evidence this system did not cite"
              className={`rounded-full border px-2 py-0.5 font-mono text-[11px] ${MARK_STYLE.missed.chip} ${
                activeCite === g ? "ring-2 ring-ink/60" : ""
              }`}
            >
              missed {g.slice(4, 9)}…:{g.split(":")[1]}
            </Link>
          ))}
          {!row.answer.citations.length && !missed.length && (
            <span className="text-[12.5px] text-faint">none</span>
          )}
        </div>
      </div>

      <div className="mt-4 grid grid-cols-4 gap-3 border-t border-hairline pt-3 max-sm:grid-cols-2">
        <Stat label="Cost" value={usd(row.cost_usd)} />
        <Stat
          label="Tokens in · out"
          value={`${tokens(row.input_tokens)} · ${tokens(row.output_tokens)}`}
        />
        <Stat
          label="Latency"
          value={row.latency_measured === false ? "batched" : secs(row.latency_s)}
          title={row.latency_measured === false ? "Served from a Message Batch: not a latency measurement" : undefined}
        />
        <Stat label="Tool calls" value={String(row.tool_calls)} />
        <Stat label="Exact match" value={pct(row.exact_match)} />
        <Stat label="Cite P · R" value={`${pct(row.cite_precision)} · ${pct(row.cite_recall)}`} />
        {row.retrieved.length > 0 && goldNotes.length > 0 && (
          <Stat
            label="Gold meeting seen"
            value={`${goldRetrieved}/${goldNotes.length}`}
            title={`${retrievedNotes.size} distinct meetings retrieved; did they include the meeting(s) holding the gold evidence?`}
          />
        )}
      </div>
    </Card>
  );
}
