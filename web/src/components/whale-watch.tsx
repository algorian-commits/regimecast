"use client";

// Whale Watch (M12): big moves by top Hyperliquid accounts, fresh wallets and suspicious timing,
// each with the reason it fired. Data from /whales/events and /whales/status (public on-chain data).
import { useState } from "react";
import { api, load, SYMBOLS, type Schemas, type Symbol } from "@/lib/api";
import { ago, coin } from "@/lib/format";
import { clock } from "@/lib/replay";
import { useLoad } from "@/lib/use-load";
import { Badge, Card, LoadView, NotReady } from "./ui";

type Kind = Schemas["WhaleEvent"]["kind"];
const KINDS: { v: Kind; label: string; tone: "warn" | "bad" | "muted" }[] = [
  { v: "whale_move", label: "Whale move", tone: "muted" },
  { v: "fresh_wallet", label: "Fresh wallet", tone: "warn" },
  { v: "suspicious_timing", label: "Timing pattern", tone: "bad" },
];
const sel = "max-w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm";
const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`;

function Status() {
  const [st] = useLoad(() => load(() => api.GET("/whales/status")), "status", 60_000);
  if (st.state !== "ok") return null;
  const s = st.data;
  return (
    <p className="text-xs text-muted">
      {s.running ? (s.connected ? "● live" : "○ connecting") : "○ not running on this server"} · {s.tracked_accounts} top accounts tracked ·{" "}
      {s.known_accounts} accounts seen{s.last_row_at ? ` · last data ${ago(s.last_row_at)}` : ""}
    </p>
  );
}

export function WhaleWatch() {
  const [symbol, setSymbol] = useState<Symbol | "">("");
  const [kind, setKind] = useState<Kind | "">("");
  const [ev, reload] = useLoad(
    () => load(() => api.GET("/whales/events", { params: { query: { symbol: symbol || undefined, kind: kind || undefined, limit: 100 } } })),
    `${symbol}|${kind}`,
    60_000,
  );
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <select className={sel} value={symbol} onChange={(e) => setSymbol(e.target.value as Symbol | "")} aria-label="Coin">
            <option value="">All coins</option>
            {SYMBOLS.map((s) => (
              <option key={s} value={s}>
                {coin(s)}
              </option>
            ))}
          </select>
          <select className={sel} value={kind} onChange={(e) => setKind(e.target.value as Kind | "")} aria-label="Kind">
            <option value="">All kinds</option>
            {KINDS.map((k) => (
              <option key={k.v} value={k.v}>
                {k.label}
              </option>
            ))}
          </select>
          <div className="ml-auto">
            <Status />
          </div>
        </div>
        <LoadView load={ev} lines={1} notReady={() => null}>
          {(d) => (
            <p className="mt-3 text-xs text-muted">
              Whale move: a top account&apos;s position changes by at least ${(d.thresholds.whale_move_usd / 1e6).toFixed(0)}M. Fresh wallet: an account
              not seen before shows up holding at least ${(d.thresholds.fresh_wallet_usd / 1e6).toFixed(0)}M. Timing pattern: a position of at least $
              {(d.thresholds.timing_usd / 1e6).toFixed(0)}M opened up to {d.thresholds.timing_hours} h before the stress gauge turned elevated or high.{" "}
              {d.note} Get them in your inbox with a &ldquo;Whale Watch&rdquo; alert on your account page.
            </p>
          )}
        </LoadView>
      </Card>
      <LoadView load={ev} onRetry={reload} lines={6} notReady={(d) => <NotReady title="Whale Watch not available">{d}</NotReady>}>
        {(d) =>
          d.events.length === 0 ? (
            <NotReady title="No events yet">The feed checks the top accounts every 5 minutes; events show up here as they happen.</NotReady>
          ) : (
            <ul className="space-y-2">
              {d.events.map((e) => {
                const k = KINDS.find((x) => x.v === e.kind)!;
                return (
                  <li key={`${e.kind}-${e.address}-${e.ts}`} className="rounded-xl border border-border bg-surface p-3 sm:p-4">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge tone={k.tone}>{k.label}</Badge>
                      <span className="text-sm font-medium">{e.title}</span>
                      <span className="ml-auto text-xs text-muted">{clock(e.ts)}</span>
                    </div>
                    <p className="mt-1.5 text-sm text-muted">{e.reason}</p>
                    <a href={e.explorer_url} target="_blank" rel="noreferrer" className="mt-1 inline-block font-mono text-xs text-accent hover:underline">
                      {short(e.address)} on the Hyperliquid explorer
                    </a>
                  </li>
                );
              })}
            </ul>
          )
        }
      </LoadView>
    </div>
  );
}
