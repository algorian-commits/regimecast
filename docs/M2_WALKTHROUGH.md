# M2: Front-end architecture and UI (walkthrough for the P1 Mid defence)

Presenter: Ryan Ali. Built in Phase 2 of `docs/PLAN.md`.

## What M2 is

M2 is the part of RegimeCast people actually use: a website that works on a phone or a laptop.
You type a crypto futures trade and it tells you, in plain English, where you would be liquidated,
how likely that is in the next hour or day, how much leverage keeps the risk small, and what it costs
in fees. It also shows live prices, the news mood and the health of our data. Paper trading only.

## The pages

| Page | What it shows | Where the data comes from |
|---|---|---|
| **Risk Radar** (home) | Live mark price, funding and open interest for BTC, ETH and SOL; the News Mood panel; regime and forecast cards | Binance, fetched live by our API; news mood from M3 |
| **Risk Check** | A trade form (entry pre-filled with the live price) and the result: liquidation price, chance of liquidation, max safe leverage, fees, warnings and a "Why" explanation | Our liquidation engine (M1) via `/risk-check` |
| **Data Health** | Every dataset we downloaded since 2020, how many rows, gaps and duplicates, and how each known Binance archive problem is handled | The validator's report (M1) via `/data-health/BTCUSDT` |
| **Live Status** | Is the API up? Is the 24/7 recorder healthy, and when did each stream last receive data? | API `/health` and the recorder's `/health` |

Parts that are not built yet (regime model, volatility forecaster, news mood until M3 is online)
show a **friendly placeholder** that says when they arrive, never an error.

Design basics: dark and light theme (remembers your choice), works at phone width, shared
building blocks (cards, buttons, stats, charts), and a loading skeleton while data arrives.
Wireframes for Risk Check, Risk Radar, Data Health and Track Record are in `docs/wireframes/index.html`.

## How the front end and the API talk

```
 Phone / browser ──► web (Next.js, Railway) ──/api/*──► api (FastAPI, Railway) ──► Binance (live prices)
                          │                                   └──► liquidation engine, data reports
                          └──/recorder/health──► recorder (Railway, 24/7)
```

1. The browser only ever talks to the **web** service. It forwards `/api/...` calls to the **api**
   service. This means no cross-site (CORS) problems, and the API address can change without
   rebuilding the website.
2. The **contract** between them is `docs/openapi.json`, generated from the Python code. The website's
   TypeScript types are generated from that file (`npm run gen:api`). If someone changes the API and
   forgets to update the website, **CI fails**, so the two can never silently drift apart.
3. **Live market data**: when you open Risk Check, the API asks Binance for the current mark price,
   funding and open interest, and keeps the answer for 5 seconds so many visitors share one call.
   If Binance is unreachable it shows the last price (marked "delayed") for up to 5 minutes, then a
   placeholder.
4. **Not ready** is a rule, not an accident: any endpoint whose model is not trained answers
   `503 {"status":"not_ready"}`, and the website turns that into a placeholder card.

Tech: Next.js 16, TypeScript, Tailwind CSS, Recharts for charts, openapi-typescript for the typed
client, Vitest for tests, Docker for both services, Railway for hosting (EU region, because Binance
blocks US servers).

## Quality

- Tests run on every push (GitHub Actions): 29 Python tests, including the live-price cache and the
  "Binance is down" fallback; 9 website tests for the form checks, response handling, number
  formatting and the news-mood summary.
- The news-mood summary only uses hours that have **finished**, so it never shows a value from the future.
- CI also builds the website and checks the typed client matches the API contract.

## Live URLs

- Website: _(added once the Railway services are created, see `docs/DEPLOY_RAILWAY.md`)_
- API docs: _(same)_ `/docs`
- Recorder health: https://recorder-production-796f.up.railway.app/health

## Demo script (3 minutes)

1. Open the website on a phone. **Risk Radar**: point at the three live prices ("updated 2s ago"),
   then the News Mood panel (M3) and the two "coming soon" cards: "this is how unfinished parts look".
2. Tap **Check a trade in BTC**. The entry is already the live price. Keep 10x long, $100 margin, 24h.
   Press **Check risk**: liquidation about 9.6% below entry, a small chance of liquidation, max safe
   leverage around 13x, fees $1. Read one line of "Why".
3. Change to **Short**, leverage **50x**, stop above entry a few % away. Show the **high risk** badge
   and the red warning that the stop is beyond the liquidation price.
4. Type a long with a stop **above** the entry: the form explains the mistake before calling the API.
5. **Data Health**: 3.6 million minutes of BTC since 2020; open the known-issues list. Tap ETH to show
   the placeholder.
6. **Live Status**: the recorder's streams and when each last received data.
7. Toggle **dark/light** theme.

## What's next for M2

- Phase 5: live dashboard with prices over WebSocket and regime-coloured charts; accounts and profiles.
- Phase 7: full Risk Check UI with explanation charts and saved history; the LLM trade parser
  ("long 0.1 BTC at 10x") as an alternative to the form.
- Phase 9: public Track Record page (wireframe ready), end-to-end tests with Playwright.

## Not done or not verified (Phase 2)

- **Railway deployment is waiting on a Railway login.** Code, Dockerfiles and per-service configs are
  ready; the 10-minute click-through is in `docs/DEPLOY_RAILWAY.md`. Until then the phone test of the
  live URL has not been done.
- `docker compose up` was not run: Docker is not installed on the development PC. The same
  Dockerfiles are what Railway builds. The web image's production build was tested locally.
- Wireframes are static HTML, not Figma: the connected Figma account has a view-only seat, so we
  could not create design files. The working website itself serves as the clickable prototype.
- The News Mood panel was tested with sample data in the agreed shape; it shows real data once
  Phase 3's `/news-mood` endpoint is merged.
- The volatility in Risk Check is still a labelled placeholder until the forecaster (Phase 6).
