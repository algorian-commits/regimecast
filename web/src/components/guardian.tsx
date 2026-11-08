"use client";

// Position Guardian page (Phase 13C): Telegram, connected sources, watched positions with live risk, alert history.
import Link from "next/link";
import { useState, type FormEvent, type ReactNode } from "react";
import { useSession } from "@/lib/account";
import { SYMBOLS, type Load, type Symbol } from "@/lib/api";
import { ago, coin, pct, prob, usd } from "@/lib/format";
import {
  addBinance,
  addHyperliquid,
  addManual,
  isAddress,
  parseManualForm,
  patchPosition,
  positionTone,
  readGuardian,
  removePosition,
  removeSource,
  ruleWords,
  saferText,
  telegramLink,
  telegramPatch,
  telegramTest,
  telegramUnlink,
  type GuardianData,
  type ManualForm,
  type Position,
  type Source,
  type Telegram,
} from "@/lib/guardian";
import { useLoad } from "@/lib/use-load";
import { Badge, Button, Card, ErrorBox, Loading, LoadView, NotReady } from "./ui";

const input = "w-full rounded-lg border border-border bg-bg px-3 py-2.5 font-mono text-base outline-none focus:border-accent";
const sel = "rounded-lg border border-border bg-bg px-3 py-2.5 text-sm";

const msgOf = (r: Load<unknown>) => (r.state === "error" ? r.message : r.state === "not_ready" ? r.detail : null);

function Field({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string; children: ReactNode }) {
  return (
    <label className="block min-w-0">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      {children}
      {error ? <span className="mt-1 block text-xs text-bad">{error}</span> : hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

// ------------------------------------------------------------------ Telegram
function TelegramCard({ t, reload }: { t: Telegram; reload: () => void }) {
  const [link, setLink] = useState<{ url: string; expires_at: string } | null>(null);
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (f: () => Promise<Load<unknown>>, ok?: string) => {
    setBusy(true);
    setNote(null);
    const r = await f();
    setBusy(false);
    const m = msgOf(r);
    setNote(m ? { ok: false, text: m } : ok ? { ok: true, text: ok } : null);
    if (!m) reload();
    return r;
  };
  if (!t.configured)
    return (
      <Card title="Telegram">
        <NotReady title="Telegram is not set up on this server">Alerts still arrive in the bell at the top of the page.</NotReady>
      </Card>
    );
  if (!t.linked)
    return (
      <Card title="Telegram">
        <p className="text-sm text-muted">Get Guardian alerts (and your other alerts) on your phone. Open the link on the phone that has Telegram and tap Start.</p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button
            disabled={busy}
            onClick={async () => {
              const r = await run(() => telegramLink());
              if (r.state === "ok") setLink(r.data as { url: string; expires_at: string });
            }}
          >
            {link ? "New link" : "Connect Telegram"}
          </Button>
          {link && (
            <Button variant="ghost" onClick={reload}>
              I tapped Start
            </Button>
          )}
        </div>
        {link && (
          <div className="mt-3 rounded-lg bg-surface-2 p-3 text-sm">
            <a href={link.url} target="_blank" rel="noreferrer" className="break-all font-mono text-accent hover:underline">
              {link.url}
            </a>
            <div className="mt-1 text-xs text-muted">Works once, until {link.expires_at.slice(11, 16)} UTC. Do not share it: whoever opens it gets your alerts.</div>
          </div>
        )}
        {note && <p className={`mt-2 text-sm ${note.ok ? "text-good" : "text-bad"}`}>{note.text}</p>}
      </Card>
    );
  return (
    <Card title="Telegram" action={<Badge tone={t.enabled ? "good" : "muted"}>{t.enabled ? "on" : "paused"}</Badge>}>
      <p className="text-sm">
        Linked to <span className="font-medium">{t.username ? `@${t.username}` : "your Telegram chat"}</span>
        {t.linked_at && <span className="text-muted"> since {t.linked_at.slice(0, 10)}</span>}.{" "}
        <span className="text-muted">{t.kinds == null ? "Every alert kind is sent." : `Sends: ${t.kinds.join(", ") || "nothing"}.`}</span>
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button variant="ghost" disabled={busy} onClick={() => run(() => telegramTest(), "Sent. Check Telegram.")}>
          Send a test message
        </Button>
        <Button variant="ghost" disabled={busy} onClick={() => run(() => telegramPatch({ enabled: !t.enabled, all_kinds: false }))}>
          {t.enabled ? "Pause Telegram" : "Resume Telegram"}
        </Button>
        <Button variant="ghost" disabled={busy} onClick={() => run(() => telegramUnlink(), "Unlinked.")} className="hover:text-bad">
          Unlink
        </Button>
      </div>
      {note && <p className={`mt-2 text-sm ${note.ok ? "text-good" : "text-bad"}`}>{note.text}</p>}
    </Card>
  );
}

// ------------------------------------------------------------------ sources
function sourceStatus(s: Source) {
  if (s.status === "ok") return <Badge tone="good">ok</Badge>;
  if (s.status === "new") return <Badge>first read soon</Badge>;
  return <Badge tone="bad">{s.status}</Badge>;
}

function SourcesCard({ g, reload }: { g: GuardianData; reload: () => void }) {
  const [mode, setMode] = useState<"hyperliquid" | "binance">("hyperliquid");
  const [hl, setHl] = useState({ address: "", label: "" });
  const [bn, setBn] = useState({ key: "", secret: "", label: "" });
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function add(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    if (mode === "hyperliquid" && !isAddress(hl.address)) return setErr("A wallet address is 0x followed by 40 letters and digits.");
    if (mode === "binance" && (!bn.key.trim() || !bn.secret.trim())) return setErr("Paste both the API key and the secret.");
    setBusy(true);
    const r = mode === "hyperliquid" ? await addHyperliquid(hl.address, hl.label) : await addBinance(bn.key.trim(), bn.secret.trim(), bn.label);
    setBusy(false);
    // The key and secret leave the page's memory whatever the answer: they are never shown again.
    setBn({ key: "", secret: "", label: "" });
    const m = msgOf(r);
    if (m) return setErr(m);
    setHl({ address: "", label: "" });
    reload();
  }
  return (
    <Card title="Connected sources" action={<span className="text-xs text-muted">read once a minute · up to 5</span>}>
      {g.sources.length === 0 ? (
        <p className="text-sm text-muted">Nothing connected yet. Add a Hyperliquid wallet or a read-only Binance key below, or enter a position by hand.</p>
      ) : (
        <ul className="divide-y divide-border text-sm">
          {g.sources.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
              <span className="font-medium">{s.kind === "hyperliquid" ? "Hyperliquid" : "Binance"}</span>
              <span className="min-w-0 truncate font-mono text-xs">{s.label ?? (s.address ? `${s.address.slice(0, 8)}…${s.address.slice(-6)}` : "read-only key")}</span>
              {sourceStatus(s)}
              <span className="text-xs text-muted">
                {s.positions} open position{s.positions === 1 ? "" : "s"}
                {s.last_polled_at && ` · read ${ago(s.last_polled_at)}`}
              </span>
              {s.kind === "binance" && s.permissions && <span className="text-xs text-muted">reading only, checked every 6 h</span>}
              {s.last_error && <span className="w-full text-xs text-bad">{s.last_error}</span>}
              <button
                className="ml-auto text-xs text-muted hover:text-bad"
                onClick={async () => (await removeSource(s.id), reload())}
                title={s.kind === "binance" ? "Stops watching and deletes the key at once" : "Stops watching this address"}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={add} className="mt-4 space-y-3 border-t border-border pt-4" noValidate>
        <div className="flex gap-2">
          {(["hyperliquid", "binance"] as const).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={mode === m}
              onClick={() => (setMode(m), setErr(null))}
              className={`rounded-md px-3 py-2 text-sm font-medium ${mode === m ? "bg-accent text-accent-fg" : "bg-surface-2 text-muted hover:text-text"}`}
            >
              {m === "hyperliquid" ? "Hyperliquid wallet" : "Binance read-only key"}
            </button>
          ))}
        </div>
        {mode === "hyperliquid" ? (
          <div className="grid gap-3 sm:grid-cols-[2fr_1fr]">
            <Field label="Wallet address" hint="Public: Hyperliquid shows every account's positions, so no key is needed.">
              <input className={input} placeholder="0x…" spellCheck={false} autoComplete="off" value={hl.address} onChange={(e) => setHl({ ...hl, address: e.target.value })} />
            </Field>
            <Field label="Name (optional)">
              <input className={input} maxLength={60} value={hl.label} onChange={(e) => setHl({ ...hl, label: e.target.value })} />
            </Field>
          </div>
        ) : !g.binance_keys_enabled ? (
          <NotReady title="Binance keys are switched off on this server">Use a Hyperliquid wallet or enter the position by hand.</NotReady>
        ) : (
          <>
            <div className="rounded-lg border border-warn/40 bg-warn/10 p-3 text-sm">
              Make a <span className="font-medium">new</span> API key on Binance with <span className="font-medium">only &quot;Enable Reading&quot;</span> ticked. RegimeCast asks
              Binance what the key may do and refuses it if it can trade, withdraw or transfer. It is stored encrypted, never shown again, and
              deleted the moment you remove it.
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="API key">
                <input className={input} type="password" autoComplete="off" spellCheck={false} value={bn.key} onChange={(e) => setBn({ ...bn, key: e.target.value })} />
              </Field>
              <Field label="Secret key">
                <input className={input} type="password" autoComplete="off" spellCheck={false} value={bn.secret} onChange={(e) => setBn({ ...bn, secret: e.target.value })} />
              </Field>
              <Field label="Name (optional)">
                <input className={input} maxLength={60} value={bn.label} onChange={(e) => setBn({ ...bn, label: e.target.value })} />
              </Field>
            </div>
          </>
        )}
        {err && <ErrorBox message={err} />}
        {(mode === "hyperliquid" || g.binance_keys_enabled) && (
          <Button type="submit" variant="ghost" disabled={busy}>
            {busy ? (mode === "binance" ? "Checking the key with Binance…" : "Reading positions…") : "Connect"}
          </Button>
        )}
      </form>
    </Card>
  );
}

// ------------------------------------------------------------------ positions
function PositionRow({ p, reload }: { p: Position; reload: () => void }) {
  const tone = positionTone(p);
  const holding = ruleWords(p.states);
  const safer = saferText(p.safer);
  const crossed = p.distance_pct != null && p.distance_pct <= 0;
  return (
    <li className={`space-y-2 py-3 ${p.muted ? "opacity-60" : ""}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-medium">
          {coin(p.symbol)} {p.side}
          {p.leverage ? ` ${+p.leverage.toFixed(1)}x` : ""}
        </span>
        <span className="font-mono text-xs">
          {+p.qty.toPrecision(4)} {coin(p.symbol)} from {usd(p.entry)}
        </span>
        <span className="text-xs text-muted">{p.source}</span>
        {p.muted && <Badge>muted</Badge>}
        {holding.map((h) => (
          <Badge key={h} tone="bad">
            {h}
          </Badge>
        ))}
        <div className="ml-auto flex gap-3 text-xs">
          <button className="text-muted hover:text-text" onClick={async () => (await patchPosition(p.id, { muted: !p.muted, clear_risk_limit: false }), reload())}>
            {p.muted ? "Unmute" : "Mute"}
          </button>
          {p.manual && (
            <button className="text-muted hover:text-bad" onClick={async () => (await removePosition(p.id), reload())}>
              Remove
            </button>
          )}
        </div>
      </div>
      {p.covered === false ? (
        <p className="text-xs text-muted">{p.note ?? "RegimeCast does not model this coin, so it cannot judge the risk. It is listed only."}</p>
      ) : p.p_liquidation == null ? (
        <p className="text-xs text-muted">{p.note ? `Not judged this minute: ${p.note}.` : "First check within a minute."}</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-5">
            <div>
              <div className="text-xs text-muted">Liquidation in 24 h</div>
              <div className={`font-mono font-semibold ${tone === "bad" ? "text-bad" : tone === "warn" ? "text-warn" : "text-good"}`}>{prob(p.p_liquidation)}</div>
              <div className="text-xs text-muted">limit {prob(p.risk_limit)}</div>
            </div>
            <div>
              <div className="text-xs text-muted">Mark / liquidation</div>
              <div className="font-mono">{usd(p.mark_price)}</div>
              <div className="font-mono text-xs text-muted">{usd(p.liquidation_price)}</div>
            </div>
            <div>
              <div className="text-xs text-muted">Distance</div>
              {crossed ? (
                <div className="font-semibold text-bad">past liquidation</div>
              ) : (
                <>
                  <div className="font-mono">{p.distance_pct != null ? pct(p.distance_pct / 100, 1) : "–"}</div>
                  <div className="text-xs text-muted">{p.moves_1h != null ? `${p.moves_1h.toFixed(1)} typical 1 h moves` : ""}</div>
                </>
              )}
            </div>
            <div>
              <div className="text-xs text-muted">Stress</div>
              <div>{p.stress_level ?? "–"}</div>
            </div>
            <div>
              <div className="text-xs text-muted">Checked</div>
              <div className="text-xs">{p.checked_at ? ago(p.checked_at) : "–"}</div>
            </div>
          </div>
          {crossed ? (
            <p className="rounded-lg bg-bad/10 px-3 py-2 text-sm">
              The mark price is already past the liquidation price: on the exchange this position would have been liquidated. If it is still open,
              check it there now.
            </p>
          ) : safer && p.safer?.status !== "safe" && (
            <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm">
              <span className="font-medium">Safer version: </span>
              {safer}
              {p.safer?.stop_advice && <span className="text-muted"> {p.safer.stop_advice}</span>}
            </p>
          )}
        </>
      )}
    </li>
  );
}

const EMPTY_FORM: ManualForm = { symbol: "BTCUSDT", side: "long", entry: "", leverage: "", margin: "", stop: "" };

function ManualForm({ reload }: { reload: () => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<ManualForm>(EMPTY_FORM);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);
  if (!open)
    return (
      <Button variant="ghost" onClick={() => setOpen(true)}>
        Enter a position by hand
      </Button>
    );
  async function save(e: FormEvent) {
    e.preventDefault();
    const r = parseManualForm(f);
    if (!r.ok) return setErrors(r.errors);
    setErrors({});
    const out = await addManual(r.body);
    const m = msgOf(out);
    if (m) return setErr(m);
    setErr(null);
    setF(EMPTY_FORM);
    setOpen(false);
    reload();
  }
  return (
    <form onSubmit={save} className="space-y-3 rounded-lg border border-border p-3" noValidate>
      <div className="flex flex-wrap gap-2">
        <select className={sel} value={f.symbol} onChange={(e) => setF({ ...f, symbol: e.target.value as Symbol })} aria-label="Coin">
          {SYMBOLS.map((s) => (
            <option key={s} value={s}>
              {coin(s)}
            </option>
          ))}
        </select>
        <select className={sel} value={f.side} onChange={(e) => setF({ ...f, side: e.target.value as "long" | "short" })} aria-label="Side">
          <option value="long">long</option>
          <option value="short">short</option>
        </select>
      </div>
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="Entry price" error={errors.entry}>
          <input className={input} inputMode="decimal" value={f.entry} onChange={(e) => setF({ ...f, entry: e.target.value })} />
        </Field>
        <Field label="Leverage" error={errors.leverage}>
          <input className={input} inputMode="decimal" value={f.leverage} onChange={(e) => setF({ ...f, leverage: e.target.value })} />
        </Field>
        <Field label="Margin (USDT)" error={errors.margin} hint="Isolated margin">
          <input className={input} inputMode="decimal" value={f.margin} onChange={(e) => setF({ ...f, margin: e.target.value })} />
        </Field>
        <Field label="Stop (optional)" error={errors.stop}>
          <input className={input} inputMode="decimal" value={f.stop} onChange={(e) => setF({ ...f, stop: e.target.value })} />
        </Field>
      </div>
      {err && <ErrorBox message={err} />}
      <div className="flex gap-2">
        <Button type="submit">Watch it</Button>
        <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function PositionsCard({ g, reload }: { g: GuardianData; reload: () => void }) {
  return (
    <Card title="Watched positions" action={<span className="text-xs text-muted">your limit: {prob(g.profile_risk_limit)} liquidation chance in 24 h</span>}>
      {g.positions.length === 0 ? (
        <p className="text-sm text-muted">No open positions found. Positions from connected sources appear here by themselves once they are open.</p>
      ) : (
        <ul className="divide-y divide-border">
          {g.positions.map((p) => (
            <PositionRow key={p.id} p={p} reload={reload} />
          ))}
        </ul>
      )}
      <div className="mt-3">
        <ManualForm reload={reload} />
      </div>
      <p className="mt-3 text-xs text-muted">
        An alert fires when the 24 h liquidation chance goes over your limit, when stress turns high while you hold a long at 2x or more, or when
        the price comes within 2 typical one-hour moves of your liquidation price. Each fires once and waits for the condition to clear before it
        can fire again. Change the limit on your <Link href="/account" className="text-accent hover:underline">account page</Link>.
      </p>
    </Card>
  );
}

function HistoryCard({ g }: { g: GuardianData }) {
  return (
    <Card title="Guardian alerts">
      {g.alerts.length === 0 ? (
        <p className="text-sm text-muted">No Guardian alerts yet.</p>
      ) : (
        <ul className="divide-y divide-border text-sm">
          {g.alerts.map((a) => (
            <li key={a.id} className="py-2">
              <div className="flex flex-wrap items-center gap-x-3">
                <span className="font-medium">{a.title}</span>
                <span className="text-xs text-muted">{a.fired_at.slice(0, 16).replace("T", " ")} UTC</span>
              </div>
              <p className="mt-0.5 whitespace-pre-line text-muted">{a.message}</p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function Signed() {
  const [g, reload] = useLoad(() => readGuardian(), "guardian", 60_000);
  return (
    <LoadView load={g} onRetry={reload} lines={8} notReady={(d) => <NotReady title="The Guardian is not available right now">{d}</NotReady>}>
      {(d) => (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <Badge tone={d.status.running ? "good" : "warn"}>{d.status.running ? "Guardian running" : "Guardian not running on this server"}</Badge>
            {d.status.last_run && <span>last check {ago(d.status.last_run)}</span>}
          </div>
          <PositionsCard g={d} reload={reload} />
          <div className="grid gap-4 lg:grid-cols-2">
            <SourcesCard g={d} reload={reload} />
            <TelegramCard t={d.telegram} reload={reload} />
          </div>
          <HistoryCard g={d} />
        </div>
      )}
    </LoadView>
  );
}

export function Guardian() {
  const s = useSession();
  if (s.state === "loading") return <Loading lines={6} />;
  if (s.state === "unavailable") return <NotReady title="Accounts are not available right now">{s.detail}</NotReady>;
  if (s.state === "out")
    return (
      <Card className="mx-auto max-w-md text-center">
        <p className="text-sm text-muted">Log in to let the Guardian watch your open positions.</p>
        <div className="mt-4 flex justify-center gap-3">
          <Link href="/login?next=/guardian" className="rounded-lg bg-accent px-4 py-2.5 text-sm font-semibold text-accent-fg">
            Log in
          </Link>
          <Link href="/signup" className="rounded-lg border border-border px-4 py-2.5 text-sm font-semibold">
            Sign up
          </Link>
        </div>
      </Card>
    );
  return <Signed />;
}
