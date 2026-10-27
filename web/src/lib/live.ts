"use client";

// Live prices over a WebSocket (API /ws/live, one snapshot per second), shared by every
// component on the page through one connection. Reconnects by itself with growing pauses
// (1 s, 2 s, 4 s ... 15 s) and also when no message arrives for a while.
import { useSyncExternalStore } from "react";
import type { Symbol } from "./api";

export type LiveCandle = { t: string; open: number; high: number; low: number; close: number; volume: number; closed: boolean };
export type BookSummary = {
  bid: number;
  ask: number;
  mid: number;
  spread_bps: number;
  bid_depth_usdt: number;
  ask_depth_usdt: number;
  depth_2bps_usdt: number;
  imbalance: number;
  levels: number;
  reach_bps: number;
  exchange_time: string;
};
export type LiveCoin = {
  mark_price?: number;
  index_price?: number;
  funding_rate?: number;
  next_funding_time?: string;
  exchange_time?: string;
  candle?: LiveCandle;
  book?: BookSummary;
  stale: boolean;
};
export type LiveTick = { type: "tick"; at: string; upstream: "ok" | "reconnecting"; coins: Partial<Record<Symbol, LiveCoin>> };
export type LiveStatus = "connecting" | "live" | "reconnecting";
export type BookPoint = { t: number; spread_bps: number; depth: number; imbalance: number };

export const HISTORY_POINTS = 300; // 5 minutes at one snapshot per second
const SILENCE_MS = 10_000;

/** Pause before reconnect attempt `n` (0-based): 1 s, 2 s, 4 s, 8 s, then 15 s. */
export function backoffMs(n: number) {
  return Math.min(15_000, 1000 * 2 ** n);
}

/** Add one snapshot's order-book numbers to the rolling per-coin history (oldest first, capped). */
export function appendBookHistory(hist: Partial<Record<Symbol, BookPoint[]>>, tick: LiveTick, max = HISTORY_POINTS) {
  const out: Partial<Record<Symbol, BookPoint[]>> = { ...hist };
  const t = Date.parse(tick.at);
  for (const [s, c] of Object.entries(tick.coins) as [Symbol, LiveCoin][]) {
    if (!c.book || c.stale) continue;
    const prev = out[s] ?? [];
    if (prev.length && prev[prev.length - 1].t >= t) continue;
    const next = [...prev, { t, spread_bps: c.book.spread_bps, depth: c.book.bid_depth_usdt + c.book.ask_depth_usdt, imbalance: c.book.imbalance }];
    out[s] = next.length > max ? next.slice(next.length - max) : next;
  }
  return out;
}

export function isTick(x: unknown): x is LiveTick {
  return !!x && typeof x === "object" && (x as LiveTick).type === "tick" && typeof (x as LiveTick).coins === "object";
}

type State = { status: LiveStatus; tick: LiveTick | null; history: Partial<Record<Symbol, BookPoint[]>> };

// ------------------------------------------------------------------ one shared connection
let state: State = { status: "connecting", tick: null, history: {} };
const listeners = new Set<() => void>();
let ws: WebSocket | null = null;
let attempt = 0;
let retry: ReturnType<typeof setTimeout> | undefined;
let watchdog: ReturnType<typeof setTimeout> | undefined;
let stopTimer: ReturnType<typeof setTimeout> | undefined;
let wsUrl: string | null = null;
let opening = false;

function set(p: Partial<State>) {
  state = { ...state, ...p };
  listeners.forEach((l) => l());
}

function armWatchdog() {
  clearTimeout(watchdog);
  watchdog = setTimeout(() => ws?.close(), SILENCE_MS); // silent connection: start over
}

function scheduleReconnect() {
  clearTimeout(retry);
  if (!listeners.size) return;
  set({ status: "reconnecting" });
  retry = setTimeout(() => {
    retry = undefined;
    open();
  }, backoffMs(attempt++));
}

async function open() {
  if (ws || opening || !listeners.size) return;
  opening = true;
  try {
    wsUrl ??= ((await (await fetch("/live-config", { cache: "no-store" })).json()) as { ws_url: string }).ws_url;
  } catch {
    scheduleReconnect();
    return;
  } finally {
    opening = false;
  }
  if (ws || !listeners.size) return;
  const sock = new WebSocket(wsUrl);
  ws = sock;
  armWatchdog();
  sock.onmessage = (e) => {
    let msg: unknown;
    try {
      msg = JSON.parse(e.data);
    } catch {
      return;
    }
    if (!isTick(msg)) return;
    attempt = 0;
    armWatchdog();
    set({ status: "live", tick: msg, history: appendBookHistory(state.history, msg) });
  };
  sock.onclose = () => {
    if (ws !== sock) return;
    ws = null;
    clearTimeout(watchdog);
    scheduleReconnect();
  };
  sock.onerror = () => sock.close();
}

function subscribe(l: () => void) {
  listeners.add(l);
  clearTimeout(stopTimer);
  if (!ws && !retry) open();
  return () => {
    listeners.delete(l);
    if (!listeners.size) {
      // keep the socket for a moment so moving between pages does not reconnect
      stopTimer = setTimeout(() => {
        if (listeners.size) return;
        clearTimeout(retry);
        retry = undefined;
        const s = ws;
        ws = null;
        s?.close();
        clearTimeout(watchdog);
        set({ status: "connecting" });
      }, 5000);
    }
  };
}

const getSnapshot = () => state;
const SERVER: State = { status: "connecting", tick: null, history: {} };

/** Latest live snapshot, connection status and the last 5 minutes of order-book numbers. */
export function useLive() {
  return useSyncExternalStore(subscribe, getSnapshot, () => SERVER);
}
