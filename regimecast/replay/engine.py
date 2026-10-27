"""Crash replay at run time (M12): the stored minutes of a crash, seen through the app.

The replay file (replay/build.py) holds the mark price per minute and the frozen models'
outputs per hour. This module adds the viewer's position: for a chosen side, leverage and
entry it computes, at every minute, what the Risk Check would have said about that open
position (liquidation price, distance, chance of liquidation over the horizon with FHS on
the HAR-S volatility of that hour), and when the mark price actually reached it.

Timing rule (no look-ahead): the minute bar opened at t closes at T = t + 1 min; the frame
for T shows that close and the hourly outputs of the last complete hour H <= T. Nothing
from after T is used, so a replay cut at T gives the same frames up to T (tests/test_replay.py).
"""
from __future__ import annotations

import gzip
import json
import math
from functools import lru_cache

import numpy as np
import pandas as pd

from regimecast.replay.build import EVENTS, EVENTS_DIR
from regimecast.risk import fhs_live
from regimecast.risk import liquidation as liq

DEFAULT_MARGIN = 1_000.0


def available() -> list[dict]:
    """Every known event, with whether its replay file has been generated."""
    out = []
    for eid, ev in EVENTS.items():
        f = EVENTS_DIR / f"{eid}.json.gz"
        out.append({"id": eid, "title": ev["title"], "summary": ev["summary"], "start": ev["start"],
                    "end": ev["end"], "available": f.exists()})
    return out


@lru_cache(maxsize=4)
def load(event_id: str) -> dict | None:
    f = EVENTS_DIR / f"{event_id}.json.gz"
    if event_id not in EVENTS or not f.exists():
        return None
    with gzip.open(f, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def hour_index(close_times: pd.DatetimeIndex, hour_ends: pd.DatetimeIndex) -> np.ndarray:
    """For each minute close T, the position of the last hour end H <= T (-1 if none)."""
    return np.searchsorted(hour_ends.asi8, close_times.asi8, side="right") - 1


def _ffill(a: list) -> np.ndarray:
    return pd.Series(a, dtype=float).ffill().to_numpy()


def position_path(low, high, close, sigma, entry_fhs: dict, side: str, liq_price: float) -> dict:
    """Minute by minute for an open position: distance to liquidation (log), P(liquidation)
    within the horizon, and whether the mark price has reached the liquidation price yet.

    ``sigma`` is the horizon volatility in force at each minute (from the last complete hour)."""
    n = len(close)
    dist = np.full(n, np.nan)
    p = np.full(n, np.nan)
    hit = (low <= liq_price) if side == "long" else (high >= liq_price)
    liquidated = np.maximum.accumulate(np.nan_to_num(hit, nan=0).astype(bool)) if n else np.zeros(0, bool)
    for i in range(n):
        if liquidated[i]:
            dist[i], p[i] = 0.0, 1.0
            continue
        if not np.isfinite(close[i]) or not np.isfinite(sigma[i]):
            continue
        d = math.log(close[i] / liq_price) if side == "long" else math.log(liq_price / close[i])
        dist[i] = d
        p[i] = fhs_live.survival(entry_fhs, d, sigma[i]) if math.isfinite(liq_price) else 0.0
    return {"distance": dist, "p": p, "liquidated": liquidated}


def frames(replay: dict, symbol: str, side: str, leverage: float, entry: float | None = None,
           horizon_hours: int = 24, risk_limit: float = 0.01, margin: float = DEFAULT_MARGIN,
           fhs: dict | None = None) -> dict:
    coin = replay["coins"][symbol]
    fhs = fhs if fhs is not None else fhs_live.load()
    start = pd.Timestamp(coin["minute_start"])
    n = coin["n_minutes"]
    closes_at = pd.date_range(start + pd.Timedelta(minutes=1), periods=n, freq="1min")
    hours = coin["hours"]
    hour_ends = pd.DatetimeIndex([pd.Timestamp(h["t"]) for h in hours])
    hidx = hour_index(closes_at, hour_ends)

    close = _ffill(coin["mark"]["close"])
    low, high = np.array(coin["mark"]["low"], float), np.array(coin["mark"]["high"], float)
    entry = float(entry) if entry else float(close[np.isfinite(close)][0])
    qty = margin * leverage / entry
    pos = liq.Position(symbol=symbol, side=side, entry=entry, qty=qty, leverage=leverage)
    lp = liq.liquidation_price(pos)

    key = f"sigma_{horizon_hours}h"
    sig_h = np.array([h[key] if h[key] is not None else np.nan for h in hours], float)
    sigma = np.where(hidx >= 0, sig_h[np.clip(hidx, 0, None)], np.nan)
    entry_fhs = fhs[symbol][f"{horizon_hours}h"][side]
    path = position_path(low, high, close, sigma, entry_fhs, side, lp)
    first_liq = int(np.argmax(path["liquidated"])) if path["liquidated"].any() else None

    # A new trade opened at each hour's price: the Risk Check's highest safe leverage then.
    safe = []
    for h, s_h in zip(hours, sig_h):
        i = int(np.searchsorted(closes_at.asi8, pd.Timestamp(h["t"]).value, side="right")) - 1
        if i < 0 or not np.isfinite(s_h):
            safe.append(None)
            continue
        p0 = liq.Position(symbol=symbol, side=side, entry=float(close[i]), qty=margin * leverage / float(close[i]),
                          leverage=leverage)

        def prob(p_, s=s_h):
            return fhs_live.survival(entry_fhs, liq.adverse_log_distance(p_, liq.liquidation_price(p_)), s)
        safe.append(liq.max_safe_leverage(prob, p0, risk_limit))

    def clean(a, nd):
        return [None if not np.isfinite(v) else round(float(v), nd) for v in a]

    return {
        "event": {k: replay[k] for k in ("id", "title", "summary", "start", "end", "marks", "models")},
        "symbol": symbol, "side": side, "leverage": leverage, "entry": round(entry, 4), "margin": margin,
        "horizon_hours": horizon_hours, "risk_limit": risk_limit,
        "liquidation_price": round(lp, 4) if math.isfinite(lp) and lp > 0 else None,
        "liquidated_at": closes_at[first_liq].isoformat() if first_liq is not None else None,
        "first_close": closes_at[0].isoformat(), "n_minutes": n,
        "minutes": {
            "price": clean(close, 4),
            "low": clean(low, 4), "high": clean(high, 4),
            "p_liquidation": clean(path["p"], 5),
            # the move still needed to reach liquidation, in % of the current price
            "distance_pct": clean(100 * ((1 - np.exp(-path["distance"])) if side == "long"
                                         else (np.exp(path["distance"]) - 1)), 3),
            "hour": [int(i) for i in hidx],
        },
        "hours": [{"t": h["t"], "regime": h["regime"], "p_stressed": h["p_stressed"], "p_stress": h["p_stress"],
                   "stress_level": h["stress_level"], "stress_drivers": h["stress_drivers"],
                   "p_stress_walk_forward": h["p_stress_walk_forward"],
                   "sigma_pct": None if not np.isfinite(s) else round(100 * float(s), 3),
                   "safe_leverage": sl} for h, s, sl in zip(hours, sig_h, safe)],
        "stress_base_rate": coin.get("stress_base_rate"),
    }
