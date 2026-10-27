"use client";

// Regime, volatility and stress gauges for one coin, from /regime, /forecast and /stress
// (all recomputed by the API from live Binance data every 10 minutes).
import { getJson, type Symbol } from "@/lib/api";
import { scalePos } from "@/lib/dashboard";
import { ago, coin, pct } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { Badge, Card, LoadView, NotReady } from "./ui";

type Regime = { regime: "calm" | "active" | "stressed"; p_calm: number; p_active: number; p_stressed: number; typical_next_24h_vol_pct: Record<string, number>; inputs_until: string; model: string };
type Forecast = { annualised_vol_pct: number; sigma_pct: number; model: string; inputs_until: string; drivers: { name: string; effect_pct: number }[] };
type Stress = { p_stress_next_hour: number; level: "low" | "elevated" | "high"; base_rate: number; inputs_until: string; model: string; drivers: { name: string; direction: "up" | "down" }[] };

const REGIME_TONE = { calm: "good", active: "warn", stressed: "bad" } as const;
const STRESS_TONE = { low: "good", elevated: "warn", high: "bad" } as const;
const BG = { good: "bg-good", warn: "bg-warn", bad: "bg-bad" } as const; // full class names, so Tailwind sees them
const refreshing = (what: string) => (
  <NotReady title={`${what} refreshing`}>The API recomputes it from live Binance data every 10 minutes.</NotReady>
);

function Footer({ until }: { until: string }) {
  return <div className="mt-3 text-xs text-muted">Data to {until.slice(11, 16)} UTC · {ago(until)}</div>;
}

function RegimeGauge({ symbol }: { symbol: Symbol }) {
  const [rg, reload] = useLoad(() => getJson<Regime>(`/api/regime?symbol=${symbol}`), symbol, 60_000);
  return (
    <Card title={`Market regime (${coin(symbol)})`}>
      <LoadView load={rg} onRetry={reload} lines={3} notReady={() => refreshing("Regime")}>
        {(d) => (
          <div>
            <div className="flex items-baseline gap-2">
              <span className="text-2xl font-semibold capitalize">{d.regime}</span>
              <Badge tone={REGIME_TONE[d.regime]}>{pct(d[`p_${d.regime}`], 0)} sure</Badge>
            </div>
            <div className="mt-3 flex h-3 w-full overflow-hidden rounded-full bg-surface-2" aria-hidden>
              <div className="bg-good transition-all duration-700" style={{ width: pct(d.p_calm, 1) }} />
              <div className="bg-warn transition-all duration-700" style={{ width: pct(d.p_active, 1) }} />
              <div className="bg-bad transition-all duration-700" style={{ width: pct(d.p_stressed, 1) }} />
            </div>
            <div className="mt-1 grid grid-cols-3 text-xs text-muted">
              <span>calm {pct(d.p_calm, 0)}</span>
              <span className="text-center">active {pct(d.p_active, 0)}</span>
              <span className="text-right">stressed {pct(d.p_stressed, 0)}</span>
            </div>
            <Footer until={d.inputs_until} />
          </div>
        )}
      </LoadView>
    </Card>
  );
}

function VolatilityGauge({ symbol }: { symbol: Symbol }) {
  const [fc, r1] = useLoad(() => getJson<Forecast>(`/api/forecast?symbol=${symbol}`), symbol, 60_000);
  const [rg] = useLoad(() => getJson<Regime>(`/api/regime?symbol=${symbol}`), symbol, 60_000);
  const typical = rg.state === "ok" ? rg.data.typical_next_24h_vol_pct : undefined;
  return (
    <Card title={`Volatility, next 24 h (${coin(symbol)})`}>
      <LoadView load={fc} onRetry={r1} lines={3} notReady={() => refreshing("Forecast")}>
        {(d) => {
          const max = Math.max(150, d.annualised_vol_pct * 1.2, ...(typical ? Object.values(typical) : []));
          return (
            <div>
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-2xl font-semibold">{d.annualised_vol_pct.toFixed(0)}%</span>
                <span className="text-xs text-muted">a year · ±{d.sigma_pct.toFixed(1)}% over 24 h</span>
              </div>
              <div className="relative mt-4 h-3 w-full rounded-full bg-surface-2" aria-hidden>
                {typical &&
                  (["calm", "active", "stressed"] as const).map((r) =>
                    typical[r] ? (
                      <div key={r} className={`absolute -top-1 h-5 w-0.5 ${BG[REGIME_TONE[r]]}`} style={{ left: `${scalePos(typical[r], max)}%` }} title={`typical after ${r}: ${typical[r]}%`} />
                    ) : null,
                  )}
                <div className="absolute top-0 h-3 rounded-full bg-accent transition-all duration-700" style={{ width: `${scalePos(d.annualised_vol_pct, max)}%` }} />
              </div>
              {typical && (
                <div className="mt-1 text-xs text-muted">
                  Ticks: typical after calm {typical.calm}%, active {typical.active}%, stressed {typical.stressed}%.
                </div>
              )}
              {d.drivers[0] && (
                <div className="mt-2 text-xs text-muted">
                  Biggest driver: {d.drivers[0].name} ({d.drivers[0].effect_pct > 0 ? "+" : ""}
                  {d.drivers[0].effect_pct.toFixed(0)}%)
                </div>
              )}
              <Footer until={d.inputs_until} />
            </div>
          );
        }}
      </LoadView>
    </Card>
  );
}

function StressGauge({ symbol }: { symbol: Symbol }) {
  const [st, reload] = useLoad(() => getJson<Stress>(`/api/stress?symbol=${symbol}`), symbol, 60_000);
  return (
    <Card title={`Stress risk, next hour (${coin(symbol)})`}>
      <LoadView load={st} onRetry={reload} lines={3} notReady={() => refreshing("Stress gauge")}>
        {(d) => {
          const max = Math.max(0.3, d.p_stress_next_hour * 1.2);
          return (
            <div>
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-2xl font-semibold">{pct(d.p_stress_next_hour, 0)}</span>
                <Badge tone={STRESS_TONE[d.level]}>{d.level}</Badge>
              </div>
              <div className="relative mt-4 h-3 w-full rounded-full bg-surface-2" aria-hidden>
                <div className="absolute -top-1 h-5 w-0.5 bg-text/60" style={{ left: `${scalePos(d.base_rate, max)}%` }} />
                <div className={`absolute top-0 h-3 rounded-full transition-all duration-700 ${BG[STRESS_TONE[d.level]]}`} style={{ width: `${Math.max(2, scalePos(d.p_stress_next_hour, max))}%` }} />
              </div>
              <div className="mt-1 text-xs text-muted">Tick = a normal hour ({pct(d.base_rate, 0)}).</div>
              {d.drivers.length > 0 && (
                <div className="mt-2 text-xs text-muted">
                  Pushing it {d.drivers[0].direction}: {d.drivers[0].name}
                </div>
              )}
              <Footer until={d.inputs_until} />
            </div>
          );
        }}
      </LoadView>
    </Card>
  );
}

export function Gauges({ symbol }: { symbol: Symbol }) {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <RegimeGauge symbol={symbol} />
      <VolatilityGauge symbol={symbol} />
      <StressGauge symbol={symbol} />
    </div>
  );
}
