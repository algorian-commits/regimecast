// Crash replay (M12): pure helpers for the player in components/replay.tsx.
// The API sends every minute of the crash; the player only ever shows minutes up to the
// current one, so the screen never knows more than the app would have known then.
import type { Schemas } from "./api";

export type Replay = Schemas["ReplayOut"];
export type ReplayHour = Schemas["ReplayHour"];

/** Minutes of market time per second of playback. */
export const SPEEDS = [
  { v: 1, label: "1 min/s" },
  { v: 10, label: "10 min/s" },
  { v: 60, label: "1 h/s" },
  { v: 360, label: "6 h/s" },
] as const;
export const TICK_MS = 250;

/** Close time of minute i (ISO, UTC). */
export function timeAt(r: Pick<Replay, "first_close">, i: number): string {
  return new Date(Date.parse(r.first_close) + i * 60_000).toISOString();
}

export function frameAt(r: Replay, i: number) {
  const j = Math.max(0, Math.min(r.n_minutes - 1, Math.round(i)));
  const h = r.minutes.hour[j];
  return {
    i: j,
    t: timeAt(r, j),
    price: r.minutes.price[j],
    p: r.minutes.p_liquidation[j],
    distance: r.minutes.distance_pct[j],
    hour: h >= 0 ? r.hours[h] : null,
    liquidated: r.liquidated_at != null && Date.parse(timeAt(r, j)) >= Date.parse(r.liquidated_at),
  };
}

/** Next frame index after one tick at `speed` minutes per second (stops at the end). */
export function advance(i: number, speed: number, n: number, tickMs = TICK_MS): number {
  return Math.min(n - 1, i + Math.max(1, Math.round((speed * tickMs) / 1000)));
}

/**
 * Chart points up to frame i only (never later): one every `every` minutes, plus frame i itself.
 * Each point is {t, price, p} so price and liquidation chance can be drawn as two charts.
 */
export function pointsUpTo(r: Replay, i: number, every = 10) {
  const out: { t: string; price: number | null; p: number | null }[] = [];
  for (let k = 0; k <= i; k += every) out.push({ t: timeAt(r, k), price: r.minutes.price[k], p: r.minutes.p_liquidation[k] });
  if (i % every !== 0) out.push({ t: timeAt(r, i), price: r.minutes.price[i], p: r.minutes.p_liquidation[i] });
  return out;
}

/** The event's marked moments that have happened by time t. */
export function marksUpTo(marks: unknown, t: string): [string, string][] {
  const list = Array.isArray(marks) ? (marks as [string, string][]) : [];
  return list.filter(([at]) => Date.parse(at) <= Date.parse(t));
}

/** "Tue 8 Nov 16:03 UTC" */
export function clock(iso: string) {
  const d = new Date(iso);
  const day = d.toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
  return `${day} ${d.toISOString().slice(11, 16)} UTC`;
}
