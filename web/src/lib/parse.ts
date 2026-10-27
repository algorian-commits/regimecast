import type { Schemas, Symbol } from "./api";
import { usd } from "./format";
import { DEFAULT_FORM, entryFromMark, type TradeForm } from "./trade-form";

export type Parsed = Schemas["ParsedTrade"];
export type ParseResult = Schemas["ParseOut"];

/** Example messages shown under the text box (none of them is in the parser test set). */
export const EXAMPLES = [
  "Long 0.1 BTC at 81k, 10x, stop 79k",
  "Short ETH 20x with $250 margin, stop 3% above entry",
  "sol long 5x $1,000 position at market",
];

const tidy = (x: number) => String(Math.round(x * 100) / 100);

/**
 * Turn the parser's fields into the Risk Check form. The form asks for margin, so a size given in coins or as a
 * dollar position is converted (and the note says how). An entry that was not given becomes the live price.
 * `filled` lists the form fields that came from the user's text, for highlighting.
 */
export function formFromParsed(p: Parsed, mark: number | null): { form: TradeForm; notes: string[]; filled: (keyof TradeForm)[] } {
  const notes: string[] = [];
  const filled: (keyof TradeForm)[] = [];
  const f: TradeForm = { ...DEFAULT_FORM, leverage: "", margin: "" };
  const set = <K extends keyof TradeForm>(k: K, v: TradeForm[K]) => {
    f[k] = v;
    filled.push(k);
  };
  if (p.asset && p.asset !== "OTHER") set("symbol", `${p.asset}USDT` as Symbol);
  if (p.side) set("side", p.side);
  if (p.leverage != null) set("leverage", String(p.leverage));
  if (p.stop != null) set("stop", String(p.stop));
  if (p.margin_mode) set("margin_mode", p.margin_mode);
  if (p.wallet_usdt != null) set("wallet_balance", String(p.wallet_usdt));
  if (p.entry != null) set("entry", String(p.entry));
  else if (mark) {
    f.entry = entryFromMark(mark);
    notes.push("No entry price given, so the live price is used.");
  }
  const entry = p.entry ?? (mark ? Number(f.entry) : null); // the same rounded price the form shows
  const lev = p.leverage;
  if (p.margin_usdt != null) set("margin", String(p.margin_usdt));
  else if (p.notional_usdt != null && lev) {
    set("margin", tidy(p.notional_usdt / lev));
    notes.push(`Margin worked out from your $${usd(p.notional_usdt, 0)} position at ${lev}x.`);
  } else if (p.qty_coins != null && lev && entry) {
    set("margin", tidy((p.qty_coins * entry) / lev));
    notes.push(`Margin worked out from ${p.qty_coins} ${p.asset ?? "coins"} at ${usd(entry)} and ${lev}x.`);
  }
  return { form: f, notes, filled };
}
