// Phase 13C: the Guardian page's form check and wording.
import { describe, expect, it } from "vitest";
import { isAddress, parseManualForm, positionTone, ruleWords, saferText, type Position } from "./guardian";

const pos = (p: Partial<Position>): Position => ({
  id: 1, source_id: null, source: "entered by hand", manual: true, symbol: "BTCUSDT", side: "long", entry: 100000, qty: 0.01,
  leverage: 10, margin_mode: "isolated", stop: null, muted: false, risk_limit: 0.02, own_limit: false, covered: true,
  p_liquidation: 0.001, moves_1h: 12, states: { guardian_risk: false, guardian_stress: false, guardian_near: false }, ...p,
});

describe("Guardian page", () => {
  it("accepts only 0x + 40 hex digits as a wallet address", () => {
    expect(isAddress("0x" + "a".repeat(40))).toBe(true);
    expect(isAddress(" 0x" + "A1".repeat(20) + " ")).toBe(true);
    expect(isAddress("0x" + "a".repeat(39))).toBe(false);
    expect(isAddress("0x" + "g".repeat(40))).toBe(false);
  });
  it("checks the hand-entry form and builds an isolated position", () => {
    const f = { symbol: "ETHUSDT" as const, side: "long" as const, entry: "3,000", leverage: "20x", margin: "$100", stop: "" };
    expect(parseManualForm(f)).toEqual({
      ok: true,
      body: { symbol: "ETHUSDT", side: "long", entry: 3000, leverage: 20, margin: 100, margin_mode: "isolated", stop: null },
    });
    const bad = parseManualForm({ ...f, entry: "", leverage: "200", stop: "3100" });
    expect(bad.ok).toBe(false);
    if (!bad.ok) expect(Object.keys(bad.errors).sort()).toEqual(["entry", "leverage"]);
    const wrongStop = parseManualForm({ ...f, stop: "3100" });
    expect(!wrongStop.ok && wrongStop.errors.stop).toBe("A long's stop is below the entry.");
  });
  it("colours a row by its rules", () => {
    expect(positionTone(pos({}))).toBe("good");
    expect(positionTone(pos({ p_liquidation: 0.015 }))).toBe("warn");
    expect(positionTone(pos({ moves_1h: 3 }))).toBe("warn");
    expect(positionTone(pos({ states: { guardian_risk: true } }))).toBe("bad");
    expect(positionTone(pos({ covered: false }))).toBe("muted");
    expect(positionTone(pos({ p_liquidation: null }))).toBe("muted");
  });
  it("says the safer version in one sentence", () => {
    expect(saferText(null)).toBeNull();
    expect(saferText({ status: "safe", limit: 0.02 })).toBe("Already within your limit.");
    expect(saferText({ status: "fixed", limit: 0.02, leverage_now: 25, leverage_safe: 8, add_margin_usdt: 212.5, reduce_to_usdt: 800 })).toBe(
      "Add 212.50 USDT margin, or reduce the position to 800.00 USDT (25x now, 8x is safe).",
    );
    expect(saferText({ status: "none", limit: 0.02 })).toMatch(/closing it/);
  });
  it("names the rules that hold", () => {
    expect(ruleWords({ guardian_risk: true, guardian_stress: null, guardian_near: true })).toEqual(["over your limit", "near liquidation"]);
    expect(ruleWords(null)).toEqual([]);
  });
});
