// 13D: the Traders page's sums and wording.
import { describe, expect, it } from "vitest";
import { bandRows, copyBadge, holdWords, mapTotals, money, netWords, rowsAbovePrice, type Band, type NetCoin } from "./traders";

const band = (from_pct: number, long_usd: number, short_usd: number): Band => ({
  from_pct,
  to_pct: from_pct + 1,
  price_low: 100 + from_pct,
  price_high: 101 + from_pct,
  long_usd,
  short_usd,
  n_long: long_usd ? 1 : 0,
  n_short: short_usd ? 1 : 0,
});
const net = (o: Partial<NetCoin>): NetCoin => ({ coin: "BTC", long_usd: 0, short_usd: 0, net_usd: 0, n_long: 0, n_short: 0, n_flat: 0, n_read: 0, ...o });

describe("liquidation map rows", () => {
  const bands = [band(-2, 100, 0), band(1, 0, 50), band(-1, 200, 0)];
  it("puts the highest price first and scales bars to the biggest", () => {
    const rows = bandRows(bands);
    expect(rows.map((r) => r.from_pct)).toEqual([1, -1, -2]);
    expect(rows[1].longShare).toBe(1);
    expect(rows[2].longShare).toBe(0.5);
    expect(rows[0].shortShare).toBe(0.25);
  });
  it("knows where the current price falls", () => {
    expect(rowsAbovePrice(bands)).toBe(1);
    expect(bandRows(bands).map((r) => r.above)).toEqual([true, false, false]);
  });
  it("adds up the totals and copes with an empty map", () => {
    expect(mapTotals(bands)).toEqual({ long_usd: 300, short_usd: 50, n_long: 2, n_short: 1 });
    expect(bandRows([])).toEqual([]);
  });
});

describe("words", () => {
  it("formats money", () => {
    expect(money(1_234_000)).toBe("$1.2M");
    expect(money(-350_000)).toBe("-$350k");
    expect(money(2.5e9)).toBe("$2.50B");
    expect(money(90)).toBe("$90");
    expect(money(null)).toBe("–");
    expect(money(999_600)).toBe("$1.0M");
    expect(money(999.7)).toBe("$1k");
  });
  it("gives every net figure its sample size", () => {
    const w = netWords(net({ net_usd: 2e6, n_long: 3, n_short: 1, n_flat: 2, n_read: 6 }));
    expect(w).toBe("$2.0M net long (3 long, 1 short, 2 flat, of 6 read)");
    expect(netWords(net({ net_usd: -5e5, n_short: 1, n_read: 1 }))).toContain("net short");
    expect(netWords(net({ n_flat: 4, n_read: 4 }))).toContain("Nobody holds it");
    expect(netWords(net({}))).toBe("No skilled account read yet");
  });
  it("describes holding time", () => {
    expect(holdWords(0.5)).toBe("30 min");
    expect(holdWords(12)).toBe("12.0 h");
    expect(holdWords(72)).toBe("3.0 days");
    expect(holdWords(null)).toBe("–");
  });
  it("names the copy test's state; only a pass reads as good", () => {
    expect(copyBadge("passed").tone).toBe("good");
    for (const s of ["failed", "pending_forward", "not_run"] as const) expect(copyBadge(s).tone).not.toBe("good");
  });
});
