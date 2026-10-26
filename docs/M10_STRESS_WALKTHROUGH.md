# M10 (part 1) Stress Early Warning and the 10 October Crash: walkthrough

Presenter: Saeed Ahmad Jan. Plain English. Numbers from `data/reports/phase6_*.csv` (`python scripts/run_phase6.py`);
pages: `data/reports/phase6.html` and `data/reports/oct10_forensics.html`.

## The question

**Will the market hit a stress event in the next hour?** A stress event is defined without any model: 5 or more
minutes in a row where 15-minute volatility is above the 99th percentile of the previous 30 days. It happens in about
4% of hours for BTC, ETH and SOL. Every flag only uses the past: a minute counts as stress once the run has lasted
5 minutes, never earlier in hindsight (tested on every push).

## The model

A LightGBM classifier on what is known at the hour's end: volatility now vs the past week, time since the last
stress, time of day, open-interest change, top traders' and accounts' long/short ratios, taker buy/sell ratio,
funding, DVOL, and the M4 regime probabilities. Walk-forward, refitted every 90 days, scored 2022 to Sep 2025
(about 28,500 hours per coin). Compared with three simple rules: "stress continues", "volatility above its 95th
percentile" and "the regime says stressed".

## Results

| | BTC | ETH | SOL |
|---|---|---|---|
| AUC, best simple rule | 0.68 | 0.66 | 0.71 |
| **AUC, stress classifier** | **0.83** | **0.80** | **0.83** |
| Average precision, best rule | 0.16 | 0.14 | 0.17 |
| **Average precision, classifier** | **0.30** | **0.25** | **0.34** |

* As an alarm at "high" (30% or more): right 36-45% of the time (the base rate is 4%), 0.3-0.4 false alarms a day.
  At 50% or more: right 57-58% of the time.
* Honest limit: at about one alarm a day, it is only slightly better than "stress continues". Its value is the
  ladder of confidence, which a yes/no rule cannot give.
* Before the FTX and Yen-carry crashes the probability was 20%+ for 2-4 hours before the 10% drop on BTC and ETH,
  86-100% at onset. SOL's FTX sell-off (it started a day early) was missed.
* What drives it: volatility vs the past week, time since the last stress, time of day, then open interest change,
  top traders' long/short ratio and volume, the leverage and crowding signals the proposal was built on.
* The order book did not help the hourly model: with five months of history it cannot learn from it. A minute-level
  model is future work.

## 10 October 2025, minute by minute (descriptive, BTC)

| UTC | |
|---|---|
| 20:00 | The model (frozen on 30 Sep 2025, never fitted on October) already says 22% for the next hour: 5x normal |
| 20:49 | A large BTC/ETH short is opened on Hyperliquid (reported by The Block) |
| 20:50 | Trump posts the 100% tariff on China (reported) |
| 20:55 | Bids within 0.02% of the price fall from $1.78M to $0.09M (-95%); our stress flag fires |
| 21:00 | The model's next-hour probability: 60% ("high") |
| 21:10-21:20 | Liquidation cascade: BTC to $104k; open interest falls by about $1.4B |
| 21:30 | The first "100% tariff" headline reaches our news feed |

The order book reacted within five minutes of the post and before the cascade; headlines came 40 minutes later.
For a live warning, market data beats news.

## In the app

The dashboard shows "Stress risk, next hour" (low below 10%, elevated 10-30%, high 30%+) with its top drivers, and the
risk check warns when it is elevated or high. Live inputs come from Binance's public API and go through the **same**
feature code as training; a check showed them identical to the archive hour by hour.
