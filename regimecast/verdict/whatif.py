"""What-if strip: what the trade gains or loses, in dollars, on a normal day, a bad day and a crash day.

* normal day / bad day: the 90th / 99th percentile of the size of the 24 h move of the mark price on the dev
  period, in the regime the market is in now (frozen per coin and regime by scripts/run_phase12.py). Shown both
  ways: if the price goes your way and if it goes against you. Until the table is frozen, the sizes come from
  today's volatility forecast (1.64 and 2.58 typical moves, the same percentiles of a normal distribution).
* crash day: a fall as large as on 10 October 2025 (CRASH_DAY: reported figures, fixed numbers; no code reads
  the final test period).

Each outcome follows the price straight to the day's end: against you, the stop exits first if it sits before the
liquidation price, else the position is liquidated (all its margin). Fees for opening and closing are included;
funding is not (a few cents to dollars a day).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

QUANTILES = {"normal": 0.90, "bad": 0.99}
FALLBACK_MOVES = {"normal": 1.6449, "bad": 2.5758}     # the same percentiles of |N(0, 1)|, in typical moves
REGIMES = ("calm", "active", "stressed")

# A fall as large as on 10 October 2025, per coin (fraction of the price): Binance's MARK price (what liquidates),
# highest 20:00-20:50 UTC to the lowest of 10-11 Oct (21:20-21:21 UTC), from Binance's public mark-price candles
# (fapi/v1/markPriceKlines), looked up once by hand on 28 Oct 2026. Fixed numbers, a scenario size only: no code
# reads the final test period, and nothing is fitted or scored on them.
CRASH_DAY: dict[str, dict] = {
    s: {"drop": d, "basis": f"Binance mark price on 10 Oct 2025: {a:,} to {b:,} in about 30 minutes"}
    for s, d, a, b in (("BTCUSDT", 0.1302, 117_128.3, 101_877.69), ("ETHUSDT", 0.1483, 4_022.00, 3_425.34),
                       ("SOLUSDT", 0.3070, 207.56, 143.83))
}
CRASH_LABEL = "Crash day like 10 Oct 2025"


def move_table(P: pd.DataFrame) -> dict:
    """{regime: {"normal": q90, "bad": q99, "n": hours}} of |24 h log move| (P: dev-period panel)."""
    out = {}
    a = P["fwd_24h"].abs()
    for i, name in enumerate(REGIMES):
        x = a[P["regime"] == i].dropna()
        if len(x) >= 100:
            out[name] = {k: float(x.quantile(q)) for k, q in QUANTILES.items()} | {"n": int(len(x))}
    x = a.dropna()
    out["all"] = {k: float(x.quantile(q)) for k, q in QUANTILES.items()} | {"n": int(len(x))}
    return out


def pnl(side: str, entry: float, qty: float, move: float, fees: float, stop: float | None,
        liquidation: float | None, liquidation_loss: float) -> tuple[float, str | None]:
    """Dollar result if the mark price moves by ``move`` (log) and the position is closed at the day's end."""
    s = 1 if side == "long" else -1
    end = entry * np.exp(move)
    passed = lambda level: level is not None and np.isfinite(level) and s * (end - level) <= 0   # noqa: E731
    stop_first = stop is not None and (liquidation is None or not np.isfinite(liquidation) or s * (stop - liquidation) > 0)
    if s * (end - entry) < 0:
        if stop_first and passed(stop):
            return -(qty * abs(entry - stop) + fees), "stop"
        if passed(liquidation):
            return -liquidation_loss, "liquidation"
    return s * qty * (end - entry) - fees, None


@dataclass
class Row:
    scenario: str            # "normal", "bad", "crash"
    label: str
    move_pct: float          # size of the move, % of the price
    up_usdt: float | None    # result if the price goes up by that much (None for the crash day)
    up_exit: str | None
    down_usdt: float
    down_exit: str | None
    basis: str


def strip(coin_symbol: str, side: str, entry: float, qty: float, fees: float, stop: float | None,
          liquidation: float | None, liquidation_loss: float, regime: str | None, sigma_24h: float,
          table: dict | None) -> list[Row]:
    rows = []
    t = (table or {}).get(regime or "") or (table or {}).get("all")
    for name, label in (("normal", "Normal day"), ("bad", "Bad day")):
        if t:
            m = t[name]
            basis = (f"{int(100 * QUANTILES[name])}th percentile 24 h move "
                     f"{'in ' + ('an ' if regime[:1] in 'aeiou' else 'a ') + regime + ' market' if regime and regime in (table or {}) else 'on any day'}, 2022-2025")
        else:
            m = FALLBACK_MOVES[name] * sigma_24h
            basis = "from today's volatility forecast (the dev-period table is not fixed yet)"
        up, ue = pnl(side, entry, qty, m, fees, stop, liquidation, liquidation_loss)
        dn, de = pnl(side, entry, qty, -m, fees, stop, liquidation, liquidation_loss)
        rows.append(Row(name, label, 100 * (np.exp(m) - 1), up, ue, dn, de, basis))
    c = CRASH_DAY.get(coin_symbol)
    if c:
        dn, de = pnl(side, entry, qty, float(np.log(1 - c["drop"])), fees, stop, liquidation, liquidation_loss)
        rows.append(Row("crash", CRASH_LABEL, 100 * c["drop"], None, None, dn, de, c["basis"]))
    return rows
