// Popular trader tracker (13D): the small sums the Traders page shows, kept apart from the screen so they are tested.
import type { Schemas } from "@/lib/api";

export type Band = Schemas["Band"];
export type Card = Schemas["Scorecard"];
export type NetCoin = Schemas["NetCoin"];

/** Highest price at the top, like an order book. Each row knows which side of the current price it is on. */
export function bandRows(bands: Band[]) {
  const max = Math.max(1, ...bands.map((b) => Math.max(b.long_usd, b.short_usd)));
  return [...bands]
    .sort((a, b) => b.from_pct - a.from_pct)
    .map((b) => ({ ...b, longShare: b.long_usd / max, shortShare: b.short_usd / max, above: b.from_pct >= 0 }));
}

/** Where the line for "price now" goes: the number of rows above it. */
export function rowsAbovePrice(bands: Band[]) {
  return bands.filter((b) => b.from_pct >= 0).length;
}

/** Total longs and shorts on the map, with how many positions. */
export function mapTotals(bands: Band[]) {
  return bands.reduce(
    (t, b) => ({ long_usd: t.long_usd + b.long_usd, short_usd: t.short_usd + b.short_usd, n_long: t.n_long + b.n_long, n_short: t.n_short + b.n_short }),
    { long_usd: 0, short_usd: 0, n_long: 0, n_short: 0 },
  );
}

/** "$1.2M", "$350k", "$90". */
export function money(x: number | null | undefined) {
  if (x == null || !Number.isFinite(x)) return "–";
  const a = Math.abs(x);
  const s = x < 0 ? "-" : "";
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(0)}k`;
  return `${s}$${a.toFixed(0)}`;
}

/** The net line in words, with its sample size: every number carries one. */
export function netWords(n: NetCoin) {
  if (n.n_read === 0) return `No skilled account read yet`;
  const counted = `${n.n_long} long, ${n.n_short} short, ${n.n_flat} flat, of ${n.n_read} read`;
  if (n.n_long + n.n_short === 0) return `Nobody holds it (${counted})`;
  const lean = n.net_usd > 0 ? "net long" : n.net_usd < 0 ? "net short" : "balanced";
  return `${money(Math.abs(n.net_usd))} ${lean} (${counted})`;
}

export function holdWords(hours: number | null | undefined) {
  if (hours == null || !Number.isFinite(hours)) return "–";
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} days`;
}

export function labelTone(label: Card["label"]): "good" | "bad" | "muted" {
  return label === "skilled" ? "good" : label === "losing" ? "bad" : "muted";
}

/** What the copy test's status means, in a few words next to the page's sentence. */
export function copyBadge(status: Schemas["CopyTestOut"]["status"]): { text: string; tone: "good" | "warn" | "bad" | "muted" } {
  if (status === "passed") return { text: "Copy test passed", tone: "good" };
  if (status === "failed") return { text: "Copy test failed", tone: "bad" };
  if (status === "pending_forward") return { text: "Forward test pending", tone: "warn" };
  return { text: "Copy test not run yet", tone: "muted" };
}
