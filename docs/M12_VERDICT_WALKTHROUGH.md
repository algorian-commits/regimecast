# M9 (extended) Trade Verdict for Beginners: walkthrough

Presenter: Saeed Ahmad Jan. Plain English. Dev-period numbers come from `data/reports/phase12_*.csv`
(`python scripts/run_phase12.py`); the settings it fixes are in `regimecast/verdict/frozen.json`.

## The question

**Is this trade well built, and how do I make it safer?** The main user is a complete beginner who types a trade
("long BTC 25x with $100") and gets one grade, three plain reasons and one suggested fix.

What it does **not** do: say which way the price will go. Phase 6b tried hard (funding, open interest, crowding,
stress, recent returns) and the direction of the next day's move was not predictable from our data. So the verdict
judges only the setup: size, leverage, stop, costs, timing and crowding. Every result screen says so.

## The Trade Grade (A to F)

Built only from what the risk check already computes. Two parts:

| Part | Scored | From |
|---|---|---|
| Knocked out | 0-4 by bands of the chance that the stop or the liquidation price is reached within the horizon | FHS (the app's calibrated model) |
| Liquidation vs your limit | 0 if within it; 1-4 at 2, 5, 10 times it and above | FHS and the user's own risk limit |
| The stop | 1 if there is none (liquidation is the only exit); 4 if it sits past the liquidation price | the trade |
| Costs | 0-3 when fees and funding take 10%, 25%, 50% of a typical move | fees, funding, volatility forecast |
| Market | +1 stressed regime, +1 / +2 stress elevated / high, +1 crowd warning on your side, +1 bad timing (at most +2 in total) | M4, M10, below |

The worst setup part sets the grade (0 = A ... 4 = F), and the market adds up to two steps. The three reasons are
the worst parts first, filled with what is fine when fewer than three are wrong. The knock-out bands, and whether
each market part counts, are chosen on the dev period (below).

**Break-even win rate.** Pure arithmetic: if a win makes G, a loss costs L and every trade pays fees and funding C,
you need to be right (L + C) / (G + L) of the time. Without a take-profit the app uses one as far away as your stop.

## Fix my trade

The closest safer version of the same trade: same coin, side, entry and margin. It tries lower leverage (so a
smaller position) and stops at or beyond one typical move ("outside normal noise"). A version counts if its
liquidation chance is within your limit, its stop triggers well before liquidation, and it cannot lose more than
your original. Among those reaching grade B (or the best any of them reaches), it shows the smallest change, side
by side with yours.

## What-if strip

In dollars, one day from now: a **normal day** and a **bad day** (the 90th and 99th percentile 24 h moves of
2022-2025 in the regime the market is in now), both up and down, and a **crash day like 10 October 2025** (Binance's
mark price fell 13.0% for BTC, 14.8% for ETH and 30.7% for SOL in about 30 minutes). A stop exits first if it sits
before the liquidation price; otherwise the position is liquidated and the margin is gone.

## Crowd warning

When funding, top traders' long/short ratio and open-interest growth are all at extremes on the same side, traders
are piled in, and a sudden move against them is more common. The app shows the historical odds: "after warnings like
this, BTC fell 5% or more within a day X% of the time, vs Y% in other hours". The extremes (percentiles) are fixed per
coin on 2022-01 to 2024-09 and frozen as numbers.

## Similar moments

The past hours that looked most like now: same regime, similar volatility forecast, last day's and week's trend,
funding, top traders' ratio and open-interest growth. Only hours whose next 24 h had **fully played out before now**
count, so nothing from the future (or still running) is used; this is tested on every push. It shows how big the
next day's move usually was (the 80% range) and how often it went up. When that is between 40% and 60%, it says plainly
that the direction is close to a coin flip.

## Bad timing

Warnings when a US rate decision (FOMC), inflation data (CPI) or the jobs report falls inside the trade's horizon,
at weekends and in the four quietest hours of the day, and when stress is rising fast. Event times come from a fixed
calendar in the repo (2020-2027, checked against the Federal Reserve's and BLS's own pages, with the 2025 shutdown
delays).

## How the settings were fixed

Same discipline as the Phase 9 agent: the choice rules were written down before the run.

1. **Choose** on 2022-01 to 2024-09: 48 grade settings, 8 crowd extremes, 24 similar-moments settings.
   * Grade: among settings whose hit share rises from A to F on all three coins, the highest worst-coin F/A ratio.
   * Crowd: the highest second-best coin's ratio, with at least 50 warning hours per coin.
   * Similar moments: a Brier score no worse than the base rate's on every coin, then coverage closest to 80%.
2. **Check** once on 2024-10 to 2025-09, never used for choosing.
3. **Freeze** in `frozen.json` with the date. A later run that would choose differently is refused.

The grade is tested on a fixed grid of example trades: every 6 hours, each coin, long and short, leverage
2/3/5/10/20/50x, stops at 0.5/1/1.5/2/3 typical moves, $1,000 margin, 1% limit. A trade is "hit" if the mark price
reaches its stop or liquidation price within 24 h.

## Results (dev period)

Run `python scripts/run_phase12.py` on the PC with the data. It writes, per coin and period (choose, check, whole dev):

| File | What |
|---|---|
| `phase12_grade.csv`, `phase12_grade_bar.csv` | Hit share by grade on the example trades; does it rise A to F, and F / A |
| `phase12_crowd.csv` | Warning hours, the 5% move after a warning vs other hours, the ratio and its 90% interval |
| `phase12_similar.csv` | How often the 80% range held the actual move; Brier score vs always saying the base rate |
| `phase12_whatif.csv`, `phase12_thin_hours.csv` | The normal and bad day per regime; the quiet hours |
| `phase12_*_grid.csv` | Every setting tried, with its result |

Numbers go here after that run. Until it is done the app runs on the written defaults and says so; the crowd
warning and similar moments switch on with the frozen file.

## Example: a beginner's 25x BTC long

$100 margin, no stop, 24 h, at a 60% yearly volatility (an illustration: on the live site the forecast is live).

| | Your trade | Safer version |
|---|---|---|
| Grade | **F** | **B** |
| Leverage, position | 25x, $2,500 | 12x, $1,200 |
| Stop | none | 1.25 typical moves below entry |
| Chance of liquidation, 24 h | 24% (24 times the 1% limit) | 0.8% |
| Most it can lose | $100 | $47 |
| Crash day like 10 Oct 2025 | liquidated, -$100 | stop closes it (crash stops can fill worse) |

## Honest limits

* The grade measures how likely a setup is to be knocked out, not whether it will make money.
* In the dev tables the liquidation model is the app's (fitted on the whole dev period), so they are partly
  in-sample; the final test period is the clean test.
* Stops can fill far past their price in a crash; the what-if strip says so.
* The crash-day sizes are fixed numbers from Binance's public mark prices, a scenario only: nothing is fitted or
  scored on the final test period.

## Final evaluation (fixed 27 Oct, before opening)

| Part | Pass if |
|---|---|
| Trade Grade | The share hit within 24 h rises from A to F on all 3 coins, and F's is at least 3 times A's |
| Crowd warning | A 5% move against the crowd within 24 h at least 1.5 times as often after a warning, 90% interval above 1, on at least 2 of 3 coins |
| Similar moments | The 80% range holds the actual move 75-85% of the time on every coin; up-chance Brier no worse than the dev-period up share |

`regimecast.verdict.final.run_final(final=True)` runs these once, in Phase 10.
