# M7 Volatility Forecaster and Liquidation Risk Engine: walkthrough

Presenter: Saeed Ahmad Jan. One page, plain English. Numbers come from `data/reports/phase5_*.csv`
(`python scripts/run_phase5.py`, about 30 minutes); the demo page is `data/reports/phase5.html`.

## What M7 does

Before you open a leveraged trade, M7 answers: **how likely is it that the mark price reaches my liquidation price
in the next hour or day?** It needs two things:

1. **How volatile will the market be?** A volatility forecast for the next 1 h and 24 h.
2. **How far do prices actually travel against a position, given that volatility?** A model of the worst move
   (the deepest point the mark price reaches, not where it closes), turned into a probability for any leverage.

## How we tested it (no cheating)

* Development period only: every model is refitted every 90 days on the past and scored on the next block, from
  January 2022 to September 2025: about **32,700 hourly forecasts per coin and horizon**, and about **613,000
  liquidation predictions** (10 leverages from 2x to 100x, long and short).
* The final test period (from 1 October 2025, including the October crash) stays closed until the final
  evaluation.
* The liquidation models learn from volatility forecasts that were themselves out of sample.

## Result 1: volatility, better than HAR on every coin and horizon (success criterion 1)

QLIKE error versus HAR-RV, the standard benchmark (negative = better):

| | BTC 1 h | BTC 24 h | ETH 1 h | ETH 24 h | SOL 1 h | SOL 24 h |
|---|---|---|---|---|---|---|
| HAR-S (adds time-of-day and day-of-week) | -6.2% | -14.1% | -5.3% | -9.8% | -4.1% | -5.2% |
| LightGBM on the M4 features | -9.5% | -15.7% | -8.5% | -9.1% | -8.9% | +0.3% |
| **HAR-S + LightGBM (combination)** | **-9.5%** | **-18.1%** | **-9.2%** | **-13.8%** | **-8.3%** | **-5.7%** |

* The combination beats HAR in all six cases (Diebold-Mariano p < 0.002) and is in the Model Confidence Set
  every time. It also beats HAR-S significantly in four of six (not for ETH and SOL at 24 h).
* **DVOL** (Deribit's options-implied volatility) helps: without it, LightGBM on BTC 24 h is -13.0% instead of
  -15.7%. Biggest inputs by SHAP: recent realised volatility, day of week, DVOL, the largest 1-minute range.
* Order-book features carry little weight so far: they exist only from May 2025.

## Result 2: liquidation probabilities that mean what they say (success criterion 2)

Calibration error (ECE: the average gap between "we said X%" and "X% happened"; lower is better):

| | BTC 1 h | BTC 24 h | ETH 1 h | ETH 24 h | SOL 1 h | SOL 24 h |
|---|---|---|---|---|---|---|
| Random walk (the app's old model) | 1.12% | 3.87% | 1.55% | 3.74% | 2.22% | 3.84% |
| **FHS (the app's model now)** | **0.08%** | **0.49%** | **0.06%** | **0.54%** | **0.21%** | **0.65%** |

* **FHS** (filtered historical simulation) scales each past worst move by the volatility forecast of its time and
  asks how often moves that large happened. It cuts calibration error 6 to 25 times and the Brier score by 2-5%.
  The 95% interval for BTC 24 h is 0.26-0.67% for FHS against 3.2-3.8% for the random walk.
* The random walk **overstated** risk (BTC 24 h: 23.6% predicted on average vs 20.2% liquidated).
* A quantile LightGBM model of the worst move was tried and is **not** used: worse than FHS at 24 h.

## The honest weak spot: crashes are one-way

In the FTX (Nov 2022) and Yen-carry (Aug 2024) crash windows, both models **understate the risk for longs** and
overstate it for shorts. Example, BTC 10x long, next 24 h, FTX week: FHS said 6%, 31% were liquidated. The models
treat up and down moves alike; crashes are not alike. The **Phase 6 stress model** adds the directional signals
(funding, crowding, order book, Whale Watch) this needs.

## In the app now

* Volatility: HAR-S on live Binance data (its out-of-sample calibration with FHS is as good as the combination's).
* Liquidation probability, safe leverage and stop-hit probability: FHS fitted on 2021-2025 worst moves.
* "What moves the volatility forecast": exact SHAP values of HAR-S, e.g. *volatility in the last week (-16%),
  last day (-15%), day of week (+13%)*.
* Next step for the app: the HAR-S + LightGBM combination needs live funding, open interest, basis and DVOL
  inputs (parking list).
