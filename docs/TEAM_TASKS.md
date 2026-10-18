# Team tasks up to P1 Mid (early November 2026)

## Saeed: M1 Data Pipeline & Backend Foundation
- [x] Repo skeleton, downloader, validator, live recorder, liquidation engine, HAR baselines, API contract
- [ ] Deploy recorder on Railway (non-US region) and check `/health` shows messages
- [ ] Download BTC history 2020 to now; fix anything the validator flags
- [ ] Run `scripts/run_har_baseline.py` and put the table in the P1 Mid slides
- [ ] Run `scripts/fetch_brackets.py` with a read-only Binance key
- [ ] Ask Mr. Awais about rewording success criteria 1 and 4 ("report either way")

## Ryan: M2 Front-End Architecture & UI/UX
Stack: React + Vite + TypeScript, Tailwind, Recharts (or TradingView lightweight-charts).
- [ ] Wireframes (Figma) for: Risk Check, Risk Radar, Data Health, Track Record
- [ ] Generate a typed API client from `docs/openapi.json`
      (`npx openapi-typescript docs/openapi.json -o src/api/schema.d.ts`)
- [ ] Risk Check page working against the real `/risk-check` endpoint
      (run the backend with `uvicorn regimecast.api.main:app --reload`)
- [ ] Handle `503 {"status":"not_ready"}` from `/forecast` and `/regime` with a friendly placeholder
- [ ] Data Health page reading `/data-health/BTCUSDT`
- Put the code in `frontend/`.

## Aila: M3 News Sentiment Pipeline
Sources: RSS feeds (CoinDesk, Cointelegraph, The Block, Decrypt) + GDELT.
Do NOT use CryptoPanic (free API closed April 2026) or Reddit.
- [ ] RSS collector: title, summary, link, source, **published time (UTC)**, fetched time
- [ ] De-duplicate (same link or near-identical title)
- [ ] FinBERT scoring in batches (positive/negative/neutral + confidence); compare with a CryptoBERT model
- [ ] Hand-label 100 headlines and report accuracy of both models
- [ ] Hourly table: count, mean score, share negative, labelled by the hour END
      (a headline published 10:20 counts for the hour ending 11:00)
- [ ] Save to `data/parquet/sentiment/` and write a short README
- Put the code in `regimecast/sentiment/`.

Sentiment is tested as a "with vs without" comparison. It is not a success criterion.
