# Position Guardian: walkthrough

Plain English. The page is at `/guardian`. The code is `regimecast/guardian/`, `regimecast/notify/`,
`regimecast/api/guardian.py` and `web/src/components/guardian.tsx`. Tests: `tests/test_guardian.py`.

## What it is

The Risk Check judges a trade once, before you open it. After that, the market moves and the risk changes. The
Guardian keeps checking your open positions every minute and tells you when the risk has changed, on the page, in the
bell, and on your phone through Telegram. Each message comes with the closest safer version of the position: how much
margin to add, or how small to make the position, to bring the risk back within your limit.

## Where the positions come from

| Source | What you give | What RegimeCast can do with it |
|---|---|---|
| Hyperliquid | Your wallet address | Read your positions. On Hyperliquid every account's positions are public, so no key is needed at all. |
| Binance | A **read-only** API key | Read your futures positions. Nothing else: a key that can trade, withdraw or transfer is refused. |
| By hand | Coin, side, entry, leverage, margin | Nothing to connect. Useful for any exchange, or to try the Guardian out. |

**Why a read-only key is safe to hand over.** Before a Binance key is stored, RegimeCast asks Binance what the key may
do (`apiRestrictions`). It is accepted only if "Enable Reading" is on and every trading, withdrawal and transfer
permission is off. The same check runs again every 6 hours: if someone has since given the key more rights, it is
deleted at once and you are told. The key is stored encrypted (Fernet) with a server secret that is not in the
database, never written to a log, and never sent back to the browser. Removing the source deletes it immediately.
On Binance's test network that endpoint does not exist, so there RegimeCast asks Binance to check a test order
(`/fapi/v1/order/test`, which never places anything): a read-only key is refused for permission, a trading key is not.

## What it checks each minute

For each position, from the current mark price (for Hyperliquid and Binance, the exchange's own mark and liquidation
price):

1. **Chance of liquidation within 24 h.** The Risk Check's model: FHS on the live HAR-S volatility forecast.
2. **Distance to liquidation**, in % and in *typical one-hour moves* (the 1 h forecast). "2 typical moves away" means
   an ordinary hour or two going the wrong way would reach it.
3. **Stress**: the stress gauge's level for the next hour.

Only BTC, ETH and SOL are judged, because the models cover those three. Other coins are listed and marked as not
covered.

## When it alerts

| Alert | Fires when | Waits until |
|---|---|---|
| Risk above your limit | The 24 h chance goes over your risk limit (account page, or the position's own limit) | It falls to 80% of the limit |
| Stress high | Stress turns high while you hold a long at 2x or more | Stress is below high again |
| Near liquidation | The price comes within 2 typical one-hour moves of liquidation | It is 2.5 moves away again |

Each alert fires **once**, then waits until its condition has cleared before it can fire again (the rule from the
Phase 11 alerts), so a position sitting near its limit does not send a message every minute. A muted position is still
shown with its risk but sends nothing. A hand-entered position whose liquidation price the market has already passed
gets one plain message saying so, with no "fix".

## The safer version

The position as it stands now carries the same risk as a trade opened right now, at the mark price, with whatever
leverage gives the same liquidation price ("leverage from now"). RegimeCast runs Phase 12's "fix my trade" on that
trade and lowers the leverage until the 24 h chance is within your limit and liquidation is at least 2.5 typical moves
away. It then says it two ways: **add $Y margin** (same size), or **reduce the position to $X** (same margin). On a
stress alert it uses half your limit, because the model is known to understate the risk for longs on crash days
(Phase 5).

## Telegram

Press "Connect Telegram" on the page and open the link on your phone: it opens the RegimeCast bot, and tapping Start
links the chat to your account. The link works once and for 10 minutes. After that, every alert (Guardian alerts and
your other alerts) is sent there too, at most 20 an hour. You can pause it, send a test message, or unlink at any
time; if you block the bot, RegimeCast unlinks the chat by itself.

## Example message

> RegimeCast Guardian
> BTC 10x long (entered by hand): liquidation risk above your limit
>
> Chance of liquidation within 24 h: 3.1% (your limit 1.0%).
> Liquidation at 68,410, 9.6% below the mark price 75,700, about 7.4 typical hourly moves.
> Safer version: add $412 margin (same size), or reduce the position to $4,380 (0.058 BTC) and keep the margin in it.
> Either one takes it from about 10x to 6x from here: chance of liquidation within 24 h 0.9%, liquidation at 63,240.
> Odds from RegimeCast's liquidation model on the live volatility forecast. Not financial advice.

(The numbers are illustrative; the real ones come from the live forecast.)

## Limits, honestly

- It checks once a minute, so a crash inside one minute can liquidate a position before any message goes out. It is a
  warning system, not a stop-loss.
- The chance is the Risk Check's model, with the same tested accuracy and the same known weakness on crash days.
- Telegram can be slow or down; the in-app bell does not depend on it.
- One API process runs the loop. If the API restarts, the first check comes about 30 seconds after it is back.
