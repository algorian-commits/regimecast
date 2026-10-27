import type { Schemas, Symbol } from "./api";

export type TradeForm = {
  symbol: Symbol;
  side: "long" | "short";
  entry: string;
  leverage: string;
  margin: string;
  stop: string;
  horizon_hours: 1 | 24;
  margin_mode: "isolated" | "cross";
  wallet_balance: string;
};

export const DEFAULT_FORM: TradeForm = {
  symbol: "BTCUSDT",
  side: "long",
  entry: "",
  leverage: "10",
  margin: "100",
  stop: "",
  horizon_hours: 24,
  margin_mode: "isolated",
  wallet_balance: "",
};

/** Highest acceptable chance of liquidation for the "max safe leverage" answer. */
export const RISK_LIMIT = 0.01;

/** What a logged-in user's account sets for the form (Phase 8 profile; see riskDefaults in account.ts). */
export type FormDefaults = { risk_limit: number; wallet_balance: number; margin_mode: "isolated" | "cross" };

/** Fill margin mode and wallet from the account, except the fields the user's own text gave. */
export function withDefaults(f: TradeForm, d: FormDefaults | null, filled: (keyof TradeForm)[] = []): TradeForm {
  if (!d) return f;
  return {
    ...f,
    margin_mode: filled.includes("margin_mode") ? f.margin_mode : d.margin_mode,
    wallet_balance: filled.includes("wallet_balance") ? f.wallet_balance : String(d.wallet_balance),
  };
}

const num = (s: string) => (s.trim() === "" ? undefined : Number(s.replace(/,/g, "")));

/** Check the form and build the /risk-check body, or return plain-English problems per field. */
export function buildTrade(f: TradeForm, riskLimit: number = RISK_LIMIT): { ok: true; body: Schemas["TradeIn"] } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const entry = num(f.entry), lev = num(f.leverage), margin = num(f.margin), stop = num(f.stop), wallet = num(f.wallet_balance);
  if (!(entry && entry > 0)) errors.entry = "Enter the entry price.";
  if (!(lev && lev > 0 && lev <= 150)) errors.leverage = "Leverage must be above 0 and at most 150.";
  if (!(margin && margin > 0)) errors.margin = "Enter how much margin (USDT) you put in.";
  if (stop !== undefined) {
    if (!(stop > 0)) errors.stop = "Stop must be a positive price.";
    else if (entry && (f.side === "long" ? stop >= entry : stop <= entry))
      errors.stop = f.side === "long" ? "For a long, the stop goes below entry." : "For a short, the stop goes above entry.";
  }
  if (f.margin_mode === "cross" && !(wallet && wallet > 0)) errors.wallet_balance = "Cross margin needs your wallet balance.";
  if (Object.keys(errors).length) return { ok: false, errors };
  return {
    ok: true,
    body: {
      symbol: f.symbol,
      side: f.side,
      entry: entry!,
      leverage: lev!,
      margin: margin!,
      stop: stop ?? null,
      horizon_hours: f.horizon_hours,
      margin_mode: f.margin_mode,
      wallet_balance: f.margin_mode === "cross" ? wallet! : null,
      risk_limit: riskLimit,
    },
  };
}

/** A sensible starting entry from the live mark price (rounded to a tidy tick). */
export function entryFromMark(mark: number): string {
  const decimals = mark >= 1000 ? 1 : mark >= 10 ? 2 : 4;
  return mark.toFixed(decimals);
}

/** The form for a saved /risk-check body (to edit or re-run a saved check). */
export function formFromRequest(b: Schemas["TradeIn"]): TradeForm {
  const s = (x: number | null | undefined) => (x == null ? "" : String(x));
  return {
    symbol: b.symbol,
    side: b.side,
    entry: s(b.entry),
    leverage: s(b.leverage),
    margin: s(b.margin),
    stop: s(b.stop),
    horizon_hours: b.horizon_hours ?? 24,
    margin_mode: b.margin_mode ?? "isolated",
    wallet_balance: s(b.wallet_balance),
  };
}
