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

## Rules we follow (to keep results honest)

1. A value labelled at time *t* only uses data up to *t*.
2. Liquidations use **mark price**, not last price.
3. Walk-forward only, with a purge gap equal to the forecast horizon. No random K-fold.
4. QLIKE on variance (not log variance). Log models are bias-corrected.
5. Every model we try is logged. The final test period (from `config.HOLDOUT_START` = 2025-10-01, including the Oct 2025 crash) is touched once: scripts stop before it unless run with `--final-test`.
6. Margin tiers are Binance's real ones (`regimecast/risk/binance_brackets.json`, with its fetch date). Refresh them with `scripts/fetch_brackets.py` (read-only key, set up with `scripts/set_binance_key.ps1`) and commit the file.

See `docs/TEAM_TASKS.md` for who does what.
