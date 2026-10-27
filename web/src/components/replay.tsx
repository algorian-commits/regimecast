"use client";

// Crash replay (M12): the stored minutes of a crash, played back as the dashboard and the
// Risk Check would have shown them, for a position the viewer picks. Data from /replay/{event}.
import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, load, SYMBOLS, type Symbol } from "@/lib/api";
import { coin, pct, prob, usd } from "@/lib/format";
import { advance, clock, frameAt, marksUpTo, pointsUpTo, SPEEDS, TICK_MS, type Replay } from "@/lib/replay";
import { useLoad } from "@/lib/use-load";
import { Badge, Button, Card, LoadView, NotReady, Stat } from "./ui";

const REGIME_TONE = { calm: "good", active: "warn", stressed: "bad" } as const;
const STRESS_TONE = { low: "good", elevated: "warn", high: "bad" } as const;
const sel = "max-w-full rounded-lg border border-border bg-bg px-3 py-2 text-sm";

type Opts = { event: string; symbol: Symbol; side: "long" | "short"; leverage: number; horizon: "1" | "24" };

function Chart({ r, i, field }: { r: Replay; i: number; field: "price" | "p" }) {
  const pts = useMemo(() => pointsUpTo(r, i), [r, i]);
  const axis = { stroke: "var(--muted)", fontSize: 11, tickLine: false, axisLine: false } as const;
  const start = Date.parse(r.first_close);
  const end = start + (r.n_minutes - 1) * 60_000;
  const prices = r.minutes.price.filter((v): v is number => v != null);
  const domain: [number, number] = field === "price" ? [Math.min(...prices, r.liquidation_price ?? Infinity) * 0.98, Math.max(...prices) * 1.01] : [0, 1];
  const data = pts.map((d) => ({ x: Date.parse(d.t), v: field === "price" ? d.price : d.p }));
  return (
    <div style={{ height: field === "price" ? 220 : 120 }} role="img" aria-label={field === "price" ? "Mark price so far" : "Chance of liquidation so far"}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 4" />
          <XAxis dataKey="x" type="number" domain={[start, end]} tickFormatter={(x: number) => clock(new Date(x).toISOString()).slice(0, 10)} minTickGap={48} {...axis} />
          <YAxis width={52} domain={domain} tickFormatter={(v: number) => (field === "price" ? usd(v, 0) : pct(v, 0))} {...axis} />
          <Tooltip
            contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
            labelFormatter={(x) => clock(new Date(Number(x)).toISOString())}
            formatter={(v) => [typeof v === "number" ? (field === "price" ? usd(v) : prob(v)) : "–", field === "price" ? "Mark price" : "Chance of liquidation"]}
          />
          {field === "price" && r.liquidation_price != null && (
            <ReferenceLine y={r.liquidation_price} stroke="var(--bad)" strokeDasharray="4 4" label={{ value: "liquidation", fill: "var(--bad)", fontSize: 11, position: "insideBottomRight" }} />
          )}
          {marksUpTo(r.event.marks, new Date(start + i * 60_000).toISOString()).map(([at]) => (
            <ReferenceLine key={at} x={Date.parse(at)} stroke="var(--muted)" strokeDasharray="2 3" />
          ))}
          <Line dataKey="v" stroke={field === "price" ? "var(--chart-1)" : "var(--bad)"} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function Player({ r }: { r: Replay }) {
  const [i, setI] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<number>(60);
  const atEnd = i >= r.n_minutes - 1;
  const running = playing && !atEnd;
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setI((k) => advance(k, speed, r.n_minutes)), TICK_MS);
    return () => clearInterval(id);
  }, [running, speed, r.n_minutes]);
  const f = frameAt(r, i);
  const h = f.hour;
  const marks = marksUpTo(r.event.marks, f.t);
  const side = `${coin(r.symbol)} ${r.leverage}x ${r.side}`;
  return (
    <div className="space-y-4">
      <Card>
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => (atEnd ? (setI(0), setPlaying(true)) : setPlaying(!running))} className="w-24">
            {running ? "Pause" : atEnd ? "Replay" : "Play"}
          </Button>
          <select className={sel} value={speed} onChange={(e) => setSpeed(Number(e.target.value))} aria-label="Speed">
            {SPEEDS.map((s) => (
              <option key={s.v} value={s.v}>
                {s.label}
              </option>
            ))}
          </select>
          <span className="ml-auto font-mono text-sm">{clock(f.t)}</span>
        </div>
        <input
          type="range"
          min={0}
          max={r.n_minutes - 1}
          value={i}
          onChange={(e) => setI(Number(e.target.value))}
          className="mt-3 w-full accent-[var(--accent)]"
          aria-label="Time in the replay"
        />
        {marks.length > 0 && <p className="mt-2 text-sm"><span className="text-muted">Latest news: </span>{marks[marks.length - 1][1]} ({clock(marks[marks.length - 1][0])})</p>}
      </Card>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card title="Mark price">
          <Stat label={coin(r.symbol)} value={usd(f.price)} sub={`entry ${usd(r.entry)}`} />
        </Card>
        <Card title="Market regime">
          {h?.regime ? (
            <div className="flex items-baseline gap-2">
              <span className="text-xl font-semibold capitalize">{h.regime}</span>
              <Badge tone={REGIME_TONE[h.regime as keyof typeof REGIME_TONE]}>{pct(h.p_stressed, 0)} stressed</Badge>
            </div>
          ) : (
            <span className="text-sm text-muted">no hour finished yet</span>
          )}
          <div className="mt-1 text-xs text-muted">as known at {h ? h.t.slice(11, 16) : "–"} UTC</div>
        </Card>
        <Card title="Stress gauge (next hour)">
          {h?.p_stress != null ? (
            <>
              <div className="flex items-baseline gap-2">
                <span className="font-mono text-xl font-semibold">{pct(h.p_stress, 0)}</span>
                <Badge tone={STRESS_TONE[(h.stress_level ?? "low") as keyof typeof STRESS_TONE]}>{h.stress_level}</Badge>
              </div>
              <div className="mt-1 text-xs text-muted">
                normally {pct(r.stress_base_rate, 0)} · walk-forward model {pct(h.p_stress_walk_forward, 0)}
              </div>
              {h.stress_drivers.length > 0 && <div className="mt-1 text-xs text-muted">driven by {h.stress_drivers.map((d) => `${d[0]} ${d[1] === "up" ? "↑" : "↓"}`).join(", ")}</div>}
            </>
          ) : (
            <span className="text-sm text-muted">–</span>
          )}
        </Card>
        <Card title={`Your ${side}`}>
          {f.liquidated ? (
            <Stat label="Liquidated" value="100%" tone="bad" sub={`at ${clock(r.liquidated_at!)}`} />
          ) : (
            <Stat
              label={`Chance of liquidation, next ${r.horizon_hours} h`}
              value={f.p != null ? prob(f.p) : "–"}
              tone={f.p == null ? undefined : f.p < r.risk_limit ? "good" : f.p < 5 * r.risk_limit ? "warn" : "bad"}
              sub={f.distance != null ? `liquidation ${usd(r.liquidation_price)} · ${f.distance.toFixed(1)}% away` : undefined}
            />
          )}
        </Card>
      </div>

      <Card title={`${coin(r.symbol)} mark price so far`} action={<span className="text-xs text-muted">only minutes up to {f.t.slice(11, 16)} UTC are drawn</span>}>
        <Chart r={r} i={f.i} field="price" />
      </Card>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Risk Check: chance your ${side} is liquidated within ${r.horizon_hours} h`}>
          <Chart r={r} i={f.i} field="p" />
        </Card>
        <Card title="Risk Check for a new trade now">
          {h ? (
            <div className="grid grid-cols-2 gap-3">
              <Stat label={`Safe leverage (risk ≤ ${pct(r.risk_limit, 0)})`} value={h.safe_leverage != null ? `${h.safe_leverage}x` : "–"} sub={`${r.side}, ${r.horizon_hours} h`} />
              <Stat label={`Volatility, next ${r.horizon_hours} h`} value={h.sigma_pct != null ? `${h.sigma_pct.toFixed(2)}%` : "–"} sub="HAR-S forecast" />
            </div>
          ) : (
            <span className="text-sm text-muted">–</span>
          )}
          <p className="mt-3 text-xs text-muted">The highest leverage the Risk Check would have allowed for a fresh {r.side} at this hour&apos;s price.</p>
        </Card>
      </div>
    </div>
  );
}

export function CrashReplay() {
  const [events] = useLoad(() => load(() => api.GET("/replay/events")), "events");
  const [o, setO] = useState<Opts>({ event: "ftx_2022", symbol: "BTCUSDT", side: "long", leverage: 10, horizon: "24" });
  const key = JSON.stringify(o);
  const [rep, reload] = useLoad(
    () => load(() => api.GET("/replay/{event_id}", { params: { path: { event_id: o.event }, query: { symbol: o.symbol, side: o.side, leverage: o.leverage, horizon_hours: o.horizon } } })),
    key,
  );
  return (
    <div className="space-y-4">
      <Card title="What to replay">
        <div className="flex flex-wrap items-end gap-2">
          <LoadView load={events} lines={1} notReady={() => null}>
            {(list) => (
              <select className={sel} value={o.event} onChange={(e) => setO({ ...o, event: e.target.value })} aria-label="Crash">
                {list.map((e) => (
                  <option key={e.id} value={e.id} disabled={!e.available}>
                    {e.title}
                    {e.available ? "" : " (after the final evaluation)"}
                  </option>
                ))}
              </select>
            )}
          </LoadView>
          <select className={sel} value={o.symbol} onChange={(e) => setO({ ...o, symbol: e.target.value as Symbol })} aria-label="Coin">
            {SYMBOLS.map((s) => (
              <option key={s} value={s}>
                {coin(s)}
              </option>
            ))}
          </select>
          <select className={sel} value={o.side} onChange={(e) => setO({ ...o, side: e.target.value as Opts["side"] })} aria-label="Side">
            <option value="long">long</option>
            <option value="short">short</option>
          </select>
          <select className={sel} value={o.leverage} onChange={(e) => setO({ ...o, leverage: Number(e.target.value) })} aria-label="Leverage">
            {[2, 3, 5, 10, 20, 50].map((l) => (
              <option key={l} value={l}>
                {l}x
              </option>
            ))}
          </select>
          <select className={sel} value={o.horizon} onChange={(e) => setO({ ...o, horizon: e.target.value as Opts["horizon"] })} aria-label="Horizon">
            <option value="24">next 24 h</option>
            <option value="1">next 1 h</option>
          </select>
        </div>
        <p className="mt-3 text-xs text-muted">
          The position is opened at the first minute (isolated margin) and held. Every number is what the app&apos;s frozen models say with the data
          up to that minute; nothing later is used. These models were fitted on 2020 to September 2025, which includes this crash, so this is a
          demonstration of the app, not a test of it: the walk-forward figure next to the stress gauge comes from models that had never seen these hours.
        </p>
      </Card>
      <LoadView load={rep} onRetry={reload} lines={8} notReady={(d) => <NotReady title="Replay not available">{d}</NotReady>}>
        {(r) => (
          <>
            <p className="text-sm text-muted">{r.event.summary as string}</p>
            <Player key={key} r={r} />
          </>
        )}
      </LoadView>
    </div>
  );
}
