import Link from "next/link";

import { PageShell } from "@/components/AppNav";
import { AsOfSlider } from "@/components/memory/AsOfSlider";
import { dayLabel, Lines } from "@/components/memory/Lines";
import { Card, Chip } from "@/components/results/ui";
import {
  type CommitmentRow,
  commitmentStatus,
  type FactRow,
  type FactState,
  factLines,
  factState,
} from "@/lib/asof";
import { loadMemory, type Memory, nameOf } from "@/lib/memory";

export const dynamic = "force-dynamic";

type Params = Record<string, string | string[] | undefined>;
type Tab = "facts" | "commitments" | "rejections";
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? null;
const DAY_RE = /^\d{4}-\d{2}-\d{2}$/;

const STATE: Record<FactState, { label: string; badge: string; bar: string }> = {
  current: { label: "true on this day", badge: "bg-accent-soft text-accent", bar: "bg-accent" },
  superseded: { label: "superseded", badge: "bg-canvas text-muted", bar: "bg-faint" },
  scheduled: { label: "known, not yet valid", badge: "bg-warn-soft text-warn", bar: "bg-them" },
  unknown: { label: "not learnt yet", badge: "bg-canvas text-faint", bar: "bg-line" },
};

const STATUS: Record<string, string> = {
  open: "bg-canvas text-ink",
  no_follow_up: "bg-warn-soft text-warn",
  slipped: "bg-warn-soft text-warn",
  reassigned: "bg-canvas text-ink",
  done: "bg-accent-soft text-accent",
  dropped: "bg-canvas text-muted",
};

function q(p: Record<string, string | null>): string {
  const s = new URLSearchParams();
  for (const [k, v] of Object.entries(p)) if (v) s.set(k, v);
  return `/memory?${s.toString()}`;
}

function Pill({ href, active, children }: { href: string; active: boolean; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      scroll={false}
      aria-current={active ? "page" : undefined}
      className={`rounded-full px-3 py-1 text-[12.5px] transition-colors ${
        active ? "bg-card font-medium text-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]" : "text-muted hover:text-ink"
      }`}
    >
      {children}
    </Link>
  );
}

export default async function MemoryPage({ searchParams }: { searchParams: Promise<Params> }) {
  const m = loadMemory();
  if (!m) {
    return (
      <PageShell current="memory">
        <h1 className="mt-8 font-serif text-[2.75rem] leading-[1.08] text-ink">Memory</h1>
        <p className="mt-4 text-[15px] text-muted">
          No finished memory store under <code>data/cache/concentrate/*/*/</code>. Build one with{" "}
          <code>tenor eval --xl --systems c</code> (or symlink <code>data/cache</code> from the main checkout).
        </p>
      </PageShell>
    );
  }
  const sp = await searchParams;
  const days = [...m.meetings.values()].map((x) => x.day).sort();
  const [min, max] = [days[0], days[days.length - 1]];
  const asOfRaw = one(sp.as_of);
  const asOf = asOfRaw && DAY_RE.test(asOfRaw) ? (asOfRaw < min ? min : asOfRaw > max ? max : asOfRaw) : max;
  const tab = (["facts", "commitments", "rejections"].includes(one(sp.tab) ?? "") ? one(sp.tab) : "facts") as Tab;

  const subjects = [...new Set(m.facts.map((f) => f.subject))];
  const known = (s: string) => m.facts.filter((f) => f.subject === s && factState(f, asOf) !== "unknown").length;
  const kindRank: Record<string, number> = { project: 0, org: 1, person: 2 };
  subjects.sort(
    (a, b) =>
      (kindRank[m.kinds.get(a) ?? ""] ?? 3) - (kindRank[m.kinds.get(b) ?? ""] ?? 3) ||
      nameOf(m, a).localeCompare(nameOf(m, b)),
  );
  const entity = subjects.includes(one(sp.entity) ?? "") ? one(sp.entity)! : subjects.includes("teams") ? "teams" : subjects[0];
  const base = { as_of: asOf === max ? null : asOf, entity, tab };
  const kept = m.complete.detail.kept ?? {};
  const rejected = m.complete.detail.rejected ?? {};
  const cfg = (m.complete.detail.config ?? {}) as { ingest_model?: string; prompts?: Record<string, string> };

  return (
    <PageShell current="memory" wide>
      <h1 className="mt-8 font-serif text-[2.75rem] leading-[1.08] tracking-[-0.01em] text-ink">
        What the memory knew, and when
      </h1>
      <p className="mt-3 max-w-[46rem] text-[15px] leading-relaxed text-muted">
        Concentrate reads each meeting once and writes facts, commitments and their changes into a
        bitemporal store: when something was true in the world (<em>valid from / to</em>) and when the
        memory learnt it (<em>recorded</em>). Drag the date to replay hindsight: each view shows only
        what had been heard by that day. Every item links to the transcript lines it rests on.
      </p>
      <div className="mt-4 flex flex-wrap gap-1.5">
        <Chip title={`${m.store}: the newest build with complete.json under data/cache/concentrate (same rule as \`tenor brief\`)`}>
          store {m.store.split("/").slice(-2).join("/")}
        </Chip>
        <Chip>{m.meetings.size} meetings (Northwind-XL)</Chip>
        {cfg.ingest_model && <Chip title={JSON.stringify(cfg.prompts ?? {})}>built with {cfg.ingest_model}</Chip>}
        <Chip>ingest ${m.complete.cost_usd.toFixed(2)}</Chip>
        <Chip title={JSON.stringify(kept)}>
          {m.facts.length} fact rows · {m.commitments.length} commitments
        </Chip>
        <Chip title={JSON.stringify(rejected)}>{m.rejections.length} rejected by the gate</Chip>
        {m.missingNotes > 0 && (
          <Chip tone="warn">{m.missingNotes} store meetings not in the XL fixtures: line links will 404</Chip>
        )}
      </div>

      <div className="mt-6">
        <AsOfSlider min={min} max={max} value={asOf} meetingDays={days} />
      </div>

      <div className="mt-6 inline-flex flex-wrap gap-0.5 rounded-full bg-canvas p-0.5">
        <Pill href={q({ ...base, tab: "facts" })} active={tab === "facts"}>
          Facts by entity
        </Pill>
        <Pill href={q({ ...base, tab: "commitments" })} active={tab === "commitments"}>
          Commitments
        </Pill>
        <Pill href={q({ ...base, tab: "rejections" })} active={tab === "rejections"}>
          Gate rejections
        </Pill>
      </div>

      {tab === "facts" && (
        <div className="mt-5 grid gap-5 lg:grid-cols-[15rem_minmax(0,1fr)]">
          <nav
            aria-label="Entities"
            className="flex max-h-[34rem] flex-col overflow-y-auto rounded-[20px] border border-line bg-card p-2 max-lg:max-h-56"
          >
            {subjects.map((s) => {
              const n = known(s);
              return (
                <Link
                  key={s}
                  href={q({ ...base, entity: s })}
                  scroll={false}
                  aria-current={s === entity ? "page" : undefined}
                  className={`flex items-center justify-between gap-2 rounded-xl px-3 py-1.5 text-[13.5px] ${
                    s === entity ? "bg-sheet font-medium text-ink" : n ? "text-ink hover:bg-sheet" : "text-faint hover:bg-sheet"
                  }`}
                >
                  <span className="truncate">{nameOf(m, s)}</span>
                  <span className="shrink-0 text-[11.5px] tabular-nums text-muted">{n}</span>
                </Link>
              );
            })}
          </nav>
          <EntityFacts m={m} entity={entity} asOf={asOf} min={min} max={max} base={base} />
        </div>
      )}
      {tab === "commitments" && <Commitments m={m} asOf={asOf} status={one(sp.status)} base={base} />}
      {tab === "rejections" && <Rejections m={m} asOf={asOf} />}
    </PageShell>
  );
}

function EntityFacts({
  m,
  entity,
  asOf,
  min,
  max,
  base,
}: {
  m: Memory;
  entity: string;
  asOf: string;
  min: string;
  max: string;
  base: Record<string, string | null>;
}) {
  const mine = m.facts.filter((f) => f.subject === entity);
  const byId = new Map(m.facts.map((f) => [f.id, f]));
  const preds = [...new Set(mine.map((f) => f.predicate))];
  const t0 = Date.parse(`${min}T00:00:00Z`);
  const span = Date.parse(`${max}T00:00:00Z`) - t0 + 86_400_000;
  const x = (day: string) => `${((Date.parse(`${day.slice(0, 10)}T00:00:00Z`) - t0) / span) * 100}%`;
  const hidden = mine.filter((f) => factState(f, asOf) === "unknown").length;

  return (
    <div className="min-w-0">
      <h2 className="font-serif text-[1.9rem] leading-tight text-ink">{nameOf(m, entity)}</h2>
      <p className="mt-1 text-[13px] text-muted">
        {m.kinds.get(entity) ?? "topic"} · {mine.length - hidden} fact{mine.length - hidden === 1 ? "" : "s"} known on{" "}
        {dayLabel(asOf)}
        {hidden > 0 && <> · {hidden} learnt later (hidden)</>}
      </p>
      {preds.map((p) => {
        const versions = mine.filter((f) => f.predicate === p);
        const visible = versions.filter((f) => factState(f, asOf) !== "unknown");
        if (!visible.length) return null;
        return (
          <Card key={p} className="mt-4 p-4">
            <div className="flex items-baseline justify-between gap-2">
              <h3 className="text-[14px] font-semibold text-ink">{p.replace(/_/g, " ")}</h3>
              <span className="text-[11.5px] text-faint">
                {visible.length} version{visible.length === 1 ? "" : "s"}
              </span>
            </div>
            {/* Validity timeline: one bar per version, the slider's day as a vertical rule. */}
            <div className="relative mt-3 h-[calc(var(--n)*14px)]" style={{ ["--n" as string]: visible.length }}>
              <div className="absolute inset-y-0 w-px bg-ink/70" style={{ left: x(asOf) }} title={`as of ${asOf}`} />
              {visible.map((f, i) => {
                const st = factState(f, asOf);
                const end = st === "superseded" && f.valid_to ? f.valid_to : max;
                return (
                  <div
                    key={f.id}
                    title={`${f.value} · ${f.valid_from} → ${st === "superseded" ? f.valid_to : "…"}`}
                    className={`absolute h-2 rounded-full ${STATE[st].bar}`}
                    style={{
                      top: i * 14 + 3,
                      left: x(f.valid_from < min ? min : f.valid_from),
                      width: `max(6px, calc(${x(end)} - ${x(f.valid_from < min ? min : f.valid_from)}${st === "superseded" ? "" : " + 1%"}))`,
                    }}
                  />
                );
              })}
            </div>
            <ol className="mt-3 divide-y divide-hairline">
              {visible.map((f) => (
                <FactItem key={f.id} m={m} f={f} asOf={asOf} next={f.superseded_by ? byId.get(f.superseded_by) : undefined} base={base} />
              ))}
            </ol>
          </Card>
        );
      })}
      {!mine.some((f) => factState(f, asOf) !== "unknown") && (
        <p className="mt-6 text-[14px] text-muted">
          Nothing known about {nameOf(m, entity)} yet on {dayLabel(asOf)}. Drag the date forward.
        </p>
      )}
    </div>
  );
}

function FactItem({
  m,
  f,
  asOf,
  next,
  base,
}: {
  m: Memory;
  f: FactRow;
  asOf: string;
  next?: FactRow;
  base: Record<string, string | null>;
}) {
  const st = factState(f, asOf);
  const nextKnown = next && factState(next, asOf) !== "unknown";
  const valueEntity = m.names.has(f.value_norm) && f.value_norm !== f.subject ? f.value_norm : null;
  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`rounded-full px-2 py-0.5 text-[11.5px] font-medium ${STATE[st].badge}`}>{STATE[st].label}</span>
        <span className={`text-[14.5px] ${st === "superseded" ? "text-muted line-through decoration-faint" : "text-ink"}`}>
          {f.value}
        </span>
        {valueEntity && (
          <Link href={q({ ...base, entity: valueEntity })} scroll={false} className="text-[12px] text-accent hover:underline">
            → {nameOf(m, valueEntity)}
          </Link>
        )}
        {f.contradiction ? <Chip tone="warn">contradicted the same day</Chip> : null}
      </div>
      <div className="mt-1 text-[12px] text-muted">
        valid {dayLabel(f.valid_from)} → {st === "superseded" && f.valid_to ? dayLabel(f.valid_to) : "now"} · recorded{" "}
        {dayLabel(f.recorded_at)} · {f.gate === "entailed" ? "checked by entailment" : (f.gate ?? "")} · confidence{" "}
        {f.confidence.toFixed(2)}
      </div>
      {f.quote && <blockquote className="mt-1.5 border-l-2 border-line pl-3 text-[13px] italic text-transcript">“{f.quote}”</blockquote>}
      {f.reason && <p className="mt-1 text-[12.5px] text-ink">Reason given: {f.reason}</p>}
      {nextKnown && next && (
        <p className="mt-1 text-[12.5px] text-muted">
          Superseded on {dayLabel(next.valid_from)} by “{next.value}”.
        </p>
      )}
      <div className="mt-2">
        <Lines m={m} lines={factLines(f, asOf)} />
      </div>
    </li>
  );
}

function Commitments({
  m,
  asOf,
  status,
  base,
}: {
  m: Memory;
  asOf: string;
  status: string | null;
  base: Record<string, string | null>;
}) {
  const rows = m.commitments
    .map((c) => ({ c, s: commitmentStatus(c, asOf) }))
    .filter((r) => r.s.status !== "unknown");
  const counts = new Map<string, number>();
  for (const r of rows) counts.set(r.s.status, (counts.get(r.s.status) ?? 0) + 1);
  const shown = rows.filter((r) => !status || r.s.status === status).reverse();
  const order = ["open", "no_follow_up", "slipped", "reassigned", "done", "dropped"];
  return (
    <div className="mt-5">
      <p className="max-w-[46rem] text-[13.5px] leading-relaxed text-muted">
        {rows.length} commitments made by {dayLabel(asOf)}. Status replays each one&apos;s events up to that
        day. <em>No follow-up</em> means it was never mentioned again, which is not the same as done.
        Owners the transcript couldn&apos;t pin down are shown as candidates with a question mark.
      </p>
      <div className="mt-3 inline-flex flex-wrap gap-0.5 rounded-full bg-canvas p-0.5">
        <Pill href={q({ ...base, status: null })} active={!status}>
          All {rows.length}
        </Pill>
        {order
          .filter((s) => counts.get(s))
          .map((s) => (
            <Pill key={s} href={q({ ...base, status: s })} active={status === s}>
              {s.replace(/_/g, " ")} {counts.get(s)}
            </Pill>
          ))}
      </div>
      <div className="mt-4 flex flex-col gap-3">
        {shown.map(({ c, s }) => (
          <CommitmentCard key={c.id} m={m} c={c} s={s} asOf={asOf} />
        ))}
      </div>
    </div>
  );
}

function CommitmentCard({
  m,
  c,
  s,
  asOf,
}: {
  m: Memory;
  c: CommitmentRow;
  s: ReturnType<typeof commitmentStatus>;
  asOf: string;
}) {
  const overdue = s.due && s.due < asOf && !["done", "dropped"].includes(s.status);
  return (
    <Card className="p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[14.5px] font-medium text-ink">{c.task}</div>
          <div className="mt-0.5 text-[12.5px] text-muted">
            {nameOf(m, s.owner)}
            {s.owner !== c.owner && <> (originally {nameOf(m, c.owner)})</>} · due{" "}
            {s.due ? dayLabel(s.due) : "unspecified"}
            {s.due !== c.due && c.due && <> (was {dayLabel(c.due)})</>}
            {overdue && <span className="text-date-red"> · overdue</span>}
          </div>
        </div>
        <span className={`rounded-full px-2.5 py-0.5 text-[12px] font-medium ${STATUS[s.status] ?? "bg-canvas"}`}>
          {s.status.replace(/_/g, " ")}
        </span>
      </div>
      <ol className="mt-3 border-l border-line pl-4 text-[12.5px]">
        <li className="relative pb-2">
          <span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-ink" />
          <span className="text-ink">Made {dayLabel(c.made_on)}</span>{" "}
          {c.quote && <span className="italic text-transcript">“{c.quote}”</span>}
          <div className="mt-1">
            <Lines m={m} lines={c.source_lines} />
          </div>
        </li>
        {c.events.map((ev, i) => {
          const future = ev.day > asOf;
          return (
            <li key={i} className={`relative pb-2 ${future ? "opacity-40" : ""}`}>
              <span className={`absolute -left-[21px] top-1.5 h-2 w-2 rounded-full ${future ? "bg-line" : "bg-accent"}`} />
              <span className="text-ink">
                {ev.kind} {dayLabel(ev.day)}
                {ev.new_due && <> → due {dayLabel(ev.new_due)}</>}
                {ev.new_owner && <> → {nameOf(m, ev.new_owner)}</>}
              </span>{" "}
              {future && <span className="text-faint">(after this date)</span>}{" "}
              {ev.quote && <span className="italic text-transcript">“{ev.quote}”</span>}
              <div className="mt-1">
                <Lines m={m} lines={ev.source_lines} />
              </div>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

function Rejections({ m, asOf }: { m: Memory; asOf: string }) {
  const rows = m.rejections.filter((r) => (m.meetings.get(r.note_id)?.day ?? "") <= asOf);
  return (
    <div className="mt-5">
      <p className="max-w-[46rem] text-[13.5px] leading-relaxed text-muted">
        Before anything is written, a gate checks that the cited lines actually say it (an entailment
        check by a second model, plus deterministic checks such as &ldquo;the value appears in the
        lines&rdquo;). These {rows.length} of {m.rejections.length} claims were extracted from meetings up
        to {dayLabel(asOf)} and kept out of memory. Read them to judge whether the gate is too strict.
      </p>
      <div className="mt-4 flex flex-col gap-3">
        {rows.map((r, i) => {
          const p = r.payload as Record<string, unknown>;
          const claim =
            (p.task as string | undefined) ??
            [p.subject, p.predicate, p.value].filter(Boolean).join(" · ") ??
            JSON.stringify(p);
          return (
            <Card key={i} className="p-4">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-full bg-date-red/10 px-2 py-0.5 text-[11.5px] font-medium text-date-red">
                  {r.reason.replace(/_/g, " ")}
                </span>
                <span className="text-[11.5px] text-faint">
                  {r.item === "ExtractedUpdate" ? `commitment update (${String(p.kind ?? "")})` : "fact"} ·{" "}
                  {m.meetings.get(r.note_id)?.title} · {dayLabel(m.meetings.get(r.note_id)?.day ?? "")}
                </span>
              </div>
              <div className="mt-2 text-[14px] text-ink">
                {claim}
                {p.owner ? <span className="text-muted"> · owner {nameOf(m, String(p.owner))}</span> : null}
                {p.new_due ? <span className="text-muted"> · new due {String(p.new_due)}</span> : null}
              </div>
              {typeof p.quote === "string" && (
                <blockquote className="mt-1.5 border-l-2 border-line pl-3 text-[13px] italic text-transcript">“{p.quote}”</blockquote>
              )}
              <p className="mt-2 text-[12.5px] leading-relaxed text-muted">
                <span className="font-medium text-ink">Gate: </span>
                {r.detail}
              </p>
              <div className="mt-2">
                <Lines m={m} lines={(p.source_lines as string[] | undefined) ?? []} />
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
