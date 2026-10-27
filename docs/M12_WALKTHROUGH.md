# M12 Crash Replay and Alerts: walkthrough

Presenter: Saeed Ahmad Jan. Plain English. Replay numbers from `regimecast/replay/events/ftx_2022.json.gz`
(`python scripts/make_replay.py ftx_2022`); Whale Watch numbers from the recorder's data, 20-26 Oct 2026.

## What it is

Three things that turn RegimeCast from a page you check into something that watches for you:

1. **Crash replay.** Pick a past crash, a coin, a side and a leverage, press play, and watch the crash minute by minute
   as the app would have shown it: the price, the market regime, the stress gauge, and the chance that your position
   is liquidated in the next 24 hours.
2. **Alerts.** Rules on your account, checked once a minute. When one fires, it goes to the bell in the top bar.
3. **Whale Watch.** What the biggest accounts on Hyperliquid (an exchange where every position is public) are doing,
   with the reason each event fired.

## Crash replay

**How it works.** For every hour of the crash, the app's own models (the ones it runs live, loaded from their files)
are run on the stored data: HAR-S for volatility, the HMM for the regime, the classifier for stress. Nothing is
fitted. For every minute, the mark price is kept (liquidations use the mark price). When you choose a position, the
API works out at each minute where it would be liquidated, how far away that is, and the chance of getting there
within the horizon (FHS, the Risk Check's model, with that hour's volatility forecast).

**No look-ahead.** A minute that closes at 10:00 sees the hour that ended at 10:00, never the next one. The chart only
draws minutes up to the current one. Tested: cutting the data at any minute gives exactly the same frames up to that
minute, and the hourly model outputs at hour H are the same whether the data stops at H or goes on
(`tests/test_replay.py`).

**Be honest about what it shows.** The frozen models were fitted on 2020 to September 2025, which includes this crash.
So the replay is a demonstration of the app, not a test of it. Next to the stress gauge the page shows the walk-forward
figure from Phase 6, made by models that had never seen these hours. The real tests are the Phase 5 and 6 results and
the final evaluation.

### The FTX collapse (6-11 Nov 2022), a 10x long opened at the start

BTC at 21,295 on Sunday 6 Nov, liquidation price 19,243 (9.6% below).

| UTC | Price | What the app said |
|---|---|---|
| Sun 6 Nov 00:01 | 21,295 | Calm regime, stress 1% (normally 4%). Chance of liquidation in 24 h: 0.4%. Safe leverage for a new long: 12.6x. |
| Sun 6 Nov 15:47 | | Binance says it will sell its FTT. The app stays calm: the price hardly moves yet. |
| Mon 7 Nov 07:08 | | Your liquidation chance passes 1% (a typical risk limit). |
| Tue 8 Nov 04:12 | | Liquidation chance 20%. |
| Tue 8 Nov 05:00 | | Regime turns **stressed**; stress gauge **high** (66%; walk-forward model: 67%). |
| Tue 8 Nov 06:01 | 19,673 | Liquidation chance 35%, 2.2% away. Safe leverage for a new long: 9.6x. |
| Tue 8 Nov 15:30 | 19,243 | **Liquidated.** |
| Tue 8 Nov 16:03 | | Binance signs a letter of intent to buy FTX. |
| Wed 9 Nov | 15,543 (low) | Stress high for 14 of 24 hours (up to 89%). Safe leverage for a new long falls to 4.2x by 10 Nov. |

Other positions, same start:
* BTC 5x long (liquidation 17,105): the chance passed 1% at 17:54 on 8 Nov, 20% at 19:27, liquidated 19:33.
* ETH 10x long: already above 1% at the start (the safe leverage was 8.4x), liquidated at 04:12 on 8 Nov.
* SOL 10x long: liquidated on the evening of 6 Nov, before BTC moved. SOL fell first (FTX held a lot of it); the
  stress gauge for SOL was already high.
* BTC 10x short: never in danger (peak chance below 1%).

**What it shows.** The app does not predict the crash on Sunday: the news was out, but the market had not moved, and
the models read the market. It turns red early on Tuesday, about 10 hours before a 10x long is liquidated and
two days before the bottom. The stress gauge says that a big move is coming, not which way.

**October 2025.** The replay of the 10 October 2025 crash is built and ready (`python scripts/make_replay.py oct_2025`),
but the script refuses to run until the final evaluation has been written up (`docs/FINAL_EVALUATION.md`), because
those hours are in the closed final test period. It is listed in the app as "after the final evaluation".

## Alerts

Kinds: the price crosses a level (above or below), the stress gauge reaches elevated or high, the regime turns
stressed, a saved position's chance of liquidation goes above your limit (your profile's risk limit, or one you
set), and Whale Watch events for a coin.

**Fires once, then waits.** Each alert has one switch. Armed and the condition holds: it fires one message and
disarms. Disarmed and the condition has cleared: it arms again. So "BTC below 80,000" sends one message when the price
drops through 80,000, not one a minute while it stays below, and sends another the next time it drops through.
A small band stops a price wobbling around the level from firing every minute (it must come back 0.1% to clear; a
position alert clears below 80% of the limit). A price alert set when the price is already past the level waits for a
real crossing; a stress, regime or position alert set when the condition already holds tells you straight away.

**Where the numbers come from.** The same live data the dashboard uses: the mark price from Binance, the stress gauge,
regime and volatility the API recomputes every 10 minutes. A position alert re-runs the Risk Check's model from the
current price, so it rises as the price nears your liquidation price.

**In the app.** Alerts are set on the account page and show their state (waiting, fired and waiting to clear, how
many times). Fired alerts appear under the bell with a red count; "mark all read" clears it. Each user sees only
their own (tested).

## Whale Watch

| Event | Fires when | About how often (recorded week) |
|---|---|---|
| Whale move | one of the ~200 tracked top accounts changes a BTC, ETH or SOL position by $5M or more between two snapshots (5 minutes apart), counting only the change in size, so a price move alone never counts | 2-3 a day |
| Fresh wallet | an account Whale Watch has never seen shows up holding $5M or more (spotted when it places a big order) | about 10 a day |
| Suspicious timing | the stress gauge turns from low to elevated or high, and someone opened $5M or more on that coin in the 6 hours before | not measured yet: the recorded week has no stress-gauge history to replay against |

Every event says why it fired, for example: *"A top Hyperliquid account, a best-month-PnL account, flipped its BTC
position from $53k long to $12.7M short between 20 Oct 16:57 UTC and 20 Oct 17:02 UTC: size +0.6412 -> -153.7 BTC at
about 82,664.00. Leverage 40x."* (from the recorded sample)

**A pattern, not an accusation.** Suspicious timing is labelled "Pattern" and every one of them says: this is a timing
pattern, not evidence of wrongdoing; large traders open positions all the time, and most of these are coincidences. It
is there because of 10 October 2025, when a large short was opened minutes before the tariff post (see the M10
walkthrough).

**Where the data comes from.** The recorder's files sit on its own server, which the API cannot read, so the API reads
Hyperliquid's public data itself with the recorder's own code (the recorder is not changed): the leaderboard once a
day for the top accounts, their positions every 5 minutes, and the public trade stream for big orders from unknown
wallets. The rules were tested on a sample of the recorder's own rows (`tests/test_whales.py`).

## What does not work yet, or works with limits

* The replay uses models fitted on data that includes the FTX crash (see above). The October 2025 replay waits for the
  final evaluation.
* Alerts are in-app only: no e-mail or phone push.
* The alert engine runs in the API process. Railway runs one copy; with two copies each alert would fire twice.
* Whale Watch starts from scratch after an API restart (the first sweep is the new starting point, so a move that
  happens across a restart is missed). Accounts already seen are remembered in the database, so they never come back
  as "fresh".
* "Fresh" means new to Whale Watch, not provably a brand-new account.
