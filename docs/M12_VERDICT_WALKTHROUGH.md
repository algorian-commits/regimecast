# M9 (extended) Trade Verdict for Beginners: walkthrough

Presenter: Saeed Ahmad Jan. Plain English. Numbers from `data/reports/phase12_*.csv` (`python scripts/run_phase12.py`,
about 2 minutes); page: `data/reports/phase12.html`. Settings: `regimecast/verdict/frozen.json` (frozen 30 Oct 2026).

## The question

**Is this a well-built trade, and how do I make it safer?** Someone who knows nothing about trading types a trade
and gets one grade, three plain reasons and one suggested fix. A "Show me the numbers" button opens the full Phase 7
detail for experienced users.

It judges the **set-up** (size, leverage, stop, costs, timing, crowding), never which way the price will go. Phase 6b
showed that our data cannot predict direction, so the app never pretends to.

## What the user sees

| Part | What it says | Where it comes from |
|---|---|---|
| **Trade Grade A-F** | "C: Risky in places" and the three reasons that matter most | Penalty points from written rules (below) |
| **Make it safer** | The closest safer version of the same trade, side by side with the original, and its grade | Same coin, side, entry and margin; lower leverage and a stop outside normal noise |
| **What a day could do** | Gain or loss in dollars on a normal day, a bad day (1 in 10), a very bad day (1 in 100) and a crash day like 10 Oct 2025 | Worst moves within 24 h in the same market phase, 2020 to Sep 2025 |
| **To make money** | "You need to be right 41% of the time to make money after fees and funding" | Pure arithmetic from the stop and target |
| **Timing and the crowd** | Fed decision, inflation or jobs report in the next day; weekend or quiet hours; stress rising; crowd warning | A fixed calendar file in the repo; Phase 6 stress; funding and positioning data |
| **Similar moments** | "In the 400 past hours most like now, the price usually moved about 1.3% over the next day ... it went up 57% of the time: close to a coin flip" | The past hours that looked most like now, strictly before now |

## The grade

Every trade collects penalty points. The verdict always looks one day ahead.

| Rule | Points |
|---|---|
| Chance the trade is ended within 24 h (by the stop, or by liquidation if there is no stop): under 15% / 15-30% / 30-50% / 50%+ | 0 / 2 / 4 / 6 |
| Chance of liquidation vs your own limit L: up to L/2, L, 3L, 10L, more | 0 / 1 / 2 / 3 / 4 |
| No stop, or a stop past the liquidation price | 2 |
| Fees and funding as a share of a typical day's move: under 10% / 10-25% / 25%+ | 0 / 1 / 2 |
| Market stressed (Phase 4 regime) | 1 |
| Stress gauge elevated / high (Phase 6) | 1 / 2 |
| Stress rising (at least 10% and doubled in 3 hours) | 1 |
| Crowd warning on your side | 2 |
| Fed, CPI or jobs report within the next day | 1 |
| Weekend, or one of the 4 quietest hours (03:00-07:00 UTC, chosen on volume) | 1 |

Total points to letters: **A** 0-1, **B** 2-4, **C** 5-7, **D** 8-10, **F** 11+. These cut points were chosen on
2022-01 to 2024-09 from five sets written beforehand, by the rule "meets the final bar on all three coins with every
letter used; largest smallest F/A ratio".

## Does a worse grade really mean a riskier trade? (dev period)

The test the final evaluation will repeat: a fixed grid of example trades (every 6 hours, each coin, long and short,
leverage 2x to 50x, stops at 0.5 to 3 times the forecast move: about 380,000 trades per coin), graded with what the
app knew at the time (HAR-S and FHS refitted every 90 days on the past only). Share knocked out by the stop or
liquidation within 24 h, 2022-01 to 2025-09:

| | A | B | C | D | F | F / A |
|---|---|---|---|---|---|---|
| BTC | 5.7% | 13.8% | 27.0% | 36.0% | 48.8% | **8.6x** |
| ETH | 5.8% | 14.8% | 29.4% | 34.7% | 53.0% | **9.1x** |
| SOL | 5.6% | 14.8% | 29.7% | 36.0% | 58.7% | **10.5x** |

It rises from A to F on every coin, in the choosing period and in the check period (2024-10 to 2025-09: F/A 9.3x,
8.7x, 10.6x). The bar asks for at least 3x.

What leverage looks like: on BTC, 2-5x trades are mostly A or B, 10x mostly B, 20-25x mostly C or D, and 50x is
D or F. A 25x BTC long with no stop is graded by the market it meets: on a calm day like 30 Oct 2026 it got a **C**
(9.6% chance of liquidation in a day, ten times a 1% limit), on wild days D or F. Its fix (12x, stop at 74,051)
got an **A**.

## Crowd warning

When funding, top traders' long/short ratio and open-interest growth are all extreme against their own last 14 days,
the crowd is leaning one way. The warning: a sudden 5% move against the crowd within 24 h. Chosen on 2022-01 to
2024-09 from nine settings: at least two of the three signals in their top (or bottom) 5%.

| Dev 2022-01 to 2025-09 | Warning hours | 5% move against the crowd after a warning | in other hours | Ratio (90% interval) |
|---|---|---|---|---|
| BTC | 1,206 | 13.5% | 7.3% | **1.84 (1.24-2.55)** |
| ETH | 1,270 | 17.4% | 13.4% | 1.30 (0.91-1.71) |
| SOL | 1,419 | 28.8% | 26.0% | 1.11 (0.90-1.32) |

**Honest result: it works for BTC only.** No setting worked on ETH or SOL in the choosing period. The bar needs 2 of
3 coins, so this part will probably fail the final evaluation. In the app the warning shows its own past odds, so
the user sees how weak or strong it is.

## Similar moments

Eight numbers describe each hour (regime, volatility forecast, last day's and week's trend, funding, positioning,
open-interest growth). The 400 most similar past hours whose next day was already over give an 80% range of the
next day's move (scaled to today's volatility) and how often it went up, pulled hard toward the long-run up share.

| Dev 2022-01 to 2025-09 | Range contains the actual move (bar: 75-85%) | Brier, up chance | Brier, always the dev up share |
|---|---|---|---|
| BTC | 79.8% | 0.2496 | 0.2495 |
| ETH | 77.4% | 0.2496 | 0.2499 |
| SOL | 78.0% | 0.2499 | 0.2502 |

The ranges are honest about size. On direction they are a coin flip, as Phase 6b found: the up chance is no better
than always saying 51%, and on BTC a hair worse. The app says so in plain words every time.

## What-if strip and break-even

* Bad and very bad days: the 90th and 99th percentile of the worst move against the position within 24 h, in the
  current regime (BTC overall: 1.4% typical, 5.1% bad, 13.3% very bad for a long).
* Crash day: the fall within 24 h on 10 Oct 2025: BTC -16.3%, ETH -21.8%, SOL -35.6%. This one number per coin is
  read from the final test period as a fixed scenario; nothing is fitted or chosen on it.
* Break-even win rate = loss / (win + loss), with fees on both sides and funding over the day included.

## How it stays honest

* Every input is walk-forward: a grade at hour H uses only what was known at H. Similar moments only use past hours
  whose next 24 h had finished (tested: changing the future changes nothing).
* Settings chosen on 2022-01 to 2024-09, checked on 2024-10 to 2025-09, frozen. The final test year stays closed;
  `regimecast/verdict/final.py` runs it once with `final=True` in Phase 10.
* The crowd and similar-moments choice rules were tightened once after the first run (to follow the bars
  themselves), before freezing. Logged in the plan.
