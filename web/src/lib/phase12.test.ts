// Phase 12: trade verdict helpers and the take-profit target in the Risk Check form.
import { describe, expect, it } from "vitest";
import { buildTrade, DEFAULT_FORM, formFromRequest } from "./trade-form";
import { gradeTone, moneyTone, signedPct, signedUsd } from "./verdict";

describe("verdict helpers", () => {
  it("colours grades", () => {
    expect(["A", "B", "C", "D", "F"].map(gradeTone)).toEqual(["good", "good", "warn", "bad", "bad"]);
  });
  it("signs money and percentages", () => {
    expect(signedUsd(1234.5)).toBe("+$1,234.50");
    expect(signedUsd(-20)).toBe("−$20.00");
    expect(signedPct(-4)).toBe("−4.0%");
    expect(signedPct(0)).toBe("0.0%");
    expect([moneyTone(5), moneyTone(-5), moneyTone(0)]).toEqual(["good", "bad", "muted"]);
  });
});

describe("take-profit target", () => {
  const f = { ...DEFAULT_FORM, entry: "100000", stop: "95000" };
  it("is optional and sent as null when empty", () => {
    const r = buildTrade(f);
    expect(r.ok && r.body.target).toBeNull();
  });
  it("must be on the profit side", () => {
    const bad = buildTrade({ ...f, target: "90000" });
    expect(!bad.ok && bad.errors.target).toBe("For a long, the target goes above entry.");
    const short = buildTrade({ ...f, side: "short", stop: "105000", target: "110000" });
    expect(!short.ok && short.errors.target).toBe("For a short, the target goes below entry.");
    const ok = buildTrade({ ...f, target: "110000" });
    expect(ok.ok && ok.body.target).toBe(110000);
  });
  it("round-trips through a saved check", () => {
    const r = buildTrade({ ...f, target: "110000" });
    expect(r.ok && formFromRequest(r.body).target).toBe("110000");
  });
});
