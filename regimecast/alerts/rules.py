"""Alert rules (M12): when an alert's condition holds, and when it has cleared.

Each alert is a small state machine with one flag, ``armed``:

* armed and the condition holds  -> it FIRES once (one inbox message) and is disarmed;
* disarmed and the condition has CLEARED -> it is armed again, ready for the next time;
* anything else -> nothing changes (no repeat messages while the condition lasts).

A condition is three-valued: True (holds), False (cleared) or None (unknown: no data,
or inside the small band around a level where it neither holds nor has cleared, so a
price wobbling around the level cannot fire every minute).

The first check of a new alert sets the starting state. A price alert starts disarmed if
the price is already past its level: it waits for a real crossing. A state alert
(stress, regime, position risk) fires on its first check if the state is already there.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from regimecast.risk import fhs_live
from regimecast.risk import liquidation as liq

PRICE_CLEAR_BAND = 0.001    # a price alert re-arms once the price is 0.1% back on the other side
RISK_CLEAR_SHARE = 0.8      # a position alert re-arms once the chance is below 80% of the limit
STATE_KINDS = ("stress_elevated", "stress_high", "regime_stressed", "position_risk")
PRICE_KINDS = ("price_above", "price_below")
STRESS_RANK = {"low": 0, "elevated": 1, "high": 2}


@dataclass
class Check:
    state: bool | None          # True holds, False cleared, None unknown / in between
    value: float | None = None  # the number compared (price, probability), stored with the alert
    title: str = ""
    message: str = ""


def step(armed: bool | None, state: bool | None, kind: str) -> tuple[bool, bool | None]:
    """(fire?, new armed flag) for one check."""
    if armed is None:                       # first check of a new alert
        if state is None:
            return False, None
        if kind in PRICE_KINDS:
            return False, not state         # already past the level: wait for a crossing
        return (True, False) if state else (False, True)
    if armed and state is True:
        return True, False
    if not armed and state is False:
        return False, True
    return False, armed


def coin(symbol: str) -> str:
    return symbol.removesuffix("USDT")


def fmt_price(p: float) -> str:
    return f"{p:,.2f}" if p < 1000 else f"{p:,.0f}"


def price_check(kind: str, level: float, price: float | None, symbol: str) -> Check:
    if price is None:
        return Check(None)
    above = kind == "price_above"
    if (price >= level) if above else (price <= level):
        state = True
    elif (price < level * (1 - PRICE_CLEAR_BAND)) if above else (price > level * (1 + PRICE_CLEAR_BAND)):
        state = False
    else:
        state = None
    word = "above" if above else "below"
    return Check(state, price, f"{coin(symbol)} crossed {word} {fmt_price(level)}",
                 f"The {coin(symbol)} mark price is {fmt_price(price)} USDT, {word} your level of "
                 f"{fmt_price(level)}.")


def stress_check(kind: str, stress: dict | None, symbol: str) -> Check:
    if not stress:
        return Check(None)
    need = 2 if kind == "stress_high" else 1
    rank = STRESS_RANK[stress["level"]]
    p, base = stress["p_stress_next_hour"], stress.get("base_rate")
    normally = f" (normally {100 * base:.0f}%)" if base else ""
    return Check(rank >= need, p, f"{coin(symbol)} stress is {stress['level']}",
                 f"{100 * p:.0f}% chance of a market stress event on {coin(symbol)} in the next hour{normally}. "
                 f"Stress means a burst of volatility, not a direction.")


def regime_check(regime: dict | None, symbol: str) -> Check:
    if not regime:
        return Check(None)
    p = regime.get("p_stressed")
    return Check(regime["regime"] == "stressed", p, f"{coin(symbol)} regime turned stressed",
                 f"The market regime for {coin(symbol)} is now stressed (probability {100 * (p or 0):.0f}%). "
                 f"After stressed hours, the next day has usually been much more volatile.")


def position_from_trade(trade: dict) -> liq.Position:
    """The position a saved risk check describes (the body sent to /risk-check)."""
    entry, lev = float(trade["entry"]), float(trade["leverage"])
    qty = trade.get("qty") or float(trade["margin"]) * lev / entry
    return liq.Position(symbol=trade["symbol"], side=trade["side"], entry=entry, qty=float(qty), leverage=lev,
                        margin_mode=trade.get("margin_mode", "isolated"), wallet_balance=trade.get("wallet_balance"))


def position_risk(trade: dict, price: float, sigma: float, fhs: dict) -> tuple[float, float, float] | None:
    """(P(liquidation) over the horizon from the CURRENT price, liquidation price, distance in %)
    for an open position; FHS on the live HAR-S volatility, as in the Risk Check."""
    pos = position_from_trade(trade)
    lp = liq.liquidation_price(pos)
    if not (math.isfinite(lp) and lp > 0):
        return 0.0, math.nan, math.inf
    h = int(trade.get("horizon_hours", 24))
    entry = fhs.get(pos.symbol, {}).get(f"{h}h", {}).get(pos.side)
    d = math.log(price / lp) if pos.side == "long" else math.log(lp / price)
    p = (fhs_live.survival(entry, d, sigma) if entry else liq.first_passage_prob(d, sigma)) if d > 0 else 1.0
    pct = 100 * ((1 - lp / price) if pos.side == "long" else (lp / price - 1))
    return p, lp, pct


def risk_check(trade: dict, limit: float, price: float | None, sigma: float | None, fhs: dict) -> Check:
    if price is None or not sigma:
        return Check(None)
    p, lp, pct = position_risk(trade, price, sigma, fhs)
    if p > limit:
        state = True
    elif p <= RISK_CLEAR_SHARE * limit:
        state = False
    else:
        state = None
    h = int(trade.get("horizon_hours", 24))
    name = f"{coin(trade['symbol'])} {float(trade['leverage']):g}x {trade['side']} from {fmt_price(float(trade['entry']))}"
    where = (f" Liquidation at {fmt_price(lp)} USDT, {max(pct, 0):.1f}% from the current price "
             f"{fmt_price(price)}." if math.isfinite(lp) else "")
    return Check(state, p, f"Your {name}: liquidation risk above your limit",
                 f"Chance of liquidation within {h} h is {100 * p:.1f}%, above your limit of {100 * limit:.1f}%.{where}")
