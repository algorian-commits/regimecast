# M5 Live Dashboard and M6 Accounts: walkthrough

Built in Phase 8 of `docs/PLAN.md`. One page, plain English, for presenting.

## What they are

* **M5, the live dashboard (Risk Radar, the home page).** One screen that shows what the market is doing right
  now: prices that move every second, a price chart whose background shows the market's mood (calm, active or
  stressed), how tight the order book is, and our three model readings (regime, volatility, stress) plus the news
  mood.
* **M6, accounts.** You can sign up with an e-mail and password, keep a **paper** (pretend) balance and a risk
  limit, pick favourite coins and a default margin mode, and keep your own saved risk checks and alerts. Nobody
  else can see them.

## The dashboard, piece by piece

| Part | What you see | Where it comes from |
|---|---|---|
| Live prices | BTC, ETH, SOL mark price every second, % move this hour, funding, open interest | Binance, through **our** server over a WebSocket |
| Regime chart | Hourly candles for 30 days (last 7 shown, drag for more). Background green / amber / red = calm / active / stressed | `/candles`: the M4 regime model, run on live Binance data |
| Order book (inside) | Spread in basis points, money waiting on the buy and sell side, who is heavier, a 5-minute line of both | Binance's top 20 price levels, twice a second |
| Regime gauge | Calm / active / stressed with how sure the model is | `/regime` (M4) |
| Volatility gauge | Expected movement over the next 24 h, against what is typical after each regime | `/forecast` (M7, HAR-S) |
| Stress gauge | Chance of a market stress event in the next hour, against a normal hour | `/stress` (M10) |
| News mood | The last 7 days of headline sentiment | `/news-mood` (M3) |

### How "live" works

```
 Binance (EU) ──2 WebSockets──► our API (Railway, EU) ──1 WebSocket per visitor, 1 message/second──► browser
                                  keeps the latest price, candle and order book per coin
```

1. Our API holds **one** connection to Binance for everyone (a hundred visitors cost one connection), and sends
   each browser one small snapshot per second. It runs in Europe because Binance blocks US servers.
2. If Binance drops the connection (it does every 24 hours), the API reconnects by itself and the page shows
   "Binance reconnecting…".
3. If the browser loses **our** connection (Wi-Fi drops, the server restarts), it reconnects by itself, waiting
   1, 2, 4, 8, then 15 seconds between tries. If no message arrives for 10 seconds it starts over. Tested: we
   stopped the API, the badge said "reconnecting…", we started it again, and it went back to "● live" with no
   reload.

### No hindsight in the chart

Each candle's background colour is the regime the model knew **when that hour closed**, never a later estimate.
A test changes all prices after a chosen hour and checks that no earlier candle or colour changes.

### Honest limits

* The order-book panel uses Binance's **top 20 levels**. For BTC those sit within about 0.3 basis points of the
  price, so it shows the inside of the book, not its full depth. The page says so.
* Regime, volatility and stress refresh every 10 minutes (they are hourly models); prices refresh every second.

## Accounts: how they stay safe

| Question | Answer |
|---|---|
| Where is the data? | PostgreSQL on Railway (EU), next to the API. |
| Is my password stored? | No. Only an **argon2id** hash (the current recommended method). It is never written to logs. A test checks the database and the logs. |
| How do I stay logged in? | A random 256-bit token in a cookie that page scripts cannot read (httpOnly), only sent over HTTPS (Secure) and not sent by other sites (SameSite=Lax). The database stores only a keyed fingerprint (HMAC) of the token, so a copy of the database alone cannot log anyone in. Sessions last 30 days; logging out ends that session. |
| Guessing passwords? | After 10 wrong passwords for one e-mail in 15 minutes, logins for it pause. The error never says whether the e-mail exists. |
| Can another user see my checks? | No. Every query is filtered by the logged-in user; tests make two users and check that one cannot list, read or delete the other's checks, alerts or profile. |
| Schema changes? | Versioned migrations in code (Alembic). The API applies them by itself the first time it uses the database after a deploy. |

### What the profile is for

* **Paper balance**: pretend money; the risk check uses it as the wallet for cross-margin trades.
* **Risk limit**: the highest chance of liquidation you accept per trade (default 1%). The risk check turns it
  into the safest leverage and the largest position for your margin.
* **Default margin mode** and **favourite coins** (favourites come first on the dashboard).
* **Saved checks** (`POST /me/checks`, `GET /me/checks` with filters by coin and date): the new Risk Check
  (Phase 7) saves to your account when you are logged in.
* **Alerts** (price above/below, stress elevated, regime stressed) are stored now and start firing with the
  alert engine in Phase 11.

## Quality

* Tests on every push (GitHub Actions): 19 new Python tests (sign-up, log-in, sessions, throttling, profile
  checks, data separation, the live feed and the candles), run on **real PostgreSQL** in CI as well as SQLite;
  12 new website tests (reconnect timing, order-book history, account forms, safe redirects after log-in).
* CI also builds the website and checks the typed API client matches the API contract.

## Demo script (4 minutes)

1. Open the website on a phone. **Risk Radar**: the "● live" badge and the prices ticking every second.
2. Point at the chart: green is calm, the red band is a stressed hour. "The colour is what the model knew at that
   hour, no hindsight." Drag left to show 30 days.
3. Order book: spread, the buy/sell bar moving, the 5-minute lines. Then the three gauges: "calm, 46% volatility a
   year, 3% stress chance, about normal."
4. Tap **Log in → Create an account** with a test e-mail. You land on **Your account**.
5. Set the paper balance to 2,500 and the risk limit to 2%, save. Tap **SOL** as a favourite.
6. Back to Risk Radar: the strip shows the balance and limit, and SOL is now second.
7. Add an alert ("BTC falls below 70,000"), then log out and log in as a second test user: their account is empty.
8. Switch dark/light.

## Not done or not verified

* Verified locally against live Binance (dashboard, reconnect, sign-up, profile, favourites, alerts, phone width,
  dark and light). Not yet on the live URL: it updates when this branch is merged into `main`.
* Accounts were tested on SQLite locally; PostgreSQL is tested in CI (no Docker on the development PC).
* The Risk Check page is Phase 7's: it starts using the risk limit, balance and saved checks after both
  branches are merged (`riskDefaults`, `saveCheck` in `web/src/lib/account.ts`).
* Alerts do not notify anyone yet (Phase 11). No "forgot password" e-mail and no account deletion yet (parking list).
