export const coin = (s: string) => s.replace(/USDT$/, "");

export function usd(x: number | null | undefined, digits?: number) {
  if (x == null || !Number.isFinite(x)) return "–";
  const d = digits ?? (Math.abs(x) >= 1000 ? 2 : Math.abs(x) >= 1 ? 2 : 4);
  return x.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function compact(x: number | null | undefined) {
  if (x == null || !Number.isFinite(x)) return "–";
  return x.toLocaleString("en-US", { notation: "compact", maximumFractionDigits: 1 });
}

/** 0.0123 -> "1.23%" */
export function pct(x: number | null | undefined, digits = 2) {
  if (x == null || !Number.isFinite(x)) return "–";
  return `${(100 * x).toFixed(digits)}%`;
}

/** Probability with enough precision to tell tiny risks apart: 0.00004 -> "<0.01%". */
export function prob(p: number) {
  if (p <= 0) return "0%";
  if (p < 0.0001) return "<0.01%";
  return pct(p, p < 0.01 ? 2 : 1);
}

/** Seconds/minutes/hours since `iso`, measured at `now`. */
export function ago(iso: string, now: number = Date.now()) {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  if (s < 90) return `${s}s ago`;
  if (s < 90 * 60) return `${Math.round(s / 60)} min ago`;
  if (s < 48 * 3600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

/** "Low / Elevated / High" band for a liquidation probability, for colour and wording. */
export function riskBand(p: number): "low" | "elevated" | "high" {
  if (p < 0.01) return "low";
  if (p < 0.05) return "elevated";
  return "high";
}
