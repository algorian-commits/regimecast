// Phase 12 trade verdict: the beginner view's wording and numbers, kept out of the component so they are tested.
import type { Schemas } from "./api";
import { prob, usd } from "./format";

export type Verdict = Schemas["VerdictOut"];
export type Fix = Schemas["FixOut"];
export type WhatIf = Schemas["WhatIfOut"];
export type Version = Schemas["TradeVersion"];
export type Tone = "good" | "warn" | "bad";

export function gradeTone(g: string): Tone {
  return g === "A" || g === "B" ? "good" : g === "C" ? "warn" : "bad";
}

/** 12.5 -> "+$12.50", -100 -> "−$100.00" (a real minus sign, so it reads as a loss). */
export function money(x: number): string {
  const s = `$${usd(Math.abs(x), 2)}`;
  return x > 0 ? `+${s}` : x < 0 ? `−${s}` : s;
}

const EXIT = { stop: "your stop closes it", liquidation: "liquidated" } as const;

/** One what-if outcome in words: "+$62.10" or "−$100.00, liquidated". */
export function outcome(usdt: number, exit: WhatIf["down_exit"]): string {
  return exit ? `${money(usdt)}, ${EXIT[exit]}` : money(usdt);
}

/** The two lines of a what-if tile: rises first, then falls (the crash day only falls). */
export function whatIfLines(w: WhatIf): { label: string; text: string; tone: Tone }[] {
  const tone = (x: number): Tone => (x > 0 ? "good" : x < 0 ? "bad" : "warn");
  const pct = `${w.move_pct.toFixed(1)}%`;
  const out = [];
  if (w.up_usdt != null) out.push({ label: `Rises ${pct}`, text: outcome(w.up_usdt, w.up_exit ?? null), tone: tone(w.up_usdt) });
  out.push({ label: `Falls ${pct}`, text: outcome(w.down_usdt, w.down_exit ?? null), tone: tone(w.down_usdt) });
  return out;
}

/** Rows of the side-by-side "your trade vs the safer version" table. */
export function fixRows(o: Version, f: Version): { label: string; original: string; fixed: string }[] {
  const price = (x: number | null | undefined) => (x == null ? "none" : usd(x));
  return [
    { label: "Grade", original: o.grade, fixed: f.grade },
    { label: "Leverage", original: `${o.leverage}x`, fixed: `${f.leverage}x` },
    { label: "Position", original: `$${usd(o.notional_usdt, 0)}`, fixed: `$${usd(f.notional_usdt, 0)}` },
    { label: "Stop-loss", original: price(o.stop), fixed: price(f.stop) },
    { label: "Liquidation price", original: price(o.liquidation_price), fixed: price(f.liquidation_price) },
    { label: "Chance of liquidation", original: prob(o.p_liquidation), fixed: prob(f.p_liquidation) },
    { label: "Most you can lose", original: `$${usd(o.max_loss_usdt)}`, fixed: `$${usd(f.max_loss_usdt)}` },
  ];
}

/** Break-even win rate as a short phrase: 0.54 -> "54%", above 1 -> "never". */
export function breakEven(v: Verdict): string | null {
  const p = v.break_even_win_rate;
  if (p == null) return null;
  return p >= 1 ? "never" : `${Math.round(100 * p)}%`;
}
