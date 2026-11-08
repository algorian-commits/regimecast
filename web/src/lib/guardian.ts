// Position Guardian (Phase 13C): API calls and the small pure helpers the page uses (tested in guardian.test.ts).
import { api, load, type Schemas, type Symbol } from "./api";
import { usd } from "./format";

export type GuardianData = Schemas["GuardianOut"];
export type Position = Schemas["PositionOut"];
export type Source = Schemas["SourceOut"];
export type Safer = Schemas["SaferOut"];
export type Telegram = Schemas["TelegramOut"];

export const readGuardian = () => load(() => api.GET("/me/guardian"));
export const addHyperliquid = (address: string, label?: string) =>
  load(() => api.POST("/me/guardian/sources/hyperliquid", { body: { address: address.trim(), label: label?.trim() || null } }));
export const addBinance = (api_key: string, api_secret: string, label?: string) =>
  load(() => api.POST("/me/guardian/sources/binance", { body: { api_key, api_secret, label: label?.trim() || null } }));
export const removeSource = (id: number) => load(() => api.DELETE("/me/guardian/sources/{source_id}", { params: { path: { source_id: id } } }));
export const addManual = (body: Schemas["ManualIn"]) => load(() => api.POST("/me/guardian/positions", { body }));
export const patchPosition = (id: number, body: Schemas["PositionPatch"]) =>
  load(() => api.PATCH("/me/guardian/positions/{position_id}", { params: { path: { position_id: id } }, body }));
export const removePosition = (id: number) => load(() => api.DELETE("/me/guardian/positions/{position_id}", { params: { path: { position_id: id } } }));

export const telegramLink = () => load(() => api.POST("/me/telegram/link"));
export const telegramPatch = (body: Schemas["TelegramIn"]) => load(() => api.PATCH("/me/telegram", { body }));
export const telegramUnlink = () => load(() => api.DELETE("/me/telegram"));
export const telegramTest = () => load(() => api.POST("/me/telegram/test"));

/** Same check the server makes: 0x followed by 40 hex digits. */
export const isAddress = (s: string) => /^0x[0-9a-fA-F]{40}$/.test(s.trim());

export type ManualForm = { symbol: Symbol; side: "long" | "short"; entry: string; leverage: string; margin: string; stop: string };

const num = (s: string) => Number(s.replace(/[,\s$x]/gi, ""));

/** The hand-entry form as a request body, or field errors. Isolated margin; size given as margin in USDT. */
export function parseManualForm(f: ManualForm): { ok: true; body: Schemas["ManualIn"] } | { ok: false; errors: Record<string, string> } {
  const errors: Record<string, string> = {};
  const entry = num(f.entry);
  const lev = num(f.leverage);
  const margin = num(f.margin);
  const stop = f.stop.trim() ? num(f.stop) : null;
  if (!(entry > 0)) errors.entry = "Enter the entry price.";
  if (!(lev > 0) || lev > 150) errors.leverage = "Leverage between 1 and 150.";
  if (!(margin > 0)) errors.margin = "Enter the margin in USDT.";
  if (stop != null && !(stop > 0)) errors.stop = "Leave empty or enter a price.";
  else if (stop != null && entry > 0 && (f.side === "long" ? stop >= entry : stop <= entry))
    errors.stop = f.side === "long" ? "A long's stop is below the entry." : "A short's stop is above the entry.";
  if (Object.keys(errors).length) return { ok: false, errors };
  return { ok: true, body: { symbol: f.symbol, side: f.side, entry, leverage: lev, margin, margin_mode: "isolated", stop } };
}

/** Colour of a position row: bad when any rule holds, warn when close (half the limit, or under 4 moves), else good. */
export function positionTone(p: Position): "good" | "warn" | "bad" | "muted" {
  if (p.covered === false || p.p_liquidation == null) return "muted";
  if (p.states && Object.values(p.states).some((v) => v === true)) return "bad";
  if (p.p_liquidation > p.risk_limit / 2 || (p.moves_1h != null && p.moves_1h < 4)) return "warn";
  return "good";
}

/** The safer version in one sentence, as the Telegram message says it. */
export function saferText(s: Safer | null | undefined): string | null {
  if (!s) return null;
  if (s.status === "safe") return "Already within your limit.";
  if (s.status === "none") return "No lower leverage brings this within your limit: closing it is the safe choice.";
  const parts: string[] = [];
  if (s.add_margin_usdt != null) parts.push(`add ${usd(s.add_margin_usdt)} USDT margin`);
  if (s.reduce_to_usdt != null) parts.push(`reduce the position to ${usd(s.reduce_to_usdt)} USDT`);
  const how = parts.length ? parts.join(", or ") : "lower the leverage";
  const lev = s.leverage_now != null && s.leverage_safe != null ? ` (${s.leverage_now}x now, ${s.leverage_safe}x is safe)` : "";
  return `${how[0].toUpperCase()}${how.slice(1)}${lev}.`;
}

/** Which rules hold right now, in words. */
export function ruleWords(states: Position["states"]): string[] {
  const names: Record<string, string> = {
    guardian_risk: "over your limit",
    guardian_stress: "stress high while long",
    guardian_near: "near liquidation",
  };
  return Object.entries(states ?? {})
    .filter(([, v]) => v === true)
    .map(([k]) => names[k] ?? k);
}
