# RegimeCast

![tests](https://github.com/algorian-commits/regimecast/actions/workflows/tests.yml/badge.svg)

Explainable crypto risk forecasting for BTC, ETH and SOL perpetual futures.
Final Year Project, BS Artificial Intelligence, SZABIST Islamabad (Fall 2026).
**Paper trading only. Not financial advice.**

Team: Saeed Ahmad Jan (22108199), Ryan Ali (22108197), Aila Jabeen (22108167). Supervisor: Mr. Awais.

## What is in the repo now (Module M1)

| Part | File | What it does |
|---|---|---|
| Archive downloader | `regimecast/data/binance_archive.py` | Downloads Binance USD-M history (1m klines, mark/index/premium price, funding, open interest) with checksum checks. Safe to re-run. |
| Data health | `regimecast/data/validate.py` | Finds gaps, duplicates, bad prices and the known Binance archive bugs. Writes `data/reports/data_health_<symbol>.json`. |
| Live recorder | `regimecast/recorder/recorder.py` | Runs 24/7 on Railway. Records liquidations, mark price + funding, top-20 order book and open interest. Binance does not keep these for free, so every day we record is data we keep. |
| Whale Watch recording | `regimecast/recorder/hyperliquid.py` | Runs inside the recorder. Hyperliquid is on-chain, so trades carry wallet addresses: records big orders (>= $50k) with the wallet, positions of ~200 top accounts every 5 min, one-off snapshots of unknown wallets that place big orders, and funding / open interest. Raw material for whale alerts and the smart-money tilt feature. |
| Liquidation engine | `regimecast/risk/liquidation.py` | Binance liquidation price (isolated + cross, margin tiers, funding), random-walk baseline P(liq), FHS model on mark-price paths, safe-leverage search. |
| Volatility | `regimecast/vol/` | Realised volatility targets (1h from 1-min, 24h from subsampled 5-min), HAR family baselines, walk-forward evaluation, QLIKE, Diebold-Mariano, Model Confidence Set. |
| API | `regimecast/api/main.py` | FastAPI backend: `/risk-check`, `/forecast`, `/regime`, `/track-record`, `/data-health`. Contract in `docs/openapi.json`. |

## Setup (Windows)

Use Python 3.12, the same version as the recorder image and CI.

```bat
git clone https://github.com/algorian-commits/regimecast.git
cd regimecast
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest
```

## Step by step: first results for BTC

```bat
:: 1. download BTC history (about 30-60 min the first time)
python -m regimecast.data.binance_archive --symbols BTCUSDT --start 2020-01

:: 2. check the data
python -m regimecast.data.validate --symbols BTCUSDT

:: 3. HAR baselines, walk-forward, out of sample from 2024
python scripts/run_har_baseline.py --symbol BTCUSDT --test-start 2024-01-01   :: stops before the final test period

:: 4. run the API and open http://127.0.0.1:8000/docs
uvicorn regimecast.api.main:app --reload
```

If Binance is blocked on your network, run step 1 on a VPN or on the Railway server.

## Live recorder (Railway)

Deployed from this repo with `Dockerfile.recorder`. It needs:
* a **volume** mounted at `/data`
* a region **outside the US** (Binance blocks US servers)
* env var `RECORDER_TOKEN` (any long random text) to protect file downloads

Check it: `https://<recorder-url>/health` returns 200 when every stream is fresh and 503 with a `stale` list when one is not. A connection that goes silent for 60 s is reopened, and if data stays stale for 15 min the process exits so Railway restarts it.

Copy files to your PC (do this often: it is the only backup):
`python scripts/pull_live_data.py` (set `RECORDER_URL` and `RECORDER_TOKEN` first). `/files` and `/flush` take the token as `Authorization: Bearer <token>`, never in the URL.

Only changes to the recorder's own files redeploy it (`watchPatterns` in `railway.json`), so other pushes do not interrupt recording.

## News sentiment (Module M3)

An hourly "news mood" per coin from crypto headlines. Code in `regimecast/sentiment/`, plain-English summary in `docs/M3_WALKTHROUGH.md`.

| Part | File | What it does |
|---|---|---|
| GDELT backfill | `sentiment/gdelt.py`, `.github/workflows/gdelt_backfill.yml` | Scans GDELT's 15-minute GKG files (May 2025 on) for crypto headlines and macro tariff/sanctions headlines. The full scan (~250 GB) runs on GitHub Actions and leaves its result on the branch `gdelt-backfill-data`. |
| RSS collector | `sentiment/rss.py` | CoinDesk, Cointelegraph, The Block, Decrypt. |
| Tagging | `sentiment/tagging.py` | Coin (BTC, ETH, SOL, general, other), policy shock (tariffs, sanctions, regulation), de-duplication (same link or near-identical title within 3 days). |
| Models | `sentiment/models.py` | FinBERT (`ProsusAI/finbert`) and CryptoBERT (`ElKulako/cryptobert`), in batches. Score = P(positive) - P(negative). |
| Hourly mood | `sentiment/mood.py` | Per coin and hour END: count, mean score, share negative, policy-shock count. Read with `store.news_mood(symbol)`; API `GET /news-mood`. |
| Pipeline | `sentiment/pipeline.py` | `import-backfill`, `score`, `mood`, `collect` (one live update, or `--every 15` as the worker). |
| Results | `scripts/m3_report.py` | Accuracy on 100 hand labels (`docs/m3/labels_100.csv`), mood vs BTC price, the 10 Oct 2025 crash. Writes `data/reports/m3_*` and `phase3.html`. |

```bat
pip install -r requirements-ml.txt                                    :: torch + transformers (CPU), scoring only
git fetch origin gdelt-backfill-data
python -m regimecast.sentiment.pipeline import-backfill               :: ~600k rows -> ~325k headlines
python -m regimecast.sentiment.pipeline score --model all             :: FinBERT + CryptoBERT, about an hour on a laptop CPU
python -m regimecast.sentiment.pipeline collect                       :: catch up to now (GDELT + RSS) and build the mood
python scripts/m3_report.py                                           :: tables and demo page in data/reports/
powershell -ExecutionPolicy Bypass -File scripts\news_worker.ps1 -Register   :: keep the PC copy current every 15 min
```

Live without the PC: `.github/workflows/news_live.yml` runs the same `collect` every 15 minutes on GitHub Actions and
publishes the state to the branch `news-data`; the deployed API serves `news-data/api/<SYMBOL>.json` when it has no
local data (cached 5 minutes).

## Features, regimes and the data on Modal (Module M4)

| Part | File | What it does |
|---|---|---|
| Batch jobs | `regimecast/jobs/modal_app.py` | Runs on Modal (EU region): Binance history for BTC/ETH/SOL (download, repair, validate), Bybit order-book replay (1-second and 1-minute features, May 2025 onward), feature tables, and a daily update at 06:30 UTC. Data lives on the `regimecast-data` Modal Volume, laid out like `data/`. |
| Features | `regimecast/features/build.py` | 1-minute and hourly tables. A row at time t uses only data known at t (candles at their close, open interest at `available_at`, hours labelled by their end). |
| Leakage tests | `tests/test_features.py` | Scramble or delete every input after a time T: no feature at or before T may change. |
| Walk-forward harness | `regimecast/models/harness.py` | One harness for all models: purge and embargo gaps, baselines registered (HAR family, no-change, historical volatility), and the final test period (from 2025-10-01) closed unless `final=True`. |
| Regimes | `regimecast/models/hmm.py`, `regimes.py` | 3-state Gaussian HMM (calm, active, stressed), refitted on the past only, forward-only probabilities, frozen from 2025-10-01. |

```bat
set MODAL_PROFILE=saeed355ahmed
modal run -m regimecast.jobs.modal_app::features_all
modal volume get regimecast-data parquet/features_1h/BTCUSDT/all.parquet data/parquet/features_1h/BTCUSDT/all.parquet
python scripts/run_regimes.py --symbol BTCUSDT      :: regime chart + crash table -> data/reports/phase4.html
```

See `docs/M4_WALKTHROUGH.md`.

## Volatility forecaster and liquidation risk (Module M7)

| Part | File | What it does |
|---|---|---|
| Targets | `regimecast/features/targets.py` | What happens after each hourly origin: realised variance and the largest adverse mark-price move over 1 h and 24 h (a separate table, never features). |
| Volatility | `regimecast/models/volatility.py` | LightGBM on the M4 features + DVOL (`regimecast/data/dvol.py`), and its average with HAR-S. |
| Liquidation risk | `regimecast/models/liquidation_risk.py`, `calibration.py` | Random walk, FHS and quantile LightGBM, isotonic calibration, Brier/ECE/reliability, block-bootstrap intervals. |
| Live | `regimecast/vol/live.py`, `regimecast/risk/fhs_live.py` | The app: HAR-S on live Binance candles, FHS liquidation probability, exact SHAP drivers. |

```bat
python scripts/run_phase5.py          :: all results -> data/reports/phase5_*.csv (about 30 min)
python scripts/phase5_report.py       :: data/reports/phase5.html
python scripts/fit_live_har.py --symbols BTCUSDT ETHUSDT SOLUSDT
python scripts/fit_live_fhs.py        :: refit the models the app uses (dev period only)
```

See `docs/M7_WALKTHROUGH.md`.

## Stress early warning (Module M10, part 1)

| Part | File | What it does |
|---|---|---|
| Stress label | `regimecast/features/build.py` (`stress_minutes`) | Model-free, past-only stress events (about 4% of hours). |
| Classifier | `regimecast/models/stress.py` | P(stress in the next hour), walk-forward vs simple rules; the app's saved models in `models/stress_live/`. |
| Live inputs | `regimecast/features/live.py` | Binance/Deribit public data through the training feature code (`scripts/check_live_features.py`). |
| Forensics | `scripts/oct10_forensics.py` | 10 October 2025, minute by minute. |

```bat
python scripts/run_phase6.py && python scripts/phase6_report.py && python scripts/oct10_forensics.py
```

See `docs/M10_STRESS_WALKTHROUGH.md`.

## Trade parser and the full risk check (Modules M8, M9)

Type a trade in your own words; RegimeCast reads it into fields, asks for anything missing, and runs the explained
risk check. Plain-English overview: `docs/M8_M9_WALKTHROUGH.md`.

| Part | File | What it does |
|---|---|---|
| Parser | `regimecast/parsing/` | Claude Haiku 5.5 through the Anthropic API: fixed JSON schema (structured outputs), Pydantic and plausibility checks, retries on invalid output, follow-up questions and refusals written by code (the model has no free-text field). A rule reader is the baseline and the fallback without a key. |
| API | `regimecast/api/parser.py` | `POST /parse-trade`; paid calls capped (`PARSE_PER_MINUTE`, `PARSE_PER_DAY`). |
| Test set | `docs/m8/` | 200 messages, two blind AI labelling passes, written rules. |
| Evaluation | `scripts/m8_parser_eval.py` | Field-by-field accuracy for the rule reader and prompts v1/v2 -> `data/reports/m8_*`. |
| Risk Check page | `web/src/components/risk-check.tsx` | Three steps (type, confirm, results), drivers chart, saved checks with filters (`web/src/lib/history.ts`). |

```bat
powershell -ExecutionPolicy Bypass -File scripts\set_anthropic_key.ps1   :: stores ANTHROPIC_API_KEY (hidden input)
python scripts/m8_parser_eval.py                                         :: accuracy table, about $0.15 the first time
```

For the live site, add the same key as the `ANTHROPIC_API_KEY` variable on the Railway `api` service. Until the key
is set, the parser runs on the rule reader (the key is skipped for now; see the open items in `docs/PLAN.md`).

## Live dashboard and accounts (Modules M5, M6)

| Part | File | What it does |
|---|---|---|
| Live feed | `regimecast/api/live_ws.py` | One API-side connection to Binance (mark price, hourly candle, top-20 order book) relayed to browsers at `WS /ws/live`, one snapshot a second; `GET /candles` gives hourly candles with the regime known at each close. |
| Accounts | `regimecast/api/accounts.py` | `/auth/signup`, `/auth/login`, `/auth/logout`, `/me`, `PATCH /me/profile`, `/me/checks` (save, list with coin/date filters, delete), `/me/alerts`. argon2id passwords, httpOnly session cookie. |
| Database | `regimecast/db/` | SQLAlchemy tables and Alembic migrations, applied by the API the first time it uses the database (`DATABASE_URL`; `python -m regimecast.db upgrade` by hand). |
| Web | `web/src/components/live-dashboard.tsx`, `account.tsx`, `web/src/lib/live.ts`, `account.ts` | Risk Radar, log in / sign up, account page; the browser reconnects to the feed by itself. |

API settings: `DATABASE_URL` (Postgres; without it the account endpoints answer 503 not_ready), `SESSION_SECRET`
(32+ random characters; without it sessions end when the API restarts), `SESSION_COOKIE_SECURE=0` only for plain-http
local development. Web: `PUBLIC_API_URL` when `API_URL` is not reachable from the browser (docker compose).

```bat
set DATABASE_URL=sqlite:///data/dev_accounts.sqlite
set SESSION_COOKIE_SECURE=0
uvicorn regimecast.api.main:app --reload          :: then in web/:  npm run dev
```

See `docs/M5_M6_WALKTHROUGH.md`.

## Paper-trading risk agent (Module M10, part 2)

A risk manager for someone who holds BTC, ETH and SOL: every hour it sets each perpetual's position (0x to 1x of its
third of the account, never short) from what RegimeCast knew at that hour, and is judged against buy-and-hold after
fees and funding. Plain-English overview and results: `docs/M10_AGENT_WALKTHROUGH.md`.

| Part | File | What it does |
|---|---|---|
| Backtester | `regimecast/agent/backtest.py` | Hourly, mark price, 0.05% taker fee on every change, archived funding at each settlement, no-trade band; refuses the final test period unless `final=True`. Sharpe, drawdown, Calmar, turnover, block-bootstrap Sharpe difference. |
| Rule and baselines | `regimecast/agent/rules.py` | min(1, target vol / forecast vol), cut by stress probability and regime; buy-and-hold and trailing-volatility targeting. |
| Signals | `regimecast/agent/signals.py` | The walk-forward, out-of-sample forecasts of Phases 4-6 (`data/reports/phase5_liq_*_24h`, `phase6_pred_*`, `regimes_base_*`) and Binance's archived funding. |
| Settings | `regimecast/agent/frozen.json` | Chosen on 2022-01 to 2024-09 (every setting logged in `data/reports/phase9_grid.csv`), checked on 2024-10 to 2025-09, frozen 27 Oct 2026. |
| Final evaluation | `regimecast/agent/final.py` | `run_final(final=True)`: Phase 10 runs it once. |
| Live paper log | `regimecast/agent/live.py`, `.github/workflows/agent_live.yml` | Hourly on GitHub Actions from the live API; each decision is pushed to the branch `agent-data` before the hour plays out. |
| API and app | `regimecast/api/agent.py`, `web/src/components/agent.tsx` | `GET /agent` and the Agent page: live paper account vs buy-and-hold, the latest decision with its reasons, the backtest. |

```bat
python -m regimecast.data.binance_archive --symbols ETHUSDT SOLUSDT --datasets fundingRate --start 2020-01 --end 2025-10-01
python scripts/run_phase9.py                    :: grid, check, freeze, data/reports/phase9_* (about 6 min)
python -m regimecast.agent.live step --dir state :: one live hourly step into ./state
```

## Crash replay and alerts (Module M12)

| Part | File | What it does |
|---|---|---|
| Replay builder | `regimecast/replay/build.py`, `scripts/make_replay.py` | Runs the app's frozen models (HAR-S, HMM regimes, stress classifier; nothing fitted) over a stored crash, hour by hour, and keeps the mark price per minute. Writes `regimecast/replay/events/<event>.json.gz`, which ships with the code. |
| Replay engine | `regimecast/replay/engine.py`, `regimecast/api/replay.py` | `GET /replay/events`, `GET /replay/{event}?symbol=&side=&leverage=&horizon_hours=`: every minute as the dashboard and Risk Check would have shown it, with the chosen position's liquidation chance (FHS). A minute closing at T only sees hours ending at or before T. |
| Alert engine | `regimecast/alerts/rules.py`, `engine.py` | Background task in the API, once a minute: price crosses a level, stress elevated or high, regime turns stressed, a saved position's liquidation chance above the limit, Whale Watch events. Each alert fires once and re-arms after its condition clears. `ALERT_ENGINE=0` switches it off. |
| Inbox | `regimecast/api/inbox.py` | `GET /me/inbox`, `POST /me/inbox/read`, `GET /alerts/engine`. Tables: `alert_events` (migration 0002). |
| Whale Watch | `regimecast/whales/rules.py`, `live.py`, `regimecast/api/whales.py` | Whale move, fresh wallet, suspicious timing (a pattern, never an accusation), each with a written reason. Live from Hyperliquid's public API with the recorder's own code (recorder unchanged); `GET /whales/events`, `GET /whales/status`. `WHALE_WATCH=0` switches it off. |
| Web | `web/src/components/replay.tsx`, `inbox-bell.tsx`, `whale-watch.tsx`, `account.tsx` | Replay page (play, pause, speed, scrubber), the bell in the nav, Whale Watch page, alert settings on the account page. |

```bat
python scripts/make_replay.py ftx_2022     :: needs data/parquet on this PC; ETH/SOL minutes come from Binance's public API
python scripts/make_replay.py oct_2025     :: refused until docs/FINAL_EVALUATION.md exists (final test period)
```

Whale thresholds (env): `WHALE_MOVE_USD`, `WHALE_FRESH_USD`, `WHALE_TIMING_USD` (all $5M), `WHALE_TIMING_HOURS` (6).
See `docs/M12_WALKTHROUGH.md`.

## Trade verdict for beginners (Module M9, extended)

For someone who knows nothing about trading: one grade (A to F), three plain reasons and one suggested fix, first on
the Risk Check results; "Show me the numbers" opens the full detail. It judges how the trade is built (size,
leverage, stop, costs, timing, crowding), never the direction. Plain-English overview and results:
`docs/M12_VERDICT_WALKTHROUGH.md`.

| Part | File | What it does |
|---|---|---|
| Rules | `regimecast/verdict/rules.py` | Trade Grade (written step tables, letters from points), break-even win rate, "fix my trade" (closest safer version within the user's risk limit), the what-if strip. One table drives both the app and the vectorised dev/final scoring (tested equal). |
| Crowd warning | `regimecast/verdict/crowd.py` | Funding, top traders' long/short ratio and 24 h open-interest growth as 14-day ranks (past only); odds of a sudden 5% move against the crowd within 24 h; block-bootstrap ratio. |
| Similar moments | `regimecast/verdict/similar.py` | The 400 nearest past hours whose next day had finished; 80% range of the next 24 h move and the up chance, pulled toward the dev-period up share. Pools ship as `similar_pool_<coin>.parquet` (dev hours only). |
| Timing | `regimecast/verdict/calendar.py`, `macro_events.csv` | Fed decisions, CPI and jobs reports 2020-2027 (from the Fed and BLS calendars, in UTC), weekends, the 4 quietest hours. |
| History and scoring | `regimecast/verdict/history.py`, `evaluate.py` | Hour-by-hour inputs on past data (walk-forward HAR-S, FHS refitted every 90 days, Phase 6 stress, Phase 4 regimes); the measures behind the three final bars. |
| Settings | `regimecast/verdict/frozen.json` | Chosen on 2022-01 to 2024-09 (every candidate in `data/reports/phase12_*_grid.csv`), checked on 2024-10 to 2025-09, frozen 30 Oct 2026. |
| Final evaluation | `regimecast/verdict/final.py` | `run_final(final=True)`: Phase 10 runs it once. |
| API and app | `regimecast/verdict/live.py`, `schema.py`, `web/src/components/verdict.tsx` | `POST /risk-check` returns `verdict` (grade, reasons, fix, what-if, break-even, crowd, similar moments, timing); live crowd inputs refresh every 10 minutes from Binance's public 1-hour history. `target` (take-profit) added to the request. |

```bat
python scripts/run_phase12.py               :: choose, check, freeze; data/reports/phase12_* (about 2 min)
python scripts/run_phase12.py --refreeze    :: only after logging why in docs/PLAN.md
```

## Rules we follow (to keep results honest)

1. A value labelled at time *t* only uses data up to *t*.
2. Liquidations use **mark price**, not last price.
3. Walk-forward only, with a purge gap equal to the forecast horizon. No random K-fold.
4. QLIKE on variance (not log variance). Log models are bias-corrected.
5. Every model we try is logged. The final test period (from `config.HOLDOUT_START` = 2025-10-01, including the Oct 2025 crash) is touched once: scripts stop before it unless run with `--final-test`.
6. Margin tiers are Binance's real ones (`regimecast/risk/binance_brackets.json`, with its fetch date). Refresh them with `scripts/fetch_brackets.py` (read-only key, set up with `scripts/set_binance_key.ps1`) and commit the file.

## Web app and live API (Module M2)

| Part | File | What it does |
|---|---|---|
| Web app | `web/` | Next.js + TypeScript + Tailwind. Risk Radar, Risk Check, Data Health, Live Status, News Mood panel. See `web/README.md`. |
| Live market data | `regimecast/api/market.py` | `/market/{symbol}`: Binance mark price, funding and open interest, cached 5 s. |
| Typed client | `web/src/api/schema.d.ts` | Generated from `docs/openapi.json` (`npm run gen:api` in `web/`). CI fails if it is out of date. |
| Run both locally | `docker-compose.yml` | `docker compose up --build`, then http://localhost:3000 |
| Hosting | `docs/DEPLOY_RAILWAY.md` | Live at https://web-production-210f7.up.railway.app. `api` and `web` on Railway (EU West), deployed from generated branches `railway/api` and `railway/web`. |

Plain-English overview for the defence: `docs/M2_WALKTHROUGH.md`. Wireframes: `docs/wireframes/index.html`.

See `docs/TEAM_TASKS.md` for who does what.
