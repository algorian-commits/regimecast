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
