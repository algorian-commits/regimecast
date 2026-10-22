import { describe, expect, it } from "vitest";
import { toLoad } from "./api";
import { ago, prob, riskBand, usd } from "./format";
import { summarize, type MoodHour } from "./news-mood";
import { buildTrade, DEFAULT_FORM, entryFromMark, RISK_LIMIT } from "./trade-form";

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
