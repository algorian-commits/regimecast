// Contract agreed with Phase 3 (M3). GET /news-mood?symbol=BTCUSDT&hours=168.
// Not in docs/openapi.json until Phase 3 merges; then switch to the generated type.
export type MoodHour = {
  hour_end: string; // ISO, UTC. Headlines published in (hour_end - 1h, hour_end]
  count: number;
  mean_score: number; // -1 (negative) .. 1 (positive)
  share_negative: number; // 0..1
  policy_shock_count: number;
};
export type NewsMood = { symbol: string; updated_at: string; hours: MoodHour[] };

export type MoodSummary = {
  headlines: number;
  mean_score: number | null; // headline-weighted, null when there were no headlines
  share_negative: number | null;
  policy_shocks: number;
  label: "negative" | "neutral" | "positive" | "no news";
};

/**
 * Summary of the last `windowHours` hours that have fully ended by `now`. Hours ending after
 * `now` are ignored, so the panel never shows a value from an hour that is not finished.
 */
export function summarize(hours: MoodHour[], now: number, windowHours = 24): MoodSummary {
  const from = now - windowHours * 3600_000;
  const rows = hours.filter((h) => {
    const t = Date.parse(h.hour_end);
    return t <= now && t > from;
  });
  const n = rows.reduce((a, h) => a + h.count, 0);
  const shocks = rows.reduce((a, h) => a + h.policy_shock_count, 0);
  if (n === 0) return { headlines: 0, mean_score: null, share_negative: null, policy_shocks: shocks, label: "no news" };
  const mean = rows.reduce((a, h) => a + h.mean_score * h.count, 0) / n;
  const neg = rows.reduce((a, h) => a + h.share_negative * h.count, 0) / n;
  const label = mean <= -0.15 ? "negative" : mean >= 0.15 ? "positive" : "neutral";
  return { headlines: n, mean_score: mean, share_negative: neg, policy_shocks: shocks, label };
}
