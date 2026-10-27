# RegimeCast build plan

Status: Phases 1-6 approved (1-3 on 23 Oct, 4-6 on 26 Oct) and done. Phases 7+ are an outline until approved.

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

## Where we are (26 Oct 2026)

| Phase | Status |
|---|---|
| 1 Data foundation (M1) | Done. BTC 2020-now validated, HAR table, crash order-book features, real margin tiers. |
| 2 App skeleton + API online (M2) | Done, merged 26 Oct. Live at https://web-production-210f7.up.railway.app. |
| 3 News sentiment (M3) | Done, merged 26 Oct. Hourly mood May 2025 to now; labels from two blind AI passes (kappa 0.92). |
| 4 Features, regimes, full data (M4) | Done, merged 26 Oct. Features for 3 coins (2020-now, order book from May 2025) on Modal with a daily update; regimes: next-day volatility 43% / 63% / 109% for calm / active / stressed. |
| 5 Volatility forecaster + liquidation risk (M7) | Done 26 Oct. HAR-S + LightGBM beats HAR on all 3 coins x 2 horizons (-5.7% to -18.1% QLIKE, p < 0.002); FHS liquidation probabilities calibrated to 0.06-0.65% ECE (random walk 1.1-3.9%). App: FHS on live HAR-S with SHAP drivers. Weak spot: long-side risk understated in crashes (Phase 6). |
| 6 Stress early warning (M10 part 1) | Done 26 Oct. Classifier AUC 0.80-0.83 vs 0.66-0.71 for the best simple rule; average precision about 2x. Live stress gauge (identical live features, checked hour by hour). 10 Oct forensic timeline. |
| 6b Direction-aware liquidation risk | Done 26 Oct: tried and not adopted (see decision log); FHS stays in the app. |
| 7 LLM trade parser + full risk-check screen (M8, M9) | Built 27 Oct on branch `phase7-parser`: `/parse-trade` (Claude Haiku 5.5), three-step Risk Check, drivers chart, saved history. 200-message test set (two blind AI passes, full agreement); rule-reader baseline 76% all fields right. Claude accuracy run waits for the API key. |
| 8 Live dashboard and accounts (M5, M6) | Built 27 Oct on branch `phase8-dashboard-accounts` (`docs/M5_M6_WALKTHROUGH.md`); live after merge. |

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

## Later phases (outline only, detailed when we get there)

| Phase | Modules | What |
|---|---|---|
| 9 | M10, part 2 | Paper-trading risk agent vs buy-and-hold, after fees and funding. |
| 10 | M11 | Public track record, Playwright end-to-end tests, final deployment hardening, user and admin guides. Final evaluation on the held-out period, once. |
| 11 | M12 | October 2025 crash replay; alert engine including Whale Watch alerts (whale move, fresh wallet, suspicious timing). |

## Open questions

1. **Criteria 1 and 4:** do you want to ask Mr. Awais to word them as "we test and report either way", or leave them as they are?

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

## Parking list (not now)

- Any new recorder streams. The recorder stays as is.

- Whale Watch alerts and UI (Phase 11).
- Direct feeds from X or Truth Social. RSS covers it for now; X's API costs about $200/month.
- `/forecast?horizon_hours=24` is rejected (422): FastAPI does not turn the query text "24" into the `Literal[1, 24]` number. The app calls it without the parameter (24 h is the default). Fix when the forecaster lands (Phase 5).
- Pin the API image's Python packages like the recorder's (`requirements-api.txt`), so a redeploy never picks up a surprise upgrade.
- Risk Check explanation repeats brackets ("(PLACEHOLDER (volatility model not trained yet))"); tidy the wording in the API.
- Track Record page (wireframe ready; built in Phase 10).
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
- Switch saved risk checks to `/me/checks` once Phase 8 is merged (an `ApiHistory` store in `web/src/lib/history.ts`).
- Read take-profit and horizon from the typed trade too.
- Accounts: "forgot password" e-mail, account deletion, and changing the e-mail or password.
- Full order-book depth on the dashboard (diff stream + local book) instead of the top 20 levels.
