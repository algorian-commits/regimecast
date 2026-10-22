"use client";

import { useState } from "react";
import { getJson, SYMBOLS, type Symbol } from "@/lib/api";
import { ago, coin, pct } from "@/lib/format";
import { summarize, type NewsMood } from "@/lib/news-mood";
import { useLoad } from "@/lib/use-load";
import { TimeChart } from "./charts";
import { Badge, Card, LoadView, NotReady, Stat } from "./ui";

export function NewsMoodPanel() {
  const [symbol, setSymbol] = useState<Symbol>("BTCUSDT");
  const [mood, reload] = useLoad(() => getJson<NewsMood>(`/api/news-mood?symbol=${symbol}&hours=168`), symbol, 5 * 60_000);
  return (
    <Card
      title="News mood (last 7 days)"
      action={
        <div className="flex gap-1" role="tablist">
          {SYMBOLS.map((s) => (
            <button
              key={s}
              role="tab"
              aria-selected={s === symbol}
              onClick={() => setSymbol(s)}
              className={`rounded-md px-2 py-1 text-xs font-medium ${s === symbol ? "bg-surface-2 text-text" : "text-muted"}`}
            >
              {coin(s)}
            </button>
          ))}
        </div>
      }
    >
      <LoadView
        load={mood}
        onRetry={reload}
        lines={4}
        notReady={() => (
          <NotReady title="News mood is being prepared">
            Headlines from CoinDesk, Cointelegraph, The Block, Decrypt and GDELT are scored hour by hour (module M3). This panel fills in
            as soon as that data is online.
          </NotReady>
        )}
      >
        {(d) => {
          const s = summarize(d.hours, Date.parse(d.updated_at));
          const tone = s.label === "negative" ? "bad" : s.label === "positive" ? "good" : "muted";
          return (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Stat label="Mood, last 24h" value={<Badge tone={tone}>{s.label}</Badge>} sub={s.mean_score == null ? "" : `score ${s.mean_score.toFixed(2)}`} />
                <Stat label="Headlines, 24h" value={s.headlines} />
                <Stat label="Share negative" value={s.share_negative == null ? "–" : pct(s.share_negative, 0)} />
                <Stat label="Policy shocks, 24h" value={s.policy_shocks} tone={s.policy_shocks > 0 ? "warn" : undefined} />
              </div>
              <div>
                <div className="mb-1 text-xs text-muted">Mean score per hour (−1 negative … +1 positive)</div>
                <TimeChart kind="line" label="Mean score" data={d.hours.map((h) => ({ t: h.hour_end, v: h.count ? h.mean_score : null }))} domain={[-1, 1]} zeroLine format={(v) => v.toFixed(2)} />
              </div>
              <div>
                <div className="mb-1 text-xs text-muted">Headlines per hour</div>
                <TimeChart kind="bar" label="Headlines" data={d.hours.map((h) => ({ t: h.hour_end, v: h.count }))} height={110} />
              </div>
              <div className="text-xs text-muted">Updated {ago(d.updated_at)}. Each hour counts headlines published up to its end.</div>
            </div>
          );
        }}
      </LoadView>
    </Card>
  );
}
