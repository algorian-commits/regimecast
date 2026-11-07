// Track Record page helpers: a missing hour stays a gap, comparisons use the same rows, the signs read correctly.
import { describe, expect, it } from "vitest";
import { forecastPoints, minutesAfterStart, runningError, signedPct, span, volPct, type TrackRow } from "./track";

const row = (origin: string, extra: Partial<TrackRow> = {}) =>
  ({ origin, logged_at: origin, forecast_variance: 1e-4, realised_variance: null, qlike_forecast: null, qlike_benchmark: null, ...extra }) as TrackRow;

describe("forecastPoints", () => {
  it("is empty with no rows", () => expect(forecastPoints([])).toEqual([]));

  it("keeps a missing hour as a gap and sorts oldest first", () => {
    const p = forecastPoints([row("2026-11-05T14:00:00Z"), row("2026-11-05T12:00:00Z", { realised_variance: 4e-4 })]);
    expect(p.map((x) => x.t)).toEqual(["2026-11-05T12:00:00.000Z", "2026-11-05T13:00:00.000Z", "2026-11-05T14:00:00.000Z"]);
    expect(p[1]).toMatchObject({ forecast: null, realised: null, missing: true });
    expect(p[0].forecast).toBeCloseTo(1);
    expect(p[0].realised).toBeCloseTo(2);
    expect(p[2].realised).toBeNull();
  });
});

describe("runningError", () => {
  it("averages only rows with both errors", () => {
    const p = runningError([
      row("2026-11-05T14:00:00Z", { qlike_forecast: 0.3, qlike_benchmark: 0.5 }),
      row("2026-11-05T13:00:00Z", { qlike_forecast: 9 }),
      row("2026-11-05T12:00:00Z", { qlike_forecast: 0.1, qlike_benchmark: 0.3 }),
    ]);
    expect(p).toHaveLength(2);
    expect(p[0]).toMatchObject({ forecast: 0.1, benchmark: 0.3 });
    expect(p[1].forecast).toBeCloseTo(0.2);
    expect(p[1].benchmark).toBeCloseTo(0.4);
  });
});

describe("formats", () => {
  it("volatility from variance", () => {
    expect(volPct(1e-4)).toBeCloseTo(1);
    expect(volPct(null)).toBeNull();
  });
  it("signs", () => {
    expect(signedPct(-12.34)).toBe("-12.3%");
    expect(signedPct(4)).toBe("+4.0%");
    expect(signedPct(null)).toBe("–");
  });
  it("spans and lead times", () => {
    expect(span(30)).toBe("30 h");
    expect(span(72)).toBe("3 days");
    expect(minutesAfterStart({ origin: "2026-11-05T12:00:00Z", logged_at: "2026-11-05T12:11:20Z" })).toBe(11);
  });
});
