# Data health: BTCUSDT, ETHUSDT, SOLUSDT

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


## ETH and SOL (added 26 Oct 2026, Phase 4)

Downloaded, repaired and validated on Modal (`regimecast/jobs/modal_app.py::archive_symbol`, EU region), the
same pipeline as BTC. Reports: `data/reports/data_health_ETHUSDT.json`, `data_health_SOLUSDT.json` (served at
`/data-health/<SYMBOL>`). The BTC report was refreshed by the same run (data to 2026-10-24).

| Data | ETHUSDT | SOLUSDT |
|---|---|---|
| 1-min candles (traded price) | 2020-01-01 to 2026-10-24, 3,584,160 rows, no gaps | 2020-09-14 (listing) to 2026-10-24, 3,213,660 rows, no gaps |
| 1-min mark price | 8 gaps, 66 minutes missing (max 30 min) | 3 gaps, 32 minutes missing (max 25 min) |
| 1-min index / premium | 61 / 198 minutes missing | 27 / 168 minutes missing |
| Funding payments | 7,395 (from 2020-01-01) | 6,700 (from 2020-09-13), max rate 2% (early listing days) |
| 5-min open interest | from 2021-12-01 (Binance's archive starts there), 140 steps missing | from 2021-12-01, 157 steps missing |
| Order book (Bybit, replayed) | 2025-05-01 to 2026-10-25, 1-second and 1-minute | same |

Findings, beyond those already listed for BTC (which apply to all three coins: daily-file repair, halts,
open-interest relabelling, short gaps kept as gaps):

| # | Finding | Size | What we did |
|---|---|---|---|
| 12 | Repairs from daily files | ETH and SOL: 5-14 days per dataset (e.g. SOL premium 2021-07-27, 2022-07-12/13) | **Fixed** by `--repair`, as for BTC. One SOL index day is missing at Binance too. |
| 13 | Mark price far from the traded price | ETH 66 minutes (max 17.2%), SOL 321 minutes (max 8.9%); worst minutes 19 May 2021 13:09-13:40 and 12 Mar 2020 | **Real market events, kept.** The traded price spiked while the index-based mark price did not; this is why liquidation risk always uses mark price. |
| 14 | Open interest starts 2021-12-01 for ETH and SOL (BTC 2020-09-01) | 15 months fewer than BTC | **Explained.** Binance's archive starts there. Models using open interest train from Dec 2021 for these coins. |
| 15 | SOL starts 2020-09-14 | about 8 months after BTC and ETH | **Explained.** SOLUSDT futures listed then. The COVID crash (Mar 2020) has no SOL data. |
| 16 | `bookDepth` archive downloaded (2023-01 onward, all three coins) | stored on the Modal volume only | **Not used**, for the reasons in finding 9; Bybit's full order book is the order-book source. |

## New-coin exam: XRP, DOGE, BNB, ADA, AVAX (added 3 Nov 2026, Phase 13B)

Downloaded, repaired and validated on Modal with the same steps as Phase 4 (`regimecast/jobs/modal_newcoins.py`).
Reports: `data/reports/data_health_<SYMBOL>.json`. Before the exam was frozen, only these checks looked at the coins.

| Data | XRP | DOGE | BNB | ADA | AVAX |
|---|---|---|---|---|---|
| 1-min candles start (listing) | 2020-01-06 | 2020-07-10 | 2020-02-10 | 2020-01-31 | 2020-09-23 |
| Rows, to 2026-11-01 | 3,586,539 | 3,318,660 | 3,536,159 | 3,550,559 | 3,210,780 |
| Mark price minutes missing | 2,940 | 2,911 | 2,910 | 2,941 | 2,911 |
| Funding payments | 7,472 | 6,914 | 7,367 | 7,432 | 6,691 |
| Open-interest steps missing (from 2021-12-01) | 141 | 140 | 140 | 140 | 199 |

| # | Finding | Size | What we did |
|---|---|---|---|
| 17 | Binance has no files for 28-29 Oct 2026 | about 1,440-2,880 minutes per dataset, every coin | **Outside the exam.** Scoring ends 2026-09-30, and windows that cross a gap stay empty. |
| 18 | Other mark-price gaps | at most 30 minutes (e.g. 2020-12-17, 2022-07-12) | **Kept as gaps**, as for BTC, ETH and SOL. About 0.3% of the 24 h liquidation outcomes are empty. |
| 19 | Mark price far from the traded price | 102-201 minutes per coin, largest 7.8-16.1%, mostly 19 May 2021 and 10 Oct 2025 | **Real market events, kept** (finding 13). Liquidation risk uses mark price. |
| 20 | Each coin's first 6 months | listing-period trading | **Dropped** before any model sees them (the exam's warm-up). |
| 21 | No Bybit order book and no DVOL for these coins | those inputs are empty | **Explained.** The models treat them as missing, as for BTC, ETH and SOL hours before May 2025. |
