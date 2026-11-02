// Fixture times are UTC; render in UTC so server output is stable.
const opts = { timeZone: "UTC" } as const;

export function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    ...opts,
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

export function timeLabel(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-US", { ...opts, hour: "numeric", minute: "2-digit" });
}

/** Offset from the first utterance as m:ss, like Granola's transcript timestamps. */
export function offsetLabel(iso: string, startIso: string): string {
  const s = Math.max(0, Math.round((Date.parse(iso) - Date.parse(startIso)) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
