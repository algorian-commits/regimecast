"use client";

// Agent page (M10 part 2): the live paper log of the frozen risk agent vs buy-and-hold, its latest decision with
// the reasons, and the development-period backtest the settings were frozen on. Data: GET /agent.
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, load, type Schemas } from "@/lib/api";
import { ago, coin, pct } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { Badge, Card, LoadView, NotReady, Stat } from "./ui";

type Agent = Schemas["AgentOut"];
type Series = { key: string; name: string; color: string; dash?: string };

const REGIMES = ["calm", "active", "stressed"];
const STRATEGY: Record<string, string> = { agent: "RegimeCast agent", buy_and_hold: "Buy-and-hold", vol_target: "Volatility targeting" };

const tickDay = (iso: string) => {
  const d = new Date(iso);
  return `${d.toLocaleString("en-US", { month: "short", timeZone: "UTC" })} ${String(d.getUTCFullYear()).slice(2)}`;
};
const tickHour = (iso: string) => {
  const d = new Date(iso);
  return `${d.getUTCDate()} ${d.toLocaleString("en-US", { month: "short", timeZone: "UTC" })}`;
};
const when = (iso: string) => new Date(iso).toISOString().slice(0, 16).replace("T", " ") + " UTC";
const money = (v: number) => v.toFixed(3);
const change = (v: number) => `${v >= 1 ? "+" : ""}${pct(v - 1, Math.abs(v - 1) < 0.01 ? 2 : 1)}`;

/** Account values on one axis (they share a scale: start = 1). */
export function EquityChart({ data, x, series, tick, height = 260 }: { data: Record<string, unknown>[]; x: string; series: Series[]; tick: (iso: string) => string; height?: number }) {
  const axis = { stroke: "var(--muted)", fontSize: 11, tickLine: false, axisLine: false } as const;
  return (
    <div style={{ height }} role="img" aria-label={`Account value over time: ${series.map((s) => s.name).join(", ")}`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 4" />
          <XAxis dataKey={x} tickFormatter={tick} minTickGap={56} {...axis} />
          <YAxis width={44} domain={["auto", "auto"]} tickFormatter={(v: number) => v.toFixed(2)} {...axis} />
          <Tooltip
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: "var(--muted)" }}
            labelFormatter={(l) => when(String(l))}
            formatter={(v, name) => [typeof v === "number" ? `${money(v)} (${change(v)})` : "–", name]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          {series.map((s) => (
            <Line key={s.key} dataKey={s.key} name={s.name} stroke={s.color} strokeDasharray={s.dash} strokeWidth={2} dot={false} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function Live({ live }: { live: NonNullable<Agent["live"]> }) {
  const d = live.latest;
  return (
    <>
      <Card title="Live paper account (since the log started)">
        <div className="mb-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Stat label="Agent" value={change(live.equity)} tone={live.equity >= live.bh_equity ? "good" : undefined} />
          <Stat label="Buy-and-hold" value={change(live.bh_equity)} />
          <Stat label="Exposure now" value={d ? pct(d.total_exposure, 0) : "–"} sub="average of the three coins" />
          <Stat label="Hours logged" value={live.hours_logged} sub={live.started_at ? `since ${when(live.started_at)}` : undefined} />
        </div>
        {live.curve.length > 1 ? (
          <EquityChart
            data={live.curve}
            x="hour"
            tick={tickHour}
            series={[
              { key: "agent", name: STRATEGY.agent, color: "var(--chart-1)" },
              { key: "buy_and_hold", name: STRATEGY.buy_and_hold, color: "var(--muted)" },
            ]}
          />
        ) : (
          <NotReady title="The curve appears after the second hour">One point is logged so far.</NotReady>
        )}
        <p className="mt-2 text-xs text-muted">Updated {ago(live.updated_at)}. Each decision is published before the hour it is for plays out; the data branch&apos;s history shows when.</p>
      </Card>
      {d && (
        <Card title={`Latest decision: the hour after ${when(d.hour)}`} action={<span className="text-xs text-muted">written {when(d.decided_at)}</span>}>
          <div className="grid gap-4 sm:grid-cols-3">
            {Object.entries(d.coins).map(([s, c]) => (
              <div key={s} className="min-w-0 rounded-lg border border-border p-3">
                <div className="mb-1 flex items-center justify-between">
                  <span className="font-semibold">{coin(s)}</span>
                  {c.regime != null && <Badge tone={c.regime === 2 ? "bad" : c.regime === 1 ? "warn" : "good"}>{REGIMES[c.regime]}</Badge>}
                </div>
                <div className="font-mono text-xl font-semibold">{pct(c.exposure, 0)}</div>
                <div className="text-xs text-muted">
                  of its third, was {pct(c.exposure_before, 0)}
                  {c.traded !== 0 && ` (${c.traded > 0 ? "bought" : "sold"} ${pct(Math.abs(c.traded), 1)} of the account)`}
                </div>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs">
                  {c.reasons.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Card>
      )}
    </>
  );
}

function Backtest({ b }: { b: NonNullable<Agent["backtest"]> }) {
  const s = b.settings;
  const cut = (x: number) => (x > 1 ? "off" : pct(x, 0));
  return (
    <>
      <Card title="Backtest on past data (development period, Jan 2022 to Sep 2025)">
        <EquityChart
          data={b.equity}
          x="day"
          tick={tickDay}
          series={[
            { key: "agent", name: STRATEGY.agent, color: "var(--chart-1)" },
            { key: "buy_and_hold", name: STRATEGY.buy_and_hold, color: "var(--muted)" },
            { key: "vol_target", name: STRATEGY.vol_target, color: "var(--warn)", dash: "4 3" },
          ]}
        />
        <div className="mt-4 overflow-x-auto">
          <table className="w-full min-w-[560px] text-right text-sm">
            <thead className="text-xs text-muted">
              <tr>
                <th className="py-1 text-left font-medium">Period</th>
                <th className="text-left font-medium">Strategy</th>
                <th className="font-medium">Return</th>
                <th className="font-medium">Sharpe</th>
                <th className="font-medium">Max drawdown</th>
                <th className="font-medium">Fees paid</th>
                <th className="font-medium">Funding paid</th>
                <th className="font-medium">Avg exposure</th>
              </tr>
            </thead>
            <tbody className="font-mono">
              {b.results.map((r) => (
                <tr key={r.period + r.strategy} className="border-t border-border">
                  <td className="py-1 text-left font-sans">{r.period}</td>
                  <td className="text-left font-sans">{STRATEGY[r.strategy] ?? r.strategy}</td>
                  <td>{pct(r.total_return, 0)}</td>
                  <td>{r.sharpe.toFixed(2)}</td>
                  <td>{pct(r.max_drawdown, 0)}</td>
                  <td>{pct(r.fees_paid, 1)}</td>
                  <td>{pct(r.funding_paid, 1)}</td>
                  <td>{pct(r.avg_exposure, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-muted">
          Every hour uses only the forecasts RegimeCast had at that hour (walk-forward, out of sample), Binance&apos;s 0.05% taker fee on every change and the
          archived funding. Settings chosen on {b.chosen_on} ({b.settings_tried} tried), checked on {b.checked_on}. The final test period is not shown.
        </p>
      </Card>
      <Card title={`Frozen settings (${b.frozen_at})`}>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Stat label="Target volatility" value={pct(s.target_vol, 0)} sub="a year, per coin" />
          <Stat label="Stress: halve at" value={cut(s.stress_elevated)} />
          <Stat label="Stress: exit at" value={cut(s.stress_high)} />
          <Stat label="Stressed regime keeps" value={pct(s.regime_mult, 0)} />
          <Stat label="No-trade band" value={pct(s.band, 0)} />
        </div>
      </Card>
    </>
  );
}

export function AgentView() {
  const [a, reload] = useLoad(() => load(() => api.GET("/agent")), "agent", 5 * 60_000);
  return (
    <LoadView load={a} onRetry={reload} lines={6} notReady={(d) => <NotReady title="The agent has not published anything yet">{d}</NotReady>}>
      {(data: Agent) => (
        <div className="space-y-4">
          {data.live ? (
            <Live live={data.live} />
          ) : (
            <NotReady title="The live paper log starts with its first hourly run">It runs a few minutes after every hour on GitHub Actions.</NotReady>
          )}
          {data.backtest && <Backtest b={data.backtest} />}
          <p className="text-xs text-muted">Paper trading only: no real orders are placed. Not financial advice.</p>
        </div>
      )}
    </LoadView>
  );
}
