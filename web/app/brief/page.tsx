import Link from "next/link";

import { PageShell } from "@/components/AppNav";
import { dayLabel, Lines } from "@/components/memory/Lines";
import { Card, Chip, Section } from "@/components/results/ui";
import { lineHref } from "@/lib/asof";
import { type Brief, briefCommand, getBrief } from "@/lib/brief";
import { listNotes, meetingStart, XL_ROOT } from "@/lib/data";
import { loadMemory, type Memory, nameOf } from "@/lib/memory";

export const dynamic = "force-dynamic";

type Params = Record<string, string | string[] | undefined>;
const one = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v) ?? null;

// Starting points that fill every section (found by briefing all 184 XL meetings): a customer
// call (requests + one change), a launch sync and the all-hands (most changes since last time).
const SUGGESTED = ["not_4i3VrPvoRzpS1n", "not_MX1VBL2uoUhUwx", "not_McGrj4gw9NNyT2"];

const STATUS: Record<string, string> = {
  open: "bg-canvas text-ink",
  no_follow_up: "bg-warn-soft text-warn",
  slipped: "bg-warn-soft text-warn",
  reassigned: "bg-canvas text-ink",
};

/** "… ARR at $312k [not_x:0100][not_x:0101]." → text with the citations as line links. */
function Cited({ text }: { text: string }) {
  const parts = text.split(/((?:\[not_[A-Za-z0-9]{14}:\d{4}\])+)/);
  return (
    <>
      {parts.map((part, i) => {
        const ids = [...part.matchAll(/\[(not_[A-Za-z0-9]{14}):(\d{4})\]/g)];
        if (!ids.length) return <span key={i}>{part}</span>;
        const lines = ids.map((m) => `${m[1]}:${m[2]}`);
        return (
          <Link
            key={i}
            href={lineHref(ids[0][1], lines)}
            title={lines.join("\n")}
            className="mx-0.5 rounded-full bg-accent-soft px-1.5 py-px align-[1px] text-[11px] text-accent hover:underline"
          >
            {lines.length === 1 ? `line ${Number(ids[0][2])}` : `${lines.length} lines`}
          </Link>
        );
      })}
    </>
  );
}

export default async function BriefPage({ searchParams }: { searchParams: Promise<Params> }) {
  const sp = await searchParams;
  const m = loadMemory();
  const notes = listNotes(XL_ROOT);
  const ids = new Set(notes.map((n) => n.id));
  const requested = one(sp.meeting);
  const meeting = requested && ids.has(requested) ? requested : SUGGESTED[0];
  let brief: Brief | null = null;
  let error: string | null = null;
  if (m) {
    try {
      brief = await getBrief(meeting);
    } catch (e) {
      error = String((e as { stderr?: string }).stderr || (e as Error).message).slice(-1200);
    }
  }
  const byTitle = new Map<string, typeof notes>();
  for (const n of [...notes].reverse()) byTitle.set(n.title ?? "Untitled", [...(byTitle.get(n.title ?? "Untitled") ?? []), n]);

  return (
    <PageShell current="brief">
      <h1 className="mt-8 font-serif text-[2.75rem] leading-[1.08] tracking-[-0.01em] text-ink">
        Pre-meeting brief
      </h1>
      <p className="mt-3 max-w-[44rem] text-[15px] leading-relaxed text-muted">
        What you&apos;d want to know walking into a meeting, before you ask: open commitments of the
        people in the room, what changed since the group last met, open customer requests, and last
        time&apos;s digest. It&apos;s a handful of as-of SQL reads over the memory, using only what
        was recorded before the meeting started. No LLM, $0. Pick a past meeting, read the brief, then
        open the meeting to see what actually came up.
      </p>

      <form action="/brief" className="mt-6 flex flex-wrap items-center gap-2">
        <label htmlFor="meeting" className="sr-only">
          Meeting
        </label>
        <select
          id="meeting"
          name="meeting"
          defaultValue={meeting}
          className="min-w-0 max-w-full flex-1 rounded-full border border-line bg-card px-4 py-2 text-[14px] text-ink"
        >
          {[...byTitle].map(([title, list]) => (
            <optgroup key={title} label={title}>
              {list.map((n) => (
                <option key={n.id} value={n.id}>
                  {title} · {dayLabel(meetingStart(n))}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        <button type="submit" className="rounded-full bg-ink px-4 py-2 text-[13.5px] text-card hover:opacity-90">
          Brief me
        </button>
      </form>
      <div className="mt-2 flex flex-wrap gap-1.5 text-[12.5px] text-muted">
        Try:
        {SUGGESTED.filter((id) => ids.has(id)).map((id) => {
          const n = notes.find((x) => x.id === id)!;
          return (
            <Link key={id} href={`/brief?meeting=${id}`} className="underline hover:text-ink">
              {n.title} ({dayLabel(meetingStart(n))})
            </Link>
          );
        })}
      </div>

      {!m && (
        <p className="mt-8 rounded-2xl bg-warn-soft px-4 py-3 text-[13.5px] text-warn">
          No finished memory store under data/cache/concentrate; the brief reads from it.
        </p>
      )}
      {error && (
        <pre className="mt-8 whitespace-pre-wrap rounded-2xl bg-warn-soft px-4 py-3 text-[12px] text-warn">
          {briefCommand(meeting)} failed:{"\n"}
          {error}
        </pre>
      )}
      {m && brief && <BriefView m={m} b={brief} meeting={meeting} />}
    </PageShell>
  );
}

function BriefView({ m, b, meeting }: { m: Memory; b: Brief; meeting: string }) {
  const start = new Date(b.meeting.start);
  return (
    <>
      <Card className="mt-8 p-5">
        <div className="text-[12px] uppercase tracking-[0.05em] text-faint">Brief for</div>
        <h2 className="mt-1 font-serif text-[1.9rem] leading-tight text-ink">{b.meeting.title}</h2>
        <div className="mt-3 flex flex-wrap gap-1.5">
          <Chip>
            {start.toLocaleDateString("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" })},{" "}
            {start.toLocaleTimeString("en-US", { timeZone: "UTC", hour: "numeric", minute: "2-digit" })} UTC
          </Chip>
          {b.meeting.customer && <Chip>customer: {nameOf(m, b.meeting.customer)}</Chip>}
          <Chip title={b.meeting.attendees.map((a) => nameOf(m, a)).join(", ")}>
            {b.meeting.attendees.length} attendee{b.meeting.attendees.length === 1 ? "" : "s"}
          </Chip>
          <Chip title="Only rows recorded before this instant are read">memory as of the meeting start</Chip>
        </div>
        <p className="mt-3 text-[12.5px] text-muted">
          <code className="rounded bg-canvas px-1.5 py-0.5">{briefCommand(meeting)}</code> ·{" "}
          <Link href={`/notes/${meeting}?set=xl`} className="text-accent hover:underline">
            open what happened in this meeting →
          </Link>
        </p>
      </Card>

      <Section title={`Open commitments of attendees · ${b.commitments.length}`}>
        {b.commitments.length === 0 && <p className="text-[14px] text-muted">None open.</p>}
        <ul className="divide-y divide-hairline rounded-[20px] border border-line bg-card">
          {b.commitments.map((c, i) => (
            <li key={i} className="px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className={`rounded-full px-2 py-0.5 text-[11.5px] font-medium ${STATUS[c.status] ?? "bg-canvas"}`}>
                  {c.status.replace(/_/g, " ")}
                </span>
                <span className="text-[14.5px] text-ink">{c.task}</span>
              </div>
              <div className="mt-1 text-[12.5px] text-muted">
                {nameOf(m, c.owner)} · due {c.due ? dayLabel(c.due) : "unspecified"}
                {c.original_due && c.original_due !== c.due && <> (originally {dayLabel(c.original_due)})</>}
                {c.overdue && <span className="text-date-red"> · overdue</span>} · made {dayLabel(c.made_on)}
              </div>
              <div className="mt-1.5">
                <Lines m={m} lines={c.lines} />
              </div>
            </li>
          ))}
        </ul>
      </Section>

      <Section
        title={`What changed since this group last met · ${b.changes.length}`}
        hint="New or superseded facts about the attendees, the customer, or anything this series discussed before, learnt after the previous meeting in the series."
      >
        {b.changes.length === 0 && <p className="text-[14px] text-muted">Nothing new.</p>}
        <ul className="divide-y divide-hairline rounded-[20px] border border-line bg-card">
          {b.changes.map((c, i) => (
            <li key={i} className="px-4 py-3">
              <div className="text-[12px] text-muted">
                <Link href={`/memory?entity=${c.subject}&as_of=${b.as_of.slice(0, 10)}`} className="hover:text-accent">
                  {nameOf(m, c.subject)}
                </Link>{" "}
                · {c.predicate.replace(/_/g, " ")} · learnt {dayLabel(c.learnt_on)}
              </div>
              <div className="mt-0.5 text-[14.5px] text-ink">
                {c.previous && <span className="text-muted line-through decoration-faint">{c.previous}</span>}
                {c.previous && <span className="text-muted"> → </span>}
                {c.value}
                {!c.previous && <span className="ml-2 rounded-full bg-accent-soft px-2 py-0.5 text-[11px] text-accent">new</span>}
              </div>
              <div className="mt-1.5">
                <Lines m={m} lines={c.lines} />
              </div>
            </li>
          ))}
        </ul>
      </Section>

      {b.meeting.customer && (
        <Section title={`Open requests from ${nameOf(m, b.meeting.customer)} · ${b.requests.length}`}>
          {b.requests.length === 0 && <p className="text-[14px] text-muted">None open.</p>}
          <ul className="divide-y divide-hairline rounded-[20px] border border-line bg-card">
            {b.requests.map((r, i) => (
              <li key={i} className="px-4 py-3">
                <div className="text-[14.5px] text-ink">{r.request}</div>
                <div className="mt-1 text-[12.5px] text-muted">since {dayLabel(r.since)}</div>
                <div className="mt-1.5">
                  <Lines m={m} lines={r.lines} />
                </div>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Last time">
        {b.last_meeting ? (
          <Card className="p-4">
            <div className="text-[12.5px] text-muted">
              <Link href={`/notes/${b.last_meeting.note_id}?set=xl`} className="text-ink hover:text-accent">
                {b.last_meeting.title}
              </Link>{" "}
              · {dayLabel(b.last_meeting.day)} · Concentrate&apos;s digest
            </div>
            <p className="mt-2 text-[14.5px] leading-[1.6] text-ink">
              <Cited text={b.last_meeting.digest} />
            </p>
          </Card>
        ) : (
          <p className="text-[14px] text-muted">First meeting in this series.</p>
        )}
      </Section>
    </>
  );
}
