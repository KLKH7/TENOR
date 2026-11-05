// Paired significance + Pareto frontier from <stamp>.analysis.json (`concentrate analyze`).
// Read-only rendering of numbers computed in Python; see docs/archive/northwind/ANALYSIS.md for the methods.

import { Card, systemLabel } from "@/components/results/ui";
import { pct, usd } from "@/lib/explore";
import type { Analysis } from "@/lib/results";

const TH = "px-3 py-2.5 text-left text-[11.5px] font-medium uppercase tracking-[0.04em] text-muted";
const TD = "px-3 py-2 text-[13px] tabular-nums text-ink";

function pval(p: number): string {
  return p < 0.001 ? "<0.001" : p.toFixed(3);
}

export function Significance({ analysis }: { analysis: Analysis }) {
  const pairs = [...analysis.pairs.overall].sort((x, y) => x.p_holm - y.p_holm || x.p - y.p);
  const power = new Map(analysis.power.per_pair.map((p) => [p.pair, p]));
  const volumes = Object.keys(analysis.pareto).sort((a, b) => Number(a) - Number(b));
  const vol = volumes.includes(String(analysis.meta.volume)) ? String(analysis.meta.volume) : volumes[0];
  const front = [...(analysis.pareto[vol] ?? [])].sort((a, b) => a.usd_per_q - b.usd_per_q);
  const reliable = pairs.filter((p) => p.significant).length;

  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-[46rem] text-[13.5px] leading-relaxed text-ink">
        {reliable === 0 ? (
          <>
            <strong className="font-semibold">No pair of systems differs reliably</strong> at α ={" "}
            {analysis.meta.alpha} after Holm correction over {pairs.length} pairs.
          </>
        ) : (
          <>
            <strong className="font-semibold">
              {reliable} of {pairs.length} pairs
            </strong>{" "}
            differ reliably (Holm-adjusted p &lt; {analysis.meta.alpha}).
          </>
        )}{" "}
        Only questions where exactly one system is right carry information (McNemar exact test); the
        interval is a paired bootstrap of the accuracy gap.
      </p>

      <Card className="overflow-x-auto">
        <table className="w-full min-w-[46rem] border-collapse">
          <thead className="border-b border-hairline">
            <tr>
              <th className={TH}>Pair (A vs B)</th>
              <th className={TH}>Gap A − B</th>
              <th className={TH}>95% CI</th>
              <th className={TH} title="Questions only A got right · only B got right">
                A only · B only
              </th>
              <th className={TH}>p · Holm</th>
              <th className={TH} title="Questions needed to detect a 10-point gap with 80% power, at the observed discordance">
                n for 10 pts
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {pairs.map((p) => {
              const pw = power.get(`${p.a} vs ${p.b}`);
              return (
                <tr key={`${p.a}-${p.b}`}>
                  <td className={TD}>
                    {p.significant ? "✅ " : ""}
                    {systemLabel(p.a)} <span className="text-faint">vs</span> {systemLabel(p.b)}
                  </td>
                  <td className={TD}>
                    {p.diff >= 0 ? "+" : ""}
                    {(p.diff * 100).toFixed(0)} pts
                  </td>
                  <td className={`${TD} text-muted`}>
                    [{(p.ci_low * 100).toFixed(0)}, {(p.ci_high * 100).toFixed(0)}]
                  </td>
                  <td className={TD}>
                    {p.a_only} · {p.b_only}
                  </td>
                  <td className={TD}>
                    {pval(p.p)} · <span className={p.significant ? "text-accent" : "text-muted"}>{pval(p.p_holm)}</span>
                  </td>
                  <td className={`${TD} text-muted`}>{pw?.needed["0.1"] ?? "–"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>

      <div>
        <h3 className="text-[14px] font-semibold text-ink">
          Pareto frontier at {Number(vol).toLocaleString("en-US")} questions per corpus
        </h3>
        <p className="mt-1 max-w-[46rem] text-[13px] leading-relaxed text-muted">
          Cost per question = answer $ + one-off ingest $ ÷ volume. A system is dominated when another is
          at least as accurate and as cheap, and strictly better on one. Frontier accuracy is a point
          estimate: check the pair above before calling a domination real.
        </p>
        <Card className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[36rem] border-collapse">
            <thead className="border-b border-hairline">
              <tr>
                <th className={TH}>System</th>
                <th className={TH}>Accuracy</th>
                <th className={TH}>$ / question</th>
                <th className={TH}>Frontier</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-hairline">
              {front.map((p) => (
                <tr key={p.name}>
                  <td className={TD}>{systemLabel(p.name)}</td>
                  <td className={TD}>{pct(p.accuracy)}</td>
                  <td className={TD}>{usd(p.usd_per_q)}</td>
                  <td className={TD}>
                    {p.on_frontier ? (
                      <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[12px] text-accent">on frontier</span>
                    ) : (
                      <span className="text-[12.5px] text-muted">
                        dominated by {p.dominated_by.map(systemLabel).join(", ")}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </div>
    </div>
  );
}
