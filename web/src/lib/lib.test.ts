import { describe, expect, it } from "vitest";
import { toLoad, type Symbol } from "./api";
import { ago, prob, riskBand, usd } from "./format";
import { summarize, type MoodHour } from "./news-mood";
import { BrowserHistory, filterChecks, fromAccount, type NewCheck, type SavedCheck } from "./history";
import { formFromParsed, type Parsed } from "./parse";
import { buildTrade, DEFAULT_FORM, entryFromMark, formFromRequest, RISK_LIMIT, withDefaults } from "./trade-form";

describe("toLoad", () => {
  it("maps API answers to page states", () => {
    expect(toLoad(200, { a: 1 })).toEqual({ state: "ok", data: { a: 1 } });
    expect(toLoad(503, { status: "not_ready", detail: "model not trained yet" })).toEqual({ state: "not_ready", detail: "model not trained yet" });
    expect(toLoad(404, { detail: "Not Found" }).state).toBe("not_ready"); // endpoint not deployed yet
    expect(toLoad(503, { detail: "overloaded" }).state).toBe("error"); // a real outage is not a placeholder
    expect(toLoad(500, null)).toEqual({ state: "error", message: "The server answered 500." });
  });
  it("turns FastAPI validation errors into one readable line", () => {
    const r = toLoad(422, { detail: [{ loc: ["body", "stop"], msg: "Value error, stop must be below entry" }] });
    expect(r).toEqual({ state: "error", message: "stop: stop must be below entry" });
  });
});

describe("buildTrade", () => {
  const f = { ...DEFAULT_FORM, entry: "81,447.0", leverage: "10", margin: "100" };
  it("builds the /risk-check body", () => {
    const r = buildTrade(f);
    expect(r.ok).toBe(true);
    if (r.ok) {
      expect(r.body).toMatchObject({ symbol: "BTCUSDT", side: "long", entry: 81447, leverage: 10, margin: 100, stop: null, wallet_balance: null });
      expect(r.body.risk_limit).toBe(RISK_LIMIT);
    }
  });
  it("catches bad input before calling the API", () => {
    const r = buildTrade({ ...f, entry: "", leverage: "200", stop: "90000" });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(Object.keys(r.errors).sort()).toEqual(["entry", "leverage"]);
    const s = buildTrade({ ...f, stop: "90000" }); // long with stop above entry
    expect(!s.ok && s.errors.stop).toBeTruthy();
    expect(buildTrade({ ...f, side: "short", stop: "90000" }).ok).toBe(true);
    const c = buildTrade({ ...f, margin_mode: "cross" });
    expect(!c.ok && c.errors.wallet_balance).toBeTruthy();
  });
  it("rounds the live price to a tidy entry", () => {
    expect(entryFromMark(81447.02135507)).toBe("81447.0");
    expect(entryFromMark(103.7261)).toBe("103.73");
  });
});

describe("format", () => {
  it("formats numbers and probabilities", () => {
    expect(usd(81447.021)).toBe("81,447.02");
    expect(usd(null)).toBe("–");
    expect(prob(0.00004)).toBe("<0.01%");
    expect(prob(0.0234)).toBe("2.3%");
    expect(riskBand(0.005)).toBe("low");
    expect(riskBand(0.02)).toBe("elevated");
    expect(riskBand(0.2)).toBe("high");
    expect(ago("2026-10-23T00:00:00Z", Date.parse("2026-10-23T00:05:00Z"))).toBe("5 min ago");
  });
});

describe("news mood summary", () => {
  const h = (end: string, count: number, mean: number, neg: number, shocks = 0): MoodHour => ({
    hour_end: end,
    count,
    mean_score: mean,
    share_negative: neg,
    policy_shock_count: shocks,
  });
  const now = Date.parse("2026-10-23T12:30:00Z");
  it("weights by headline count over the last 24 finished hours", () => {
    const s = summarize([h("2026-10-23T12:00:00Z", 3, -0.5, 1, 1), h("2026-10-23T11:00:00Z", 1, 0.5, 0)], now);
    expect(s.headlines).toBe(4);
    expect(s.mean_score).toBeCloseTo(-0.25);
    expect(s.share_negative).toBeCloseTo(0.75);
    expect(s.policy_shocks).toBe(1);
    expect(s.label).toBe("negative");
  });
  it("ignores hours that have not ended yet and hours older than the window (no look-ahead)", () => {
    const s = summarize([h("2026-10-23T13:00:00Z", 50, -1, 1), h("2026-10-22T12:00:00Z", 50, -1, 1), h("2026-10-23T10:00:00Z", 2, 0.4, 0)], now);
    expect(s.headlines).toBe(2);
    expect(s.label).toBe("positive");
  });
  it("says 'no news' instead of a fake neutral score", () => {
    expect(summarize([], now)).toMatchObject({ headlines: 0, mean_score: null, label: "no news" });
  });
});

describe("formFromParsed", () => {
  const base: Parsed = {
    intent: "trade", asset: "ETH", side: "short", leverage: 10, entry: 2600, stop: 2700,
    qty_coins: null, notional_usdt: null, margin_usdt: 250, margin_mode: null, wallet_usdt: null,
  };
  it("copies the stated fields and marks them as from the text", () => {
    const { form, notes, filled } = formFromParsed(base, 2650);
    expect(form).toMatchObject({ symbol: "ETHUSDT", side: "short", leverage: "10", entry: "2600", stop: "2700", margin: "250", margin_mode: "isolated" });
    expect(notes).toEqual([]);
    expect(filled.sort()).toEqual(["entry", "leverage", "margin", "side", "stop", "symbol"]);
    expect(buildTrade(form).ok).toBe(true);
  });
  it("works out the margin from a coin amount or a dollar position, and uses the live price when no entry is given", () => {
    const coins = formFromParsed({ ...base, margin_usdt: null, qty_coins: 0.5, entry: null }, 2650.123);
    expect(coins.form.entry).toBe("2650.1");
    expect(coins.form.margin).toBe("132.51"); // 0.5 x 2650.1 / 10, rounded to cents
    expect(coins.notes.join(" ")).toMatch(/live price.*Margin worked out from 0.5 ETH/);
    expect(formFromParsed({ ...base, margin_usdt: null, notional_usdt: 3000 }, null).form.margin).toBe("300");
  });
  it("leaves the margin empty when it cannot be worked out", () => {
    const r = formFromParsed({ ...base, margin_usdt: null, qty_coins: 1, leverage: null }, 2600);
    expect(r.form.margin).toBe("");
    expect(r.form.leverage).toBe("");
  });
});

describe("saved checks", () => {
  const mem = () => {
    const m = new Map<string, string>();
    return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
  };
  const check = (symbol: Symbol): NewCheck => ({
    symbol, text: null,
    request: { symbol, side: "long", entry: 100, leverage: 10, margin: 10, margin_mode: "isolated", horizon_hours: 24, risk_limit: 0.01 },
    result: { symbol, side: "long", p_liquidation: 0.01 } as NewCheck["result"],
  });
  it("adds newest first, removes, and survives broken storage", async () => {
    const kv = mem();
    const h = new BrowserHistory(() => kv);
    const a = await h.add(check("BTCUSDT"));
    await h.add(check("SOLUSDT"));
    expect((await h.list()).map((c) => c.symbol)).toEqual(["SOLUSDT", "BTCUSDT"]);
    await h.remove(a.id);
    expect((await h.list()).length).toBe(1);
    kv.setItem("regimecast.checks.v1", "{not json");
    expect(await h.list()).toEqual([]);
    const blocked = new BrowserHistory(() => ({ getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } }));
    expect(await blocked.list()).toEqual([]);
    await expect(blocked.add(check("BTCUSDT"))).resolves.toMatchObject({ symbol: "BTCUSDT" });
    expect(await new BrowserHistory(() => null).list()).toEqual([]);
  });
  it("filters by coin and by UTC day, both ends included", () => {
    const at = (symbol: Symbol, created_at: string) => ({ ...check(symbol), id: created_at, created_at }) as SavedCheck;
    const list = [at("BTCUSDT", "2026-10-27T23:59:00Z"), at("ETHUSDT", "2026-10-26T10:00:00Z"), at("BTCUSDT", "2026-10-20T00:00:00Z")];
    expect(filterChecks(list, { symbol: "BTCUSDT", from: "", to: "" }).length).toBe(2);
    expect(filterChecks(list, { symbol: "all", from: "2026-10-26", to: "2026-10-27" }).map((c) => c.symbol)).toEqual(["BTCUSDT", "ETHUSDT"]);
    expect(filterChecks(list, { symbol: "BTCUSDT", from: "", to: "2026-10-26" }).length).toBe(1);
  });
});

describe("formFromRequest", () => {
  it("round-trips a saved body through the form", () => {
    const body = buildTrade({ ...DEFAULT_FORM, entry: "81000", stop: "79000", margin_mode: "cross", wallet_balance: "5000" });
    expect(body.ok).toBe(true);
    if (body.ok) expect(buildTrade(formFromRequest(body.body))).toEqual(body);
  });
});

describe("account defaults (Phase 7 + 8)", () => {
  const d = { risk_limit: 0.02, wallet_balance: 5000, margin_mode: "cross" as const };
  it("fill margin mode and wallet unless the user's text gave them", () => {
    expect(withDefaults(DEFAULT_FORM, null)).toBe(DEFAULT_FORM);
    expect(withDefaults(DEFAULT_FORM, d)).toMatchObject({ margin_mode: "cross", wallet_balance: "5000" });
    const typed = { ...DEFAULT_FORM, margin_mode: "isolated" as const };
    expect(withDefaults(typed, d, ["margin_mode"])).toMatchObject({ margin_mode: "isolated", wallet_balance: "5000" });
  });
  it("send the account's risk limit with the check", () => {
    const f = { ...DEFAULT_FORM, entry: "100", margin_mode: "cross" as const, wallet_balance: "5000" };
    const a = buildTrade(f), b = buildTrade(f, 0.02);
    expect(a.ok && a.body.risk_limit).toBe(RISK_LIMIT);
    expect(b.ok && b.body.risk_limit).toBe(0.02);
  });
  it("read a /me/checks row as a saved check", () => {
    const c = fromAccount({ id: 7, symbol: "ETHUSDT", side: "short", text: "short eth 5x", trade: { symbol: "ETHUSDT", side: "short" }, result: { p_liquidation: 0.03 }, created_at: "2026-10-27T10:00:00+00:00" });
    expect(c).toMatchObject({ id: "7", symbol: "ETHUSDT", text: "short eth 5x", created_at: "2026-10-27T10:00:00+00:00" });
    expect(filterChecks([c], { symbol: "ETHUSDT", from: "2026-10-27", to: "2026-10-27" }).length).toBe(1);
  });
});
