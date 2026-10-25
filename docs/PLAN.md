# RegimeCast build plan

Status: **draft for review**, 20 Oct 2026. Nothing below is executed until Saeed approves the queued phases.

## How we work

1. **Plan, review, then build.** Each phase is written down with its tasks and a "done when" list. We agree on it, then build it. Phases are sized by what makes sense to build together, not by calendar.
2. **Change only when it's worth it.** If we find a better way mid-phase, we switch only if it clearly improves the result. Every change gets one line in the decision log at the bottom: what changed and why. That log becomes the revision-history section of the report.
3. **Done means done, not perfect.** A phase is finished when its "done when" list is met. Ideas for improvements go on the parking list at the bottom, not into the current phase.
4. **You see it working.** Every phase ends with a demo you run or watch yourself (browser, terminal, numbers on real data), plus a short plain-English walkthrough of how it works.
5. **Quality bar for code:** tests in CI for every piece of logic, no look-ahead in anything time-based, results reported honestly, a README section per module.

## Architecture (target)

```
 PAST DATA (main source)                     Your PC: download, build features, train, evaluate
 Binance archive, Bybit order books, GDELT ─►  data/ (Parquet) ─► models + result tables

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
| Models | Files versioned by date + every forecast logged | So the track record shows what we predicted *before* the outcome. |

Code layout: `regimecast/` (Python: data, features, models, api, worker), `web/` (Next.js), `docs/`, `tests/`.

## Where we are (20 Oct 2026)

**Working**
- Live recorder on Railway since 19 Oct: Binance mark price, order book (top 20), liquidations, open interest; Hyperliquid big orders with wallets, top-account positions, funding and open interest. Kept as is; only its liquidation and Whale Watch data are needed (see Data strategy).
- Binance archive downloader and data validator (code, not run on real data yet).
- Liquidation price engine, safe-leverage search, first-passage baseline probability.
- HAR baselines and evaluation (QLIKE, Diebold-Mariano, Model Confidence Set) with the final test period protected (from 2025-10-01).
- FastAPI backend with `/risk-check`; CI runs 21 tests on every push.

**Known problems**
- `/risk-check` explanation shows the wrong distance to liquidation for longs (10.67% instead of 9.64% for a 10x long). Fix is in Phase 1.
- Order-book history for the October 2025 crash isn't downloaded yet. Bybit has it (200 levels, about 300 MB per coin per day), and a replay prototype exists outside the repo.

## Queued phases (to approve)

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

## Later phases (outline only, detailed when we get there)

| Phase | Modules | What |
|---|---|---|
| 4 | M4 | Features every minute and hour, walk-forward harness with leakage tests, HMM regimes (calm, active, stressed). ETH and SOL data. Full Bybit order-book history. |
| 5 | M5, M6 | Live dashboard (prices over WebSocket, regime-coloured charts); accounts and profiles in Postgres. |
| 6 | M7 | Volatility forecaster vs HAR, max-adverse-move model, calibrated liquidation probability, SHAP explanations. |
| 7 | M8, M9 | LLM trade parser (200-case test set, 95% target); full risk-check UI with explanation charts and history. |
| 8 | M10 | Stress-risk classifier (with Whale Watch features); paper-trading risk agent vs buy-and-hold. |
| 9 | M11 | Public track record, Playwright end-to-end tests, final deployment hardening, user and admin guides. |
| 10 | M12 | October 2025 crash replay; alert engine including Whale Watch alerts (whale move, fresh wallet, suspicious timing). |

## Open questions

1. **Railway plan:** you're on a $20/month plan. Is that Railway? Five services plus Postgres fit in it, but I'd like to confirm.
2. **Criteria 1 and 4:** do you want to ask Mr. Awais to word them as "we test and report either way", or leave them as they are?

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
| 2026-10-26 | Real Binance margin tiers shipped with the code, replacing placeholders | They differ from the placeholders (BTC/ETH first tier to $300k at up to 150x, not $50k at 125x; SOL up to 100x). Public numbers, so they live in the repo and the API on Railway uses the same ones. |

## Parking list (not now)

- Any new recorder streams. The recorder stays as is.

- Whale Watch alerts and UI (Phase 10).
- Full Bybit order-book history for ETH and SOL (Phase 4).
- Direct feeds from X or Truth Social. RSS covers it for now; X's API costs about $200/month.
- Deribit DVOL as a volatility feature (Phase 6).
