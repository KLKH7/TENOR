import Link from "next/link";

import { Card, categoryLabel, systemLabel } from "@/components/results/ui";
import { paretoFront, pct, secs, tokens, usd } from "@/lib/explore";
import type { IngestStats, SystemMetrics } from "@/lib/results";

type Metrics = Record<string, SystemMetrics>;

const th = "whitespace-nowrap px-3 py-2.5 text-left text-[11.5px] font-medium uppercase tracking-[0.04em] text-muted";
const td = "whitespace-nowrap px-3 py-3 text-[13.5px] tabular-nums text-ink";

/** Accuracy bar with the 95% bootstrap CI drawn as a whisker on a 0–100% track. */
function AccuracyCell({ m }: { m: SystemMetrics }) {
  const [lo, hi] = m.accuracy_ci95;
  return (
    <div className="flex items-center gap-3">
      <span className="w-11 font-semibold">{pct(m.accuracy)}</span>
      <span className="relative h-2 w-28 rounded-full bg-round" aria-hidden>
        <span
          className="absolute inset-y-0 left-0 rounded-full bg-accent/80"
          style={{ width: `${m.accuracy * 100}%` }}
        />
        <span
          className="absolute top-1/2 h-[2px] -translate-y-1/2 bg-ink/70"
          style={{ left: `${lo * 100}%`, width: `${Math.max(0, hi - lo) * 100}%` }}
        />
        <span className="absolute -top-0.5 h-3 w-px bg-ink/70" style={{ left: `${lo * 100}%` }} />
        <span className="absolute -top-0.5 h-3 w-px bg-ink/70" style={{ left: `${hi * 100}%` }} />
      </span>
      <span className="text-[12px] text-muted">
        {pct(lo)}–{pct(hi)}
      </span>
    </div>
  );
}

export function Leaderboard({
  metrics,
  ingest,
  base,
}: {
  metrics: Metrics;
  ingest: Record<string, IngestStats> | undefined;
  base: string;
}) {
  const systems = Object.keys(metrics).sort((a, b) => metrics[b].accuracy - metrics[a].accuracy);
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[60rem] border-collapse">
        <thead className="border-b border-hairline">
          <tr>
            <th className={th}>System</th>
            <th className={th}>Accuracy · 95% CI</th>
            <th className={th} title="Normalised exact match against answer_norm">
              Exact
            </th>
            <th className={th} title="Cited lines that are gold evidence">
              Cite P
            </th>
            <th className={th} title="Gold evidence lines that were cited">
              Cite R
            </th>
            <th className={th}>$ / q</th>
            <th className={th}>Tokens in / q</th>
            <th className={th}>Latency p50 · p95</th>
            <th className={th}>Tools / q</th>
            <th className={th} title="One-off memory build for write-time systems">
              Ingest $
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-hairline">
          {systems.map((s) => {
            const m = metrics[s];
            const ing = ingest?.[s];
            return (
              <tr key={s} className="hover:bg-sheet/60">
                <td className={`${td} font-medium`}>
                  <Link
                    href={`${base}?sys=${s}#questions`}
                    className="hover:text-accent"
                    title={`n = ${m.n} · show this system's questions`}
                  >
                    {systemLabel(s)}
                  </Link>
                  <div className="text-[11.5px] font-normal text-faint">{s}</div>
                </td>
                <td className={td}>
                  <AccuracyCell m={m} />
                </td>
                <td className={td}>{pct(m.exact_match)}</td>
                <td className={td}>{pct(m.cite_precision)}</td>
                <td className={td}>{pct(m.cite_recall)}</td>
                <td className={td}>{usd(m.cost_per_q)}</td>
                <td className={td}>{tokens(m.input_tokens_per_q)}</td>
                <td className={td}>
                  {secs(m.latency_p50)} · {secs(m.latency_p95)}
                </td>
                <td className={td}>{m.tool_calls_per_q.toFixed(1)}</td>
                <td className={td}>
                  {ing ? (
                    <span title={`${ing.llm_calls} calls${ing.cached ? " · reused cached memory" : ""}`}>
                      {usd(ing.cost_usd)}
                      {ing.cached && <span className="ml-1 text-[11px] text-muted">cached</span>}
                    </span>
                  ) : (
                    <span className="text-faint">none</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

/** Cost per question (log x) vs accuracy (y) with CI whiskers and the Pareto front dashed. */
export function CostScatter({ metrics }: { metrics: Metrics }) {
  const W = 640;
  const H = 320;
  const pad = { l: 52, r: 56, t: 30, b: 44 };
  const pts = Object.entries(metrics).map(([name, m]) => ({
    name,
    cost: Math.max(m.cost_per_q, 1e-5),
    accuracy: m.accuracy,
    ci: m.accuracy_ci95,
  }));
  if (!pts.length) return null;
  const lx = pts.map((p) => Math.log10(p.cost));
  const x0 = Math.floor(Math.min(...lx) - 0.15);
  const x1 = Math.ceil(Math.max(...lx) + 0.15);
  const x = (c: number) => pad.l + ((Math.log10(c) - x0) / (x1 - x0)) * (W - pad.l - pad.r);
  const y = (a: number) => pad.t + (1 - a) * (H - pad.t - pad.b);
  const ticksX: number[] = [];
  for (let e = x0; e <= x1; e++) for (const k of [1, 2, 5]) if (e + Math.log10(k) <= x1) ticksX.push(k * 10 ** e);
  const front = paretoFront(pts);

  return (
    <Card className="max-w-[46rem] p-4">
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label="Cost vs accuracy">
        {[0, 0.25, 0.5, 0.75, 1].map((a) => (
          <g key={a}>
            <line x1={pad.l} x2={W - pad.r} y1={y(a)} y2={y(a)} className="stroke-hairline" />
            <text x={pad.l - 8} y={y(a) + 4} textAnchor="end" className="fill-muted text-[11px]">
              {pct(a)}
            </text>
          </g>
        ))}
        {ticksX.map((c) => (
          <text
            key={c}
            x={x(c)}
            y={H - pad.b + 18}
            textAnchor="middle"
            className="fill-muted text-[11px]"
          >
            {usd(c)}
          </text>
        ))}
        <text x={(W + pad.l) / 2} y={H - 6} textAnchor="middle" className="fill-muted text-[11.5px]">
          cost per question (log scale) →
        </text>
        <text
          transform={`translate(13 ${(H - pad.b) / 2}) rotate(-90)`}
          textAnchor="middle"
          className="fill-muted text-[11.5px]"
        >
          accuracy ↑
        </text>
        {front.length > 1 && (
          <polyline
            points={front.map((p) => `${x(p.cost)},${y(p.accuracy)}`).join(" ")}
            className="fill-none stroke-accent/50"
            strokeDasharray="4 4"
            strokeWidth={1.5}
          />
        )}
        {pts.map((p) => (
          <g key={p.name}>
            <title>{`${systemLabel(p.name)}: ${pct(p.accuracy)} (CI ${pct(p.ci[0])}–${pct(p.ci[1])}) at ${usd(p.cost)}/q`}</title>
            <line
              x1={x(p.cost)}
              x2={x(p.cost)}
              y1={y(p.ci[0])}
              y2={y(p.ci[1])}
              className="stroke-ink/40"
              strokeWidth={1.5}
            />
            <circle
              cx={x(p.cost)}
              cy={y(p.accuracy)}
              r={6}
              className={front.includes(p) ? "fill-accent" : "fill-them"}
              stroke="white"
              strokeWidth={2}
            />
            <text x={x(p.cost) + 10} y={y(p.accuracy) - 8} className="fill-ink text-[12px] font-medium">
              {systemLabel(p.name).split(" · ")[0]}
            </text>
          </g>
        ))}
      </svg>
      <p className="mt-2 px-1 text-[12px] text-muted">
        Vertical bars are 95% CIs. Green points sit on the Pareto front (dashed): nothing else is
        both cheaper and more accurate. Per-question cost only; ingest cost is in the table.
      </p>
    </Card>
  );
}

/** Accuracy by category × system. Cell links filter the question list below. */
export function CategoryMatrix({
  metrics,
  categories,
  base,
}: {
  metrics: Metrics;
  categories: string[];
  base: string;
}) {
  const systems = Object.keys(metrics);
  return (
    <Card className="overflow-x-auto p-2">
      <table className="w-full border-separate border-spacing-1">
        <thead>
          <tr>
            <th className={th}>Category</th>
            <th className={`${th} text-right`}>n</th>
            {systems.map((s) => (
              <th key={s} className={`${th} text-center normal-case tracking-normal`}>
                {systemLabel(s).split(" · ")[0]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {categories.map((c) => {
            const n = Math.max(...systems.map((s) => metrics[s].by_category[c]?.n ?? 0));
            return (
              <tr key={c}>
                <td className="px-3 py-2 text-[13.5px] text-ink">
                  <Link href={`${base}?cat=${c}#questions`} className="hover:text-accent">
                    {categoryLabel(c)}
                  </Link>
                </td>
                <td className="px-3 py-2 text-right text-[12.5px] tabular-nums text-muted">{n}</td>
                {systems.map((s) => {
                  const cell = metrics[s].by_category[c];
                  if (!cell) return <td key={s} className="rounded-lg bg-sheet" />;
                  // Lightness ramp from red-ish (0%) through cream to accent green (100%).
                  const a = cell.accuracy;
                  const bg =
                    a >= 0.5
                      ? `rgba(79,127,26,${0.12 + (a - 0.5) * 1.3})`
                      : `rgba(212,80,63,${0.12 + (0.5 - a) * 1.0})`;
                  return (
                    <td key={s} className="p-0">
                      <Link
                        href={`${base}?cat=${c}&sys=${s}#questions`}
                        title={`${systemLabel(s)} · ${categoryLabel(c)}: ${Math.round(a * cell.n)}/${cell.n}`}
                        className="block rounded-lg px-2 py-2 text-center text-[13px] tabular-nums ring-ink/30 hover:ring-1"
                        style={{ background: bg, color: a > 0.85 || a < 0.15 ? "white" : undefined }}
                      >
                        {pct(a)}
                        <span className="ml-1 text-[10.5px] opacity-70">
                          {Math.round(a * cell.n)}/{cell.n}
                        </span>
                      </Link>
                    </td>
                  );
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}
