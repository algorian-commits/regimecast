"use client";

// Risk Radar: live prices (WebSocket), the regime-coloured chart, the order book's inside
// (spread and depth), the regime / volatility / stress gauges and the news mood.
import Link from "next/link";
import { useState } from "react";
import { orderCoins, useSession } from "@/lib/account";
import { api, load, SYMBOLS, type Symbol } from "@/lib/api";
import { hourChangePct, sparkPath } from "@/lib/dashboard";
import { ago, coin, compact, pct, usd } from "@/lib/format";
import { useLive, type BookPoint, type LiveCoin, type LiveStatus } from "@/lib/live";
import { useLoad } from "@/lib/use-load";
import { Gauges } from "./gauges";
import { NewsMoodPanel } from "./news-mood-panel";
import { RegimeChart } from "./regime-chart";
import { Badge, Card, NotReady, Skeleton, Stat } from "./ui";

export function ConnectionBadge({ status, upstream }: { status: LiveStatus; upstream?: "ok" | "reconnecting" }) {
  if (status === "live" && upstream === "ok") return <Badge tone="good">● live</Badge>;
  if (status === "live") return <Badge tone="warn">Binance reconnecting…</Badge>;
  if (status === "reconnecting") return <Badge tone="warn">reconnecting…</Badge>;
  return <Badge>connecting…</Badge>;
}

function PriceTile({ symbol, c, selected, onSelect }: { symbol: Symbol; c?: LiveCoin; selected: boolean; onSelect: () => void }) {
  // Open interest is not in Binance's price stream: read it from /market every 30 s (also the fallback price).
  const [m] = useLoad(() => load(() => api.GET("/market/{symbol}", { params: { path: { symbol } } })), symbol, 30_000);
  const rest = m.state === "ok" ? m.data : undefined;
  const price = c && !c.stale && c.mark_price != null ? c.mark_price : rest?.mark_price;
  const funding = c?.funding_rate ?? rest?.funding_rate;
  const ch = hourChangePct(c);
  return (
    <button
      onClick={onSelect}
      aria-pressed={selected}
      className={`min-w-0 rounded-xl border bg-surface p-2.5 text-left transition hover:border-accent sm:p-4 ${selected ? "border-accent ring-1 ring-accent" : "border-border"}`}
    >
      {/* phones: one compact row of three; wider screens add funding and open interest */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between">
        <span className="text-xs font-semibold text-muted sm:text-sm">
          {coin(symbol)}
          <span className="hidden sm:inline"> perpetual</span>
        </span>
        {ch != null && (
          <span className={`order-last font-mono text-[11px] sm:order-none sm:text-xs ${ch >= 0 ? "text-good" : "text-bad"}`}>
            {`${ch >= 0 ? "+" : ""}${ch.toFixed(2)}%`}
            <span className="hidden sm:inline"> this hour</span>
          </span>
        )}
        {price == null ? (
          <Skeleton className="mt-1 h-5 w-full sm:hidden" />
        ) : (
          <span className="truncate font-mono text-sm font-semibold tabular-nums sm:hidden">{usd(price)}</span>
        )}
      </div>
      {price == null ? (
        <Skeleton className="mt-2 hidden h-7 w-32 sm:block" />
      ) : (
        <div className="mt-1 hidden font-mono text-2xl font-semibold tabular-nums sm:block">{usd(price)}</div>
      )}
      <div className="mt-2 hidden grid-cols-2 gap-2 text-xs text-muted sm:grid">
        <span>
          Funding <span className={`font-mono ${funding != null && funding < 0 ? "text-warn" : "text-text"}`}>{pct(funding, 4)}</span>
        </span>
        <span>
          OI <span className="font-mono text-text">{rest ? `$${compact(rest.open_interest_usdt)}` : "–"}</span>
        </span>
      </div>
    </button>
  );
}

function Spark({ points, pick, label }: { points: BookPoint[]; pick: (p: BookPoint) => number; label: string }) {
  const d = sparkPath(points.map(pick), 200, 36);
  return (
    <svg viewBox="0 0 200 36" className="h-9 w-full" preserveAspectRatio="none" role="img" aria-label={label}>
      <path d={d} fill="none" stroke="var(--chart-1)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

function BookPanel({ symbol, c, history }: { symbol: Symbol; c?: LiveCoin; history: BookPoint[] }) {
  const b = c?.book;
  return (
    <Card title={`Order book, inside (${coin(symbol)})`}>
      {!b || c?.stale ? (
        <NotReady title="Waiting for the order book">Binance&apos;s top 20 levels arrive twice a second once the live connection is up.</NotReady>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Spread" value={`${b.spread_bps.toFixed(2)} bps`} sub={`${usd(b.ask - b.bid)} USDT`} />
            <Stat label="Depth, top 20 levels" value={`$${compact(b.bid_depth_usdt + b.ask_depth_usdt)}`} sub={`within ±${b.reach_bps.toFixed(1)} bps`} />
          </div>
          <div>
            <div className="mb-1 flex justify-between text-xs text-muted">
              <span>Bids ${compact(b.bid_depth_usdt)}</span>
              <span>Asks ${compact(b.ask_depth_usdt)}</span>
            </div>
            <div className="flex h-3 w-full overflow-hidden rounded-full bg-surface-2" aria-label={`Order book imbalance ${(b.imbalance * 100).toFixed(0)}%`}>
              <div className="bg-good transition-all duration-500" style={{ width: `${((b.imbalance + 1) / 2) * 100}%` }} />
              <div className="flex-1 bg-bad/70" />
            </div>
          </div>
          <div>
            <div className="text-xs text-muted">Depth, last {Math.max(1, Math.round(history.length / 60))} min</div>
            <Spark points={history} pick={(p) => p.depth} label="Top-20 depth over the last minutes" />
            <div className="text-xs text-muted">Spread, last {Math.max(1, Math.round(history.length / 60))} min</div>
            <Spark points={history} pick={(p) => p.spread_bps} label="Spread over the last minutes" />
          </div>
          <div className="text-xs text-muted">
            Top 20 levels only: the inside of the book, not its full depth. Updated {ago(b.exchange_time)}.
          </div>
        </div>
      )}
    </Card>
  );
}

function AccountStrip() {
  const s = useSession();
  if (s.state === "in") {
    const p = s.me.profile;
    return (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-border bg-surface px-4 py-3 text-sm">
        <span>
          Paper balance <span className="font-mono font-semibold">{usd(p.paper_balance)} USDT</span>
        </span>
        <span>
          Risk limit <span className="font-mono font-semibold">{pct(p.risk_limit, p.risk_limit < 0.01 ? 2 : 1)}</span> chance of liquidation
        </span>
        <span className="text-muted">Default {p.default_margin_mode} margin</span>
        <Link href="/account" className="ml-auto font-medium text-accent hover:underline">
          Edit →
        </Link>
      </div>
    );
  }
  if (s.state === "out") {
    return (
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border border-dashed border-border px-4 py-3 text-sm">
        <span className="text-muted">Sign up to keep a paper balance, your risk limit, favourite coins and saved checks.</span>
        <span className="ml-auto flex gap-3">
          <Link href="/login" className="font-medium text-accent hover:underline">
            Log in
          </Link>
          <Link href="/signup" className="font-medium text-accent hover:underline">
            Sign up →
          </Link>
        </span>
      </div>
    );
  }
  return null; // loading, or accounts not set up on this server: say nothing
}

export function LiveDashboard() {
  const live = useLive();
  const session = useSession();
  const coins = orderCoins(SYMBOLS, session.state === "in" ? session.me.profile.favourite_coins : undefined);
  const [picked, setPicked] = useState<Symbol | null>(null);
  const symbol = picked ?? coins[0];
  const c = live.tick?.coins[symbol];
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <ConnectionBadge status={live.status} upstream={live.tick?.upstream} />
        <Link href={`/risk-check?symbol=${symbol}`} className="text-sm font-medium text-accent hover:underline">
          Check a trade in {coin(symbol)} →
        </Link>
      </div>
      <AccountStrip />
      <div className="grid grid-cols-3 gap-2 sm:gap-3">
        {coins.map((s) => (
          <PriceTile key={s} symbol={s} c={live.tick?.coins[s]} selected={s === symbol} onSelect={() => setPicked(s)} />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title={`${coin(symbol)} · 1-hour candles (drag for 30 days)`} className="lg:col-span-2">
          <RegimeChart symbol={symbol} live={c && !c.stale ? c.candle : undefined} />
        </Card>
        <BookPanel symbol={symbol} c={c} history={live.history[symbol] ?? []} />
      </div>
      <Gauges symbol={symbol} />
      <NewsMoodPanel />
    </div>
  );
}
