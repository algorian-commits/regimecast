// Phase 11 (M12): the crash-replay player never shows a minute after the current one.
import { describe, expect, it } from "vitest";
import { advance, frameAt, marksUpTo, pointsUpTo, timeAt, type Replay } from "./replay";

const hour = (t: string, regime: string) => ({ t, regime, p_stressed: 0.1, p_stress: 0.05, stress_level: "low", stress_drivers: [], p_stress_walk_forward: null, sigma_pct: 2, safe_leverage: 10 });
const r = {
  first_close: "2022-11-08T09:58:00Z",
  n_minutes: 4,
  liquidated_at: "2022-11-08T10:00:00Z",
  minutes: { price: [100, 99, 98, 97], low: [], high: [], p_liquidation: [0.1, 0.2, 1, 1], distance_pct: [3, 2, 0, 0], hour: [0, 0, 1, 1] },
  hours: [hour("2022-11-08T09:00:00Z", "active"), hour("2022-11-08T10:00:00Z", "stressed")],
} as unknown as Replay;

describe("crash replay", () => {
  it("labels minute i with its close time", () => {
    expect(timeAt(r, 0)).toBe("2022-11-08T09:58:00.000Z");
    expect(timeAt(r, 2)).toBe("2022-11-08T10:00:00.000Z");
  });
  it("shows the hour known at each minute and the liquidation from its minute on", () => {
    expect([0, 1, 2, 3].map((i) => frameAt(r, i).hour?.regime)).toEqual(["active", "active", "stressed", "stressed"]);
    expect([0, 1, 2, 3].map((i) => frameAt(r, i).liquidated)).toEqual([false, false, true, true]);
    expect(frameAt(r, 99).i).toBe(3);
  });
  it("draws only points up to the current minute", () => {
    expect(pointsUpTo(r, 1, 10).map((p) => p.price)).toEqual([100, 99]);
    expect(pointsUpTo(r, 3, 2).map((p) => p.price)).toEqual([100, 98, 97]);
    expect(pointsUpTo(r, 0, 10)).toHaveLength(1);
  });
  it("moves speed x tick minutes per tick, at least one, and stops at the end", () => {
    expect(advance(0, 1, 100, 250)).toBe(1);
    expect(advance(0, 60, 100, 250)).toBe(15);
    expect(advance(95, 360, 100, 250)).toBe(99);
  });
  it("shows only the news that has happened", () => {
    const marks = [["2022-11-08T09:00:00Z", "a"], ["2022-11-08T16:00:00Z", "b"]];
    expect(marksUpTo(marks, "2022-11-08T10:00:00Z")).toEqual([["2022-11-08T09:00:00Z", "a"]]);
    expect(marksUpTo(null, "2022-11-08T10:00:00Z")).toEqual([]);
  });
});
