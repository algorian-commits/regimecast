"use client";

// Phase 12: the Risk Check result for beginners. One verdict, three plain reasons, one suggested fix and the what-if
// strip first; "Show me the numbers" opens the grade's workings and the full risk check (children).
import { useState, type ReactNode } from "react";
import type { Schemas } from "@/lib/api";
import { coin, prob } from "@/lib/format";
import { breakEven, fixRows, gradeTone, whatIfLines, type Fix, type Tone, type Verdict, type WhatIf } from "@/lib/verdict";
import { Badge, Button, Card } from "./ui";

const GRADE_LOOK: Record<Tone, string> = {
  good: "border-good/40 bg-good/15 text-good",
  warn: "border-warn/40 bg-warn/15 text-warn",
  bad: "border-bad/40 bg-bad/15 text-bad",
};
const TEXT: Record<Tone, string> = { good: "text-good", warn: "text-warn", bad: "text-bad" };
const FACTOR = {
  knockout: "Stop or liquidation reached",
  liquidation: "Liquidation vs your limit",
  stop: "Stop-loss",
  costs: "Fees and funding",
  regime: "Market regime",
  stress: "Market stress",
  crowd: "Crowding",
  timing: "Timing",
  calm: "Market",
} as Record<string, string>;
const MARKET = new Set(["regime", "stress", "crowd", "timing"]);

export function TradeVerdict({ r, children }: { r: Schemas["RiskOut"]; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const v = r.verdict;
  if (!v) return <>{children}</>; // a check saved before Phase 12, or the verdict was unavailable
  const tone = gradeTone(v.grade);
  return (
    <div className="space-y-4">
      <Card title={`Trade Grade · ${coin(r.symbol)} ${r.side}, ${r.horizon_hours}h`}>
        <div className="flex items-start gap-4">
          <div className={`grid h-16 w-16 shrink-0 place-items-center rounded-2xl border text-4xl font-bold ${GRADE_LOOK[tone]}`} aria-label={`Grade ${v.grade}`}>
            {v.grade}
          </div>
          <div className="min-w-0">
            <p className="font-semibold">{v.headline}</p>
            <p className="mt-1 text-xs text-muted">{v.not_direction}</p>
          </div>
        </div>
        <ol className="mt-4 space-y-2 text-sm">
          {v.reasons.map((x, i) => (
            <li key={x} className="flex gap-2">
              <span className="font-mono text-muted">{i + 1}</span>
              <span>{x}</span>
            </li>
          ))}
        </ol>
        {v.break_even_note && <p className="mt-3 rounded-lg bg-surface-2 p-3 text-sm">{v.break_even_note}</p>}
      </Card>
      {r.fix && <FixCard fix={r.fix} />}
      {r.what_if && r.what_if.length > 0 && <WhatIfStrip rows={r.what_if} />}
      <Button variant="ghost" className="w-full" aria-expanded={open} onClick={() => setOpen(!open)}>
        {open ? "Hide the numbers" : "Show me the numbers"}
      </Button>
      {open && (
        <>
          <Workings v={v} />
          {children}
        </>
      )}
    </div>
  );
}

function FixCard({ fix }: { fix: Fix }) {
  if (!fix.fixed) {
    return (
      <Card title="Make it safer">
        <p className="text-sm">{fix.message}</p>
      </Card>
    );
  }
  return (
    <Card title="Make it safer: the closest safer version">
      <p className="mb-3 text-sm">{fix.message}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="py-1 pr-2 font-medium" />
              <th className="py-1 pr-2 font-medium">Your trade</th>
              <th className="py-1 font-medium">Safer version</th>
            </tr>
          </thead>
          <tbody>
            {fixRows(fix.original, fix.fixed).map((x) => (
              <tr key={x.label} className="border-t border-border">
                <td className="py-1.5 pr-2 text-muted">{x.label}</td>
                <td className={`py-1.5 pr-2 font-mono ${x.label === "Grade" ? TEXT[gradeTone(x.original)] : ""}`}>{x.original}</td>
                <td className={`py-1.5 font-mono font-semibold ${x.label === "Grade" ? TEXT[gradeTone(x.fixed)] : ""}`}>{x.fixed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {fix.changes.length > 0 && (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-sm">
          {fix.changes.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function WhatIfStrip({ rows }: { rows: WhatIf[] }) {
  return (
    <Card title="What if: your trade in dollars, one day from now">
      <div className="grid gap-3 sm:grid-cols-3">
        {rows.map((w) => (
          <div key={w.scenario} className={`min-w-0 rounded-lg p-3 ${w.scenario === "crash" ? "bg-bad/10" : "bg-surface-2"}`}>
            <div className="text-sm font-semibold">{w.label}</div>
            {whatIfLines(w).map((l) => (
              <div key={l.label} className="mt-1 flex flex-wrap justify-between gap-x-2 text-sm">
                <span className="text-muted">{l.label}</span>
                <span className={`font-mono ${TEXT[l.tone]}`}>{l.text}</span>
              </div>
            ))}
            <div className="mt-2 text-xs text-muted">{w.basis}</div>
          </div>
        ))}
      </div>
      <p className="mt-2 text-xs text-muted">Opening and closing fees included, funding not. In a crash, stops can fill well past their price.</p>
    </Card>
  );
}

function Workings({ v }: { v: Verdict }) {
  const be = breakEven(v);
  return (
    <Card title="How the grade was worked out">
      <ul className="divide-y divide-border text-sm">
        {v.factors.map((f) => (
          <li key={f.key} className="flex flex-wrap items-start gap-x-3 gap-y-0.5 py-2">
            <span className="w-full shrink-0 text-xs text-muted sm:w-36 sm:text-sm">{FACTOR[f.key] ?? f.key}</span>
            <span className="min-w-0 flex-1">{f.text}</span>
            <Badge tone={f.score === 0 ? "good" : f.score >= 3 ? "bad" : "warn"}>{MARKET.has(f.key) ? `+${f.score}` : `${f.score}/4`}</Badge>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-xs text-muted">
        Setup parts score 0 (fine) to 4; the worst one sets the grade, and market conditions add up to 2 steps. Chance the stop or
        liquidation is reached: {prob(v.p_knockout)}.{be && ` Break-even win rate: ${be}.`}
      </p>

      <h3 className="mt-4 text-sm font-semibold">Crowd warning</h3>
      <p className="mt-1 text-sm">{v.crowd ? v.crowd.message : "Not available right now (it needs the live funding and long/short data, and thresholds fixed on the dev period)."}</p>

      <h3 className="mt-4 text-sm font-semibold">Similar moments</h3>
      <p className="mt-1 text-sm">{v.similar ? v.similar.message : "Not available yet: the past hours it compares with ship once the dev-period run is done."}</p>

      <h3 className="mt-4 text-sm font-semibold">Timing</h3>
      {v.timing.length ? (
        <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
          {v.timing.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-sm">No US rate decision, inflation data or jobs report inside your trade&apos;s time, and not a weekend or quiet hour.</p>
      )}
      <p className="mt-4 text-xs text-muted">
        {v.settings_frozen_at
          ? `Grading settings fixed on 2022-2025 data (${v.settings_frozen_at}); never tuned on later data.`
          : "Grading settings: the defaults written before the dev-period run; they are fixed on 2022-2025 data next."}
      </p>
    </Card>
  );
}
