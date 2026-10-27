// Phase 12: the trade verdict's beginner view wording, and the take-profit field.
import { describe, expect, it } from "vitest";
import { buildTrade, DEFAULT_FORM, formFromRequest } from "./trade-form";
import { breakEven, fixRows, gradeTone, money, outcome, whatIfLines, type Verdict, type Version, type WhatIf } from "./verdict";

const version = (v: Partial<Version>): Version => ({
  leverage: 25, notional_usdt: 2500, margin_usdt: 100, stop: null, liquidation_price: 96385.54, p_liquidation: 0.24,
  p_stop_hit: null, max_loss_usdt: 100, grade: "F", ...v,
});

describe("trade verdict wording", () => {
  it("colours grades: A and B good, C warn, D and F bad", () => {
    expect(["A", "B", "C", "D", "F"].map(gradeTone)).toEqual(["good", "good", "warn", "bad", "bad"]);
  });
  it("signs money with a real minus and says how the trade ended", () => {
    expect(money(12.5)).toBe("+$12.50");
    expect(money(-100)).toBe("−$100.00");
    expect(outcome(-100, "liquidation")).toBe("−$100.00, liquidated");
    expect(outcome(-47.4, "stop")).toBe("−$47.40, your stop closes it");
  });
  it("shows rises and falls, and only the fall on the crash day", () => {
    const day: WhatIf = { scenario: "normal", label: "Normal day", move_pct: 5.3, up_usdt: 130, up_exit: null, down_usdt: -100, down_exit: "liquidation", basis: "" };
    expect(whatIfLines(day)).toEqual([
      { label: "Rises 5.3%", text: "+$130.00", tone: "good" },
      { label: "Falls 5.3%", text: "−$100.00, liquidated", tone: "bad" },
    ]);
    const crash: WhatIf = { ...day, scenario: "crash", move_pct: 13.02, up_usdt: null, down_usdt: 325.5, down_exit: null };
    expect(whatIfLines(crash)).toEqual([{ label: "Falls 13.0%", text: "+$325.50", tone: "good" }]); // a short gains
  });
  it("lines up the original and the safer version", () => {
    const rows = fixRows(version({}), version({ leverage: 12, notional_usdt: 1200, stop: 96150.4, liquidation_price: 92034.81, p_liquidation: 0.008, max_loss_usdt: 47.4, grade: "B" }));
    expect(rows.map((r) => r.label)).toEqual(["Grade", "Leverage", "Position", "Stop-loss", "Liquidation price", "Chance of liquidation", "Most you can lose"]);
    expect(rows[0]).toEqual({ label: "Grade", original: "F", fixed: "B" });
    expect(rows[3]).toEqual({ label: "Stop-loss", original: "none", fixed: "96,150.40" });
    expect(rows[5].fixed).toBe("0.80%");
  });
  it("says 'never' when costs eat every win", () => {
    const v = { break_even_win_rate: 0.5176 } as Verdict;
    expect(breakEven(v)).toBe("52%");
    expect(breakEven({ break_even_win_rate: 1.2 } as Verdict)).toBe("never");
    expect(breakEven({ break_even_win_rate: null } as Verdict)).toBeNull();
  });
});

describe("take-profit", () => {
  const f = { ...DEFAULT_FORM, entry: "100000", leverage: "5", margin: "100" };
  it("goes above entry for a long and below for a short", () => {
    expect(buildTrade({ ...f, take_profit: "110000" }).ok).toBe(true);
    const bad = buildTrade({ ...f, take_profit: "90000" });
    expect(bad.ok).toBe(false);
    if (!bad.ok) expect(bad.errors.take_profit).toMatch(/above entry/);
    expect(buildTrade({ ...f, side: "short", take_profit: "90000" }).ok).toBe(true);
  });
  it("is sent when given and round-trips through a saved check", () => {
    const r = buildTrade({ ...f, stop: "95000", take_profit: "110000" });
    expect(r.ok && r.body.take_profit).toBe(110000);
    if (r.ok) expect(buildTrade(formFromRequest(r.body))).toEqual(r);
    const none = buildTrade(f);
    expect(none.ok && none.body.take_profit).toBeNull();
  });
});
