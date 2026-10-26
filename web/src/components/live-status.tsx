"use client";

import { api, getJson, load } from "@/lib/api";
import { ago, compact } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { Badge, Card, LoadView, NotReady, Stat } from "./ui";

type RecorderHealth = {
  http_status: number;
  status: string;
  started: string;
  stale: Record<string, unknown>;
  connected: Record<string, boolean>;
  messages: Record<string, number>;
  last_message: Record<string, string>;
  recent_error_count: number;
  checked_at: string;
};

const STREAMS: Record<string, string> = {
  markprice: "Binance mark price + funding",
  depth: "Binance order book (top 20)",
  liquidations: "Binance liquidations",
  open_interest: "Binance open interest",
  hl_orders: "Hyperliquid big orders (Whale Watch)",
  hl_positions: "Hyperliquid top-account positions",
  hl_ctx: "Hyperliquid funding + open interest",
  hl_watchlist: "Hyperliquid wallet watchlist",
};

export function LiveStatus() {
  const [apiH, r1] = useLoad(() => load(() => api.GET("/health")), "", 30000);
  const [rec, r2] = useLoad(() => getJson<RecorderHealth>("/recorder/health"), "", 30000);
  return (
    <div className="space-y-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Card title="RegimeCast API" action={apiH.state === "ok" ? <Badge tone="good">online</Badge> : apiH.state === "error" ? <Badge tone="bad">down</Badge> : null}>
          <LoadView load={apiH} onRetry={r1} lines={2} notReady={() => <NotReady title="API starting up" />}>
            {(d) => {
              const h = d as { version?: string; time?: string };
              return <Stat label="Version" value={h.version ?? "?"} sub={h.time ? `server time ${h.time.slice(11, 19)} UTC` : undefined} />;
            }}
          </LoadView>
        </Card>
        <Card
          title="Live recorder"
          action={rec.state === "ok" ? <Badge tone={rec.data.status === "ok" ? "good" : "warn"}>{rec.data.status === "ok" ? "all streams fresh" : "some streams stale"}</Badge> : null}
        >
          <LoadView load={rec} onRetry={r2} lines={2} notReady={() => <NotReady title="Recorder status unavailable" />}>
            {(d) => <Stat label="Recording since" value={ago(d.started)} sub={`${d.recent_error_count} recent reconnects or retries (it heals itself)`} />}
          </LoadView>
        </Card>
      </div>
      {rec.state === "ok" && (
        <Card title="Streams recorded 24/7 (data with no free archive)">
          <ul className="divide-y divide-border text-sm">
            {Object.entries(STREAMS).map(([k, name]) => {
              const stale = k in rec.data.stale;
              const last = rec.data.last_message[k];
              return (
                <li key={k} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span>{name}</span>
                  <span className="flex items-center gap-2 text-xs text-muted">
                    {compact(rec.data.messages[k] ?? 0)} msgs · {last ? ago(last) : "none yet"}
                    <Badge tone={stale ? "warn" : "good"}>{stale ? "stale" : "ok"}</Badge>
                  </span>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 text-xs text-muted">
            Liquidations and Whale Watch only exist live, so the recorder keeps them. The wallet watchlist refreshes once a day by design.
          </p>
        </Card>
      )}
    </div>
  );
}
