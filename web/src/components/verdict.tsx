"use client";

import type { Schemas } from "@/lib/api";
import { prob, usd } from "@/lib/format";
import { gradeTone, moneyTone, signedPct, signedUsd } from "@/lib/verdict";
import { Badge, Card } from "./ui";

type Risk = Schemas["RiskOut"];
type Verdict = Schemas["VerdictOut"];

const MONEY = { good: "text-good", bad: "text-bad", muted: "text-muted" } as const;
const TONE_BG = { good: "bg-good/15 text-good", warn: "bg-warn/15 text-warn", bad: "bg-bad/15 text-bad" } as const;

function Reason({ text, good }: { text: string; good: boolean }) {
  return (
    <li className="flex gap-2">
      <span className={good ? "text-good" : "text-warn"} aria-hidden>
        {good ? "✓" : "▲"}
      </span>
      <span>{text}</span>
    </li>
  );
}

/** The beginner view: one grade, three reasons, one fix, then what a day could do to the trade. */
export function VerdictView({ r, v }: { r: Risk; v: Verdict }) {
  return (
    <div className="space-y-4">
      <Card title="Is this trade well built?">
        <div className="flex items-start gap-4">
          <div
            className={`flex h-20 w-20 shrink-0 items-center justify-center rounded-2xl font-mono text-5xl font-bold ${TONE_BG[gradeTone(v.grade)]}`}
            aria-label={`Grade ${v.grade}`}
          >
            {v.grade}
          </div>
          <div className="min-w-0">
            <div className="text-lg font-semibold">{v.headline}</div>
            <ul className="mt-2 space-y-1.5 text-sm">
              {v.reasons.map((x) => (
                <Reason key={x.text} text={x.text} good={x.good} />
              ))}
            </ul>
          </div>
        </div>
        <p className="mt-3 text-xs text-muted">
          The grade judges how the trade is built (size, leverage, stop, costs, timing, crowding) over the next day. It does not say where the price
          will go: nobody can tell that reliably, and we don&rsquo;t pretend to.
        </p>
      </Card>

      <FixCard r={r} v={v} />
      <WhatIfCard v={v} />

      <Card title="To make money">
        {v.break_even ? (
          <>
            <div className="font-mono text-3xl font-semibold">{(100 * v.break_even.win_rate).toFixed(0)}%</div>
            <p className="mt-1 text-sm">{v.break_even.text}</p>
          </>
        ) : (
          <p className="text-sm text-muted">{v.break_even_hint}</p>
        )}
      </Card>

      {(v.timing.length > 0 || v.crowd) && (
        <Card title="Timing and the crowd">
          <ul className="space-y-1.5 text-sm">
            {v.timing.map((t) => (
              <Reason key={t} text={t} good={false} />
            ))}
            {v.timing.length === 0 && <Reason text="No big US announcements, weekend or quiet hours in the next day." good />}
            {v.crowd && <Reason text={v.crowd.text} good={!v.crowd.side} />}
          </ul>
        </Card>
      )}

      {v.similar && (
        <Card title="Similar moments in the past">
          <ul className="list-disc space-y-1.5 pl-5 text-sm">
            {v.similar.text.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
          {v.similar.examples.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              {v.similar.examples.map((e) => (
                <span key={e.time} className="rounded-full bg-surface-2 px-2.5 py-1 font-mono">
                  {new Date(e.time).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })}:{" "}
                  <span className={e.next_24h_move_pct >= 0 ? "text-good" : "text-bad"}>{signedPct(e.next_24h_move_pct)}</span>
                </span>
              ))}
            </div>
          )}
          <p className="mt-2 text-xs text-muted">Compared with every past hour up to {v.similar.pool_until.slice(0, 10)}.</p>
        </Card>
      )}

      {v.notes.length > 0 && (
        <ul className="space-y-1 text-xs text-muted">
          {v.notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function FixCard({ r, v }: { r: Risk; v: Verdict }) {
  const f = v.fix;
  if (!f)
    return (
      <Card title="Make it safer">
        <p className="text-sm">{v.fix_note}</p>
      </Card>
    );
  const lossNow = r.stop ? r.stop.loss_at_stop_usdt : null;
  const rows: [string, string, string][] = [
    ["Grade", v.grade, f.grade],
    ["Leverage", `${v.leverage}x`, `${f.leverage}x`],
    ["Position size", `$${usd(r.notional_usdt, 0)}`, `$${usd(f.notional_usdt, 0)}`],
    ["Stop-loss", r.stop ? usd(r.stop.stop) : "none", f.stop == null ? "none" : usd(f.stop)],
    ["Liquidation price", r.liquidation_price == null ? "none" : usd(r.liquidation_price), f.liquidation_price == null ? "none" : usd(f.liquidation_price)],
    ["Chance of liquidation, a day", prob(v.p_liquidation_24h), prob(f.p_liquidation_24h)],
    ["Chance the stop is hit, a day", v.p_stop_24h == null ? "–" : prob(v.p_stop_24h), f.p_stop_24h == null ? "–" : prob(f.p_stop_24h)],
    ["Loss if the stop is hit", lossNow == null ? `all $${usd(r.margin_usdt)} at liquidation` : `$${usd(lossNow)}`, f.loss_at_stop_usdt == null ? "–" : `$${usd(f.loss_at_stop_usdt)}`],
  ];
  return (
    <Card title="Make it safer" action={<Badge tone={gradeTone(f.grade)}>fixed: {f.grade}</Badge>}>
      <p className="mb-3 text-sm">{v.fix_note}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="py-1 pr-2 font-medium"></th>
              <th className="py-1 pr-2 font-medium">Your trade</th>
              <th className="py-1 font-medium">Safer version</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.map(([k, a, b]) => (
              <tr key={k}>
                <td className="py-1.5 pr-2 text-muted">{k}</td>
                <td className="py-1.5 pr-2 font-mono">{a}</td>
                <td className={`py-1.5 font-mono ${a !== b ? "font-semibold" : ""}`}>{b}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
        {f.changes.map((c) => (
          <li key={c}>{c}</li>
        ))}
      </ul>
    </Card>
  );
}

function WhatIfCard({ v }: { v: Verdict }) {
  if (!v.what_if.length) return null;
  return (
    <Card title="What a day could do to this trade">
      <ul className="divide-y divide-border">
        {v.what_if.map((w) => (
          <li key={w.key} className="flex items-center justify-between gap-3 py-2 text-sm">
            <div className="min-w-0">
              <div className="font-medium">{w.label}</div>
              <div className="text-xs text-muted">price {signedPct(w.move_pct)} at the worst point</div>
            </div>
            <div className="text-right">
              <div className={`font-mono font-semibold ${MONEY[moneyTone(w.pnl_usdt)]}`}>{signedUsd(w.pnl_usdt)}</div>
              {(w.outcome === "liquidated" || w.outcome === "stopped out") && (
                <Badge tone={w.outcome === "liquidated" ? "bad" : "warn"}>{w.outcome}</Badge>
              )}
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-muted">
        Normal, bad and very bad days are the typical, 1-in-10 and 1-in-100 moves against you in this market phase (2020 to Sep 2025). The crash line
        is the size of the fall on 10 Oct 2025.
      </p>
    </Card>
  );
}
