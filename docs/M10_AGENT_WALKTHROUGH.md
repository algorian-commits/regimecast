# M10 (part 2) Paper-Trading Risk Agent: walkthrough

Presenter: Saeed Ahmad Jan. Plain English. Numbers from `data/reports/phase9_*.csv` (`python scripts/run_phase9.py`);
page: `data/reports/phase9.html`. In the app: the **Agent** page (`/agent`).

## The question

**Do RegimeCast's signals help someone who holds BTC, ETH and SOL, after fees and funding?**

The agent is a **risk manager, not a trading bot**. It holds a long position in each perpetual, a third of the account
each, and every hour decides how much to hold: from 0x to 1x, never short, never more than 1x. It only uses what
RegimeCast knew at that hour.

## How the backtest works

* Hourly steps from January 2022 to September 2025 (about 32,900 hours), BTC, ETH and SOL perpetuals.
* Positions are marked at the **mark price** (what Binance uses for liquidations).
* Every change of position pays Binance's **taker fee, 0.05%** of the amount traded.
* **Funding** is paid or received at Binance's archived rate at each settlement (every 8 hours; every 2 hours for SOL
  during the FTX week, as Binance did). A long pays when the rate is positive and receives when it is negative.
* A **no-trade band** skips small changes, so the agent does not pay fees to move from 51% to 53%.
* **No look-ahead:** the decision for the hour after H uses only forecasts stamped at or before H. They are the
  walk-forward, out-of-sample forecasts from Phases 4 to 6, so no signal was ever fitted on the hours it trades
  (tested on every push: scrambling everything after H changes nothing up to H).

## The rule

For each coin, every hour:

1. **Volatility target:** hold `target volatility / forecast volatility`, at most 100%. The forecast is the 24-hour
   HAR-S + LightGBM combination from M7. When the coin is expected to swing twice as hard, hold half as much.
2. **Stress cut:** halve the position, or leave the market, when the next-hour stress probability (M10 part 1) is high.
3. **Regime cut:** hold less when the regime (M4) says "stressed".

## Choosing the settings, honestly

* 225 combinations (target volatility 30-80%, five stress cut-off pairs, three regime cuts, three band widths) were run
  on **January 2022 to September 2024** only. Every one is logged with its result (`phase9_grid.csv`).
* The choice rule was written down before running: among the settings whose maximum drawdown is at least 25% smaller
  than buy-and-hold's (the final evaluation's bar), take the highest Sharpe ratio.
* The chosen settings were then run once on **October 2024 to September 2025** (the check) and frozen in
  `regimecast/agent/frozen.json` on 27 Oct 2026. The final test period (from October 2025) was not touched.
* The volatility-targeting baseline got the same treatment on its own small grid, so both are tuned equally.

**Frozen settings:** target volatility 30% a year, no stress cut, no regime cut, no-trade band 20 points.

## Results (development period)

| | Agent | Buy-and-hold | Volatility targeting (trailing week) |
|---|---|---|---|
| **Whole period, Jan 2022 - Sep 2025** | | | |
| Return | **+61%** | +38% | +71% |
| Sharpe ratio | **0.62** | 0.46 | 0.65 |
| Maximum drawdown | **-38%** | -79% | -42% |
| Calmar ratio | 0.36 | 0.11 | 0.36 |
| Fees paid | 2.4% | 0.1% | 0.6% |
| Funding paid | 10.2% | 23.2% | 10.6% |
| Average exposure | 48% | 100%+ | 49% |
| **Grid period, Jan 2022 - Sep 2024** | | | |
| Sharpe / max drawdown | 0.38 / -38% | 0.26 / -79% | 0.42 / -42% |
| **Check period, Oct 2024 - Sep 2025** | | | |
| Return | +34% | +50% | +39% |
| Sharpe / max drawdown | 1.23 / -26% | 0.96 / -53% | 1.30 / -27% |

Sharpe difference, agent minus buy-and-hold, with a 95% block-bootstrap interval (one-week blocks, 2,000 resamples):
**+0.15 (-0.19 to +0.49)** over the whole period. Agent minus volatility targeting: -0.04 (-0.23 to +0.15).

### In the crashes

| | Agent | Buy-and-hold | Volatility targeting |
|---|---|---|---|
| FTX collapse (6-12 Nov 2022): return / worst drawdown | **-12% / -16%** | -27% / -39% | -12% / -17% |
| Yen-carry unwind (3-8 Aug 2024) | **-6% / -10%** | -13% / -27% | -7% / -12% |

## What this means

* **It does what a risk manager should.** Against simply holding, the agent halves the worst fall (-38% vs -79%),
  loses less than half as much in both crashes, and has a higher Sharpe ratio in the grid, check and whole periods.
  On the development period it would pass the final evaluation's bar (higher Sharpe **and** a drawdown at least
  25% smaller). In a rising year (the check) it makes less money than holding, by design: it holds about half.
* **The honest limit: the gain comes from volatility targeting itself, not from RegimeCast's extra signals.**
  * The plain baseline that uses last week's volatility does as well (Sharpe 0.65 vs 0.62); the bootstrap cannot tell
    them apart. The M7 forecast is better at predicting volatility, but for sizing a position once the band skips
    small changes, last week's volatility is about as good.
  * The grid **switched off** the stress and regime cuts. They do lower the drawdown a little (-33% instead of -38%
    with a 50% stress exit), but the stress probability jumps up and down hour by hour, so the agent trades in and out
    and fees eat the benefit (turnover 43-186 times the account a year instead of 12; Sharpe 0.36 or lower).
* The Sharpe difference against buy-and-hold is not statistically significant over 3.75 years: the interval includes 0.
* The chosen settings are at the edge of the grid (the lowest target volatility, the widest band). A lower target or
  a wider band might score higher; the grid was fixed before running, so it was not extended.
* With HAR-S instead of the combination (what the live app has), the result is about the same: Sharpe 0.60,
  drawdown -38%.

## Final evaluation

`regimecast/agent/final.py` runs the frozen settings and both baselines on October 2025 to September 2026 with
`final=True`. It has **not** been run; Phase 10 runs it once. The bar was fixed before: a higher Sharpe ratio than
buy-and-hold **and** a maximum drawdown at least 25% smaller.

## Live paper log

The frozen agent also runs **every hour on live data**: a GitHub Actions job (`.github/workflows/agent_live.yml`,
12 minutes past each hour) reads RegimeCast's live API on Railway (volatility, stress, regime, mark price, funding),
decides, and pushes the decision to the branch `agent-data` straight away, before the hour plays out. The branch
keeps its whole history, so every decision's commit time proves it was written in advance. A buy-and-hold account
runs alongside from the same first hour.

The Agent page shows the paper account vs buy-and-hold, the latest decision for each coin with its reasons
("Forecast volatility 52% a year vs target 30%: hold 57%"), and the backtest above. Paper trading only; no real
orders are ever placed.

Live differences from the backtest: the live app's volatility forecast is HAR-S (the combination needs live inputs
the app does not have yet; the backtest shows the difference is small), paper fills are at the mark price when the
job runs (GitHub may start it some minutes late), and funding at a settlement uses the last rate seen before it.
