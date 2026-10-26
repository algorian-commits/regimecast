"use client";

import Link from "next/link";
import { api, load, SYMBOLS, type Symbol } from "@/lib/api";
import { ago, coin, compact, pct, usd } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { Badge, Card, LoadView, NotReady, Stat } from "./ui";

function MarketCard({ symbol }: { symbol: Symbol }) {
  const [m, reload] = useLoad(() => load(() => api.GET("/market/{symbol}", { params: { path: { symbol } } })), symbol, 15000);
  return (
    <Card
      title={`${coin(symbol)} perpetual`}
      action={m.state === "ok" && m.data.stale ? <Badge tone="warn">delayed</Badge> : <Badge tone="good">live</Badge>}
    >
      <LoadView
        load={m}
        onRetry={reload}
        lines={3}
        notReady={() => <NotReady title="Live price unavailable">Binance did not answer. It will retry automatically.</NotReady>}
      >
        {(d) => (
          <div className="space-y-3">
            <Stat label="Mark price (USDT)" value={usd(d.mark_price)} sub={`updated ${ago(d.exchange_time)}`} />
            <div className="grid grid-cols-2 gap-3">
              <Stat label="Funding / 8h" value={pct(d.funding_rate, 4)} tone={d.funding_rate < 0 ? "warn" : undefined} />
              <Stat label="Open interest" value={`$${compact(d.open_interest_usdt)}`} />
            </div>
            <Link href={`/risk-check?symbol=${symbol}`} className="inline-block text-sm font-medium text-accent hover:underline">
              Check a trade in {coin(symbol)} →
            </Link>
          </div>
        )}
      </LoadView>
    </Card>
  );
}

export function MarketCards() {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {SYMBOLS.map((s) => (
        <MarketCard key={s} symbol={s} />
      ))}
    </div>
  );
}
