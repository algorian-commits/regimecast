// Track Record: pure helpers for the page. Every missing hour stays a visible gap; nothing here fills one in.
import type { Schemas } from "./api";

export type TrackRow = Schemas["TrackRow"];

const HOUR = 3600_000;

/** Variance of the log return -> volatility over the window, in %: the "expected move". */
export const volPct = (variance: number | null | undefined) => (variance == null ? null : 100 * Math.sqrt(variance));

export type ForecastPoint = { t: string; forecast: number | null; realised: number | null; missing: boolean };
export type ErrorPoint = { t: string; forecast: number; benchmark: number };

/** Oldest-first hourly points from first to last logged hour; an hour with no row is a point of nulls (a gap). */
export function forecastPoints(rows: TrackRow[]): ForecastPoint[] {
  if (rows.length === 0) return [];
  const byHour = new Map(rows.map((r) => [Date.parse(r.origin), r]));
  const hours = [...byHour.keys()];
  const first = Math.min(...hours);
  const last = Math.max(...hours);
  const out: ForecastPoint[] = [];
  for (let h = first; h <= last; h += HOUR) {
    const r = byHour.get(h);
    out.push({
      t: new Date(h).toISOString(),
      forecast: volPct(r?.forecast_variance),
      realised: volPct(r?.realised_variance),
      missing: !r,
    });
  }
  return out;
}

/** Running average QLIKE of the forecast and the benchmark, on rows that have both, oldest first. */
export function runningError(rows: TrackRow[]): ErrorPoint[] {
  const scored = rows
    .filter((r) => r.qlike_forecast != null && r.qlike_benchmark != null)
    .sort((a, b) => Date.parse(a.origin) - Date.parse(b.origin));
  let f = 0;
  let b = 0;
  return scored.map((r, i) => {
    f += r.qlike_forecast as number;
    b += r.qlike_benchmark as number;
    return { t: r.origin, forecast: f / (i + 1), benchmark: b / (i + 1) };
  });
}

/** "12 h" until two days, then "3 days". */
export const span = (hours: number) => (hours < 48 ? `${hours} h` : `${Math.floor(hours / 24)} days`);

export const utc = (iso: string) => `${new Date(iso).toISOString().slice(0, 16).replace("T", " ")} UTC`;

export const num = (x: number | null | undefined, digits = 3) => (x == null || !Number.isFinite(x) ? "–" : x.toFixed(digits));

/** "-12.3%" / "+4.0%": the convention of the final evaluation, negative means lower error than the rival. */
export const signedPct = (x: number | null | undefined) =>
  x == null || !Number.isFinite(x) ? "–" : `${x > 0 ? "+" : ""}${x.toFixed(1)}%`;

/** Minutes from the start of a forecast's window to when its row was written. */
export const minutesAfterStart = (r: Pick<TrackRow, "origin" | "logged_at">) =>
  Math.round((Date.parse(r.logged_at) - Date.parse(r.origin)) / 60_000);
