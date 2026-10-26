# M4 Features, Evaluation Harness and HMM Regimes: walkthrough

Presenter: Saeed Ahmad Jan. One page, plain English. Numbers come from `data/reports/` (rebuilt by
`python scripts/run_regimes.py`); the demo page is `data/reports/phase4.html`.

## What M4 does

M4 is the foundation every model after it stands on. It answers three questions:

1. **What does the market look like at any moment since 2020?** One table per coin with a row every minute and
   every hour: returns, realised volatility, volume, buyer share, funding, open interest, long/short ratios, the
   basis, and (from May 2025) the order book: spread, depth, imbalance, order-flow imbalance.
2. **How do we test a model without cheating?** One walk-forward harness that every model must use.
3. **Is the market calm, active or stressed right now?** A hidden Markov model that labels every hour.

## The data (all three coins)

| | BTC | ETH | SOL |
|---|---|---|---|
| Binance 1-minute history | Jan 2020 to now | Jan 2020 to now | Sep 2020 (listing) to now |
| Hours in the feature table | 59,736 | 59,736 | 53,561 |
| Hours with order-book features (Bybit) | 13,008 (every hour since 1 May 2025) | 13,006 | 13,006 |

The order book alone was ~1,600 coin-days and ~400 GB of raw files. They were replayed on **Modal** (cloud
containers in the EU, because Binance and Bybit block US servers), 60 at a time, in under an hour. Only compact
features are kept. A Modal job adds yesterday's data every morning at 06:30 UTC. Data quality for all three
coins is in `docs/DATA_HEALTH.md`.

## No cheating: leakage protection

* **Every row is labelled by when its data was known.** A 1-minute candle opening at 10:59 counts at 11:00; open
  interest counts from the moment Binance published it; hourly rows are labelled by the hour's end.
* **Leakage tests run on every push** (`tests/test_features.py`): we scramble or delete every input after a time
  T and check that not one feature at or before T changes. A second test checks the opposite (scrambling the
  future *does* change later rows), so the test can't pass by accident.
* **The walk-forward harness** (`models/harness.py`) fits on the past only, leaves a gap at least as long as the
  forecast horizon, and refuses to touch the final test period (from 1 Oct 2025, including the October crash)
  unless the final evaluation asks for it explicitly, once.

## Regimes

A 3-state Gaussian hidden Markov model on hourly return and realised volatility (1 hour and 24-hour average).

* Refitted every 90 days on the past only; the hour's regime uses **only hours up to that hour** (forward
  filtering). The textbook "smoothed" probabilities would use the future; we never show those as a signal.
* States are sorted by volatility: calm, active, stressed.
* From 1 Oct 2025 the last model fitted before it is frozen: it labels the October 2025 crash without ever having
  been fitted on it.
* Built in-house (`models/hmm.py`, ~150 lines, tested against known parameters) because the usual library would
  not install on our Python version and we needed the forward-only filter anyway.

### Result 1: the regimes mean something (BTC, 2020 to Sep 2025)

| Regime now | Share of hours | Volatility over the **next** 24 h (annualised) |
|---|---|---|
| calm | 43% | 43% |
| active | 38% | 63% |
| stressed | 19% | **109%** |

When the model says "stressed", the next day is on average 2.5 times as volatile as after a "calm" reading. This
is measured with forward-only regimes and future outcomes, so it is a fair test.

### Result 2: the regime at each dev-period crash

Onset = the first hour at least 10% below the trailing 24-hour high, around each event date.

| Crash | Onset (UTC) | Stressed at onset? | Stressed for how long before | Regime 24 h before |
|---|---|---|---|---|
| COVID crash | 9 Mar 2020 05:00 | yes | 6 h | calm |
| China mining ban | 16 May 2021 21:00 | yes | 29 h | stressed |
| FTX collapse | 8 Nov 2022 19:00 | yes | 3 h | calm |
| Yen carry unwind | 5 Aug 2024 02:00 | yes | 2 h | active |

**Honest reading:** the regime was "stressed" at every crash, 2 to 29 hours before the 10% mark. But a day
before, it still read "calm" for COVID and FTX. The regime follows volatility as a sell-off builds; it is a
good description of *now*, not a crash forecast. Early warning is the job of the stress classifier (Phase 6),
which adds order-book and positioning signals. For COVID the onset is the 9 March leg, which falls inside the
window around 12 March.

### Result 3: adding the order book (May to Sep 2025 only)

With only five months of order-book history to fit on, the order-book version separates next-day volatility much
less (28% / 31% / 34% annualised). It seems to sort liquidity states rather than volatility. We keep the
volatility-based regimes for the app and test the order-book signals where they belong, in the Phase 6 stress
model.

## What's next

* Phase 5 uses these features and this harness to build the volatility forecaster and the calibrated liquidation
  probability, compared against the registered baselines.
* Phase 6 builds the stress early-warning model and the 10 October forensic timeline.
