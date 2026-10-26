"use client";

import { useState } from "react";
import { getJson, SYMBOLS, type Symbol } from "@/lib/api";
import { ago, coin, compact } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { Badge, Card, LoadView, NotReady } from "./ui";

// Shape of data/reports/data_health_<symbol>.json written by regimecast.data.validate
// (the API passes it through as free-form JSON, so it is typed here).
type Dataset = { rows: number; first: string; last: string; duplicates: number; gaps?: number; missing_steps?: number; max_abs_rate?: number };
type Report = {
  symbol: string;
  generated_at: string;
  known_issues: Record<string, { date: string; what: string; fix: string }>;
  datasets: Record<string, Dataset>;
  mark_vs_last?: { checked: number; over_limit: number; max_abs_dev: number };
};

const NAMES: Record<string, string> = {
  klines: "Price candles (1 min)",
  markPriceKlines: "Mark price (1 min)",
  indexPriceKlines: "Index price (1 min)",
  premiumIndexKlines: "Premium index (1 min)",
  fundingRate: "Funding rate (8 h)",
  metrics: "Open interest (5 min)",
};

const day = (s: string) => s.slice(0, 10);

export function DataHealth() {
  const [symbol, setSymbol] = useState<Symbol>("BTCUSDT");
  const [rep, reload] = useLoad(() => getJson<Report>(`/api/data-health/${symbol}`), symbol);
  return (
    <div className="space-y-4">
      <div className="flex gap-1">
        {SYMBOLS.map((s) => (
          <button
            key={s}
            aria-pressed={s === symbol}
            onClick={() => setSymbol(s)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${s === symbol ? "bg-surface-2 text-text" : "text-muted"}`}
          >
            {coin(s)}
          </button>
        ))}
      </div>
      <LoadView
        load={rep}
        onRetry={reload}
        lines={6}
        notReady={() => (
          <Card>
            <NotReady title={`No health report for ${coin(symbol)} yet`}>
              {symbol === "BTCUSDT" ? "The validator has not been run on this server." : `${coin(symbol)} history is downloaded and checked in Phase 4. BTC is ready now.`}
            </NotReady>
          </Card>
        )}
      >
        {(r) => (
          <>
            <Card title={`Datasets, checked ${ago(r.generated_at)}`}>
              <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
                <table className="w-full min-w-[560px] text-sm">
                  <thead className="text-left text-xs text-muted">
                    <tr>
                      <th className="py-2 pr-3 font-medium">Dataset</th>
                      <th className="py-2 pr-3 font-medium">Rows</th>
                      <th className="py-2 pr-3 font-medium">From – to</th>
                      <th className="py-2 pr-3 font-medium">Duplicates</th>
                      <th className="py-2 pr-3 font-medium">Gaps</th>
                      <th className="py-2 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(r.datasets).map(([k, d]) => {
                      const clean = d.duplicates === 0 && !d.gaps;
                      return (
                        <tr key={k} className="border-t border-border">
                          <td className="py-2 pr-3">{NAMES[k] ?? k}</td>
                          <td className="py-2 pr-3 font-mono">{compact(d.rows)}</td>
                          <td className="py-2 pr-3 font-mono text-xs">
                            {day(d.first)} – {day(d.last)}
                          </td>
                          <td className="py-2 pr-3 font-mono">{d.duplicates}</td>
                          <td className="py-2 pr-3 font-mono">{d.gaps == null ? "–" : `${d.gaps} (${d.missing_steps} steps)`}</td>
                          <td className="py-2">
                            <Badge tone={clean ? "good" : "warn"}>{clean ? "clean" : "explained"}</Badge>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {r.mark_vs_last && (
                <p className="mt-3 text-xs text-muted">
                  Mark vs last price: {r.mark_vs_last.over_limit} of {compact(r.mark_vs_last.checked)} minutes differ by more than the limit (largest{" "}
                  {(100 * r.mark_vs_last.max_abs_dev).toFixed(1)}%, during known crashes). Short gaps are forward-filled at most 5 minutes.
                </p>
              )}
            </Card>
            <Card title="Known Binance archive issues and how we handle them">
              <ul className="space-y-3 text-sm">
                {Object.entries(r.known_issues).map(([k, i]) => (
                  <li key={k}>
                    <div className="font-medium">
                      {i.what} <span className="text-xs font-normal text-muted">({i.date})</span>
                    </div>
                    <div className="text-muted">{i.fix}</div>
                  </li>
                ))}
              </ul>
            </Card>
          </>
        )}
      </LoadView>
    </div>
  );
}
