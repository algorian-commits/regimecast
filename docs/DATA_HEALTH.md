# Data health: BTCUSDT

Checked 20 Oct 2026 with `python -m regimecast.data.validate --symbols BTCUSDT`. The full report is
`data/reports/data_health_BTCUSDT.json`, which the API also serves at `/data-health/BTCUSDT`.
Every finding is fixed in code or explained below.

## What we have

| Data | Source | From | To | Rows | Size |
|---|---|---|---|---|---|
| 1-min candles (traded price) | Binance archive | 2020-01-01 | 2026-10-19 | 3,576,960 | 217 MB |
| 1-min mark price | Binance archive | 2020-01-01 | 2026-10-19 | 3,576,905 | 139 MB |
| 1-min index price | Binance archive | 2020-01-01 | 2026-10-19 | 3,576,900 | 158 MB |
| 1-min premium index | Binance archive | 2020-01-01 | 2026-10-19 | 3,576,759 | 137 MB |
| Funding payments (8-hourly) | Binance archive | 2020-01-01 | 2026-09-30 | 7,395 | 0.7 MB |
| 5-min open interest, long/short ratios | Binance archive | 2020-09-01 | 2026-10-19 | 644,486 | 53 MB |
| 1-sec order-book features | Bybit archive (replayed) | 2025-10-03 | 2025-10-17 | 1,296,000 | 235 MB |

## Findings

| # | Finding | Size | What we did |
|---|---|---|---|
| 1 | **Monthly archive files miss whole days** (mark, index, premium). The daily files have them. | 13,015 mark-price minutes, e.g. 24–27 Jul 2021 | **Fixed.** `binance_archive --repair` fetches the daily file for every day with holes. A monthly download only deletes the daily files of days it covers. |
| 2 | Short gaps that Binance's daily files miss too | mark 55 min (3 gaps, max 30 min), index 60, premium 201 | **Kept as gaps.** Code never fills more than 5 minutes; windows that cross a gap are dropped (`max_adverse_excursion`, hours with < 50 returns). |
| 3 | **Trading halts:** Binance keeps printing candles with a frozen price and zero volume | 13 halts, 369 minutes, longest 99 min (2023-11-10) | **Fixed.** Zero-volume minutes are missing data, not zero volatility (`realized.minute_close`). Before this fix one halt hour dominated the HAR results. |
| 4 | Mark price far from the traded price | 101 minutes, max 5.6 %, mostly 13 Mar 2020 and 19 May 2021 | **Real market events, kept.** These are the COVID crash and the May 2021 crash. Liquidations use mark price, so we always use mark price for liquidation risk. |
| 5 | Open-interest gaps | 156 gaps, 634 five-minute steps; longest 10.5 h (2024-02-16) | **Kept as gaps.** Features use the last known value with its `available_at` time, so a gap never leaks future data. |
| 6 | Open-interest timestamps changed meaning on 2026-06-25 (end of period to start of period) | all rows before that date | **Fixed.** `store.metrics()` relabels every row by period start and adds `available_at` = period end. |
| 7 | Open interest starts 2020-09-01 | 8 months fewer than prices | **Explained.** Binance's archive starts there. Models that use open interest train from Sep 2020. |
| 8 | Funding ends 2026-09-30 | the current month | **Explained.** Funding is archived monthly only; the live app reads current funding from the API. |
| 9 | Archive `bookDepth` is broken in places (frozen bands since 2026-09-03, misaligned Apr–May 2025) | | **Not used.** Order-book features come from Bybit's full order-book files instead. |
| 10 | Bybit's 200 levels reach only ~2–3 bps from the price for BTC | 5th percentile 2.2 bps on 10 Oct 2025 | **Fixed.** Depth is measured within 1 and 2 bps (covered 99.5 % of seconds) and over the best 10 / 50 / 200 levels, instead of wider bands that would be cut off. |
| 11 | Duplicate rows | 0 in every dataset after loading | `store.py` drops exact duplicates; for the same minute in two files, the monthly (final) file wins. |
