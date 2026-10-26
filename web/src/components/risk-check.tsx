"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, load, SYMBOLS, type Load, type Schemas, type Symbol } from "@/lib/api";
import { coin, prob, riskBand, usd } from "@/lib/format";
import { buildTrade, DEFAULT_FORM, entryFromMark, RISK_LIMIT, type TradeForm } from "@/lib/trade-form";
import { ProbabilityBar } from "./charts";
import { Badge, Button, Card, ErrorBox, Loading, Stat } from "./ui";

type Market = Schemas["MarketOut"];
type Risk = Schemas["RiskOut"];

const input = "w-full rounded-lg border border-border bg-bg px-3 py-2.5 font-mono text-base outline-none focus:border-accent";

function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {error ? <span className="mt-1 block text-xs text-bad">{error}</span> : hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

function Toggle<T extends string | number>({ value, options, onChange }: { value: T; options: { v: T; label: string }[]; onChange: (v: T) => void }) {
  return (
    <div className="grid grid-flow-col gap-1 rounded-lg bg-surface-2 p-1">
      {options.map((o) => (
        <button
          type="button"
          key={String(o.v)}
          aria-pressed={o.v === value}
          onClick={() => onChange(o.v)}
          className={`rounded-md px-3 py-2 text-sm font-medium ${o.v === value ? "bg-surface text-text shadow-sm" : "text-muted"}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function RiskCheck() {
  const params = useSearchParams();
  const initial = (SYMBOLS as string[]).includes(params.get("symbol") ?? "") ? (params.get("symbol") as Symbol) : "BTCUSDT";
  const [f, setF] = useState<TradeForm>({ ...DEFAULT_FORM, symbol: initial });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [market, setMarket] = useState<Load<Market>>({ state: "loading" });
  const [result, setResult] = useState<Load<Risk> | null>(null);
  const [busy, setBusy] = useState(false);
  const touchedEntry = useRef(false);
  const resultRef = useRef<HTMLDivElement>(null);

  const set = <K extends keyof TradeForm>(k: K, v: TradeForm[K]) => setF((p) => ({ ...p, [k]: v }));

  // Live price for the chosen coin; pre-fills the entry unless the user typed their own.
  useEffect(() => {
    let live = true;
    load(() => api.GET("/market/{symbol}", { params: { path: { symbol: f.symbol } } })).then((m) => {
      if (!live) return;
      setMarket(m);
      if (m.state === "ok" && !touchedEntry.current) setF((p) => ({ ...p, entry: entryFromMark(m.data.mark_price) }));
    });
    return () => {
      live = false;
    };
  }, [f.symbol]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const b = buildTrade(f);
    if (!b.ok) {
      setErrors(b.errors);
      return;
    }
    setErrors({});
    setBusy(true);
    const r = await load(() => api.POST("/risk-check", { body: b.body }));
    setResult(r);
    setBusy(false);
    requestAnimationFrame(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };

  const mark = market.state === "ok" ? market.data.mark_price : null;

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
      <Card title="Your trade">
        <form onSubmit={submit} className="space-y-4" noValidate>
          <Toggle
            value={f.symbol}
            options={SYMBOLS.map((s) => ({ v: s, label: coin(s) }))}
            onChange={(v) => {
              touchedEntry.current = false;
              setMarket({ state: "loading" });
              set("symbol", v);
            }}
          />
          <Toggle value={f.side} options={[{ v: "long", label: "Long" }, { v: "short", label: "Short" }]} onChange={(v) => set("side", v)} />
          <Field
            label="Entry price (USDT)"
            error={errors.entry}
            hint={
              market.state === "loading"
                ? "Fetching the live price…"
                : mark
                  ? `Live mark price ${usd(mark)}${market.state === "ok" && market.data.stale ? " (delayed)" : ""}`
                  : "Live price unavailable; type your entry."
            }
          >
            <div className="flex gap-2">
              <input
                className={input}
                inputMode="decimal"
                value={f.entry}
                onChange={(e) => {
                  touchedEntry.current = true;
                  set("entry", e.target.value);
                }}
              />
              {mark && (
                <Button
                  type="button"
                  variant="ghost"
                  className="shrink-0"
                  onClick={() => {
                    touchedEntry.current = false;
                    set("entry", entryFromMark(mark));
                  }}
                >
                  Live
                </Button>
              )}
            </div>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Leverage (x)" error={errors.leverage}>
              <input className={input} inputMode="decimal" value={f.leverage} onChange={(e) => set("leverage", e.target.value)} />
            </Field>
            <Field label="Margin (USDT)" error={errors.margin}>
              <input className={input} inputMode="decimal" value={f.margin} onChange={(e) => set("margin", e.target.value)} />
            </Field>
          </div>
          <Field label="Stop-loss (optional)" error={errors.stop} hint={f.side === "long" ? "Below your entry." : "Above your entry."}>
            <input className={input} inputMode="decimal" value={f.stop} placeholder="none" onChange={(e) => set("stop", e.target.value)} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Horizon">
              <Toggle value={f.horizon_hours} options={[{ v: 1, label: "1h" }, { v: 24, label: "24h" }]} onChange={(v) => set("horizon_hours", v)} />
            </Field>
            <Field label="Margin mode">
              <Toggle value={f.margin_mode} options={[{ v: "isolated", label: "Isolated" }, { v: "cross", label: "Cross" }]} onChange={(v) => set("margin_mode", v)} />
            </Field>
          </div>
          {f.margin_mode === "cross" && (
            <Field label="Cross wallet balance (USDT)" error={errors.wallet_balance} hint="Everything in your cross-margin wallet backs this position.">
              <input className={input} inputMode="decimal" value={f.wallet_balance} onChange={(e) => set("wallet_balance", e.target.value)} />
            </Field>
          )}
          <Button type="submit" disabled={busy} className="w-full">
            {busy ? "Checking…" : "Check risk"}
          </Button>
        </form>
      </Card>

      <div ref={resultRef} className="scroll-mt-20">
        {busy ? (
          <Card title="Result">
            <Loading lines={6} />
          </Card>
        ) : result === null ? (
          <Card title="Result">
            <p className="text-sm text-muted">
              Enter a trade and press <b>Check risk</b>. You get the liquidation price, the chance of being liquidated within the horizon,
              the highest safe leverage, fees, and a plain-English explanation.
            </p>
          </Card>
        ) : result.state === "ok" ? (
          <RiskResult r={result.data} />
        ) : (
          <Card title="Result">
            <ErrorBox message={result.state === "error" ? result.message : result.state === "not_ready" ? "The risk engine is not available right now." : ""} />
          </Card>
        )}
      </div>
    </div>
  );
}

function RiskResult({ r }: { r: Risk }) {
  const band = riskBand(r.p_liquidation);
  const tone = band === "low" ? "good" : band === "elevated" ? "warn" : "bad";
  return (
    <div className="space-y-4">
      <Card title={`${coin(r.symbol)} ${r.side}, ${r.horizon_hours}h horizon`} action={<Badge tone={tone}>{band} risk</Badge>}>
        <div className="grid grid-cols-2 gap-4">
          <Stat
            label="Liquidation price"
            value={r.liquidation_price == null ? "none" : usd(r.liquidation_price)}
            sub={r.liquidation_distance_pct == null ? "cannot be liquidated" : `${r.liquidation_distance_pct.toFixed(2)}% away`}
          />
          <div>
            <Stat label={`Chance of liquidation, ${r.horizon_hours}h`} value={prob(r.p_liquidation)} tone={tone} />
            <ProbabilityBar p={r.p_liquidation} limit={RISK_LIMIT} />
          </div>
          <Stat label="Max safe leverage" value={`${r.max_safe_leverage}x`} sub={`keeps the chance under 1%, up to $${usd(r.max_position_usdt, 0)}`} />
          <Stat label="Fees, open + close" value={`$${usd(r.fees_round_trip_usdt)}`} sub={`position $${usd(r.notional_usdt, 0)}, margin $${usd(r.margin_usdt)}`} />
        </div>
        {r.stop && (
          <div className="mt-4 rounded-lg bg-surface-2 p-3 text-sm">
            <b>Stop at {usd(r.stop.stop)}:</b> chance it is hit {prob(r.stop.p_stop_hit)}, loss if hit about ${usd(r.stop.loss_at_stop_usdt)}.
            {r.stop.stop_inside_liquidation && <span className="text-bad"> You would be liquidated before this stop.</span>}
          </div>
        )}
      </Card>
      {r.warnings.length > 0 && (
        <Card title="Warnings">
          <ul className="space-y-1.5 text-sm">
            {r.warnings.map((w) => (
              <li key={w} className="flex gap-2">
                <span className="text-warn" aria-hidden>
                  ▲
                </span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      <Card title="Why">
        <ul className="list-disc space-y-1.5 pl-5 text-sm">
          {r.explanation.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-muted">
          Model: {r.model.replaceAll("_", " ")} · maintenance margin {(100 * r.maintenance_margin_rate).toFixed(2)}% · volatility over horizon{" "}
          {r.sigma_horizon_pct.toFixed(2)}% ({r.volatility_source}). Paper trading only.
        </p>
      </Card>
    </div>
  );
}
