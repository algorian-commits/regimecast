"use client";

// Popular trader tracker (13D): the copy test's verdict, scorecards, what skilled traders hold, and the liquidation map.
// Data from /traders/*. The sentence about following them comes from the API, which allows "has worked" only after a pass.
import { useState } from "react";
import { api, load, SYMBOLS, type Symbol } from "@/lib/api";
import { ago, coin, pct, usd } from "@/lib/format";
import { clock } from "@/lib/replay";
import { bandRows, copyBadge, holdWords, labelTone, mapTotals, money, netWords, rowsAbovePrice, type Band, type Card as CardT } from "@/lib/traders";
import { useLoad } from "@/lib/use-load";
import { Badge, Card, LoadView, NotReady, Stat } from "./ui";

const sel = "max-w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm";
const short = (a: string) => `${a.slice(0, 6)}…${a.slice(-4)}`;
const explorer = (a: string) => `https://app.hyperliquid.xyz/explorer/address/${a}`;
const num = (x: number | null | undefined, d = 2) => (x == null || !Number.isFinite(x) ? "–" : x.toFixed(d));
const th = "whitespace-nowrap px-2 py-1.5 text-left text-xs font-medium text-muted";
const td = "whitespace-nowrap px-2 py-1.5 font-mono text-xs";

function Who({ a }: { a: string }) {
  return (
    <a href={explorer(a)} target="_blank" rel="noreferrer" className="font-mono text-xs text-accent hover:underline">
      {short(a)}
    </a>
  );
}

function CopyTest() {
  const [ct, reload] = useLoad(() => load(() => api.GET("/traders/copy-test")), "copy", 300_000);
  return (
    <LoadView load={ct} onRetry={reload} lines={2} notReady={() => null}>
      {(d) => {
        const b = copyBadge(d.status);
        const r = d.result as Record<string, number | boolean | number[]> | null;
        return (
          <Card>
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={b.tone}>{b.text}</Badge>
              <span className="text-sm font-medium">{d.headline}</span>
            </div>
            {d.plain && <p className="mt-2 text-sm text-muted">{d.plain}</p>}
            {r && typeof r.sharpe_copy === "number" && (
              <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Copying them (Sharpe)" value={num(r.sharpe_copy as number)} sub={`${r.k} accounts, ${r.days} days`} />
                <Stat label="Random accounts (median)" value={num(r.sharpe_random_median as number)} sub="200 random groups of the same size" />
                <Stat label="Buy-and-hold" value={num(r.sharpe_buy_and_hold as number)} sub="BTC, ETH, SOL" />
                <Stat
                  label="Lead over random, 90% range"
                  value={`${num((r.diff_vs_random_ci90 as number[])[0])} to ${num((r.diff_vs_random_ci90 as number[])[1])}`}
                  sub="must sit above 0 to pass"
                />
              </div>
            )}
            {d.status === "passed" && r && (
              <p className="mt-2 text-xs text-muted">
                What was copied: the top {String(r.k)} accounts by skill score measured before the copying period, which is a wider group than the &ldquo;skilled&rdquo; label below. It is one
                test over {String(r.days)} days, in a market where simply holding lost money, so part of the gain may come from being short at the right time.
              </p>
            )}
            {d.forward && (
              <p className="mt-2 text-xs text-muted">
                Forward test: logging from {String(d.forward.started_at).slice(0, 10)}, scored once after {String(d.forward.ends_at).slice(0, 10)}.
              </p>
            )}
            <p className="mt-2 text-xs text-muted">
              Sharpe ratio: average daily result divided by how much it swings, scaled to a year. Higher is better, and it does not depend on account size. The bar was fixed
              before any fills were downloaded. Paper results, not advice.
            </p>
          </Card>
        );
      }}
    </LoadView>
  );
}

function Skilled() {
  const [s, reload] = useLoad(() => load(() => api.GET("/traders/skilled")), "skilled", 60_000);
  return (
    <LoadView load={s} onRetry={reload} lines={4} notReady={(d) => <NotReady title="Scorecards not ready">{d}</NotReady>}>
      {(d) => (
        <div className="space-y-4">
          <Card title="What skilled traders hold, net">
            {d.n_skilled === 0 ? (
              <p className="text-sm text-muted">
                No account passed the skill test across the {d.bh_family.toLocaleString()} tested, so there is nobody to show here. That is a result: most records in this pool cannot be told
                apart from luck.
              </p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {d.net.map((n) => (
                  <li key={n.coin} className="flex flex-wrap gap-x-2">
                    <span className="w-10 font-medium">{n.coin}</span>
                    <span className="text-muted">{netWords(n)}</span>
                  </li>
                ))}
              </ul>
            )}
            {!d.live && <p className="mt-2 text-xs text-warn">The live position feed is not running on this server, so current positions are missing.</p>}
          </Card>
          {d.n_skilled > 0 && (
            <Card title={`Skilled traders and their open positions (${d.n_skilled} of ${d.bh_family.toLocaleString()} tested)`}>
              <ul className="space-y-3">
                {d.traders.map((t) => (
                  <li key={t.card.address} className="border-t border-border pt-2 first:border-t-0 first:pt-0">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                      <Who a={t.card.address} />
                      <span>
                        {t.card.n_days} days, {t.card.n_trades} trades, net {money(t.card.net_pnl)}
                      </span>
                      {t.read_at && <span className="ml-auto">read {ago(t.read_at)}</span>}
                    </div>
                    {t.positions.length === 0 ? (
                      <p className="mt-1 text-xs text-muted">No open BTC, ETH or SOL position.</p>
                    ) : (
                      <div className="mt-1 overflow-x-auto">
                        <table className="w-full">
                          <thead>
                            <tr>
                              {["Coin", "Side", "Size", "Leverage", "Entry", "Liquidation"].map((h) => (
                                <th key={h} className={th}>
                                  {h}
                                </th>
                              ))}
                            </tr>
                          </thead>
                          <tbody>
                            {t.positions.map((p) => (
                              <tr key={p.coin}>
                                <td className={td}>{p.coin}</td>
                                <td className={td}>{p.side}</td>
                                <td className={td}>{money(p.position_usd)}</td>
                                <td className={td}>{p.leverage == null ? "–" : `${p.leverage}x ${p.leverage_type}`}</td>
                                <td className={td}>{usd(p.entry)}</td>
                                <td className={td}>{usd(p.liquidation)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          )}
          {d.trades.length > 0 && (
            <Card title={`Their recent BTC, ETH and SOL trades (latest ${d.trades.length}, last 7 days)`}>
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr>
                      {["When", "Who", "Trade", "Value", "Price", "Realised"].map((h) => (
                        <th key={h} className={th}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {d.trades.map((t) => (
                      <tr key={`${t.address}-${t.ts}-${t.coin}-${t.action}`}>
                        <td className={td}>{clock(t.ts)}</td>
                        <td className={td}>
                          <Who a={t.address} />
                        </td>
                        <td className={td}>
                          {t.coin} {t.action}
                        </td>
                        <td className={td}>{money(t.usd)}</td>
                        <td className={td}>{usd(t.price)}</td>
                        <td className={td}>{money(t.closed_pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      )}
    </LoadView>
  );
}

function MapRow({ b }: { b: ReturnType<typeof bandRows>[number] }) {
  const tip = `${usd(b.price_low)} to ${usd(b.price_high)}: ${money(b.long_usd)} of longs (${b.n_long}), ${money(b.short_usd)} of shorts (${b.n_short})`;
  return (
    <div className="group grid grid-cols-[4.75rem_1fr_1fr] items-center gap-x-1 rounded py-[3px] hover:bg-surface-2" title={tip}>
      <span className="pr-1 text-right font-mono text-[11px] text-muted">{usd(b.price_low, 0)}</span>
      <div className="flex h-3 justify-end border-r border-border">
        {b.long_usd > 0 && <div className="h-3 min-w-0.5 rounded-l" style={{ width: `${100 * b.longShare}%`, background: "var(--chart-5)" }} />}
      </div>
      <div className="flex h-3">
        {b.short_usd > 0 && <div className="h-3 min-w-0.5 rounded-r" style={{ width: `${100 * b.shortShare}%`, background: "var(--chart-6)" }} />}
      </div>
    </div>
  );
}

function BandTable({ bands }: { bands: Band[] }) {
  return (
    <details className="mt-3 text-xs">
      <summary className="cursor-pointer text-muted">Show as a table</summary>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr>
              {["Price band", "From price now", "Longs", "Positions", "Shorts", "Positions"].map((h, i) => (
                <th key={i} className={th}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {bandRows(bands).map((b) => (
              <tr key={b.from_pct}>
                <td className={td}>
                  {usd(b.price_low, 0)} to {usd(b.price_high, 0)}
                </td>
                <td className={td}>
                  {b.from_pct >= 0 ? "+" : ""}
                  {b.from_pct}%
                </td>
                <td className={td}>{money(b.long_usd)}</td>
                <td className={td}>{b.n_long}</td>
                <td className={td}>{money(b.short_usd)}</td>
                <td className={td}>{b.n_short}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function LiquidationMap() {
  const [symbol, setSymbol] = useState<Symbol>("BTCUSDT");
  const [m, reload] = useLoad(() => load(() => api.GET("/traders/liquidation-map/{symbol}", { params: { path: { symbol } } })), symbol, 60_000);
  const picker = (
    <select className={sel} value={symbol} onChange={(e) => setSymbol(e.target.value as Symbol)} aria-label="Coin">
      {SYMBOLS.map((s) => (
        <option key={s} value={s}>
          {coin(s)}
        </option>
      ))}
    </select>
  );
  return (
    <Card title="Liquidation map" action={picker}>
      <LoadView load={m} onRetry={reload} lines={6} notReady={(d) => <p className="text-sm text-muted">Not available yet: {d}.</p>}>
        {(d) => {
          const t = mapTotals(d.bands);
          const rows = bandRows(d.bands);
          const above = rowsAbovePrice(d.bands);
          return (
            <>
              <p className="text-xs font-medium">{d.label[0].toUpperCase() + d.label.slice(1)}.</p>
              <p className="mt-1 text-xs text-muted">
                Each bar is the dollar value of open positions that Hyperliquid would liquidate if the price reached that band. It shows where forced selling or buying could start, not where the price
                will go. Margin changes move these prices, so this is a snapshot.
              </p>
              {rows.length === 0 ? (
                <p className="mt-3 text-sm text-muted">No tracked {d.coin} position would be liquidated within {d.span_pct}% of the price.</p>
              ) : (
                <div className="mt-3">
                  <div className="grid grid-cols-[4.75rem_1fr_1fr] gap-x-1 pb-1 text-xs text-muted">
                    <span className="pr-1 text-right">Price</span>
                    <span className="flex items-center justify-end gap-1.5">
                      Longs {money(t.long_usd)} ({t.n_long})
                      <i className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--chart-5)" }} />
                    </span>
                    <span className="flex items-center gap-1.5">
                      <i className="inline-block h-2 w-2 rounded-sm" style={{ background: "var(--chart-6)" }} />
                      Shorts {money(t.short_usd)} ({t.n_short})
                    </span>
                  </div>
                  {rows.map((b, i) => (
                    <div key={b.from_pct}>
                      {i === above && <PriceLine mark={d.mark} />}
                      <MapRow b={b} />
                    </div>
                  ))}
                  {above === rows.length && <PriceLine mark={d.mark} />}
                </div>
              )}
              <BandTable bands={d.bands} />
              <p className="mt-2 text-xs text-muted">
                {d.n_positions} open positions from {d.n_accounts} accounts, of {d.accounts_read.toLocaleString()} read so far out of {d.accounts_followed.toLocaleString()} followed.{" "}
                {money(d.outside_span_usd)} sits more than {d.span_pct}% away; {d.n_no_liquidation_price} positions ({money(d.no_liquidation_price_usd)}) have no liquidation price.
                {d.oldest_read_at ? ` Positions read ${ago(d.oldest_read_at)} at the oldest.` : ""}
              </p>
            </>
          );
        }}
      </LoadView>
    </Card>
  );
}

function PriceLine({ mark }: { mark: number }) {
  return (
    <div className="my-1 flex items-center gap-2 text-[11px] font-medium">
      <span className="h-px flex-1 bg-text/40" />
      Price now {usd(mark)}
      <span className="h-px flex-1 bg-text/40" />
    </div>
  );
}

type LabelFilter = CardT["label"] | "";

function Scorecards() {
  const [label, setLabel] = useState<LabelFilter>("skilled");
  const [cards, reload] = useLoad(() => load(() => api.GET("/traders/scorecards", { params: { query: { label: label || undefined, limit: 100 } } })), label);
  const picker = (
    <select className={sel} value={label} onChange={(e) => setLabel(e.target.value as LabelFilter)} aria-label="Label">
      <option value="skilled">Skilled</option>
      <option value="unproven">Unproven</option>
      <option value="losing">Losing</option>
      <option value="">All</option>
    </select>
  );
  return (
    <Card title="Scorecards" action={picker}>
      <LoadView load={cards} onRetry={reload} lines={5} notReady={(d) => <p className="text-sm text-muted">Not available yet: {d}.</p>}>
        {(d) => {
          const c = d.summary.counts;
          return (
            <>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Skilled" value={c.skilled ?? 0} sub={`of ${d.summary.accounts_with_perp_fills.toLocaleString()} tested`} />
                <Stat label="Unproven" value={c.unproven ?? 0} sub="can't be told from luck" />
                <Stat label="Losing" value={c.losing ?? 0} sub="net loss over the history" />
                <Stat label="Pool" value={d.summary.pool_size.toLocaleString()} sub={`${c.no_trades ?? 0} had no perp trades`} />
              </div>
              {d.cards.length === 0 ? (
                <p className="mt-3 text-sm text-muted">No account has this label.</p>
              ) : (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr>
                        {["Account", "Label", "Days", "Trades", "Win rate", "Profit factor", "Net PnL", "Return", "Sharpe", "Max drop", "Leverage", "Typical hold", "Main coins"].map((h) => (
                          <th key={h} className={th}>
                            {h}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {d.cards.map((x) => (
                        <tr key={x.address} className="border-t border-border">
                          <td className={td}>
                            <Who a={x.address} />
                          </td>
                          <td className={td}>
                            <Badge tone={labelTone(x.label)}>{x.label}</Badge>
                          </td>
                          <td className={td}>
                            {x.n_days}
                            {x.history_cut_short ? "*" : ""}
                          </td>
                          <td className={td}>{x.n_trades}</td>
                          <td className={td}>{pct(x.win_rate, 0)}</td>
                          <td className={td}>{num(x.profit_factor)}</td>
                          <td className={td}>{money(x.net_pnl)}</td>
                          <td className={td}>{pct(x.return_on_account, 0)}</td>
                          <td className={td}>{num(x.sharpe)}</td>
                          <td className={td}>{money(x.max_drawdown)}</td>
                          <td className={td}>{x.avg_leverage == null ? "–" : `${x.avg_leverage.toFixed(1)}x`}</td>
                          <td className={td}>{holdWords(x.median_hold_hours)}</td>
                          <td className={td}>{x.coins.slice(0, 3).map((k) => k.coin).join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {d.total > d.cards.length && <p className="mt-2 text-xs text-muted">Showing the {d.cards.length} with the highest skill score, of {d.total.toLocaleString()}.</p>}
              <details className="mt-3 text-xs text-muted">
                <summary className="cursor-pointer">What these numbers mean, and their limits</summary>
                <ul className="mt-2 list-disc space-y-1 pl-4">
                  <li>
                    <b>Skilled</b>: average daily profit is above zero, and still significant after correcting for testing {d.summary.accounts_with_perp_fills.toLocaleString()} accounts at once (false discovery
                    rate {pct(d.summary.fdr, 0)}). It needs 30+ days and 10+ finished trades.
                  </li>
                  <li>
                    <b>Trades</b>: finished round trips, flat to flat. <b>Win rate</b>: share of them with a profit after fees. <b>Profit factor</b>: total won divided by total lost.
                  </li>
                  <li>
                    <b>Return</b>: net PnL as a share of today&apos;s account value. <b>Sharpe</b>: average daily PnL divided by its swings, scaled to a year. <b>Max drop</b>: the largest fall in cumulative
                    PnL from a peak. <b>Leverage</b>: an estimate, average peak trade size over today&apos;s account value.
                  </li>
                  <li>* Hyperliquid no longer serves this account&apos;s older fills, so its history is cut short.</li>
                  {d.summary.limits.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              </details>
            </>
          );
        }}
      </LoadView>
    </Card>
  );
}

function Events() {
  const [ev] = useLoad(() => load(() => api.GET("/traders/events", { params: { query: { limit: 20 } } })), "events", 60_000);
  if (ev.state !== "ok" || ev.data.events.length === 0) return null;
  return (
    <Card title="Latest alerts">
      <ul className="space-y-2">
        {ev.data.events.map((e) => (
          <li key={`${e.kind}-${e.address}-${e.ts}`} className="text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone="muted">{e.kind === "skilled_trader" ? "Skilled trader" : "Liquidation cluster"}</Badge>
              <span className="font-medium">{e.title}</span>
              <span className="ml-auto text-xs text-muted">{clock(e.ts)}</span>
            </div>
            <p className="mt-1 text-muted">{e.reason}</p>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-muted">Get them in your inbox with a &ldquo;Skilled trader move or liquidation cluster&rdquo; alert on your account page.</p>
    </Card>
  );
}

export function Traders() {
  return (
    <div className="space-y-4">
      <CopyTest />
      <Skilled />
      <LiquidationMap />
      <Scorecards />
      <Events />
    </div>
  );
}
