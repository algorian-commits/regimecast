"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import {
  createAlert,
  deleteAlert,
  deleteCheck,
  listAlerts,
  listChecks,
  logout,
  parseProfileForm,
  updateProfile,
  useSession,
  type Alert,
  type Me,
  type Profile,
} from "@/lib/account";
import { SYMBOLS, type Schemas, type Symbol } from "@/lib/api";
import { coin, prob, usd } from "@/lib/format";
import { useLoad } from "@/lib/use-load";
import { Badge, Button, Card, ErrorBox, Loading, LoadView, NotReady } from "./ui";

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

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`rounded-md px-3 py-2 text-sm font-medium ${on ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted hover:text-text"}`}
    >
      {children}
    </button>
  );
}

function ProfileCard({ p }: { p: Profile }) {
  const [f, setF] = useState({ balance: String(p.paper_balance), riskPct: String(+(p.risk_limit * 100).toFixed(2)) });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    const r = parseProfileForm(f);
    if (!r.ok) return setErrors(r.errors);
    setErrors({});
    setBusy(true);
    const out = await updateProfile(r.body);
    setBusy(false);
    setMsg(out.state === "ok" ? { ok: true, text: "Saved." } : { ok: false, text: out.state === "error" ? out.message : "Could not save right now." });
  }
  return (
    <Card title="Profile (paper account)">
      <form onSubmit={save} className="space-y-4" noValidate>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Paper balance (USDT)" hint="Pretend money. Used as the wallet for cross-margin risk checks." error={errors.balance}>
            <input className={input} inputMode="decimal" value={f.balance} onChange={(e) => setF({ ...f, balance: e.target.value })} />
          </Field>
          <Field label="Risk limit (%)" hint="The highest chance of liquidation you accept per trade. Safe leverage and the largest position follow from it." error={errors.riskPct}>
            <input className={input} inputMode="decimal" value={f.riskPct} onChange={(e) => setF({ ...f, riskPct: e.target.value })} />
          </Field>
        </div>
        <div className="flex items-center gap-3">
          <Button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save profile"}
          </Button>
          {msg && <span className={`text-sm ${msg.ok ? "text-good" : "text-bad"}`}>{msg.text}</span>}
        </div>
      </form>
    </Card>
  );
}

function SettingsCard({ p }: { p: Profile }) {
  const [err, setErr] = useState<string | null>(null);
  const save = async (body: Schemas["ProfileIn"]) => {
    const r = await updateProfile(body);
    setErr(r.state === "ok" ? null : r.state === "error" ? r.message : "Could not save right now.");
  };
  const fav = p.favourite_coins as Symbol[];
  const toggle = (s: Symbol) => save({ favourite_coins: fav.includes(s) ? fav.filter((x) => x !== s) : [...fav, s] });
  return (
    <Card title="Settings">
      <div className="space-y-4">
        <div>
          <div className="mb-1 text-sm font-medium">Default margin mode</div>
          <div className="flex gap-2">
            {(["isolated", "cross"] as const).map((m) => (
              <Chip key={m} on={p.default_margin_mode === m} onClick={() => save({ default_margin_mode: m })}>
                {m}
              </Chip>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1 text-sm font-medium">Favourite coins</div>
          <div className="flex gap-2">
            {SYMBOLS.map((s) => (
              <Chip key={s} on={fav.includes(s)} onClick={() => toggle(s)}>
                {coin(s)}
              </Chip>
            ))}
          </div>
          <div className="mt-1 text-xs text-muted">Favourites come first on the Risk Radar. Changes save right away.</div>
        </div>
        {err && <ErrorBox message={err} />}
      </div>
    </Card>
  );
}

function ChecksCard() {
  const [symbol, setSymbol] = useState<Symbol | "all">("all");
  const [checks, reload] = useLoad(() => listChecks(symbol === "all" ? { limit: 50 } : { symbol, limit: 50 }), symbol);
  return (
    <Card
      title="Saved risk checks"
      action={
        <div className="flex gap-1">
          {(["all", ...SYMBOLS] as const).map((s) => (
            <button key={s} onClick={() => setSymbol(s)} aria-pressed={s === symbol} className={`rounded-md px-2 py-1 text-xs font-medium ${s === symbol ? "bg-surface-2 text-text" : "text-muted"}`}>
              {s === "all" ? "All" : coin(s)}
            </button>
          ))}
        </div>
      }
    >
      <LoadView load={checks} onRetry={reload} notReady={(d) => <NotReady title="Not available">{d}</NotReady>}>
        {(d) =>
          d.checks.length === 0 ? (
            <NotReady title="No saved checks yet">
              Checks you save from <Link href="/risk-check" className="text-accent hover:underline">Risk Check</Link> appear here, only for you.
            </NotReady>
          ) : (
            <ul className="divide-y divide-border text-sm">
              {d.checks.map((c) => {
                const t = c.trade as { leverage?: number; entry?: number };
                const r = c.result as { p_liquidation?: number; liquidation_price?: number | null; horizon_hours?: number };
                return (
                  <li key={c.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                    <span className="text-xs text-muted">{c.created_at.slice(0, 16).replace("T", " ")} UTC</span>
                    <span className="font-medium">
                      {coin(c.symbol)} {c.side}
                      {t.leverage ? ` ${t.leverage}x` : ""}
                    </span>
                    {t.entry != null && <span className="font-mono text-xs">at {usd(t.entry)}</span>}
                    {r.p_liquidation != null && <Badge tone={r.p_liquidation < 0.01 ? "good" : r.p_liquidation < 0.05 ? "warn" : "bad"}>{prob(r.p_liquidation)} liq.{r.horizon_hours ? ` / ${r.horizon_hours}h` : ""}</Badge>}
                    {c.text && <span className="w-full truncate text-xs text-muted">“{c.text}”</span>}
                    <button className="ml-auto text-xs text-muted hover:text-bad" onClick={async () => (await deleteCheck(c.id), reload())}>
                      Delete
                    </button>
                  </li>
                );
              })}
            </ul>
          )
        }
      </LoadView>
    </Card>
  );
}

const KINDS: { v: Schemas["AlertIn"]["kind"]; label: string }[] = [
  { v: "price_below", label: "Price falls below" },
  { v: "price_above", label: "Price rises above" },
  { v: "stress_elevated", label: "Stress gauge elevated or high" },
  { v: "stress_high", label: "Stress gauge high" },
  { v: "regime_stressed", label: "Regime turns stressed" },
  { v: "position_risk", label: "Saved position's liquidation risk goes above" },
  { v: "whale_watch", label: "Whale Watch event" },
];

function alertState(a: Alert) {
  if (!a.active) return <Badge>paused</Badge>;
  if (a.kind === "whale_watch") return <Badge tone="good">on</Badge>;
  if (a.armed == null) return <Badge>first check soon</Badge>;
  return a.armed ? <Badge tone="good">waiting</Badge> : <Badge tone="warn">fired, waits to clear</Badge>;
}

function AlertsCard() {
  const [alerts, reload] = useLoad(() => listAlerts(), "alerts");
  const [checks] = useLoad(() => listChecks({ limit: 50 }), "alert-checks");
  const [f, setF] = useState<{ symbol: Symbol; kind: Schemas["AlertIn"]["kind"]; threshold: string; checkId: string }>({
    symbol: "BTCUSDT",
    kind: "price_below",
    threshold: "",
    checkId: "",
  });
  const [err, setErr] = useState<string | null>(null);
  const needsPrice = f.kind.startsWith("price_");
  const isPosition = f.kind === "position_risk";
  const saved = checks.state === "ok" ? checks.data.checks : [];
  async function add(e: FormEvent) {
    e.preventDefault();
    const th = Number(f.threshold.replace(/[,\s%]/g, ""));
    if (needsPrice && !(th > 0)) return setErr("Enter the price for this alert.");
    if (isPosition && !f.checkId) return setErr("Pick the saved check that holds your position.");
    if (isPosition && f.threshold && !(th > 0 && th < 100)) return setErr("Enter the limit as a percentage, e.g. 2.");
    const r = await createAlert({
      symbol: f.symbol,
      kind: f.kind,
      threshold: needsPrice ? th : isPosition && f.threshold ? th / 100 : null,
      check_id: isPosition ? Number(f.checkId) : null,
    });
    setErr(r.state === "ok" ? null : r.state === "error" ? r.message : "Could not save right now.");
    if (r.state === "ok") {
      setF({ ...f, threshold: "" });
      reload();
    }
  }
  const sel = "rounded-lg border border-border bg-bg px-3 py-2.5 text-sm";
  const describe = (a: Alert) => {
    const label = KINDS.find((k) => k.v === a.kind)?.label;
    if (a.kind === "position_risk") {
      const c = saved.find((x) => x.id === a.check_id);
      const t = c?.trade as { leverage?: number; side?: string; entry?: number } | undefined;
      return `${label} ${a.threshold != null ? prob(a.threshold) : "your risk limit"}${t ? ` (${t.leverage}x ${t.side} from ${usd(t.entry)})` : ""}`;
    }
    return a.threshold != null ? `${label} ${usd(a.threshold)}` : label;
  };
  return (
    <Card title="Alerts" action={<span className="text-xs text-muted">checked once a minute · fired alerts go to the bell</span>}>
      <form onSubmit={add} className="flex flex-wrap items-end gap-2" noValidate>
        {!isPosition && (
          <select className={sel} value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value as Symbol })} aria-label="Coin">
            {SYMBOLS.map((s) => (
              <option key={s} value={s}>
                {coin(s)}
              </option>
            ))}
          </select>
        )}
        <select className={sel} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value as Schemas["AlertIn"]["kind"], threshold: "" })} aria-label="When">
          {KINDS.map((k) => (
            <option key={k.v} value={k.v}>
              {k.label}
            </option>
          ))}
        </select>
        {needsPrice && <input className={`${input} w-36`} inputMode="decimal" placeholder="price" value={f.threshold} onChange={(e) => setF({ ...f, threshold: e.target.value })} aria-label="Price (USDT)" />}
        {isPosition && (
          <>
            <select className={`${sel} max-w-64`} value={f.checkId} onChange={(e) => setF({ ...f, checkId: e.target.value })} aria-label="Saved position">
              <option value="">{saved.length ? "Pick a saved check" : "No saved checks yet"}</option>
              {saved.map((c) => {
                const t = c.trade as { leverage?: number; entry?: number };
                return (
                  <option key={c.id} value={c.id}>
                    {coin(c.symbol)} {t.leverage}x {c.side} from {usd(t.entry)} · {c.created_at.slice(0, 10)}
                  </option>
                );
              })}
            </select>
            <input className={`${input} w-40`} inputMode="decimal" placeholder="limit % (or profile)" value={f.threshold} onChange={(e) => setF({ ...f, threshold: e.target.value })} aria-label="Limit (%)" />
          </>
        )}
        <Button type="submit" variant="ghost">
          Add alert
        </Button>
      </form>
      {err && (
        <div className="mt-2">
          <ErrorBox message={err} />
        </div>
      )}
      <div className="mt-3">
        <LoadView load={alerts} onRetry={reload} notReady={(d) => <NotReady title="Not available">{d}</NotReady>}>
          {(list) =>
            list.length === 0 ? (
              <p className="text-sm text-muted">
                No alerts yet. Each alert fires once, then waits until its condition has cleared before it can fire again.
              </p>
            ) : (
              <ul className="divide-y divide-border text-sm">
                {list.map((a) => (
                  <li key={a.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
                    <span className="font-medium">{coin(a.symbol)}</span>
                    <span>{describe(a)}</span>
                    {alertState(a)}
                    {a.fired_count > 0 && (
                      <span className="text-xs text-muted">
                        fired {a.fired_count}× · last {a.last_fired_at?.slice(0, 16).replace("T", " ")} UTC
                      </span>
                    )}
                    <button className="ml-auto text-xs text-muted hover:text-bad" onClick={async () => (await deleteAlert(a.id), reload())}>
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
            )
          }
        </LoadView>
      </div>
    </Card>
  );
}

function Signed({ me }: { me: Me }) {
  const router = useRouter();
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="text-muted">
          Logged in as <span className="font-medium text-text">{me.email}</span> · member since {me.created_at.slice(0, 10)}
        </span>
        <Button variant="ghost" onClick={async () => (await logout(), router.push("/"))}>
          Log out
        </Button>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <ProfileCard key={`p-${me.profile.updated_at}`} p={me.profile} />
        <SettingsCard p={me.profile} />
      </div>
      <ChecksCard />
      <AlertsCard />
    </div>
  );
}

export function Account() {
  const s = useSession();
  if (s.state === "loading") return <Loading lines={6} />;
  if (s.state === "unavailable") return <NotReady title="Accounts are not available right now">{s.detail}</NotReady>;
  if (s.state === "out")
    return (
      <Card className="mx-auto max-w-md text-center">
        <p className="text-sm text-muted">Log in to see your paper balance, risk limit, saved checks and alerts.</p>
        <div className="mt-4 flex justify-center gap-3">
          <Link href="/login?next=/account" className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg">
            Log in
          </Link>
          <Link href="/signup" className="rounded-lg border border-border px-4 py-2.5 text-sm font-semibold">
            Sign up
          </Link>
        </div>
      </Card>
    );
  return <Signed me={s.me} />;
}
