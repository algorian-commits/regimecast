// Saved risk checks. Kept in this browser for now; when accounts arrive (Phase 8, /me/checks) a server-backed
// store with the same interface replaces BrowserHistory and the page does not change.
import type { Schemas, Symbol } from "./api";

export type SavedCheck = {
  id: string;
  created_at: string; // ISO time, UTC
  symbol: Symbol;
  text: string | null; // what the user typed, if the trade came from the parser
  request: Schemas["TradeIn"];
  result: Schemas["RiskOut"];
};

export type NewCheck = Omit<SavedCheck, "id" | "created_at">;

export interface HistoryStore {
  list(): Promise<SavedCheck[]>; // newest first
  add(c: NewCheck): Promise<SavedCheck>;
  remove(id: string): Promise<void>;
}

/** The part of window.localStorage we use (so tests can pass a plain object). */
export type KeyValue = Pick<Storage, "getItem" | "setItem">;

const KEY = "regimecast.checks.v1";
export const MAX_SAVED = 200;

export class BrowserHistory implements HistoryStore {
  constructor(private storage: () => KeyValue | null = () => (typeof window === "undefined" ? null : window.localStorage)) {}

  private read(): SavedCheck[] {
    try {
      const raw = this.storage()?.getItem(KEY);
      const v = raw ? JSON.parse(raw) : [];
      return Array.isArray(v) ? v : [];
    } catch {
      return []; // private window, blocked storage or a damaged entry: start empty rather than break the page
    }
  }

  private write(list: SavedCheck[]) {
    try {
      this.storage()?.setItem(KEY, JSON.stringify(list.slice(0, MAX_SAVED)));
    } catch {
      /* storage full or blocked: the check still shows, it just is not kept */
    }
  }

  async list() {
    return this.read();
  }

  async add(c: NewCheck) {
    const saved: SavedCheck = { ...c, id: `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`, created_at: new Date().toISOString() };
    this.write([saved, ...this.read()]);
    return saved;
  }

  async remove(id: string) {
    this.write(this.read().filter((c) => c.id !== id));
  }
}

export const historyStore: HistoryStore = new BrowserHistory();

export type HistoryFilter = { symbol: Symbol | "all"; from: string; to: string }; // dates as YYYY-MM-DD (UTC), "" = open

/** Checks for one coin and/or a date range (both ends included, UTC days). */
export function filterChecks(list: SavedCheck[], f: HistoryFilter): SavedCheck[] {
  return list.filter((c) => {
    const day = c.created_at.slice(0, 10);
    return (f.symbol === "all" || c.symbol === f.symbol) && (!f.from || day >= f.from) && (!f.to || day <= f.to);
  });
}
