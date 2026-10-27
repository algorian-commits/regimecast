// Phase 8 (M5 dashboard, M6 accounts): the pure logic behind the live feed and the account forms.
import { describe, expect, it } from "vitest";
import { checkAuthForm, orderCoins, parseProfileForm, riskDefaults, toSession, type Profile } from "./account";
import { hourChangePct, safeNext, scalePos, sparkPath, tint } from "./dashboard";
import { appendBookHistory, backoffMs, isTick, type LiveTick } from "./live";

const book = { bid: 100, ask: 100.1, mid: 100.05, spread_bps: 10, bid_depth_usdt: 600, ask_depth_usdt: 400, depth_2bps_usdt: 0, imbalance: 0.2, levels: 20, reach_bps: 3, exchange_time: "" };
const tick = (at: string, extra: Partial<LiveTick["coins"]["BTCUSDT"]> = {}): LiveTick => ({
  type: "tick",
  at,
  upstream: "ok",
  coins: { BTCUSDT: { stale: false, book, ...extra }, ETHUSDT: { stale: true } },
});

describe("live feed", () => {
  it("waits longer after each failed reconnect, up to 15 s", () => {
    expect([0, 1, 2, 3, 4, 5, 10].map(backoffMs)).toEqual([1000, 2000, 4000, 8000, 15000, 15000, 15000]);
  });
  it("keeps a capped, ordered order-book history and skips stale coins and repeats", () => {
    let h = appendBookHistory({}, tick("2026-10-27T10:00:00Z"));
    h = appendBookHistory(h, tick("2026-10-27T10:00:00Z")); // same snapshot twice
    h = appendBookHistory(h, tick("2026-10-27T10:00:01Z"));
    h = appendBookHistory(h, tick("2026-10-27T10:00:02Z"), 2);
    expect(h.BTCUSDT?.map((p) => p.t)).toEqual([Date.parse("2026-10-27T10:00:01Z"), Date.parse("2026-10-27T10:00:02Z")]);
    expect(h.BTCUSDT?.[0]).toMatchObject({ spread_bps: 10, depth: 1000, imbalance: 0.2 });
    expect(h.ETHUSDT).toBeUndefined();
  });
  it("only accepts snapshot messages", () => {
    expect(isTick(tick("2026-10-27T10:00:00Z"))).toBe(true);
    expect(isTick({ type: "other" })).toBe(false);
    expect(isTick(null)).toBe(false);
  });
});

describe("dashboard helpers", () => {
  it("measures the move in the current hour", () => {
    const candle = { t: "", open: 100, high: 0, low: 0, close: 0, volume: 0, closed: false };
    expect(hourChangePct({ stale: false, mark_price: 101, candle })).toBeCloseTo(1);
    expect(hourChangePct({ stale: false, mark_price: 101 })).toBeNull();
  });
  it("draws a sparkline inside its box", () => {
    expect(sparkPath([1], 100, 10)).toBe("");
    expect(sparkPath([0, 5, 10], 100, 10)).toBe("M0.0,9.0 L50.0,5.0 L100.0,1.0");
    expect(sparkPath([3, 3], 100, 10)).toBe("M0.0,9.0 L100.0,9.0"); // flat line, no division by zero
  });
  it("clamps gauge positions and softens theme colours", () => {
    expect([scalePos(-1, 100), scalePos(50, 100), scalePos(500, 100)]).toEqual([0, 50, 100]);
    expect(tint("#15803d", 0.16)).toBe("rgba(21,128,61,0.16)");
    expect(tint("not a colour", 0.5)).toBe("rgba(128,128,128,0.5)");
  });
  it("only redirects to this site's own pages after login", () => {
    expect(safeNext("/risk-check?symbol=ETHUSDT")).toBe("/risk-check?symbol=ETHUSDT");
    for (const bad of [null, "https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)"]) expect(safeNext(bad)).toBe("/account");
  });
});

describe("accounts", () => {
  const profile: Profile = { paper_balance: 2500, risk_limit: 0.02, default_margin_mode: "cross", favourite_coins: ["SOLUSDT"], updated_at: "" };
  it("reads the login state from /me", () => {
    expect(toSession(200, { email: "a@example.test" }).state).toBe("in");
    expect(toSession(401, { detail: "Log in first." })).toEqual({ state: "out" });
    expect(toSession(503, { status: "not_ready", detail: "no DATABASE_URL" })).toEqual({ state: "unavailable", detail: "no DATABASE_URL" });
  });
  it("checks the e-mail and password before sending", () => {
    expect(checkAuthForm("a@example.test", "long enough")).toEqual({});
    expect(Object.keys(checkAuthForm("nope", "short"))).toEqual(["email", "password"]);
    expect(checkAuthForm("a@example.test", "x".repeat(129)).password).toBeDefined();
  });
  it("turns the profile form into API values", () => {
    expect(parseProfileForm({ balance: "$12,500", riskPct: "1.5%" })).toEqual({ ok: true, body: { paper_balance: 12500, risk_limit: 0.015 } });
    const bad = parseProfileForm({ balance: "-1", riskPct: "80" });
    expect(bad.ok).toBe(false);
    if (!bad.ok) expect(Object.keys(bad.errors)).toEqual(["balance", "riskPct"]);
  });
  it("gives the Risk Check the user's limit, wallet and margin mode", () => {
    expect(riskDefaults(profile)).toEqual({ risk_limit: 0.02, wallet_balance: 2500, margin_mode: "cross" });
  });
  it("puts favourite coins first", () => {
    expect(orderCoins(["BTCUSDT", "ETHUSDT", "SOLUSDT"], ["SOLUSDT", "DOGEUSDT"])).toEqual(["SOLUSDT", "BTCUSDT", "ETHUSDT"]);
    expect(orderCoins(["BTCUSDT", "ETHUSDT", "SOLUSDT"], undefined)).toEqual(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
  });
});
