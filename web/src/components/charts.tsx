"use client";

// One shared time-series chart (single series, single axis) built on Recharts.
// Two measures with different scales get two charts, never a dual axis.
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Point = { t: string; v: number | null };

const tickTime = (iso: string) => {
  const d = new Date(iso);
  return `${d.getUTCDate()} ${d.toLocaleString("en-US", { month: "short", timeZone: "UTC" })}`;
};
const tipTime = (iso: string) => new Date(iso).toISOString().slice(0, 16).replace("T", " ") + " UTC";

export function TimeChart({
  data,
  kind,
  label,
  format = (v) => String(v),
  domain,
  zeroLine,
  height = 160,
}: {
  data: Point[];
  kind: "line" | "bar";
  label: string;
  format?: (v: number) => string;
  domain?: [number, number];
  zeroLine?: boolean;
  height?: number;
}) {
  const axis = { stroke: "var(--muted)", fontSize: 11, tickLine: false, axisLine: false } as const;
  const common = (
    <>
      <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 4" />
      <XAxis dataKey="t" tickFormatter={tickTime} minTickGap={56} {...axis} />
      <YAxis width={40} domain={domain} tickFormatter={(v: number) => format(v)} {...axis} />
      <Tooltip
        cursor={kind === "bar" ? { fill: "var(--surface-2)" } : { stroke: "var(--muted)" }}
        contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", borderRadius: 8, fontSize: 12 }}
        labelStyle={{ color: "var(--muted)" }}
        itemStyle={{ color: "var(--text)" }}
        labelFormatter={(l) => tipTime(String(l))}
        formatter={(v) => [typeof v === "number" ? format(v) : "–", label]}
      />
      {zeroLine && <ReferenceLine y={0} stroke="var(--muted)" />}
    </>
  );
  return (
    <div style={{ height }} role="img" aria-label={`${label} over time`}>
      <ResponsiveContainer width="100%" height="100%">
        {kind === "line" ? (
          <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            {common}
            <Line dataKey="v" stroke="var(--chart-1)" strokeWidth={2} dot={false} connectNulls={false} isAnimationActive={false} />
          </LineChart>
        ) : (
          <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
            {common}
            <Bar dataKey="v" fill="var(--chart-1)" radius={[2, 2, 0, 0]} isAnimationActive={false} />
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal meter for a probability against the user's risk limit. */
export function ProbabilityBar({ p, limit }: { p: number; limit: number }) {
  // log-ish scale so 0.1% and 5% are both visible: 0 at 0.01%, full at 50%
  const pos = (x: number) => Math.min(100, Math.max(0, ((Math.log10(Math.max(x, 1e-4)) + 4) / (Math.log10(0.5) + 4)) * 100));
  const tone = p < limit ? "bg-good" : p < 5 * limit ? "bg-warn" : "bg-bad";
  return (
    <div className="relative mt-2 h-2.5 w-full rounded-full bg-surface-2" aria-hidden>
      <div className={`h-full rounded-full ${tone}`} style={{ width: `${Math.max(2, pos(p))}%` }} />
      <div className="absolute -top-1 h-4.5 w-0.5 bg-text/60" style={{ left: `${pos(limit)}%` }} title="Your risk limit" />
    </div>
  );
}

/**
 * What moves the volatility forecast (exact SHAP values of HAR-S): one diverging bar per input, from a zero line.
 * Warm = pushes the forecast (and so the risk) up, blue = pulls it down; the signed value is printed too, so the
 * meaning never rests on colour alone.
 */
export function DriversChart({ drivers }: { drivers: { name: string; effect_pct: number }[] }) {
  const rows = [...drivers].sort((a, b) => Math.abs(b.effect_pct) - Math.abs(a.effect_pct));
  const max = Math.max(5, ...rows.map((d) => Math.abs(d.effect_pct)));
  return (
    <figure>
      <ul className="space-y-2.5" aria-label="What moves the volatility forecast">
        {rows.map((d) => {
          const up = d.effect_pct > 0;
          const w = (Math.abs(d.effect_pct) / max) * 50;
          const label = `${up ? "+" : ""}${d.effect_pct.toFixed(1)}%`;
          return (
            <li key={d.name} className="grid grid-cols-[minmax(0,1fr)_3.5rem] items-center gap-x-3 gap-y-1 sm:grid-cols-[11rem_minmax(0,1fr)_3.5rem]">
              <span className="col-span-2 text-xs text-muted sm:col-span-1 sm:text-sm">{d.name}</span>
              <div className="relative h-3 rounded-full bg-surface-2" title={`${d.name}: ${label} vs a typical hour`}>
                <div className="absolute inset-y-0 left-1/2 w-px bg-muted/60" aria-hidden />
                <div
                  className="absolute inset-y-0.5 rounded-full"
                  style={{
                    background: up ? "var(--warn)" : "var(--chart-1)",
                    width: `${Math.max(w, 1)}%`,
                    ...(up ? { left: "50%" } : { right: "50%" }),
                  }}
                />
              </div>
              <span className="text-right font-mono text-sm">{label}</span>
            </li>
          );
        })}
      </ul>
      <figcaption className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--warn)" }} aria-hidden /> raises expected volatility
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: "var(--chart-1)" }} aria-hidden /> lowers it
        </span>
        <span>vs a typical hour</span>
      </figcaption>
    </figure>
  );
}
