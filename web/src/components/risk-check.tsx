"use client";

import { useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, load, SYMBOLS, type Load, type Schemas, type Symbol } from "@/lib/api";
import { coin, prob, riskBand, usd } from "@/lib/format";
import { riskDefaults, useSession } from "@/lib/account";
import { accountHistory, browserHistory, filterChecks, type HistoryFilter, type SavedCheck } from "@/lib/history";
import { EXAMPLES, formFromParsed, type ParseResult } from "@/lib/parse";
import { buildTrade, DEFAULT_FORM, entryFromMark, formFromRequest, RISK_LIMIT, withDefaults, type TradeForm } from "@/lib/trade-form";
import { DriversChart, ProbabilityBar } from "./charts";
import { Badge, Button, Card, ErrorBox, Loading, Stat } from "./ui";
import { gradeTone } from "@/lib/verdict";
import { VerdictView } from "./verdict";

type Market = Schemas["MarketOut"];
type Risk = Schemas["RiskOut"];
type Step = "type" | "confirm" | "result";
/** Where the confirm step's fields came from, shown above the form. */
type Source = { text: string; parser: string; notes: string[]; warnings: string[]; filled: (keyof TradeForm)[] };

const input = "w-full rounded-lg border border-border bg-bg px-3 py-2.5 font-mono text-base outline-none focus:border-accent";

function Field({ label, hint, error, fromText, children }: { label: string; hint?: string; error?: string; fromText?: boolean; children: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 flex items-center gap-2 text-sm font-medium">
        {label}
        {fromText && <Badge tone="muted">from your text</Badge>}
      </span>
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

function Steps({ step, go }: { step: Step; go: (s: Step) => void }) {
  const steps: { s: Step; label: string }[] = [
    { s: "type", label: "Type" },
    { s: "confirm", label: "Confirm" },
    { s: "result", label: "Results" },
  ];
  const at = steps.findIndex((x) => x.s === step);
  return (
    <ol className="mb-4 flex items-center gap-2 text-sm" aria-label="Steps">
      {steps.map((x, i) => (
        <li key={x.s} className="flex items-center gap-2">
          {i > 0 && <span className="h-px w-4 bg-border sm:w-8" aria-hidden />}
          <button
            type="button"
            disabled={i >= at}
            onClick={() => go(x.s)}
            aria-current={i === at ? "step" : undefined}
            className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 font-medium ${
              i === at ? "bg-accent text-accent-fg" : i < at ? "bg-surface-2 text-text hover:bg-border" : "text-muted"
            }`}
          >
            <span className="font-mono">{i + 1}</span> {x.label}
          </button>
        </li>
      ))}
    </ol>
  );
}

export function RiskCheck() {
  const params = useSearchParams();
  const initial = (SYMBOLS as string[]).includes(params.get("symbol") ?? "") ? (params.get("symbol") as Symbol) : "BTCUSDT";
  const [step, setStep] = useState<Step>("type");
  const [f, setF] = useState<TradeForm>({ ...DEFAULT_FORM, symbol: initial });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [market, setMarket] = useState<Load<Market>>({ state: "loading" });
  const [result, setResult] = useState<Load<Risk> | null>(null);
  const [busy, setBusy] = useState(false);
  const [source, setSource] = useState<Source | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [history, setHistory] = useState<SavedCheck[]>([]);
  const [shownLimit, setShownLimit] = useState(RISK_LIMIT); // the risk limit the shown result was checked with
  const session = useSession();
  const defaults = session.state === "in" ? riskDefaults(session.me.profile) : null;
  const limit = defaults?.risk_limit ?? RISK_LIMIT;
  const store = session.state === "in" ? accountHistory : browserHistory;
  const touchedEntry = useRef(false);
  const top = useRef<HTMLDivElement>(null);

  const set = <K extends keyof TradeForm>(k: K, v: TradeForm[K]) => setF((p) => ({ ...p, [k]: v }));
  const go = (s: Step) => {
    setStep(s);
    requestAnimationFrame(() => top.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
  };
  const refreshHistory = useCallback(() => store.list().then(setHistory), [store]);
  useEffect(() => {
    if (session.state !== "loading") refreshHistory(); // wait until we know which store to read
  }, [refreshHistory, session.state]);

  // Live price for the chosen coin; pre-fills the entry unless the user (or their text) gave one.
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

  /** Step 1 done: the parser understood the trade. Fill the form and move to the confirm step. */
  const onParsed = async (r: ParseResult, text: string) => {
    const sym = r.trade.asset && r.trade.asset !== "OTHER" ? (`${r.trade.asset}USDT` as Symbol) : f.symbol;
    const m = await load(() => api.GET("/market/{symbol}", { params: { path: { symbol: sym } } }));
    const mark = m.state === "ok" ? m.data.mark_price : null;
    const { form, notes, filled } = formFromParsed(r.trade, mark);
    touchedEntry.current = true; // the entry is the user's or the live price taken just now
    setMarket(m);
    setF(withDefaults(form, defaults, filled));
    setErrors({});
    // the API also warns about a missing entry; the form's own note says it with the price used, so show it once
    const warnings = r.warnings.filter((w) => !(w.startsWith("No entry price given") && notes.some((n) => n.startsWith("No entry price given"))));
    setSource({ text, parser: r.parser, notes, warnings, filled });
    go("confirm");
  };

  const manual = () => {
    touchedEntry.current = false;
    setSource(null);
    setF((p) => withDefaults({ ...DEFAULT_FORM, symbol: p.symbol, entry: market.state === "ok" ? entryFromMark(market.data.mark_price) : "" }, defaults));
    go("confirm");
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const b = buildTrade(f, limit);
    if (!b.ok) {
      setErrors(b.errors);
      return;
    }
    setErrors({});
    setBusy(true);
    setSavedAt(null);
    setShownLimit(limit);
    go("result");
    const r = await load(() => api.POST("/risk-check", { body: b.body }));
    setResult(r);
    setBusy(false);
    if (r.state === "ok") {
      await store.add({ symbol: f.symbol, text: source?.text ?? null, request: b.body, result: r.data });
      refreshHistory();
    }
  };

  const openSaved = (c: SavedCheck) => {
    touchedEntry.current = true;
    setF(formFromRequest(c.request));
    setSource(c.text ? { text: c.text, parser: "saved", notes: [], warnings: [], filled: [] } : null);
    setResult({ state: "ok", data: c.result });
    setSavedAt(c.created_at);
    setShownLimit(c.request.risk_limit ?? RISK_LIMIT);
    setBusy(false);
    go("result");
  };

  const mark = market.state === "ok" ? market.data.mark_price : null;
  const from = (k: keyof TradeForm) => !!source?.filled.includes(k);

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,4fr)]">
      <div ref={top} className="min-w-0 scroll-mt-20">
        <Steps step={step} go={go} />
        {step === "type" && <TypeStep onParsed={onParsed} onManual={manual} />}

        {step === "confirm" && (
          <Card title="Check the details">
            {source ? (
              <div className="mb-4 space-y-2 rounded-lg bg-surface-2 p-3 text-sm">
                <div>
                  <span className="text-muted">You wrote: </span>&ldquo;{source.text}&rdquo;
                </div>
                {[...source.notes, ...source.warnings].map((n) => (
                  <div key={n} className="flex gap-2">
                    <span className="text-warn" aria-hidden>
                      ▲
                    </span>
                    {n}
                  </div>
                ))}
                <div className="text-xs text-muted">
                  {source.parser === "rules" || source.parser === "saved" ? "" : `Read by Claude (${source.parser}). `}Fix anything that is
                  wrong before checking.
                </div>
              </div>
            ) : (
              <p className="mb-4 text-sm text-muted">Fill in your trade.</p>
            )}
            {defaults && (
              <p className="mb-4 text-xs text-muted">
                From your account: risk limit {prob(defaults.risk_limit)} (sets the safe leverage), margin mode and wallet balance.
              </p>
            )}
            <form onSubmit={submit} className="space-y-4" noValidate>
              <Field label="Coin" fromText={from("symbol")}>
                <Toggle
                  value={f.symbol}
                  options={SYMBOLS.map((s) => ({ v: s, label: coin(s) }))}
                  onChange={(v) => {
                    touchedEntry.current = false;
                    setMarket({ state: "loading" });
                    set("symbol", v);
                  }}
                />
              </Field>
              <Field label="Side" fromText={from("side")}>
                <Toggle value={f.side} options={[{ v: "long", label: "Long" }, { v: "short", label: "Short" }]} onChange={(v) => set("side", v)} />
              </Field>
              <Field
                label="Entry price (USDT)"
                error={errors.entry}
                fromText={from("entry")}
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
                <Field label="Leverage (x)" error={errors.leverage} fromText={from("leverage")}>
                  <input className={input} inputMode="decimal" value={f.leverage} onChange={(e) => set("leverage", e.target.value)} />
                </Field>
                <Field label="Margin (USDT)" error={errors.margin} fromText={from("margin")}>
                  <input className={input} inputMode="decimal" value={f.margin} onChange={(e) => set("margin", e.target.value)} />
                </Field>
              </div>
              <Field
                label="Stop-loss (optional)"
                error={errors.stop}
                fromText={from("stop")}
                hint={f.side === "long" ? "Below your entry." : "Above your entry."}
              >
                <input className={input} inputMode="decimal" value={f.stop} placeholder="none" onChange={(e) => set("stop", e.target.value)} />
              </Field>
              <Field
                label="Take-profit target (optional)"
                error={errors.target}
                hint="Where you plan to take profit. With a stop, it shows the win rate you need."
              >
                <input className={input} inputMode="decimal" value={f.target} placeholder="none" onChange={(e) => set("target", e.target.value)} />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Horizon">
                  <Toggle value={f.horizon_hours} options={[{ v: 1, label: "1h" }, { v: 24, label: "24h" }]} onChange={(v) => set("horizon_hours", v)} />
                </Field>
                <Field label="Margin mode" fromText={from("margin_mode")}>
                  <Toggle
                    value={f.margin_mode}
                    options={[
                      { v: "isolated", label: "Isolated" },
                      { v: "cross", label: "Cross" },
                    ]}
                    onChange={(v) => set("margin_mode", v)}
                  />
                </Field>
              </div>
              {f.margin_mode === "cross" && (
                <Field
                  label="Cross wallet balance (USDT)"
                  error={errors.wallet_balance}
                  fromText={from("wallet_balance")}
                  hint="Everything in your cross-margin wallet backs this position."
                >
                  <input className={input} inputMode="decimal" value={f.wallet_balance} onChange={(e) => set("wallet_balance", e.target.value)} />
                </Field>
              )}
              <div className="flex gap-3">
                <Button type="button" variant="ghost" onClick={() => go("type")}>
                  Back
                </Button>
                <Button type="submit" disabled={busy} className="flex-1">
                  {busy ? "Checking…" : "Check risk"}
                </Button>
              </div>
            </form>
          </Card>
        )}

        {step === "result" && (
          <div className="space-y-4">
            {busy || result === null ? (
              <Card title="Results">
                <Loading lines={6} />
              </Card>
            ) : result.state === "ok" ? (
              <RiskResult r={result.data} text={source?.text ?? null} savedAt={savedAt} limit={shownLimit} />
            ) : (
              <Card title="Results">
                <ErrorBox message={result.state === "error" ? result.message : result.state === "not_ready" ? "The risk engine is not available right now." : ""} />
              </Card>
            )}
            <div className="flex gap-3">
              <Button variant="ghost" onClick={() => go("confirm")}>
                Edit this trade
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  setSource(null);
                  go("type");
                }}
              >
                New trade
              </Button>
            </div>
          </div>
        )}
      </div>

      <History list={history} where={session.state === "in" ? "on your account" : "in this browser"} onOpen={openSaved} onRemove={(id) => store.remove(id).then(refreshHistory)} />
    </div>
  );
}

/** Step 1: type the trade. A follow-up question keeps the earlier messages, so a short answer ("10x") is enough. */
function TypeStep({ onParsed, onManual }: { onParsed: (r: ParseResult, text: string) => void; onManual: () => void }) {
  const [text, setText] = useState("");
  const [thread, setThread] = useState<{ you: string; ask: string }[]>([]);
  const [reply, setReply] = useState<{ tone: "warn" | "bad"; message: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const send = async () => {
    const t = text.trim();
    if (!t || busy) return;
    setBusy(true);
    setReply(null);
    const history = thread.map((x) => x.you).slice(-4);
    const r = await load(() => api.POST("/parse-trade", { body: { text: t, history } }));
    setBusy(false);
    if (r.state !== "ok") {
      setReply({ tone: "bad", message: r.state === "error" ? r.message : "The trade reader is not available right now. Use the form instead." });
      return;
    }
    const p = r.data;
    if (p.status === "ok") {
      onParsed(p, [...history, t].join(" · "));
      setThread([]);
      setText("");
    } else if (p.status === "needs_info") {
      setThread((th) => [...th, { you: t, ask: p.message ?? "" }].slice(-4));
      setText("");
    } else {
      setThread([]);
      setReply({ tone: "warn", message: p.message ?? "" });
    }
  };

  return (
    <Card title="Describe your trade">
      {thread.length > 0 && (
        <ol className="mb-3 space-y-2 text-sm" aria-label="Conversation so far">
          {thread.map((x, i) => (
            <li key={i} className="space-y-2">
              <div className="ml-8 rounded-lg bg-accent/10 px-3 py-2">{x.you}</div>
              <div className="mr-8 rounded-lg bg-surface-2 px-3 py-2">{x.ask}</div>
            </li>
          ))}
        </ol>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
        className="space-y-3"
      >
        <label className="block">
          <span className="sr-only">Your trade</span>
          <textarea
            className={`${input} min-h-24 resize-y font-sans`}
            maxLength={1000}
            value={text}
            placeholder={thread.length ? "Your answer…" : "e.g. Long 0.1 BTC at 81k, 10x, stop 79k"}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send();
              }
            }}
          />
        </label>
        {thread.length === 0 && (
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((x) => (
              <button
                key={x}
                type="button"
                onClick={() => setText(x)}
                className="rounded-full border border-border px-3 py-1 text-xs text-muted hover:bg-surface-2 hover:text-text"
              >
                {x}
              </button>
            ))}
          </div>
        )}
        {reply && (
          <div className={`rounded-lg border p-3 text-sm ${reply.tone === "bad" ? "border-bad/40 bg-bad/10" : "border-warn/40 bg-warn/10"}`} role="alert">
            {reply.message}
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={busy || !text.trim()}>
            {busy ? "Reading…" : thread.length ? "Send" : "Read my trade"}
          </Button>
          {thread.length > 0 && (
            <Button type="button" variant="ghost" onClick={() => setThread([])}>
              Start over
            </Button>
          )}
          <button type="button" onClick={onManual} className="text-sm text-muted underline-offset-2 hover:text-text hover:underline">
            Fill in the form instead
          </button>
        </div>
      </form>
      <p className="mt-3 text-xs text-muted">
        The reader only turns your words into fields; it never gives advice. You check every field on the next step.
      </p>
    </Card>
  );
}

const REGIME_TONE = { calm: "good", active: "warn", stressed: "bad" } as const;
const STRESS_TONE = { low: "good", elevated: "warn", high: "bad" } as const;

function RiskResult({ r, text, savedAt, limit }: { r: Risk; text: string | null; savedAt: string | null; limit: number }) {
  const [numbers, setNumbers] = useState(false);
  const v = r.verdict;
  return (
    <div className="space-y-4">
      {(savedAt || text) && (
        <p className="text-sm text-muted">
          {savedAt && <>Saved check from {new Date(savedAt).toLocaleString()} (the numbers are from then). </>}
          {text && <>&ldquo;{text}&rdquo;</>}
        </p>
      )}
      {v && <VerdictView r={r} v={v} />}
      {v && (
        <Button type="button" variant="ghost" className="w-full" aria-expanded={numbers} onClick={() => setNumbers((x) => !x)}>
          {numbers ? "Hide the numbers" : "Show me the numbers"}
        </Button>
      )}
      {(!v || numbers) && <RiskNumbers r={r} limit={limit} />}
    </div>
  );
}

/** The full detail for experienced users (the Phase 7 results screen). */
function RiskNumbers({ r, limit }: { r: Risk; limit: number }) {
  const band = riskBand(r.p_liquidation);
  const tone = band === "low" ? "good" : band === "elevated" ? "warn" : "bad";
  return (
    <div className="space-y-4">
      <Card title={`${coin(r.symbol)} ${r.side}, ${r.horizon_hours}h horizon`} action={<Badge tone={tone}>{band} risk</Badge>}>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Stat label={`Chance of liquidation, ${r.horizon_hours}h`} value={prob(r.p_liquidation)} tone={tone} />
            <ProbabilityBar p={r.p_liquidation} limit={limit} />
          </div>
          <Stat
            label="Liquidation price"
            value={r.liquidation_price == null ? "none" : usd(r.liquidation_price)}
            sub={r.liquidation_distance_pct == null ? "cannot be liquidated" : `${r.liquidation_distance_pct.toFixed(2)}% away`}
          />
          <Stat label="Max safe leverage" value={`${r.max_safe_leverage}x`} sub={`keeps the chance under ${prob(limit)}, up to $${usd(r.max_position_usdt, 0)}`} />
          <Stat label="Fees, open + close" value={`$${usd(r.fees_round_trip_usdt)}`} sub={`position $${usd(r.notional_usdt, 0)}, margin $${usd(r.margin_usdt)}`} />
        </div>
        {r.stop && (
          <div className="mt-4 rounded-lg bg-surface-2 p-3 text-sm">
            <b>Stop at {usd(r.stop.stop)}:</b> chance it is hit {prob(r.stop.p_stop_hit)}, loss if hit about ${usd(r.stop.loss_at_stop_usdt)}.
            {r.stop.stop_inside_liquidation && <span className="text-bad"> You would be liquidated before this stop.</span>}
          </div>
        )}
      </Card>

      <Card title="Market now">
        <div className="grid grid-cols-2 gap-4">
          <div className="min-w-0">
            <div className="text-xs text-muted">Regime</div>
            <div className="mt-1">{r.regime ? <Badge tone={REGIME_TONE[r.regime as keyof typeof REGIME_TONE] ?? "muted"}>{r.regime}</Badge> : <span className="text-sm text-muted">not available</span>}</div>
          </div>
          <div className="min-w-0">
            <div className="text-xs text-muted">Stress risk, next hour</div>
            <div className="mt-1 flex items-center gap-2">
              {r.stress_level ? (
                <>
                  <Badge tone={STRESS_TONE[r.stress_level as keyof typeof STRESS_TONE] ?? "muted"}>{r.stress_level}</Badge>
                  {r.p_stress_next_hour != null && <span className="font-mono text-sm">{prob(r.p_stress_next_hour)}</span>}
                </>
              ) : (
                <span className="text-sm text-muted">not available</span>
              )}
            </div>
          </div>
          <Stat label={`Expected volatility, ${r.horizon_hours}h`} value={`${r.sigma_horizon_pct.toFixed(2)}%`} sub={r.volatility_source} />
        </div>
      </Card>

      <Card title="What drives the volatility forecast">
        {r.drivers.length ? (
          <DriversChart drivers={r.drivers} />
        ) : (
          <p className="text-sm text-muted">Drivers are shown when the live HAR-S forecast is in use; it is not available right now.</p>
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
      <Card title="In plain English">
        <ul className="list-disc space-y-1.5 pl-5 text-sm">
          {r.explanation.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-muted">
          Model: {r.model.replaceAll("_", " ")} · maintenance margin {(100 * r.maintenance_margin_rate).toFixed(2)}%. Paper trading only. Not financial
          advice.
        </p>
      </Card>
    </div>
  );
}

function History({ list, where, onOpen, onRemove }: { list: SavedCheck[]; where: string; onOpen: (c: SavedCheck) => void; onRemove: (id: string) => void }) {
  const [flt, setFlt] = useState<HistoryFilter>({ symbol: "all", from: "", to: "" });
  const shown = filterChecks(list, flt);
  const date = "w-full min-w-0 rounded-lg border border-border bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent";
  return (
    <Card title="Saved checks" action={<span className="text-xs text-muted">{where}</span>} className="min-w-0 self-start">
      <div className="mb-3 space-y-2">
        <Toggle
          value={flt.symbol}
          options={[{ v: "all" as const, label: "All" }, ...SYMBOLS.map((s) => ({ v: s, label: coin(s) }))]}
          onChange={(v) => setFlt((p) => ({ ...p, symbol: v }))}
        />
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-muted">
            From
            <input type="date" className={date} value={flt.from} onChange={(e) => setFlt((p) => ({ ...p, from: e.target.value }))} />
          </label>
          <label className="text-xs text-muted">
            To
            <input type="date" className={date} value={flt.to} onChange={(e) => setFlt((p) => ({ ...p, to: e.target.value }))} />
          </label>
        </div>
      </div>
      {list.length === 0 ? (
        <p className="text-sm text-muted">Your checks are saved here automatically.</p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted">No saved checks match these filters.</p>
      ) : (
        <ul className="max-h-[32rem] divide-y divide-border overflow-y-auto">
          {shown.map((c) => {
            const band = riskBand(c.result.p_liquidation);
            return (
              <li key={c.id} className="flex items-start gap-2 py-2">
                <button type="button" onClick={() => onOpen(c)} className="min-w-0 flex-1 rounded-md p-1 text-left hover:bg-surface-2">
                  <div className="flex items-center gap-2 text-sm font-medium">
                    {coin(c.symbol)} {c.result.side} {c.request.leverage}x
                    {c.result.verdict && <Badge tone={gradeTone(c.result.verdict.grade)}>{c.result.verdict.grade}</Badge>}
                    <Badge tone={band === "low" ? "good" : band === "elevated" ? "warn" : "bad"}>{prob(c.result.p_liquidation)}</Badge>
                  </div>
                  <div className="truncate text-xs text-muted">
                    {new Date(c.created_at).toLocaleString()} · entry {usd(c.request.entry)}
                    {c.text ? ` · “${c.text}”` : ""}
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => onRemove(c.id)}
                  aria-label={`Delete the ${coin(c.symbol)} check from ${new Date(c.created_at).toLocaleString()}`}
                  className="rounded-md px-2 py-1 text-muted hover:bg-surface-2 hover:text-bad"
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}
