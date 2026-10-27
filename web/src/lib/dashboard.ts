// Small pure helpers for the Risk Radar dashboard (tested in lib.test.ts).
import type { LiveCoin } from "./live";

/** Change of the live price against the open of the current hourly candle, in %, e.g. 0.12. */
export function hourChangePct(c: LiveCoin | undefined) {
  if (!c?.candle || c.mark_price == null || !c.candle.open) return null;
  return (c.mark_price / c.candle.open - 1) * 100;
}

/** SVG path for a small line of the last few minutes (no axes), scaled to w x h. */
export function sparkPath(values: number[], w: number, h: number) {
  if (values.length < 2) return "";
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  return values
    .map((v, i) => `${i ? "L" : "M"}${((i / (values.length - 1)) * w).toFixed(1)},${(h - ((v - lo) / span) * (h - 2) - 1).toFixed(1)}`)
    .join(" ");
}

/** Position (0..100%) of a value on a 0..max scale, clamped. */
export function scalePos(v: number, max: number) {
  return Math.max(0, Math.min(100, (v / max) * 100));
}

/** "#22c55e" -> "rgba(34,197,94,0.16)": theme colours, softened for the regime background. */
export function tint(hex: string, alpha: number) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return `rgba(128,128,128,${alpha})`;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

/** Only same-site paths after login, never a full URL taken from the query string. */
export function safeNext(next: string | null) {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/account";
}
