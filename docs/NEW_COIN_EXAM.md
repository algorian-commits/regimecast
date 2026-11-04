# New-coin exam: results on five coins RegimeCast had never seen

Run once on 3-4 Nov 2026 from the commit tagged `new-coin-exam` (809246a), with `scripts/new_coin_exam.py`. The pass bars
were written in `docs/PLAN.md` on 31 Oct 2026, before any of this data was downloaded, and were not changed. There was
one run for each coin and one scoring run, with no reruns (`data/reports/new_coin_log.json`).

**Why five new coins:** the sealed year was used up by the final evaluation, so a new claim needs fresh data. XRP, DOGE,
BNB, ADA and AVAX had never been touched by any model. Before the freeze, only data health checks looked at them.

**What was scored:** forecasts made every hour through 2026-09-30, with each model refitted every 90 days on that coin's
own past only. Each coin's first 6 months of data were dropped. XRP, BNB and ADA are scored from 2022-01-01, DOGE from
2022-02-01 and AVAX from 2022-04-01. The stress bar starts on 2022-06-30 for every coin, because the scorer inherited
from the final evaluation also needs the calibrated probability, which takes about 6 months to start.

**5 coins x 2 horizons (1 h and 24 h) = 10 cases.** The period includes the FTX and Yen-carry crashes and 10 Oct 2025.

## Verdict: every bar passed

| Part | Result | Needed | Got | In one line |
|---|---|---|---|---|
| Volatility forecast | **PASS** (just) | lower error in 8 of 10, significant in 5 of 10 | 8 and 6 | Clearly better at 1 h on all 5 coins; at 24 h it is much weaker. |
| Liquidation probability | **PASS** | ECE lower in 9, Brier lower in 8, ECE within limit in 8 | 10, 10, 10 | Its "X% chance" came true about X% of the time on coins it had never seen. |
| Stress warning | **PASS** | AUC at least 0.75 and above the best rule on 4 of 5; average precision above it on 4 of 5 | 5 and 5 | AUC 0.82-0.83, against 0.68-0.71 for the best simple rule. |
| Regimes | **PASS** | correct order on 4 of 5 coins | 5 | "Stressed" hours really were followed by the wildest days. |
| Crash mode | **PASS** | stressed-hours gap smaller on 4 of 5; ECE at most 0.25 points worse on all 5 | 5 and 5 | Its stressed-hours gap is much smaller, **but it does not fix the crash day itself**. |

**The main result:** the models were not just memorising BTC, ETH and SOL. They work on coins they never saw.

**Two honest limits:**
1. The 24 h volatility forecast barely passed (see section 1).
2. Crash mode passes its bar, but a 10x long on a day like 10 Oct 2025 is still badly under-warned (see section 5).

## What the numbers mean

**QLIKE** is the error of a volatility forecast. Lower is better. It punishes forecasting calm before a storm more than
the opposite. We report it as "% lower than HAR-RV", the standard textbook model.

**Diebold-Mariano p-value** asks whether one forecast is really better or just lucky. Below 0.05 counts as significant.

**ECE (calibration error)** applies to probabilities. When the model says 10%, did it happen 10% of the time? ECE is the
average gap between "said" and "happened". 0% is perfect.

**Brier score** is the average squared mistake over all yes/no predictions. Lower is better. It rewards being both honest
and confident.

**Calibration gap** (crash mode's measure) is the average chance the model gave minus how often it really happened,
ignoring the sign. "Said 34.5%, happened 36.5%" is a gap of 2.0 points.

**AUC** asks: take one hour that turned stressed and one that didn't. How often does the model give the stressed hour the
higher number? 0.5 is a coin flip and 1.0 is perfect.

**Average precision** asks: when the alarm rings, how often is something really happening, across all alarm levels?
Random guessing scores the base rate, about 0.04 here.

## The details

### 1. Volatility forecast: PASS, only just

HAR-S + LightGBM combination against HAR-RV. Negative = lower error = better. **Bold** = significant.

| | XRP | DOGE | BNB | ADA | AVAX |
|---|---|---|---|---|---|
| 1 h, error vs HAR-RV | **-7.0%** | **-7.2%** | **-8.1%** | **-9.7%** | **-5.0%** |
| 1 h, p | < 0.001 | < 0.001 | < 0.001 | < 0.001 | < 0.001 |
| 24 h, error vs HAR-RV | -6.9% | +0.2% (worse) | **-8.8%** | -2.7% | +4.2% (worse) |
| 24 h, p | 0.18 | 0.97 | 0.004 | 0.055 | 0.38 |

Lower in **8 of 10** (exactly the minimum) and significant in **6 of 10**.
- **At 1 h** it is better on every coin, and the win is real.
- **At 24 h** it is proven on BNB only. It is slightly worse than the benchmark on DOGE and AVAX, though not significantly.
- **Compared with the three original coins:** in the final evaluation the same combination was 20-27% better at 24 h on
  BTC and ETH, and 6% better on SOL. So the large 24 h edge does not carry over to every coin. The new coins have no
  options-implied volatility (DVOL), which may be part of the reason; this exam does not test that.

The app only serves BTC, ETH and SOL, where the combination did pass its 24 h test. If the app ever adds these coins, its
24 h forecast should not be assumed better than HAR-RV.

### 2. Liquidation probability: PASS, all 10 of 10

FHS on the HAR-S forecast (the app's model) against the random walk (the app's old model), pooled over long and short and
10 leverages from 2x to 100x: about 800,000 predictions per case.

| ECE, lower is better | XRP | DOGE | BNB | ADA | AVAX | Limit |
|---|---|---|---|---|---|---|
| **FHS, 1 h** | **0.11%** | **0.17%** | **0.05%** | **0.11%** | **0.06%** | 0.5% |
| Random walk, 1 h | 2.25% | 2.72% | 1.30% | 2.10% | 2.02% | |
| **FHS, 24 h** | **0.58%** | **0.90%** | **0.38%** | **0.38%** | **0.33%** | 1.5% |
| Random walk, 24 h | 5.62% | 5.25% | 3.56% | 4.45% | 4.16% | |

Brier was also lower in 10 of 10. Example: across all DOGE positions over 24 h, FHS said 31.6% on average and 32.4% happened;
the random walk said 37.4%.

**Reported alongside:** ADA, BNB, AVAX and DOGE allow at most 75x, but the pre-registered grid goes to 100x. Leaving out
leverages above each coin's real limit changes almost nothing (24 h FHS ECE 0.34-0.98%, random walk 3.4-5.6%).

### 3. Stress warning: PASS, 5 of 5

| | XRP | DOGE | BNB | ADA | AVAX |
|---|---|---|---|---|---|
| **AUC, classifier** | **0.82** | **0.83** | **0.83** | **0.83** | **0.82** |
| AUC, best simple rule | 0.71 | 0.71 | 0.70 | 0.68 | 0.69 |
| **Average precision, classifier** | **0.34** | **0.34** | **0.34** | **0.33** | **0.29** |
| Average precision, best rule | 0.18 | 0.19 | 0.18 | 0.17 | 0.17 |
| Stress hours (base rate) | 3.9% | 4.0% | 3.9% | 4.0% | 4.0% |

As an alarm:
- **"High" (30% or more):** right 39-49% of the time, with about 0.3-0.5 false alarms a day.
- **50% or more:** right 54-63% of the time, with one false alarm about every 6-11 days.

This is about as good as on BTC, ETH and SOL.

### 4. Regimes: PASS, 5 of 5

Next-day volatility (annualised) by the regime known at the time. The bar is that it rises from calm to stressed.

| | Calm | Active | Stressed |
|---|---|---|---|
| XRP | 67% | 88% | 144% |
| DOGE | 71% | 111% | 168% |
| BNB | 47% | 68% | 108% |
| ADA | 80% | 93% | 140% |
| AVAX | 86% | 103% | 165% |

### 5. Crash mode: PASS, but it does not fix the crash day

**What it is:** when the stress gauge is at 10% or more, a long position's chance of liquidation comes from FHS fitted
only on past high-stress hours. Shorts never change. It was frozen on BTC, ETH and SOL before this exam, by a rule written
before the grid ran (8 settings tried; chosen: past-stress cut 10%, replace, no floor).

**The bar, long side over 24 h, in hours with stress at 10% or more** (about 7% of all hours):

| | XRP | DOGE | BNB | ADA | AVAX |
|---|---|---|---|---|---|
| Stressed hours | 2,934 | 2,976 | 3,058 | 3,004 | 2,823 |
| Really liquidated | 36.5% | 39.9% | 30.2% | 37.3% | 38.2% |
| Plain FHS said | 34.5% | 37.5% | 28.2% | 36.2% | 37.1% |
| **Crash mode said** | **36.9%** | **39.3%** | **30.2%** | **37.8%** | **38.9%** |
| Gap, plain FHS (points) | 2.08 | 2.43 | 2.04 | 1.06 | 1.09 |
| **Gap, crash mode (points)** | **0.38** | **0.62** | **0.06** | **0.54** | **0.74** |

**Over all hours, ECE at 24 h** (the cost of crash mode; limit: at most 0.25 points worse):

| | XRP | DOGE | BNB | ADA | AVAX |
|---|---|---|---|---|---|
| Plain FHS | 0.58% | 0.90% | 0.38% | 0.38% | 0.33% |
| Crash mode | 0.66% | 0.83% | 0.45% | 0.42% | 0.33% |

The average gap fell from 1.74 to 0.47 points, at almost no cost, and the Brier score barely moved. At 1 h (no bar), the
gap shrank on 4 of 5 coins and grew slightly on ADA (0.04 to 0.24 points).

**The crash day, read carefully** (10-11 Oct 2025, no bar, long, next 24 h):

| | XRP | DOGE | BNB | ADA | AVAX |
|---|---|---|---|---|---|
| 10x: really liquidated | 46% | 48% | 46% | 48% | 54% |
| 10x: plain FHS said | 26% | 35% | 19% | 38% | 39% |
| 10x: crash mode said | 27% | 36% | 20% | 38% | 40% |
| 20x: really liquidated | 77% | 92% | 54% | 85% | 85% |
| 20x: plain FHS said | 44% | 53% | 42% | 52% | 53% |
| 20x: crash mode said | 45% | 53% | 43% | 53% | 54% |

**Crash mode moved these numbers by about 1 point. The under-warning on the crash day remains.** The reason is timing: of
the 22-26 hours whose 10x long was liquidated, the gauge was at 10% or more in only 4-6. The crash came suddenly, so
most of those positions were opened while everything still looked calm, and crash mode was off.

**What it means:**
- Crash mode makes the odds more honest in the stressed periods the gauge can see. It is a small, proven correction.
- It cannot warn about a crash that gives no signal beforehand.
- The app should not claim that crash mode protects against sudden crashes. The "crash day" line in the what-if strip is
  still the honest way to show that risk.

## Check of the exam itself

- **Rehearsal:** the whole path ran first on BTC, ETH and SOL and passed every bar scaled to 3 coins
  (`data/reports/new_coin_rehearsal_*`).
- **Consistency:** crash mode's plain FHS column equals the liquidation run's FHS exactly (difference 0), on the rehearsal
  and on the exam. On development data it also equals the final evaluation's FHS exactly.
- **After the tag:** no bug was found and nothing was changed.

## Files

`data/reports/` holds the results:
- `new_coin_summary.json`: every bar.
- `new_coin_vol.csv`, `new_coin_liq.csv`, `new_coin_stress.csv`, `new_coin_regimes.csv`, `new_coin_crash.csv`.
- `new_coin_liq_crash_day.csv`, `new_coin_crash_mode_crash_day.csv`.
- `new_coin_log.json`: every run with its commit and time.
- `p13b_crash_grid.csv`: every crash-mode setting tried.

The per-hour predictions are in `data/reports/new_coin_exam/` (390 MB), which is kept on the PC only. Data health is in
`docs/DATA_HEALTH.md`.
