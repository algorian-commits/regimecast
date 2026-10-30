# RegimeCast build plan

Status (30 Oct): Phases 1-9 and 11 done and merged; Phase 12 (trade verdict) built and frozen. Next: the final evaluation
(Phase 10, part A) once, then UI polish. The final evaluation's pass bars are fixed below **before** the final test
period is opened.

## How we work

1. **Plan, review, then build.** Each phase is written down with its tasks and a "done when" list. We agree on it, then build it. Phases are sized by what makes sense to build together, not by calendar.
2. **Change only when it's worth it.** If we find a better way mid-phase, we switch only if it clearly improves the result. Every change gets one line in the decision log at the bottom: what changed and why. That log becomes the revision-history section of the report.
3. **Done means done, not perfect.** A phase is finished when its "done when" list is met. Ideas for improvements go on the parking list at the bottom, not into the current phase.
4. **You see it working.** Every phase ends with a demo you run or watch yourself (browser, terminal, numbers on real data), plus a short plain-English walkthrough of how it works.
5. **Quality bar for code:** tests in CI for every piece of logic, no look-ahead in anything time-based, results reported honestly, a README section per module.

## Architecture (target)

```
 PAST DATA (main source)                     Modal (batch, region eu): download, build features, train
 Binance archive, Bybit order books, GDELT ─►  Modal Volume (Parquet) ─► models + result tables
                                              Your PC: code, small extracts, reports, demos

                      ┌──────────────── Railway ─────────────────────────────────┐
 Exchange APIs ─────► │ api       ── FastAPI: pulls current market data on       │
 (current data)       │              request, runs the models, answers the app   │
 News RSS ──────────► │ worker    ── scheduled jobs: news, hourly forecasts      │
 Binance, Hyperliquid │ recorder  ── only what has no archive (see below)        │
                      │ postgres  ── users, saved risk checks, alerts, track record
                      │ web       ── Next.js: the app people use                 │
                      └──────────────────────────────────────────────────────────┘
```

### Data strategy

**Past data is the main source.** Every model is built, trained and tested on downloaded history (2020 to now, with the final test period from 2025-10-01). Nothing we build waits for data to accumulate.

| Need | Source |
|---|---|
| Prices and mark price (1-min, from 2020), funding, open interest (5-min), every trade | Binance archive |
| Full order book (200 levels, from May 2025) | Bybit archive |
| News headlines (from 2015) | GDELT |
| Current market data for the live app | Exchange APIs, fetched by the `api` when a request comes in |

**The recorder stays as it is, for three things that have no archive.** No new features are added to it.
1. **Liquidation events.** Binance doesn't archive them.
2. **Whale Watch.** Hyperliquid only shows who the top accounts are and what they hold *right now*.
3. **Track record.** Forecasts must be logged live, before the outcome is known, or the track record proves nothing. (This logging is done by the `worker`; the recorder supplies the outcomes.)

| Part | Choice | Why |
|---|---|---|
| Market data store | Parquet files + DuckDB for queries | Free, fast, no server to run, the same files on Railway and your PC. Replaces TimescaleDB from the proposal. |
| App data | PostgreSQL on Railway | Users, saved risk checks, alerts and the track record need a real database (M6, M9, M11, M12). |
| Backend | Python, FastAPI | All the AI libraries are Python. One language for data, models and API. |
| Front end | Next.js + TypeScript + Tailwind | Matches the proposal. Built-in routing and auth for accounts; public track-record pages get real URLs. Typed client generated from the API contract. |
| Background jobs | One `worker` service | News collection, feature updates and forecasts on a schedule, kept separate from the API so a slow job never slows the app. |
| Heavy batch jobs | Modal (Starter plan, `region="eu"`) | Backfills, GPU scoring and model training run in parallel containers: the Bybit order-book history (~400 GB raw, ~1,600 coin-days) takes hours for ~$8, inside the $30/month free credit, and never touches the PC's disk. EU because Binance and Bybit block US servers. |
| Hosting | Railway Pro | Everything always-on: recorder, api, web, worker, Postgres. Cheaper than Modal for 24/7 containers. |
| Models | Files versioned by date + every forecast logged | So the track record shows what we predicted *before* the outcome. |

Code layout: `regimecast/` (Python: data, features, models, api, worker), `web/` (Next.js), `docs/`, `tests/`.

## Where we are (28 Oct 2026)

| Phase | Status |
|---|---|
| 1 Data foundation (M1) | Done. BTC 2020-now validated, HAR table, crash order-book features, real margin tiers. |
| 2 App skeleton + API online (M2) | Done, merged 26 Oct. Live at https://web-production-210f7.up.railway.app. |
| 3 News sentiment (M3) | Done, merged 26 Oct. Hourly mood May 2025 to now; labels from two blind AI passes (kappa 0.92). |
| 4 Features, regimes, full data (M4) | Done, merged 26 Oct. Features for 3 coins (2020-now, order book from May 2025) on Modal with a daily update; regimes: next-day volatility 43% / 63% / 109% for calm / active / stressed. |
| 5 Volatility forecaster + liquidation risk (M7) | Done 26 Oct. HAR-S + LightGBM beats HAR on all 3 coins x 2 horizons (-5.7% to -18.1% QLIKE, p < 0.002); FHS liquidation probabilities calibrated to 0.06-0.65% ECE (random walk 1.1-3.9%). App: FHS on live HAR-S with SHAP drivers. Weak spot: long-side risk understated in crashes (Phase 6). |
| 6 Stress early warning (M10 part 1) | Done 26 Oct. Classifier AUC 0.80-0.83 vs 0.66-0.71 for the best simple rule; average precision about 2x. Live stress gauge (identical live features, checked hour by hour). 10 Oct forensic timeline. |
| 6b Direction-aware liquidation risk | Done 26 Oct: tried and not adopted (see decision log); FHS stays in the app. |
| 7 LLM trade parser + full risk-check screen (M8, M9) | Built and merged 27 Oct: `/parse-trade` (Claude Haiku 5.5), three-step Risk Check, drivers chart, saved history. 200-message test set (two blind AI passes, full agreement); rule-reader baseline 76% all fields right. Claude accuracy run waits for the API key. |
| 8 Live dashboard and accounts (M5, M6) | Built and merged 27 Oct (`docs/M5_M6_WALKTHROUGH.md`): sign up / log in on Postgres, Risk Radar dashboard with live WebSocket feed and regime-coloured candles. Logged-in Risk Checks use the account's risk limit, margin mode and balance, and save to the account. |
| 9 Paper-trading risk agent (M10 part 2) | Built 27 Oct, merged 28 Oct (`docs/M10_AGENT_WALKTHROUGH.md`), settings frozen. Dev period 2022-01 to 2025-09: Sharpe 0.62 vs 0.46 for buy-and-hold, max drawdown -38% vs -79%, FTX week -12% vs -27%. Plain trailing-vol targeting does as well (0.65); the grid switched the stress and regime cuts off (fees). Live paper log hourly on `agent-data`; Agent page. Final run not done (Phase 10). |
| 11 Crash replay and alerts (M12) | Built 27 Oct, merged 28 Oct (`docs/M12_WALKTHROUGH.md`): FTX replay minute by minute with the frozen models, alert engine once a minute into an in-app inbox (bell), Whale Watch alerts from Hyperliquid's public data. October 2025 replay waits for the final evaluation. |
| 12 Trade verdict for beginners (M9, extended) | Built 30 Oct (`docs/M12_VERDICT_WALKTHROUGH.md`), settings frozen. Grade: share knocked out within 24 h rises A to F on all 3 coins, F/A 8.6-10.5x. Crowd warning: BTC 1.84x (90% interval 1.24-2.55); ETH and SOL do not pass. Similar moments: 80% range covers 77-80%; up chance no better than the base rate (a coin flip, as 6b found). Final run not done (Phase 10). |
| 10 Final evaluation, then UI polish (M11) | Waiting for Phase 12. Track Record page, end-to-end tests, hardening and guides dropped. |

## Phases 1-3: P1 Mid (approved 23 Oct)

These three phases cover M1–M3 and end in the P1 Mid working demo.

### Phase 1: Data foundation (M1)

Goal: every later module reads clean, validated data the same way.

Tasks
1. Fix the long-side liquidation distance bug and add a test.
2. Download Binance history for BTC from 2020: 1-minute candles, mark price, index, premium, funding, open interest. Run the validator and fix or document everything it flags.
3. Add one data-access layer (`regimecast/data/store.py`) so every module loads data through the same functions: archive, live recordings and features, all with consistent UTC timestamps.
4. Run the HAR baseline on real BTC data (test 2024-01 to 2025-09) and save the result table. This is our first real result.
5. Bybit order-book pipeline: download, replay, and keep 1-second features (spread, depth at 10/25/50/100 bps, imbalance, order-flow imbalance), then delete the raw file. **Scope now:** BTC, 3 Oct to 17 Oct 2025 (the crash plus a week each side). The full May 2025 onward history runs later as a background job, when M4 needs it.
6. Backup routine: `pull_live_data.py` run on a schedule from your PC (Windows Task Scheduler), so the Railway volume isn't the only copy.
7. Real margin tiers via `fetch_brackets.py`. **Done 26 Oct:** fetched with Saeed's read-only key and shipped in `regimecast/risk/binance_brackets.json`.

Done when
- The data health report for BTC exists, and every issue is fixed or explained.
- The HAR table on real data is saved in `data/reports/`.
- Crash-window order-book features exist, and a chart shows spread and depth collapsing on 10 Oct 2025.
- Tests for the store and order-book replay pass in CI.

Demo: one page of charts from real data. BTC price, spread and depth through the October crash, plus the HAR result table.

### Phase 2: App skeleton (M2) + API online

Goal: a real app at a real URL, with the risk check working end to end.

Tasks
1. Wireframes for Risk Check, Risk Radar (dashboard), Data Health and Track Record. Quick and simple, not pixel-perfect.
2. Next.js app in `web/`: layout, dark and light theme, shared components (cards, buttons, charts), loading and error states, mobile-friendly.
3. Typed API client generated from `docs/openapi.json`, and CI checks it stays in sync.
4. Live market data in the `api`: current mark price, funding and open interest fetched from Binance when requested (cached for a few seconds), so the app is always current without reading recordings.
5. Pages: Risk Check (form, pre-filled with the current price → result with liquidation price, probability, safe leverage, warnings), Data Health, and Live Status (recorder health).
6. Deploy `api` and `web` as Railway services, with HTTPS URLs.
7. `docker compose up` runs api + web locally with one command.

Done when
- You open the URL on your phone, enter a trade, and get a correct result.
- Not-ready endpoints show a friendly placeholder, not an error.

Demo: the live URL.

### Phase 3: News sentiment (M3)

Goal: an hourly "news mood" per coin, built from past headlines and measured honestly.

Tasks
1. GDELT backfill: crypto headlines from 2025-05 onward (the same period as the order-book data), with title, link, source and published time (UTC). De-duplicated.
2. Tag each headline with its coin (BTC, ETH, SOL) and a "policy shock" category (tariffs, sanctions, regulation) for the Trump-style market moves.
3. Score with FinBERT, and compare against one crypto-specific model.
4. Hand-label 100 headlines and report accuracy for both models. **Needs from you:** I draft the labels, you check them (about 20 minutes).
5. Hourly news mood (count, mean score, share negative, labelled by hour end), stored as Parquet.
6. Check against price: does the mood move before or with big BTC moves? Include the 10 Oct 2025 tariff post as a case study.
7. Keep it current: RSS collector in the `worker` (CoinDesk, Cointelegraph, The Block, Decrypt) adds new headlines through the same pipeline, so the panel in the app is live.

Done when
- The backfilled hourly mood exists for May 2025 to now.
- The accuracy table and the price check are saved in `data/reports/`.
- The mood panel in the app shows current data.

Demo: the mood chart through the October crash, the accuracy table, and the live panel.

**P1 Mid demo:** Phase 1 charts and HAR table (M1), the live app with the risk check (M2), and the news mood panel (M3).

## Phases 4-6 (approved 26 Oct)

Order changed from the earlier outline: the core model (M7) comes before the dashboard and accounts (M5, M6), because results matter more than screens. M5/M6 can run in parallel as their own session from Phase 4 on, since they only touch the app.

**The final test period stays closed.** The October 2025 crash is inside it (from 2025-10-01). Models are built and tuned on data up to 2025-09-30, using the four earlier crashes (Mar 2020, May 2021, Nov 2022, Aug 2024). October 2025 is scored once, at the final evaluation. Descriptive studies (charts, the forensic timeline) may show it, as long as no model is tuned on it.

### Phase 4: Features, regimes and full data (M4)

Goal: every model after this reads the same leak-free features, and we can see the market's regime at any minute since 2020.

Tasks
1. ETH and SOL: download the same Binance history as BTC (from 2020), validate, and document it in `docs/DATA_HEALTH.md`. Also Binance's `bookDepth` archive (depth at 1-5% from the price) for all three.
2. Full Bybit order-book features from May 2025 to now for BTC, ETH and SOL, run as a Modal batch job in the EU region (pipeline: `regimecast/data/orderbook.py`), results on the Modal Volume with a small copy for the PC. Then a daily job keeps it current.
3. Feature builder: 1-minute and 1-hour features using only past data. Returns, realised volatility, funding, open interest, premium/basis, and from May 2025 the order-book features (spread, depth, imbalance, order-flow imbalance).
4. Leakage tests in CI that every model must pass: shifting future data must not change any feature, and a change after time T must not change anything at T.
5. One walk-forward harness for all models (purge and embargo gaps), with the baselines registered (HAR family, historical volatility, no-change).
6. Gaussian HMM regimes (calm, active, stressed): fitted inside each training window only, filtered probabilities (no smoothing, which looks ahead), states sorted by volatility. One version on 2020-now features, one adding order-book features from May 2025.
7. Regime study: how early the regime turns "stressed" before each of the four dev-period crashes, and volatility by regime.

Done when
- ETH and SOL data health documented; order-book features cover May 2025 to now for all three coins (gaps documented).
- Leakage tests pass in CI.
- `data/reports/` has the six-year BTC regime chart and the crash lead-time table.

Demo: six years of BTC price coloured by regime, with each crash marked.

### Phase 5: Volatility forecaster and calibrated liquidation risk (M7)

Goal: the risk check's numbers come from a tested model, not a placeholder.

Tasks
1. Quick win first: the HAR-S forecast (already tested on real BTC data) replaces the placeholder volatility in `/risk-check`. **Done 26 Oct:** fitted per coin on the dev period (`regimecast/vol/har_live.json`), refreshed by the API every 10 minutes from live Binance candles (`regimecast/vol/live.py`); `/forecast` serves it.
2. Volatility forecaster: gradient-boosted trees (LightGBM or XGBoost) with quantile outputs on the Phase 4 features, a forecast combination with HAR-S, and Deribit's DVOL (options-implied volatility) for BTC and ETH.
3. Compare with HAR-RV and HAR-S, 1 h and 24 h, walk-forward on the dev period: QLIKE, Diebold-Mariano, Model Confidence Set. Reported whatever the result.
4. Worst-move model: quantiles of the maximum adverse move of the mark price, turned into the probability of reaching a liquidation or stop price.
5. Calibration with isotonic or Platt scaling on held-back dev data; Brier score, ECE and reliability diagrams pooled over coins, sides and a leverage grid (2x to 100x), with block-bootstrap confidence intervals; results per dev-period crash.
6. SHAP explanation for every estimate, shown in the risk check as "what is driving your risk".

Done when
- Result tables and reliability diagrams saved in `data/reports/`.
- `/risk-check` uses the model; the placeholder warning is gone.

Demo: the reliability diagram ("we said 20%, it happened about 20% of the time") and a risk check with its explanation.

### Phase 6: Stress early warning and the 10 October forensics (M10, part 1)

Goal: a calibrated "stress in the next hour" probability, and the full story of the crash on one timeline.

Tasks
1. Stress labels made separately from any model: spread or realised volatility above a high percentile of its recent values for several minutes.
2. Stress classifier for the next hour: order-book signals (Hiremath and Hiremath), regime, funding and open interest. Whale Watch tilt only as a live-period feature, since it starts in Oct 2026.
3. Evaluate against simple volatility-threshold rules: precision, recall, false alarms per day, warning lead time, calibration.
4. 10 October 2025 forensic timeline (descriptive): the tariff post, the large Hyperliquid shorts placed before it (wallet fill histories), the order-book collapse, open interest and funding, the news mood.
5. Stress gauge in the API and app: low, elevated or high, with its probability.

Done when
- Classifier results saved; the forensic chart exists; the gauge shows in the app.

Demo: the 10 October timeline and the live stress gauge.

## Phases 6b, 7, 8 (6b approved and done 26 Oct; 7 and 8 waiting for approval)

### Phase 6b: Direction-aware liquidation risk (M7 + M10)

Goal: fix Phase 5's weak spot. In crashes the liquidation model treats up and down moves alike and understates the
risk for longs (BTC 10x long in the FTX week: 6% predicted, 31% liquidated).

Tasks
1. A direct model of "will this position be liquidated within the horizon", per side, fitted on stacked rows of
   (hour, leverage): inputs are the distance to liquidation in units of forecast volatility (with a monotone
   constraint: farther away can never be riskier), the stress probability (Phase 6), regime, funding, open-interest
   change, long/short ratios, taker ratio, last hour and last day returns, DVOL vs realised volatility.
2. Walk-forward on the same folds and scoring period as Phase 5 (2022-01 to 2025-09), isotonic calibration on
   earlier out-of-sample predictions only.
3. Compare with FHS (the app's model now): overall Brier and calibration, per side, per leverage, and in the FTX
   and Yen-carry crash windows.
4. Adopt it in the app only if overall calibration is not worse and the crash-window long-side error drops;
   otherwise keep FHS and report the attempt.

Done when
- The comparison is saved in `data/reports/` and the decision is in the decision log.

Demo: the crash-window table before and after; a risk check during a stressed hour.

### Phase 7: LLM trade parser and the full risk-check screen (M8, M9)

Goal: type a trade the way you would say it and get the full, explained risk check.

Tasks
1. Trade parser with Claude through the Anthropic API: a fixed JSON schema checked with Pydantic (asset, side, size,
   leverage, entry, stop, margin mode), shorthand ("58k", "10x"), retries on invalid output, a follow-up question
   when a field is missing, and guardrails: it only reads the trade and never gives advice.
2. Test set of about 200 trade descriptions: drafted, then checked by a second blind pass (as for the M3 labels);
   field-by-field accuracy against the 95% target; two prompt versions compared.
3. `POST /parse-trade` in the API; the Risk Check page becomes three steps: type, confirm the parsed fields, results.
4. Results screen: liquidation chance, safe leverage, fees, regime, stress, and charts of what drives the risk
   (SHAP); saved history with filters (needs Phase 8 accounts; until then kept in the browser).

Done when
- Parser accuracy table saved; the three-step flow works on the live URL.

Needs from you: an Anthropic API key (paid per use; cents per hundred parses with a small model).

### Phase 8: Live dashboard and accounts (M5, M6)

Goal: the dashboard the proposal describes, and user accounts.

Tasks
1. PostgreSQL on Railway; sign up and log in with email and password (hashed, never stored as text); sessions.
2. Profile: paper account balance and risk limit (used by the risk check for position size); settings: default
   margin mode and favourite coins. Each user's saved checks and alerts are private.
3. Live dashboard: prices over WebSocket with automatic reconnect, candlestick chart with the background coloured by
   regime, spread and depth panel, volatility and stress gauges, news mood panel; smooth updates, mobile layout.
4. Tests for auth and data separation; CI stays green.

Done when
- You can sign up on the live URL, set your balance and limit, and the dashboard updates live.

Phases 7 and 8 touch different parts (parser + risk screen vs accounts + dashboard) and can run as two parallel
sessions once approved.

## Phases 9-12 (direction from Saeed, 27 Oct)

Order: Phases 9, 11 and 12 run as parallel sessions. When Phase 9's agent and Phase 12's signals are frozen, the
models are frozen and the final evaluation (Phase 10, part A) runs **once**, before any UI polish. The rest of
Phase 10 comes after.

**Every session keeps the final test period closed.** No code in Phase 9, 11 or 12 reads data from 2025-10-01 on, except
the Phase 11 replay of the October 2025 crash, which is switched on only after the final evaluation has run.

### Phase 9: Paper-trading risk agent (M10, part 2)

Goal: show that RegimeCast's signals help someone who holds BTC, ETH and SOL, after fees and funding.

The agent is a **risk manager, not a trading bot**: it holds a long position in each perpetual (0x to 1x of the
account, never short, never more than 1x) and decides every hour how much to hold, using only what RegimeCast knew at
that hour.

Tasks
1. Backtester (`regimecast/agent/`): hourly steps, equal-weight BTC/ETH/SOL, positions marked at the mark price,
   Binance taker fee 0.05% on every change of position, funding paid or received every 8 hours at the archived rate,
   a no-trade band so tiny changes are skipped. Tested: costs, funding sign, no look-ahead (a decision at hour H uses
   only signals stamped at or before H).
2. Agent rules, kept small: exposure = min(1, target volatility / forecast volatility), then cut by the stress
   probability (Phase 6) and the regime (Phase 4). Inputs are the walk-forward, out-of-sample forecasts already
   produced for 2022-01 to 2025-09 (`data/reports/phase5_*`, `phase6_pred_*`), so no signal was ever fitted on the
   hours it trades.
3. Choose the few settings (target volatility, stress cut-offs, regime cut, band width) on 2022-01 to 2024-09 with a
   small grid, check them on 2024-10 to 2025-09, then **freeze** them in `regimecast/agent/frozen.json` with the date.
   Every setting tried is logged with its result.
4. Baselines on the same hours: buy-and-hold (1x long in each perpetual, paying funding), and plain volatility
   targeting with trailing volatility (no RegimeCast signals), to show whether the signals add anything.
5. Results on the dev period: return, Sharpe ratio, maximum drawdown, Calmar ratio, turnover, fees and funding paid,
   time in the market, block-bootstrap interval for the Sharpe difference; per crash window (FTX, Yen-carry).
6. A final-evaluation entry point that runs the frozen agent on the final test period with `final=True` (it is not
   run in this phase; Phase 10 runs it once).
7. Live paper log: the frozen agent runs every hour on live data (GitHub Actions, published to a data branch like the
   news mood), writing its decision **before** the hour's outcome is known; `/agent` in the API and an "Agent" page in
   the app show the equity curve vs buy-and-hold and the latest decision with its reasons.

Done when
- Dev-period tables and the equity chart are in `data/reports/phase9_*`, the settings are frozen and logged, the live
  paper log is running, and the Agent page shows it.

Demo: the dev-period equity curves (agent vs buy-and-hold vs volatility targeting) through the FTX crash.

### Phase 11: Crash replay and alerts (M12)

Goal: replay a crash minute by minute as the app would have seen it, and alerts that actually fire.

Tasks
1. Replay engine: steps through stored minutes and shows what the dashboard and risk check would have said at each
   minute (price, regime, stress gauge, liquidation chance for a chosen position), with play, pause and speed. Uses
   the frozen models only; it never fits anything.
2. Built and tested on the FTX crash (Nov 2022) first. The October 2025 crash replay is generated only after the
   final evaluation has run (it is inside the final test period).
3. Alert engine in the API (background task, once a minute): rules per user on price crossing a level, stress level
   reaching elevated or high, regime turning stressed, and the liquidation chance of a saved position going above the
   user's limit. Fired alerts go to an in-app inbox (bell icon); each alert fires once and then waits until its
   condition clears. Uses the Phase 8 `/me/alerts` table.
4. Whale Watch alerts from the recorded Hyperliquid data: a top account's position changes by more than a set amount
   (whale move), a new large account appears (fresh wallet), and a large position opened shortly before a stress flag
   (suspicious timing, labelled as a pattern, never an accusation). The recorder itself is not changed; if its data is
   not reachable from the API, the API reads Hyperliquid's public endpoints directly.
5. Tests: alert rules (fires once, re-arms), whale rules on recorded samples, replay has no look-ahead.

Done when
- The FTX replay plays in the app, alerts fire into the inbox on the live URL, and Whale Watch alerts show with
  their reasons.

Demo: the FTX replay, and an alert set on the live site that fires.

### Phase 12: Trade verdict for beginners (M9, extended)

Goal: someone who knows nothing about trading types a trade and learns, in plain words, whether it is a **well-built**
trade and how to make it safer. It judges the setup (size, leverage, stop, costs, timing, crowding), **not** which
way the price will go: Phase 6b showed our data cannot predict direction, so the app never pretends to.

Default view is for beginners: one verdict, three plain reasons, one suggested fix. A "show me the numbers" toggle
opens the full detail for experienced users.

Tasks
1. **Trade Grade (A to F)** with three plain-English reasons, from what the risk check already computes: chance of
   liquidation vs the user's limit, chance the stop is hit by normal movement, fees and funding as a share of the
   expected move, regime, stress, crowding and timing. The grading rules are written down and fixed on the dev
   period.
2. **Break-even win rate:** "with this stop and target you need to be right X% of the time to make money after fees
   and funding". Pure arithmetic; tested.
3. **Fix my trade:** the closest safer version of the same trade (lower leverage, smaller size, a stop outside normal
   noise) that meets the user's risk limit, shown side by side with the original.
4. **What-if strip:** what the trade gains or loses on a normal day, a bad day (dev-period 90th/99th percentile moves
   at the current regime) and a crash day like 10 Oct 2025, in dollars.
5. **Crowd warning:** when funding, top traders' long/short ratio and open-interest growth are at extremes (thresholds
   chosen on dev data and frozen), show the historical odds of a sudden 5% move against the crowd within 24 h.
6. **Similar moments:** the past hours (strictly before now) that looked most like the current one (regime, volatility
   forecast, trend, funding, crowding); show how big the next 24 h move usually was and how often it went up, with a
   plain note that direction is close to a coin flip when it is.
7. **Bad timing warnings:** scheduled US macro events (FOMC, CPI, jobs report) within the trade's horizon, weekends
   and thin hours, stress rising. Event dates from a fixed calendar file in the repo.
8. API: the risk check returns the verdict, fix and what-if; the Risk Check results screen shows them first.
   Regenerate the contract and client; tests for every rule; dev-period results in `data/reports/phase12_*`.
9. Freeze the grading rules, crowding thresholds and similar-moments settings in `regimecast/verdict/frozen.json`,
   with a `final=True` entry point that is **not** run here (Phase 10 runs it once).

Done when
- A typed trade on the live URL shows its grade, reasons, the fix and the what-if strip; dev-period tables are saved;
  the settings are frozen.

Demo: a beginner's 25x BTC long graded F, the suggested fix graded B, and the crash-day line.

### Phase 10: Final evaluation, then UI polish (M11)

Part A, as soon as Phase 9's agent and Phase 12's signals are frozen:
1. Freeze: tag the commit `final-eval` (code, settings and model files); the pass bars below are already committed.
2. Run `scripts/final_eval.py` once with `final=True`. It writes `data/reports/final_*` and a log with the commit, the
   start time and the data range. The walk-forward schedule continues as in development (refit every 90 days on past
   data only, same settings), so the models keep learning from the past as time moves on, exactly as live.
3. Report every bar as passed or failed, in plain words, in `docs/FINAL_EVALUATION.md`, whatever the result.

Part B, after: UI polish only. The Track Record page, automated end-to-end tests, deployment hardening and the user
and admin guides are dropped (Saeed, 27 Oct).

## Final evaluation: what counts as a pass (fixed 27 Oct 2026, before opening)

Saeed asked Claude to set these bars. They are written here and committed before any model sees the final test
period, and they do not change after it is opened.

**Final test period:** 2025-10-01 00:00 UTC to 2026-09-30 23:00 UTC (12 months, about 8,700 hours per coin), BTC,
ETH and SOL. Same targets, labels and scoring code as in development.

| Part | Compared with | Pass if |
|---|---|---|
| Volatility forecast (M7): HAR-S + LightGBM combination, 1 h and 24 h | HAR-RV (the standard benchmark) | Lower QLIKE than HAR-RV in at least 5 of the 6 coin x horizon cases, and significantly lower (Diebold-Mariano, p < 0.05) in at least 3 of 6. |
| Liquidation probability (M7): FHS, the app's model | The random walk (the app's old model) | Lower calibration error (ECE) than the random walk in all 6 cases, lower Brier score in at least 5 of 6, and ECE at most 0.5% at 1 h and at most 1.5% at 24 h in at least 5 of 6. |
| Stress warning (M10 part 1): next-hour classifier | The best of the three simple rules (stress continues, volatility above its 95th percentile, regime says stressed) on the same hours | AUC at least 0.75 on every coin and above the best rule on every coin, and average precision above the best rule on at least 2 of 3 coins. |
| Regimes (M4) | Their own definition | Next-day volatility is ordered calm < active < stressed on all 3 coins. |
| Paper agent (M10 part 2): frozen settings, equal-weight portfolio | Buy-and-hold, 1x long in each perpetual, after the same fees and funding | Higher Sharpe ratio **and** a maximum drawdown at least 25% smaller than buy-and-hold. Return, the volatility-targeting baseline and the bootstrap interval are reported alongside, not as bars. |
| Trade Grade (Phase 12) | Its own claim: worse grades are riskier | On a fixed grid of example trades (every 6 hours, each coin, long and short, leverage 2x to 50x, stops at 0.5 to 3 times the forecast move; grades with under 200 trades merged with the next), the share hit by liquidation or the stop within 24 h rises from A to F on all 3 coins, and F's share is at least 3 times A's. |
| Crowd warning (Phase 12) | Hours without the warning | A sudden 5% move against the crowd within 24 h happens at least 1.5 times as often after a warning as in other hours, with the 90% block-bootstrap interval of that ratio above 1, on at least 2 of 3 coins. |
| Similar moments (Phase 12) | Honesty about size and direction | The 80% range of the next 24 h move contains the actual move 75-85% of the time on every coin, and its chance of an up move has a Brier score no worse than always saying the dev-period up share. |
| App (M2, M5, M6, M8, M9) | Its own spec | A one-time check on the live URL passes (Saeed clicks through a short checklist, since Claude does not create accounts on the live site): sign up, set balance and limit, type a trade, confirm, get a result, see it saved on the account, see the dashboard price update within 10 s, log out; the risk check answers in under 2 s for 95% of 50 requests. |
| Trade parser (M8) | The 95% target in the proposal | Every field at least 95% correct on the 200-message set. **Runs only once the Anthropic key is set**; until then it is reported as not run (the rule reader's 76% is the fallback, not the result). |

Rules for the run
- One run. If the evaluation code has a bug (a crash, a wrong file, a wrong date range), it is fixed and rerun, and
  both runs and the fix are logged. A model or setting is never changed after the period is opened.
- Anything tried after opening goes in a separate "after the final test" section and never replaces these results.
- Crash windows (10-11 Oct 2025) are reported for every part, with no bar: the long-side weakness in crashes is
  already known (Phase 5).
- The verdict, as Saeed defined it: the project is "good" if the model parts (volatility, liquidation, stress), the
  paper agent, the Phase 12 trade verdict (grade, crowd warning, similar moments) and the app all pass. Anything that fails is reported as failed, with what it means.

## Open questions

1. **Criteria 1 and 4:** do you want to ask Mr. Awais to word them as "we test and report either way", or leave them as they are?
2. **Anthropic API key (skipped for now, not forgotten).** The Phase 7 parser runs on the rule reader until it is set.
   Needed: (a) on the PC, `powershell -ExecutionPolicy Bypass -File scripts\set_anthropic_key.ps1` (hidden input,
   never pasted in chat or written to the repo); (b) `python scripts/m8_parser_eval.py` for the accuracy table
   (about $0.15); (c) the same key as the `ANTHROPIC_API_KEY` variable on the Railway `api` service, so the live
   site uses Claude. The final evaluation's parser bar waits for it.

## Decision log

| Date | Decision | Why |
|---|---|---|
| 2026-10-18 | Binance instead of Bybit for live data | Free archive back to 2020 plus a live public API. Bybit kept for historical order books. |
| 2026-10-19 | Parquet + DuckDB instead of TimescaleDB for market data | No database server to run; the same files locally and on Railway. Postgres stays for app data. |
| 2026-10-19 | Final test period from 2025-10-01, evaluated once | Keeps results honest; the crash is the main test event. |
| 2026-10-20 | Added Whale Watch (Hyperliquid) recording | On-chain positioning data, unique to us; it can only be recorded live. |
| 2026-10-20 | Next.js front end, FastAPI backend | Next.js as in the proposal (auth, real page URLs). Backend stays Python because every ML library we use is Python. |
| 2026-10-20 | Past data is the main source for building and testing | Archives cover prices, funding, open interest, trades, order books and news, so nothing waits for recordings to build up. |
| 2026-10-20 | Recorder kept as is, no new features | Only liquidations, Whale Watch and track-record outcomes have no archive. |
| 2026-10-20 | Live app pulls current data from exchange APIs | Always current, and no dependency on stored recordings. |
| 2026-10-20 | Order-book depth measured within 1 and 2 bps and over the best 10/50/200 levels | Bybit's 200 levels reach only ~2-3 bps for BTC, so the planned 10-100 bps bands were always cut off. |
| 2026-10-20 | Zero-volume minutes treated as missing | Binance prints frozen candles during halts; they looked like zero volatility and distorted HAR. |
| 2026-10-20 | Archive holes repaired from daily files | Monthly files miss whole days that daily files have (13,015 mark-price minutes for BTC). |
| 2026-10-20 | News mood backfilled from GDELT | Months of history to test against price right away, including the crash, instead of waiting for a live collector to build up. |
| 2026-10-23 | Web app forwards `/api/*` to the backend at run time | The browser only talks to the website: no CORS, and the API address is set on Railway without rebuilding. |
| 2026-10-23 | Live market data endpoint `/market/{symbol}`, cached 5 s, last value shown as "delayed" for up to 5 min if Binance fails | Pre-fills Risk Check with the current price; one Binance call serves many visitors. |
| 2026-10-23 | Data health report JSONs committed (`data/reports/data_health_*.json`) | The deployed API has no copy of `data/`; the report is small and changes only when the validator reruns. |
| 2026-10-23 | Wireframes as static HTML (`docs/wireframes/`) instead of Figma | The connected Figma account has a view-only seat (cannot create design files). The live app is the clickable prototype. |
| 2026-10-23 | Next.js 16 and Node 24 (current releases) | Latest stable at build time; Next's own docs ship in `node_modules/next/dist/docs`. |
| 2026-10-26 | Real Binance margin tiers shipped with the code, replacing placeholders | They differ from the placeholders (BTC/ETH first tier to $300k at up to 150x, not $50k at 125x; SOL up to 100x). Public numbers, so they live in the repo and the API on Railway uses the same ones. |
| 2026-10-26 | Modal for heavy batch jobs, Railway Pro for always-on services | The PC has ~5 GB free disk and a slow link for ~400 GB of order-book files. Modal's $30/month credit covers backfills, GPU scoring and training; Railway Pro hosts the recorder, api, web, worker and Postgres. Modal workspace: `saeed355ahmed` (the active CLI profile on Saeed's laptop; jobs pin it with `MODAL_PROFILE=saeed355ahmed` because a second profile also exists). |
| 2026-10-26 | Core model (M7) moved ahead of dashboard and accounts (M5, M6); new order: M4, M7, M10 part 1, then M8/M9 | Results are the project's main claim; M5/M6 can run in parallel since they only touch the app. |
| 2026-10-26 | HAR-S forecast feeds the risk check before the full M7 model | It is already tested on real data, so the placeholder volatility goes away now. |
| 2026-10-26 | `api` and `web` deploy from generated branches `railway/api` and `railway/web` | Railway reads the repo-root `railway.json` (the recorder's) for every service from this repo and no longer lets new services pick another config path. Each branch is the source with `railway.json` swapped for `deploy/railway.<service>.json`, regenerated on every push; the recorder (on `main`) is untouched. |
| 2026-10-26 | GDELT read from its raw 15-minute files on GitHub Actions, not its search API | The search API answers 429 to our network and returns at most 250 articles per query. The raw files are complete but ~250 GB; on Actions the scan takes 10 minutes, at home it would take days. |
| 2026-10-26 | Headline time = when GDELT had it (its batch time), not the page's publish stamp | Publish stamps are often missing or in local time. Headlines first seen more than 24 h after publication are old news and left out. |
| 2026-10-26 | Macro tariff and sanctions headlines collected too, not only crypto ones | The headline that moves the market (e.g. Trump's China tariff post) usually doesn't mention crypto. They count toward the policy-shock count only. |
| 2026-10-26 | GDELT's own outage (15 Jun - 1 Jul 2025) left as a gap | Those hours are missing in the mood table rather than shown as "no news". |
| 2026-10-26 | Mood vs price statistics on May-Sep 2025 only; the 10 Oct 2025 crash as a descriptive case study | Keeps the final test period untouched for M7, where sentiment is tested "with vs without". |
| 2026-10-26 | The 100 sentiment labels checked by a second, blind AI labelling pass instead of by Saeed | Saeed asked for it to be handled. Two independent passes (Claude), 95% agreement, Cohen's kappa 0.92, 5 disagreements settled by the written rule. Reported as AI-labelled, not human-labelled. |
| 2026-10-26 | Live collector reads new GDELT batches as well as the four RSS feeds | RSS alone covers four sites while the backfill covers thousands, so headline counts would jump when the live data takes over. |
| 2026-10-26 | Live news mood runs on GitHub Actions every 15 min and is published on the branch `news-data`; the API reads it from there | The deployed API has no copy of data/ (it lives on the PC). Actions is free for this public repo and already does the GDELT backfill; a data-only branch (not main) means no redeploys. Instead of a Railway `worker` service. |
| 2026-10-26 | Own small Gaussian HMM (`models/hmm.py`) instead of hmmlearn | hmmlearn has no wheel for Python 3.14 on Windows (needs a C++ compiler), and we need a forward-only filter anyway: its usual probabilities use future data. ~150 lines, tested on known parameters. |
| 2026-10-26 | Order-book history kept on Modal at 1-second resolution; 1-minute summaries for models and the PC | 1-second features are ~16 MB per coin-day (~26 GB in total), more than the PC's free disk. The 1-minute summary is ~60x smaller and is what the models use. |
| 2026-10-26 | All three coins' Binance history downloaded and validated on Modal, not the PC | Same pipeline, fast EU link, no PC disk; the PC keeps BTC and compact feature tables. |
| 2026-10-26 | The harness closes the final test period itself | `models/harness.py` refuses test blocks inside it and stops targets before it unless `final=True`, so no model can touch it by accident. |
| 2026-10-26 | Regimes refitted every 90 days, not 30 | Regimes shift slowly; quarterly refits cut the run about threefold. Probabilities are still forward-only. |
| 2026-10-26 | App keeps the volatility-based regimes; order-book signals go to the Phase 6 stress model | Fitted on May-Sep 2025 only, the order-book regime version barely separates next-day volatility (28/31/34%). |
| 2026-10-26 | App's liquidation model: FHS on live HAR-S volatility | Best calibrated out of sample on every coin and horizon (ECE 0.06-0.65% vs 1.1-3.9% for the random walk); FHS on HAR-S is as well calibrated as on the combination, and HAR-S is already live. |
| 2026-10-26 | Quantile LightGBM for the worst move tried, not adopted | Worse Brier score and calibration than FHS at 24 h; slightly better at 1 h only. Logged in phase5_liquidation.csv. |
| 2026-10-26 | Explanations in the app are exact SHAP values of HAR-S | HAR-S is linear in its inputs, so beta x (input - training mean) is its exact SHAP value. LightGBM SHAP is reported in the research results. |
| 2026-10-26 | Results scored from 2022-01 (vol walk-forward from 2021-01) | Gives the liquidation models a year of out-of-sample volatility to learn from, and brings the FTX and Yen-carry crashes into the scored period. |
| 2026-10-26 | Stress label: 5+ minutes of 15-minute volatility above the trailing 30-day 99th percentile | Model-free, past-only, about 4% of hours; spread-based labels would only exist from May 2025. |
| 2026-10-26 | App's stress model trained without order-book inputs | They added nothing at the hourly level (identical AUC May-Sep 2025), and there is no live order book in the API. |
| 2026-10-26 | Live features built by the training feature code from live API data | Same definitions by construction; scripts/check_live_features.py found them identical to the archive. The taker ratio endpoint is stamped at the period start (others at the end), handled. |
| 2026-10-26 | 10 Oct forensic timeline uses reported times for the whale short and the post | The wallet address is not in the public reports and the ENS lookup was blocked by a bot check; its fill history needs the address (HypurrScan/Arkham). |
| 2026-10-26 | Phase 6b (direction-aware liquidation risk) added before Phases 7-8 | The crash-window weakness is the biggest gap in the core product; Saeed approved fixing it first. |
| 2026-10-26 | Phase 6b: direction-aware liquidation model tried, NOT adopted; FHS stays | Worse overall at 24 h (ECE 0.68-1.43% vs 0.49-0.65%, Brier worse), equal at 1 h, and mixed in crash windows (better for 10x longs, worse at 50x). With funding, open interest, crowding, stress and returns, the direction of the next day's move is not predictable in advance; the stress gauge warns that a big move may come, not which way. Results: data/reports/phase6b_*.csv. |
| 2026-10-27 | Trade parser model: Claude Haiku 5.5 (`claude-haiku-5-5`), structured outputs, effort low | Reading one sentence needs no large model; about $0.0003 per parse. `PARSER_MODEL` can switch it. |
| 2026-10-27 | The parser model has no free-text output; every sentence the user sees (follow-ups, refusals, warnings) is fixed text in our code | The "never gives advice" guardrail holds by construction, not only by instruction. |
| 2026-10-27 | A rule reader (regular expressions) added as the baseline and as the fallback | The page works without a key, past the spending cap, or when the API fails; the LLM gets a baseline to beat. Written and frozen before the test set. |
| 2026-10-27 | Paid parser calls capped (60 a minute, 2,000 a day by default; past the cap the rule reader answers) | A public page must not run up a bill. |
| 2026-10-27 | Parser test set: 200 messages written by Claude, labelled again blind by a second Claude pass; reported as AI-labelled | As for the M3 labels. The passes agreed on every field (intent kappa 1.00): the rules are clear for these messages, but the set is likely cleaner than real user text. |
| 2026-10-27 | Saved risk checks kept in the browser behind one interface until `/me/checks` exists (Phase 8) | Phase 7 and 8 run in parallel; the page does not change when the store does. |
| 2026-10-27 | Accounts: Postgres on Railway via SQLAlchemy Core + Alembic migrations, applied by the API the first time it uses the database; SQLite for tests and local dev | One schema definition for both; no manual migration step on deploy. CI runs the account tests on a real Postgres too. |
| 2026-10-27 | Sessions: random token in an httpOnly, Secure, SameSite=Lax cookie; the database stores HMAC(SESSION_SECRET, token); passwords hashed with argon2id | Server-side sessions can be ended on logout (a signed JWT cannot); a database copy alone cannot log anyone in. |
| 2026-10-27 | The web forwarder passes the session cookie both ways, so it belongs to the website's domain | Keeps the Phase 2 rule that the browser only talks to the website; no cross-site cookies. |
| 2026-10-27 | Live prices: one API-side connection to Binance relayed to browsers over our own WebSocket (1 snapshot/s), not browser-to-Binance | Binance blocks some regions (US); the EU server always reaches it, and many visitors share one upstream connection. The browser connects to the API's domain directly because Next.js route handlers cannot forward WebSockets. |
| 2026-10-27 | Dashboard feed uses Binance's `/market` (mark price, candles) and `/public` (order book) WebSocket routes, like the recorder | The old `/ws` path no longer answered when tested from the PC on 26 Oct. |
| 2026-10-27 | Spread and depth from Binance's top 20 levels (`depth20@500ms`), labelled "the inside of the book" | Free and live; for BTC the 20 levels reach only ~0.3 bps, so the panel says it is not the full depth. A full book needs the diff stream and a local order book (parking list). |
| 2026-10-27 | Chart: TradingView lightweight-charts (Apache-2.0), 30 days of hourly candles, background = regime known at each candle's close | Real candlesticks and smooth live updates (Recharts has none); no hindsight in the colours (tested). |
| 2026-10-27 | Profile "risk limit" = the highest chance of liquidation accepted per trade (the Risk Check's existing `risk_limit`) | The risk check already turns it into safe leverage and the largest position; the paper balance is the cross-margin wallet. |
| 2026-10-27 | Alerts are stored per user now; notifying waits for the alert engine (Phase 11) | M6 asks for private alerts; firing them is M12. |
| 2026-10-27 | Phases 7 and 8 merged; the Risk Check uses the account when logged in (risk limit, margin mode, wallet; checks saved to `/me/checks`), the browser otherwise | One page for both; checks saved while logged out stay in that browser and are not copied to the account. |
| 2026-10-27 | Phases 9 and 11 detailed and run in parallel; final evaluation as soon as the Phase 9 agent is frozen, before UI polish | Saeed wants to know whether the project is good, not to hit milestones. |
| 2026-10-27 | Pass bars for the final evaluation set by Claude and committed before opening the final test period | Saeed asked Claude to set them; fixing them first keeps the result honest. |
| 2026-10-27 | Final test period ends 2026-09-30 (12 months) | A fixed end decided before opening; full months of archive data. |
| 2026-10-27 | Paper agent is long-only, 0x to 1x, judged against buy-and-hold on Sharpe and drawdown | RegimeCast is a risk co-pilot for someone who holds, not a trading signal; shorting would make it a different product. |
| 2026-10-27 | October 2025 crash replay generated only after the final evaluation | It shows model outputs inside the final test period. |
| 2026-10-27 | Phase 10 cut to the final evaluation plus UI polish: no Track Record page, automated end-to-end tests, hardening or guides | Saeed doesn't need them. The app bar is checked once by hand on the live URL instead of by a test script. |
| 2026-10-27 | Phase 12 added: trade verdict for beginners (grade, break-even win rate, fix my trade, what-if, crowd warning, similar moments, timing) | Saeed wants beginners to know whether a trade is worth taking. It judges the setup, not the direction (Phase 6b: direction is not predictable from our data). |
| 2026-10-27 | Final evaluation waits for Phase 12's signals; their pass bars are added now, before opening | New forecasting claims need the sealed year too, and it can be opened only once. |
| 2026-10-27 | Main user: a beginner; detail behind a "show me the numbers" toggle | Saeed left it to Claude; the app's point is to protect people who don't know trading. |
| 2026-10-27 | Agent buy-and-hold = a third of the account in each perpetual at the first hour, quantities never traded again | What "just holding" means; fixed before the final test so the bar's baseline cannot move. Its weights drift with prices, and funding is paid from the account. |
| 2026-10-27 | Agent settings chosen by a rule written before the grid ran: highest Sharpe among settings with a drawdown at least 25% smaller than buy-and-hold (the final bar); the vol-targeting baseline tuned the same way | The check period (2024-10 to 2025-09) is only for checking, never for choosing. |
| 2026-10-27 | Frozen agent: target volatility 30%, no stress cut, no regime cut, 20-point no-trade band (225 settings tried) | The stress and regime cuts lowered the drawdown a little but traded in and out every few hours; fees cost more than they saved (Sharpe 0.36 at best with a stress cut vs 0.38). Reported as found: the agent's gain over buy-and-hold is volatility targeting, and plain trailing-vol targeting does as well. |
| 2026-10-27 | Agent funding from Binance's archived settlements, downloaded for ETH and SOL to Sep 2025 (monthly files only) | The hourly feature table only shows the 00/08/16 UTC settlements; SOL settled every 2 hours from 10 to 18 Nov 2022 (FTX). |
| 2026-10-27 | Agent's stress input = the calibrated probability, the uncalibrated out-of-sample one in Jan-Jun 2022 (before the calibrator has a past) | Keeps the grid period starting in Jan 2022 with the LUNA crash in it; both are walk-forward. |
| 2026-10-27 | Live paper log on GitHub Actions reads RegimeCast's own API on Railway, not Binance | GitHub's servers are in the US, where Binance's futures API is blocked. So the live agent uses the app's HAR-S volatility (the backtest with HAR-S: Sharpe 0.60 vs 0.62, same drawdown). |
| 2026-10-27 | `agent-data` keeps its full git history (no force push, unlike `news-data`) | Each decision's commit time is the proof that it was written before the hour played out. |
| 2026-10-27 | Crash replay uses the app's frozen models (fitted to 2025-09-30, so the FTX crash is in-sample) and shows Phase 6's walk-forward stress probability next to the gauge | The plan says frozen models only; the page says plainly that it demonstrates the app and is not a test of it. |
| 2026-10-27 | Replay files ship with the code (`regimecast/replay/events/*.json.gz`, ~220 kB per crash); ETH and SOL minute prices read from Binance's public mark-price candles | The deployed API has no data/, and the PC holds the minute archive for BTC only. |
| 2026-10-27 | `make_replay.py oct_2025` refuses to run until `docs/FINAL_EVALUATION.md` exists | The October replay stays a one-command step for later and cannot be built by accident. |
| 2026-10-27 | Alerts fire once and re-arm when the condition clears, with a small band (price 0.1%, position risk below 80% of the limit); a price alert set past its level waits for a real crossing | No repeat messages while a condition lasts and no flapping around a level. |
| 2026-10-27 | Alert kinds added to the Phase 8 table: stress high, saved position's liquidation risk (a saved check, limit = profile risk limit unless set), Whale Watch (migration 0002) | Covers the plan's four rules, with Whale Watch delivered to the same inbox. |
| 2026-10-27 | Whale Watch in the API reads Hyperliquid's public endpoints with the recorder's own `HyperliquidWatcher` (recorder unchanged) | The recorder's files are on its own volume; its file endpoint needs a token the API does not have. |
| 2026-10-27 | Whale thresholds $5M (move, fresh wallet, timing), timing window 6 h; moves only for tracked top accounts; "fresh" = not seen by Whale Watch before (kept in `whale_seen`) | On the recorded week: about 2.6 moves and 10 fresh wallets a day. Hyperliquid gives no account age, so "fresh" is documented as new to Whale Watch. |
| 2026-10-30 | Trade Grade built on one "knock-out chance" (stop or liquidation, whichever comes first) plus extra points for liquidation vs the user's limit, instead of separate liquidation and stop points | With separate points, tight-stop low-leverage trades got few points but were stopped out most often, so the grade did not rise A to F (BTC grid period). One knock-out number counts each trade's real exit once. |
| 2026-10-30 | Grade cut points A < 2, B < 5, C < 8, D < 11 points, chosen on 2022-01 to 2024-09 from 5 sets written beforehand | Rule: meets the final bar on all 3 coins with every letter used by 200+ trades; largest smallest F/A ratio. Check period F/A 8.7-10.6x. |
| 2026-10-30 | The verdict always looks 24 h ahead, whatever horizon the user picks | The grade bar, the what-if strip and similar moments are all about the next day. |
| 2026-10-30 | Crowd signals as ranks against their own last 14 days, not fixed levels | Top traders' long/short ratio drifts a lot (ETH 1.1 to 3.5 in 2022-25); 14 days is what Binance's public 1-hour history lets the live app compute. |
| 2026-10-30 | Crowd warning: 2 of the 3 signals in their top/bottom 5% (9 settings tried) | Chosen by the rule "most coins meeting the bar (ratio >= 1.5, 90% interval above 1) on the grid period"; works for BTC only (dev 1.84x, interval 1.24-2.55); no setting worked for ETH or SOL. Reported as found. |
| 2026-10-30 | Crowd and similar-moments choice rules revised once after the first run, before freezing | The first crowd rule ignored the bootstrap interval the bar uses (its pick, all 3 signals in the top 10%, failed the interval even on BTC); the first similar rule chose on Brier differences of 0.0001 and left BTC's coverage at 84%, next to the 85% edge. Both now follow the bars; chosen on the grid period only. |
| 2026-10-30 | Similar moments: 400 nearest hours, moves scaled to today's volatility, up chance pulled toward the dev-period up share (10 pseudo-hours per neighbour) | 32 settings tried; coverage 77-80% on the dev period. The up chance is about the base rate: direction is a coin flip (Phase 6b), and the app says so. In the dev tables the dev-period up share is known in advance (slightly favourable); in the final run it is genuinely fixed beforehand. |
| 2026-10-30 | What-if crash line = the fall within 24 h on 10 Oct 2025 (BTC -16.3%, ETH -21.8%, SOL -35.6%), read once from the final test period | The plan asks for "a crash day like 10 Oct 2025". One descriptive number per coin; nothing is fitted or chosen on it. |
| 2026-10-30 | Macro calendar: Fed decisions, CPI and jobs reports 2020-2027 as a CSV in the repo, from the Federal Reserve and BLS calendars, converted to UTC | Fixed and reviewable; includes the 2020 emergency cuts and the 2025 shutdown's moved releases. Thin hours 03:00-07:00 UTC (lowest median volume, grid period). |
| 2026-10-30 | `target` (take-profit) added to `POST /risk-check` and the form | The break-even win rate needs it. The trade parser still does not read it (parking list). |

## Parking list (not now)

- Any new recorder streams. The recorder stays as is.

- Direct feeds from X or Truth Social. RSS covers it for now; X's API costs about $200/month.
- `/forecast?horizon_hours=24` is rejected (422): FastAPI does not turn the query text "24" into the `Literal[1, 24]` number. The app calls it without the parameter (24 h is the default). Fix when the forecaster lands (Phase 5).
- Pin the API image's Python packages like the recorder's (`requirements-api.txt`), so a redeploy never picks up a surprise upgrade.
- Risk Check explanation repeats brackets ("(PLACEHOLDER (volatility model not trained yet))"); tidy the wording in the API.
- Live HAR-S + LightGBM combination in the app: needs live funding, open interest, basis, long/short ratios and DVOL inputs (1-5% lower QLIKE than HAR-S).
- Crash scenario line in the risk check when stress is elevated or high (e.g. how much higher long-side liquidation rates were in past crash windows at this leverage).
- Minute-level stress model with order-book inputs (the book collapsed within 5 minutes of the 10 Oct post), once more order-book history exists.
- 10 Oct whale wallet fill history: needs the address from HypurrScan or Arkham.
- Download BTC from Sep 2019 (perpetual launch), so the COVID crash has more than two months of history before the first regime fit.
- Move all three Railway services to Infrastructure as Code (`.railway/railway.ts`) before 2026-12-01, when Railway stops reading the recorder's `railway.json`.
- Fine-tune a sentiment model on our own crypto headlines (needs ~1,000 labels, not 100).
- Weight headlines by how many outlets ran them (`copies`) and use GDELT's tone as an extra feature (M7).
- Fill GDELT's 15 Jun - 1 Jul 2025 outage from the news sites' own archives or sitemaps.
- Second parser test set from real user messages (logged with consent), since the AI-written set is likely too clean.
- Read take-profit and horizon from the typed trade too.
- Accounts: "forgot password" e-mail, account deletion, and changing the e-mail or password.
- Full order-book depth on the dashboard (diff stream + local book) instead of the top 20 levels.
- Agent: a stress cut that does not churn (exit only after the probability stays high for some hours, or a wider band for stress exits); try only on the dev period, never after the final test.
- Agent: lower target volatility / wider band than the grid's edge (30%, 20 points), as a dev-period sensitivity only; the frozen settings stay.
