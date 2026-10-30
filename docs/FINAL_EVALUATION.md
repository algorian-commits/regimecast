# Final evaluation: results on the sealed year

Run once on 30-31 Oct 2026 from the commit tagged `final-eval` (c0a7b3f), with `scripts/final_eval.py`. The pass bars
were written in `docs/PLAN.md` on 27 Oct 2026, before any model saw this data, and were not changed afterwards.

**Final test period:** forecasts made from 2025-10-01 00:00 to 2026-09-30 23:00 UTC: 8,760 hours per coin, BTC, ETH
and SOL. It includes the 10 October 2025 crash. Every model kept the same code, settings and refit schedule as in
development: it refits every 90 days, on past data only.

**Check that nothing changed:** the run also redid the development years. It matched what Phases 4-6 had saved
(largest difference: 0.0000000000001), so the same code produced these results.

## Verdict

| Part | Result | In one line |
|---|---|---|
| Volatility forecast | **PASS** | Beat the standard benchmark on all 6 coin x horizon cases, by 6-27%. |
| Liquidation probability | **PASS** | Its "X% chance" came true about X% of the time; 4-10x more accurate than the random walk. |
| Stress warning | **PASS** | AUC 0.82-0.86, against 0.69 for the best simple rule. |
| Regimes | **PASS** | "Stressed" hours really were followed by the wildest days, on all 3 coins. |
| Paper agent | **PASS** | In a falling year it lost 13% where holding lost 36%; worst drop -39% against -65%. |
| Trade verdict (Phase 12) | **FAIL** | The Trade Grade itself passed strongly; the crowd warning and the "similar moments" up/down odds did not. |
| App | speed **PASS**; click-through **pending** | 50 of 50 live risk checks answered in under 2 s; Saeed's one-time click-through is still to do. |
| Trade parser (Claude) | **not run** | Waits for the Anthropic API key. |

By the verdict Saeed set ("good" if the model parts, the paper agent, the trade verdict and the app all pass), the
project is **good in its core, with one honest failure**. The forecasting models and the agent passed on a year they
had never seen, including a real crash. Two extra signals in the trade verdict did not hold up.

## What the numbers mean

**QLIKE** is the error of a volatility forecast. Lower is better. It punishes forecasting calm before a storm more
than the opposite, which is the mistake that hurts traders. We report it as "% lower than HAR". HAR is the standard
textbook model every volatility paper compares against.

**Diebold-Mariano p-value** asks whether one forecast is really better or just lucky. Below 0.05 means there is
less than a 5% chance the win is luck.

**ECE (calibration error)** applies to probabilities. When the model says 10%, did it happen 10% of the time? ECE is
the average gap between "said" and "happened". 0% is perfect.

**Brier score** is the average squared mistake over all yes/no predictions. Lower is better. It rewards being both
honest and confident.

**AUC** asks: take one hour that turned stressed and one that didn't. How often does the model give the stressed
hour the higher number? 0.5 is a coin flip and 1.0 is perfect. Above 0.8 is good for a rare event.

**Average precision** asks: when the alarm rings, how often is something really happening, across all alarm levels?
Random guessing scores the base rate, about 0.04 here.

**Sharpe ratio** is return divided by how bumpy the ride was. Higher is better. It is negative when the strategy
lost money.

**Maximum drawdown** is the worst fall from a peak to the bottom that followed. Smaller is better.

**Bootstrap interval** is the range a number could plausibly have, found by re-running the year in shuffled weekly
chunks 2,000 times. If it includes 0, a difference may be luck.

## The details

### 1. Volatility forecast (M7): PASS

HAR-S + LightGBM combination against HAR-RV. Negative = lower error = better. Bar: lower in at least 5 of 6 cases,
and significant in at least 3.

| | BTC 1 h | BTC 24 h | ETH 1 h | ETH 24 h | SOL 1 h | SOL 24 h |
|---|---|---|---|---|---|---|
| Final year | **-15.2%** | **-27.0%** | **-13.4%** | **-20.3%** | **-12.1%** | **-5.8%** |
| Diebold-Mariano p | < 0.001 | < 0.001 | < 0.001 | < 0.001 | < 0.001 | 0.056 |
| Development, for comparison | -9.5% | -18.1% | -9.2% | -13.8% | -8.3% | -5.7% |

Lower than HAR in **6 of 6** and significant in **5 of 6** (SOL 24 h missed significance narrowly, p = 0.056). The
gain on the unseen year is **larger** than in development. LightGBM alone did even better than the combination on 5
of 6 cases (for example BTC 24 h -30.7%), but the bar was set on the combination, so that is the result reported.

### 2. Liquidation probability (M7): PASS

FHS on the HAR-S forecast (the app's model) against the random walk (the app's old model). Pooled over long and
short and 10 leverages from 2x to 100x: 175,200 predictions per coin and horizon. Bar: lower ECE in 6 of 6, lower
Brier in at least 5, and ECE at most 0.5% at 1 h and 1.5% at 24 h in at least 5.

| ECE (lower is better) | BTC 1 h | BTC 24 h | ETH 1 h | ETH 24 h | SOL 1 h | SOL 24 h |
|---|---|---|---|---|---|---|
| **FHS (the app)** | **0.14%** | **0.45%** | **0.09%** | **0.19%** | **0.24%** | **0.80%** |
| Random walk | 0.87% | 3.15% | 1.39% | 3.40% | 1.91% | 3.82% |
| Limit | 0.5% | 1.5% | 0.5% | 1.5% | 0.5% | 1.5% |

ECE was lower in **6 of 6**, Brier lower in **6 of 6**, and within the limit in **6 of 6**. Example: across all BTC
positions over 24 h, FHS said 18.9% on average and 19.1% happened; the random walk said 21.9%.

**The known weak spot showed up again in the crash** (10-11 Oct 2025, no bar, reported as promised):

| BTC, next 24 h, 10-11 Oct 2025 | Long: FHS said | Long: happened | Short: FHS said | Short: happened |
|---|---|---|---|---|
| 10x | 1.3% | 46% | 1.5% | 0% |
| 20x | 9.4% | 46% | 9.7% | 2% |
| 50x | 43% | 85% | 44% | 21% |

ETH and SOL look the same: in a crash, long positions are liquidated far more often than the model says, and shorts
far less. Over the whole year the errors cancel out, which is why calibration passes. On the crash day itself, a
long trader would have been badly under-warned by the liquidation number alone. Phase 6b already tried and failed
to fix this; the stress gauge (next section) is the part that warns about such days.

### 3. Stress warning (M10 part 1): PASS

Bar: AUC at least 0.75 and above the best simple rule on every coin, and average precision above the best rule on
at least 2 of 3.

| | BTC | ETH | SOL |
|---|---|---|---|
| **AUC, classifier** | **0.86** | **0.82** | **0.85** |
| AUC, best simple rule | 0.69 | 0.69 | 0.69 |
| **Average precision, classifier** | **0.35** | **0.29** | **0.29** |
| Average precision, best rule | 0.20 | 0.16 | 0.17 |
| Stress hours (base rate) | 4.5% | 4.0% | 4.1% |

Passed on **all 3 coins** for both measures, and slightly better than in development (AUC 0.80-0.83). As an alarm:
- **"High" (30% or more):** right 39-47% of the time, with about 0.4 false alarms a day.
- **50% or more:** right 59-68% of the time, with about one false alarm every 9-17 days.

### 4. Regimes (M4): PASS

Next-day volatility (annualised) by the regime known at the time. The bar is that it rises from calm to stressed on
every coin.

| | Calm | Active | Stressed |
|---|---|---|---|
| BTC | 37% | 48% | 70% |
| ETH | 47% | 67% | 93% |
| SOL | 64% | 93% | 127% |

### 5. Paper agent (M10 part 2): PASS

This was a falling year for crypto. Equal-weight BTC/ETH/SOL perpetuals, after 0.05% fees on every change and real
funding. Bar: higher Sharpe **and** a maximum drawdown at least 25% smaller than buy-and-hold.

| | Agent | Buy-and-hold | Plain volatility targeting |
|---|---|---|---|
| Return over the year | **-13.2%** | -36.2% | -14.5% |
| Sharpe ratio | **-0.38** | -0.57 | -0.42 |
| Maximum drawdown | **-38.7%** | -65.3% | -43.0% |
| 10-11 Oct 2025 crash | **-6.3%** | -14.2% | -7.5% |
| Average exposure | 58% | 100% | 57% |
| Fees + funding paid | 2.4% | 1.7% | 1.5% |

Passed: Sharpe higher, and drawdown **41% smaller** (needed 25%). Read it honestly:
- **It still lost money.** It cut the loss by almost two thirds, but it can't make money when everything falls,
  because it never goes short.
- **The bootstrap interval for the Sharpe difference is -0.24 to +0.60.** It includes 0, so over a single year the
  advantage is likely (about 79%) but not proven.
- **Plain volatility targeting did nearly as well**, as in development. The value is in "hold less when the market
  is wild", not in the extra signals.

### 6. Trade verdict (Phase 12): FAIL (the Grade passed; two extras did not)

The bar needs all three parts.

**Trade Grade: PASS, strongly.** These are 262,800 example trades. The bar is the share knocked out (liquidated or
stopped) within 24 h:

| | A | B | C | D | F | F vs A |
|---|---|---|---|---|---|---|
| BTC | 2.3% | 5.0% | 12.3% | 19.9% | 39.9% | 17x |
| ETH | 2.0% | 5.3% | 13.0% | 20.3% | 41.1% | 20x |
| SOL | 2.1% | 4.5% | 11.6% | 19.4% | 42.6% | 21x |

Every coin went in strict order from A to F, with F at least 17 times riskier than A (the bar was 3 times). **The
grade a beginner sees means what it says.**

**Crowd warning: FAIL.** The bar: a sudden 5% move against the crowd at least 1.5 times as often after a warning,
with the interval above 1, on 2 of 3 coins.
- BTC: 0.95 times. No effect at all.
- ETH: 1.32 times.
- SOL: 1.46 times.

The direction was right for ETH and SOL but weaker than required, and no interval stayed above 1. With about 160-260
warning hours a coin, the warning is not reliable.

**Similar moments: FAIL, by a hair.**
- **Range, passed on every coin:** "the next 24 h move will be inside this range" came true 79-83% of the time,
  against the 80% it promised.
- **Up chance, failed:** it was slightly worse than always saying the long-run up share on BTC and SOL (Brier 0.2503
  against 0.2501 and 0.2504 against 0.2500). It passed on ETH.

In plain words, the up/down chance is a coin flip, as Phase 6b already found.

**What it means:** the core of Phase 12 works. The grade, the "fix my trade" view (built on the passing liquidation
model) and the move ranges hold up. The crowd warning and the up/down odds don't carry reliable information, so the
app should stop presenting them as evidence. That is a product change after the test. These results stay as they
are.

### 7. App: speed passes; the click-through is still to do

50 live `/risk-check` requests from Saeed's PC (Pakistan) to Railway (EU), through the website, on 30 Oct 2026:
- **All 50 answered** in under 2 seconds.
- **Typical:** 0.36 s; 95% within 0.63 s; slowest 1.37 s.

The one-time check on the live URL is Saeed's: sign up, set balance and limit, type a trade, confirm, see it saved,
see the dashboard update, log out. It is still to do, because Claude does not create accounts on the live site.

### 8. Trade parser: not run

The 95% field-accuracy bar for the Claude parser is still to do. It needs the Anthropic API key
(`scripts/set_anthropic_key.ps1`, then `python scripts/m8_parser_eval.py`). The rule reader, the fallback the live
site uses now, scored 76% all-fields-right in development.

## Files

`data/reports/` holds the results:
- `final_summary.json`: every bar.
- `final_vol.csv`, `final_liq.csv`, `final_liq_crash.csv`, `final_stress.csv`, `final_regimes.csv`.
- `final_agent_*.csv`, `final_verdict_*.csv`.
- `final_reproducibility.csv`, `final_app_latency.json`.
- `final_log.json`: every run with its commit and time.

The per-hour predictions are in the `final_*.parquet` files, which are kept on the PC only.
