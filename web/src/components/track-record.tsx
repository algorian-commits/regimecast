"use client";

// Track Record: forecasts logged live before their outcome was known, and how they turned out.
// Data from /track-record/summary and /track-record. Every number carries its sample size; no backtest number appears here.
import { useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, load, SYMBOLS, type Schemas, type Symbol } from "@/lib/api";
import { ago, coin, pct } from "@/lib/format";
import { forecastPoints, minutesAfterStart, num, runningError, signedPct, span, utc, volPct, type TrackRow } from "@/lib/track";
import { useLoad } from "@/lib/use-load";
import { Badge, Card, LoadView, NotReady, Stat } from "./ui";

type Summary = Schemas["TrackSummaryOut"];
type Series = Schemas["SeriesSummary"];
type Horizon = "1" | "24";

const FINAL_EVALUATION = "https://github.com/algorian-commits/regimecast/blob/main/docs/FINAL_EVALUATION.md";
const sel = "max-w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm";
const FORECAST = "var(--chart-1)";
const REALISED = "var(--chart-3)";
const BENCHMARK = "var(--chart-4)";

function SampleBadge({ small, n }: { small: boolean; n: number }) {
  if (n === 0) return <Badge>no scores yet</Badge>;
  return small ? <Badge tone="warn">small sample</Badge> : <Badge tone="good">30+ days</Badge>;
}

function Banner({ s }: { s: Summary }) {
  return (
    <Card>
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Stat label="Live logging began" value={s.logging_started_at ? utc(s.logging_started_at).slice(0, 16) : "not yet"} sub={s.logging_started_at ? "UTC, first row written" : "nothing is backfilled"} />
        <Stat label="Forecasts logged" value={s.rows_logged} sub="across 3 coins × 2 horizons" />
        <Stat label="Scored so far" value={s.rows_scored} sub={`${s.rows_logged - s.rows_scored} waiting or without data`} />
        <Stat label="Last logged" value={s.last_logged_at ? ago(s.last_logged_at) : "–"} sub={s.logger_running ? "● logger running" : "○ logger not running here"} />
      </div>
      <p className="mt-4 text-xs text-muted">
        Each hour, every forecast is written down within {s.rules.log_deadline_minutes} minutes of the start of its window, using only data up to that start. The score is added as a
        separate row after the window closes. The database refuses any change or deletion of either row, and an hour with no row stays missing for good. The times come from our own
        server clock, so this shows our discipline, not independent proof.
      </p>
    </Card>
  );
}

function ScoreTable({ series, symbol, horizon, onPick }: { series: Series[]; symbol: Symbol; horizon: Horizon; onPick: (s: Symbol, h: Horizon) => void }) {
  return (
    <Card title="Error against the benchmark, per coin and horizon">
      <div className="-mx-1 overflow-x-auto px-1">
        <table className="w-full min-w-[34rem] text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted">
              <th className="py-2 pr-3 font-medium">Forecast</th>
              <th className="py-2 pr-3 text-right font-medium">Scored rows (n)</th>
              <th className="py-2 pr-3 text-right font-medium">Our error</th>
              <th className="py-2 pr-3 text-right font-medium">Benchmark error</th>
              <th className="py-2 pr-3 text-right font-medium">Difference</th>
              <th className="py-2 pr-3 text-right font-medium">Hours missing</th>
              <th className="py-2 font-medium">Sample</th>
            </tr>
          </thead>
          <tbody>
            {series.map((x) => {
              const on = x.symbol === symbol && String(x.horizon_hours) === horizon;
              return (
                <tr key={`${x.symbol}${x.horizon_hours}`} className={`border-b border-border/60 last:border-0 ${on ? "bg-surface-2" : ""}`}>
                  <td className="py-2 pr-3">
                    <button className="whitespace-nowrap font-medium text-accent hover:underline" onClick={() => onPick(x.symbol as Symbol, String(x.horizon_hours) as Horizon)} aria-pressed={on}>
                      {coin(x.symbol)} {x.horizon_hours} h
                    </button>
                  </td>
                  <td className="py-2 pr-3 text-right font-mono">{x.vs_benchmark.n}</td>
                  <td className="py-2 pr-3 text-right font-mono">{num(x.vs_benchmark.forecast)}</td>
                  <td className="py-2 pr-3 text-right font-mono">{num(x.vs_benchmark.rival)}</td>
                  <td className="py-2 pr-3 text-right font-mono">{signedPct(x.vs_benchmark.vs_rival_pct)}</td>
                  <td className="py-2 pr-3 text-right font-mono">
                    {x.hours_missing} of {x.hours_expected}
                  </td>
                  <td className="py-2">
                    <SampleBadge small={x.small_sample} n={x.rows_scored} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted">
        Error is average QLIKE on the n rows that have both numbers; lower is better. A negative difference means our forecast&apos;s error was lower than the benchmark&apos;s. Until a
        coin and horizon has 720 scored rows (30 days), treat its numbers as a small sample: they can swing a lot and may be luck.
      </p>
    </Card>
  );
}

function PairChart({
  data,
  a,
  b,
  unit,
  digits,
}: {
  data: Record<string, unknown>[];
  a: { key: string; name: string; color: string };
  b: { key: string; name: string; color: string; dashed?: boolean };
  unit: string;
  digits: number;
}) {
  const axis = { stroke: "var(--muted)", fontSize: 11, tickLine: false, axisLine: false } as const;
  const few = data.length <= 48;
  const tick = (iso: string) => {
    const d = new Date(iso);
    return data.length <= 48
      ? `${String(d.getUTCHours()).padStart(2, "0")}:00`
      : `${d.getUTCDate()} ${d.toLocaleString("en-US", { month: "short", timeZone: "UTC" })}`;
  };
  return (
    <div className="h-64" role="img" aria-label={`${a.name} and ${b.name} over time`}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 4" />
          <XAxis dataKey="t" tickFormatter={tick} minTickGap={48} {...axis} />
          <YAxis width={44} tickFormatter={(v: number) => `${v.toFixed(digits)}${unit}`} {...axis} />
          <Tooltip
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
            labelStyle={{ color: "var(--muted)" }}
            itemStyle={{ color: "var(--text)" }}
            labelFormatter={(l) => utc(String(l))}
            formatter={(v, name) => [typeof v === "number" ? `${v.toFixed(digits + 1)}${unit}` : "–", name]}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} iconType="plainline" formatter={(v) => <span style={{ color: "var(--muted)" }}>{v}</span>} />
          <Line name={a.name} dataKey={a.key} stroke={a.color} strokeWidth={2} dot={few ? { r: 3, strokeWidth: 0, fill: a.color } : false} connectNulls={false} isAnimationActive={false} />
          <Line name={b.name} dataKey={b.key} stroke={b.color} strokeWidth={2} strokeDasharray={b.dashed ? "6 4" : undefined} dot={few ? { r: 3, strokeWidth: 0, fill: b.color } : false} connectNulls={false} isAnimationActive={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function Charts({ rows, s, summary }: { rows: TrackRow[]; s: Series; summary: Summary }) {
  const points = forecastPoints(rows);
  const errors = runningError(rows);
  const name = `${coin(s.symbol)} ${s.horizon_hours} h`;
  const firstScore = `The first ${s.horizon_hours} h score appears about ${s.horizon_hours === 1 ? "an hour" : "a day"} after its forecast is logged, ${summary.rules.settle_minutes} minutes after the window closes.`;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title={`Forecast against what happened: ${name}`} action={<SampleBadge small={s.small_sample} n={s.rows_scored} />}>
        {points.length === 0 ? (
          <NotReady title="Nothing logged for this forecast yet">The chart starts with the first live row. Nothing is drawn in from before that.</NotReady>
        ) : (
          <>
            <PairChart
              data={points}
              a={{ key: "forecast", name: "Forecast", color: FORECAST }}
              b={{ key: "realised", name: "What happened", color: REALISED }}
              unit="%"
              digits={2}
            />
            <p className="mt-2 text-xs text-muted">
              Size of the price move over {s.horizon_hours} h (volatility, in %), for the window starting at each hour. Gaps are hours with nothing logged or not yet closed. {s.rows_logged} {s.rows_logged === 1 ? "forecast" : "forecasts"} logged,{" "}
              {s.rows_scored} with an outcome. {s.rows_scored === 0 ? firstScore : ""}
            </p>
          </>
        )}
      </Card>
      <Card title={`Running error against the benchmark: ${name}`} action={<SampleBadge small={s.small_sample} n={s.rows_scored} />}>
        {errors.length === 0 ? (
          <NotReady title="No scored rows yet">{firstScore}</NotReady>
        ) : (
          <>
            <PairChart
              data={errors}
              a={{ key: "forecast", name: "Our forecast", color: FORECAST }}
              b={{ key: "benchmark", name: "Benchmark", color: BENCHMARK, dashed: true }}
              unit=""
              digits={2}
            />
            <p className="mt-2 text-xs text-muted">
              Average QLIKE so far, from the first scored row up to each hour (n = {errors.length} on the last point). Lower is better. Our line below the dashed line means our forecast is ahead so far.
              {s.horizon_hours === 24 ? ` Windows logged hourly overlap: ${errors.length} rows are only about ${s.separate_windows} separate days.` : ""}
            </p>
          </>
        )}
      </Card>
    </div>
  );
}

function Detail({ s, summary }: { s: Series; summary: Summary }) {
  const st = s.stress;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title={`Also logged: HAR-S alone, ${coin(s.symbol)} ${s.horizon_hours} h`}>
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Shown forecast" value={num(s.vs_har_s.forecast)} sub={`n = ${s.vs_har_s.n}`} />
          <Stat label="HAR-S" value={num(s.vs_har_s.rival)} sub={`n = ${s.vs_har_s.n}`} />
          <Stat label="Difference" value={signedPct(s.vs_har_s.vs_rival_pct)} sub={`n = ${s.vs_har_s.n}`} />
        </div>
        <p className="mt-3 text-xs text-muted">Average QLIKE on the same rows. Negative means the forecast the app shows did better than HAR-S alone.</p>
      </Card>
      {st ? (
        <Card title={`Stress warning, next hour: ${coin(s.symbol)}`} action={st.n === 0 ? <Badge>no scores yet</Badge> : st.small_sample ? <Badge tone="warn">small sample</Badge> : <Badge tone="good">30+ events</Badge>}>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="Hours scored" value={st.n} sub={`${st.events} held stress`} />
            <Stat label="Chance given" value={st.mean_p == null ? "–" : pct(st.mean_p, 1)} sub={`average, n = ${st.n}`} />
            <Stat label="What happened" value={st.actual_rate == null ? "–" : pct(st.actual_rate, 1)} sub={`of hours, n = ${st.n}`} />
            <Stat label="Brier: ours / usual rate" value={st.brier == null ? "–" : `${num(st.brier, 4)} / ${num(st.brier_base_rate, 4)}`} sub={`lower is better, n = ${st.n}`} />
          </div>
          <p className="mt-3 text-xs text-muted">
            The label is the classifier&apos;s own: did the hour hold at least one stress minute, computed from Binance candles by the training code with its full 30-day lookback. Stress events are rare, so this stays a
            small sample until {summary.rules.small_sample_events} events have happened.
          </p>
        </Card>
      ) : (
        <Card title="Stress warning">
          <p className="text-sm text-muted">The stress chance is about the next hour, so it is scored on the 1 h rows. Pick a 1 h forecast above to see it. It is logged on the 24 h rows too, for the record.</p>
        </Card>
      )}
    </div>
  );
}

function Status({ r }: { r: TrackRow }) {
  if (r.status === "scored") return <Badge tone="good">scored</Badge>;
  if (r.status === "no_data") return <Badge tone="bad">no data</Badge>;
  return <Badge>waiting</Badge>;
}

function Records({ rows, s }: { rows: TrackRow[]; s: Series }) {
  const latest = rows.slice(0, 24);
  return (
    <Card title={`Latest records: ${coin(s.symbol)} ${s.horizon_hours} h`}>
      {latest.length === 0 ? (
        <NotReady title="No records yet">The logger writes one row an hour for each coin and horizon.</NotReady>
      ) : (
        <div className="-mx-1 overflow-x-auto px-1">
          <table className="w-full min-w-[42rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted">
                <th className="py-2 pr-3 font-medium">Window starts (UTC)</th>
                <th className="py-2 pr-3 text-right font-medium">Logged after</th>
                <th className="py-2 pr-3 text-right font-medium">Forecast</th>
                <th className="py-2 pr-3 text-right font-medium">Benchmark</th>
                <th className="py-2 pr-3 text-right font-medium">Happened</th>
                <th className="py-2 pr-3 text-right font-medium">Error (ours / bench)</th>
                <th className="py-2 pr-3 font-medium">Regime</th>
                <th className="py-2 pr-3 text-right font-medium">Stress chance</th>
                <th className="py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {latest.map((r) => (
                <tr key={r.origin} className="border-b border-border/60 last:border-0" title={r.forecast_model}>
                  <td className="py-2 pr-3 font-mono text-xs">{r.origin.slice(5, 16).replace("T", " ")}</td>
                  <td className="py-2 pr-3 text-right font-mono">{minutesAfterStart(r)} min</td>
                  <td className="py-2 pr-3 text-right font-mono">{num(volPct(r.forecast_variance), 2)}%</td>
                  <td className="py-2 pr-3 text-right font-mono">{r.benchmark_variance == null ? "–" : `${num(volPct(r.benchmark_variance), 2)}%`}</td>
                  <td className="py-2 pr-3 text-right font-mono">{r.realised_variance == null ? "–" : `${num(volPct(r.realised_variance), 2)}%`}</td>
                  <td className="py-2 pr-3 text-right font-mono">
                    {num(r.qlike_forecast)} / {num(r.qlike_benchmark)}
                  </td>
                  <td className="py-2 pr-3">{r.regime ?? "–"}</td>
                  <td className="py-2 pr-3 text-right font-mono">{r.p_stress == null ? "–" : pct(r.p_stress, 1)}</td>
                  <td className="py-2">
                    <Status r={r} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {rows[0] && <p className="mt-3 text-xs text-muted">Latest forecast came from: {rows[0].forecast_model}. Hover a row on a computer to see its model. Showing the newest {latest.length} of {s.rows_logged} rows.</p>}
    </Card>
  );
}

function Missing({ s }: { s: Series }) {
  return (
    <Card title={`Missing hours: ${coin(s.symbol)} ${s.horizon_hours} h`}>
      {s.hours_missing === 0 ? (
        <p className="text-sm text-muted">None so far, out of {s.hours_expected} hours since logging began.</p>
      ) : (
        <>
          <p className="text-sm">
            {s.hours_missing} of {s.hours_expected} hours have no forecast. They stay missing: nothing is filled in later.
          </p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {s.missing_recent.map((m) => (
              <li key={m} className="rounded-md bg-surface-2 px-2 py-0.5 font-mono text-xs">
                {m.slice(5, 16).replace("T", " ")}
              </li>
            ))}
          </ul>
          {s.hours_missing > s.missing_recent.length && <p className="mt-2 text-xs text-muted">Showing the latest {s.missing_recent.length}.</p>}
        </>
      )}
    </Card>
  );
}

function Explain() {
  const items: [string, React.ReactNode][] = [
    ["Forecast", "How big the price move is likely to be over the window, from the same forecast the app shows (HAR-S + LightGBM, or HAR-S alone if the combination has no fresh inputs). The row names the model."],
    ["What happened (realised volatility)", "How much the price really moved in the window, added up from 1-minute prices for 1 h and 5-minute prices for 24 h. This is exactly the definition the models were tested on. Charts show it as a % move; the table stores it as variance, the % move squared."],
    ["Benchmark", "The simplest possible forecast: the next window will be as wild as the one that just ended. A forecast that can't beat this isn't adding anything."],
    ["QLIKE (the error)", "How far a forecast was from what happened, with a heavier penalty for expecting too little than for expecting too much, which suits risk. 0 is a perfect forecast; lower is better. One bad hour can move the average a lot."],
    ["Difference", "Our average error compared with the rival's, on the same rows. -10% means our error was 10% lower. It is not a pass mark, and nothing here is a verdict."],
    ["HAR-S", "The linear part of the forecast, without LightGBM. It is logged next to the shown forecast because the liquidation chances use it."],
    ["Regime", "The market's state when the forecast was made: calm, active or stressed. Logged for the record, not scored."],
    ["Stress chance and stress event", "The model's chance that the next hour holds a stress event: at least 5 minutes in a row of volatility above its 99th percentile of the past 30 days."],
    ["Brier score", "The average squared gap between the chance given and what happened (1 if it happened, 0 if not). Lower is better. We compare it with always saying the usual rate."],
    ["n, scored rows and separate days", "n is how many rows a number is based on. 24 h forecasts are logged hourly, so their windows overlap: 240 scored rows are only about 10 separate days of evidence."],
    ["Small sample", "We label every coin and horizon as a small sample until it has 720 scored rows (30 days), and the stress score until it has seen 30 stress events. We set those limits before the first row was logged."],
    ["Waiting, no data and missing", "Waiting: the window hasn't closed and been scored yet. No data: too few prices in the window to score it, so no number is made up. Missing: no forecast was logged that hour (for example the server was restarting), and it stays that way."],
  ];
  return (
    <Card title="What every number means">
      <dl className="grid gap-x-6 gap-y-3 md:grid-cols-2">
        {items.map(([k, v]) => (
          <div key={k}>
            <dt className="text-sm font-medium">{k}</dt>
            <dd className="text-sm text-muted">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 text-xs text-muted">
        Results from testing on past data are kept apart from this live record. See the{" "}
        <a className="text-accent hover:underline" href={FINAL_EVALUATION} target="_blank" rel="noreferrer">
          final evaluation (backtest results)
        </a>
        .
      </p>
    </Card>
  );
}

function Selected({ symbol, horizon, summary }: { symbol: Symbol; horizon: Horizon; summary: Summary }) {
  const [rec, reload] = useLoad(
    () => load(() => api.GET("/track-record", { params: { query: { symbol, horizon_hours: horizon, limit: 720 } } })),
    `${symbol}|${horizon}`,
    120_000,
  );
  const s = summary.series.find((x) => x.symbol === symbol && String(x.horizon_hours) === horizon);
  if (!s) return null;
  return (
    <LoadView load={rec} onRetry={reload} lines={6} notReady={(d) => <NotReady title="Records not available">{d}</NotReady>}>
      {(d) => (
        <div className="space-y-4">
          <Charts rows={d.records} s={s} summary={summary} />
          <Detail s={s} summary={summary} />
          <Records rows={d.records} s={s} />
          <Missing s={s} />
        </div>
      )}
    </LoadView>
  );
}

export function TrackRecord() {
  const [symbol, setSymbol] = useState<Symbol>("BTCUSDT");
  const [horizon, setHorizon] = useState<Horizon>("1");
  const [sum, reload] = useLoad(() => load(() => api.GET("/track-record/summary")), "summary", 120_000);
  const pick = (s: Symbol, h: Horizon) => {
    setSymbol(s);
    setHorizon(h);
  };
  return (
    <div className="space-y-4">
      <LoadView
        load={sum}
        onRetry={reload}
        lines={5}
        notReady={(d) => <NotReady title="The Track Record is not available right now">{d}. Hours not logged while this lasts stay missing.</NotReady>}
      >
        {(s) => (
          <>
            <Banner s={s} />
            {s.rows_logged === 0 && (
              <NotReady title="Live logging has not written its first row yet">
                The record starts empty on purpose. Nothing is filled in from before logging began, and past test results are kept out of it.
              </NotReady>
            )}
            {s.rows_logged > 0 && s.series.some((x) => x.small_sample) && (
              <NotReady title={`Early days: ${span(Math.ceil(s.rows_logged / 6))} of live forecasts so far`}>
                Every score below is a small sample. Read them as a start, not a result.
              </NotReady>
            )}
            <ScoreTable series={s.series} symbol={symbol} horizon={horizon} onPick={pick} />
            <Card>
              <div className="flex flex-wrap items-center gap-2">
                <select className={sel} value={symbol} onChange={(e) => setSymbol(e.target.value as Symbol)} aria-label="Coin">
                  {SYMBOLS.map((x) => (
                    <option key={x} value={x}>
                      {coin(x)}
                    </option>
                  ))}
                </select>
                <select className={sel} value={horizon} onChange={(e) => setHorizon(e.target.value as Horizon)} aria-label="Horizon">
                  <option value="1">Next 1 hour</option>
                  <option value="24">Next 24 hours</option>
                </select>
              </div>
            </Card>
            <Selected symbol={symbol} horizon={horizon} summary={s} />
          </>
        )}
      </LoadView>
      <Explain />
    </div>
  );
}
