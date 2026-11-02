"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

const DAY = 86_400_000;
const toDay = (t: number) => new Date(t).toISOString().slice(0, 10);

function label(day: string): string {
  return new Date(`${day}T00:00:00Z`).toLocaleDateString("en-US", {
    timeZone: "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

/** "As of" date slider: drag to see what the memory held on that day. The date lives in the
 *  URL (?as_of=), so the server re-renders the view and any position is a shareable link.
 *  Ticks mark the days a meeting happened (when the memory could learn something). */
export function AsOfSlider({
  min,
  max,
  value,
  meetingDays,
}: {
  min: string;
  max: string;
  value: string;
  meetingDays: string[];
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const [day, setDay] = useState(value);
  useEffect(() => setDay(value), [value]);
  useEffect(() => {
    if (day === value) return;
    const t = setTimeout(() => {
      const p = new URLSearchParams(params.toString());
      p.set("as_of", day);
      router.replace(`${pathname}?${p.toString()}`, { scroll: false });
    }, 120);
    return () => clearTimeout(t);
  }, [day, value, params, pathname, router]);

  const t0 = Date.parse(`${min}T00:00:00Z`);
  const t1 = Date.parse(`${max}T00:00:00Z`);
  const span = Math.max(1, Math.round((t1 - t0) / DAY));
  const pos = Math.round((Date.parse(`${day}T00:00:00Z`) - t0) / DAY);
  const meetingsSoFar = meetingDays.filter((d) => d <= day).length;

  return (
    <div className="rounded-[20px] border border-line bg-card px-5 py-4 shadow-[0_1px_2px_rgba(0,0,0,0.03)]">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <label htmlFor="asof" className="text-[13px] text-muted">
          As of <span className="font-serif text-[22px] text-ink">{label(day)}</span>
        </label>
        <span className="text-[12px] text-muted">
          {meetingsSoFar} of {meetingDays.length} meetings heard ·{" "}
          {day === max ? (
            "today (the last meeting)"
          ) : (
            <button type="button" onClick={() => setDay(max)} className="underline hover:text-ink">
              jump to today
            </button>
          )}
        </span>
      </div>
      <div className="relative mt-3">
        <div aria-hidden className="pointer-events-none absolute inset-x-0 top-[3px] h-2">
          {[...new Set(meetingDays)].map((d) => (
            <span
              key={d}
              className={`absolute top-0 h-2 w-px ${d <= day ? "bg-accent" : "bg-line"}`}
              style={{ left: `${((Date.parse(`${d}T00:00:00Z`) - t0) / DAY / span) * 100}%` }}
            />
          ))}
        </div>
        <input
          id="asof"
          type="range"
          min={0}
          max={span}
          step={1}
          value={pos}
          onChange={(e) => setDay(toDay(t0 + Number(e.target.value) * DAY))}
          className="relative w-full accent-[#4f7f1a]"
          aria-valuetext={label(day)}
        />
      </div>
      <div className="mt-1 flex justify-between text-[11.5px] text-faint">
        <span>{label(min)}</span>
        <span>{label(max)}</span>
      </div>
    </div>
  );
}
