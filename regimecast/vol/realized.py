"""Realised volatility measures from 1-minute prices.

Time convention used everywhere in RegimeCast:
    a value indexed at time t is KNOWN at time t (it only uses data up to t).
So hourly measures are labelled by the END of the hour.

Targets (what we forecast, made at origin t):
    y_1h(t)  = realised variance over (t, t+1h]   from 1-minute returns
    y_24h(t) = realised variance over (t, t+24h]  from 5-minute returns,
               averaged over the 5 possible 5-minute grids (subsampling)
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def minute_close(df: pd.DataFrame, max_ffill: int = 5) -> pd.Series:
    """Clean 1-minute close series on a full minute grid (small gaps forward-filled)."""
    s = df.drop_duplicates("ts", keep="last").set_index("ts")["close"].sort_index()
    grid = pd.date_range(s.index[0], s.index[-1], freq="1min")
    return s.reindex(grid).ffill(limit=max_ffill)


def _end_label_hourly(x: pd.Series, how: str = "sum") -> pd.Series:
    # A 1-minute bar opening at 10:59 closes at 11:00, so the return "at" 10:59
    # (open-time label) belongs to the hour ending 11:00.
    r = x.copy()
    r.index = r.index + pd.Timedelta(minutes=1)  # open-time -> close-time
    g = r.resample("1h", label="right", closed="right")
    return getattr(g, how)(min_count=1) if how == "sum" else getattr(g, how)()


def hourly_measures(close_1m: pd.Series) -> pd.DataFrame:
    """Hourly realised measures from 1-minute log returns (index = hour END)."""
    r = np.log(close_1m).diff()
    out = pd.DataFrame({
        "rv": _end_label_hourly(r ** 2),
        "rs_pos": _end_label_hourly((r.clip(lower=0)) ** 2),
        "rs_neg": _end_label_hourly((r.clip(upper=0)) ** 2),
        "bpv": _end_label_hourly((np.pi / 2) * r.abs() * r.abs().shift(1)),
        "rq": _end_label_hourly(r ** 4),
        "ret": _end_label_hourly(r),
        "n": _end_label_hourly(r.notna().astype(float)),
    })
    out["rq"] = out["n"] / 3 * out["rq"]
    out["jump"] = (out["rv"] - out["bpv"]).clip(lower=0)
    return out


def hourly_rv5_subsampled(close_1m: pd.Series) -> pd.Series:
    """Hourly sum of squared 5-minute returns, averaged over the 5 grid offsets."""
    logp = np.log(close_1m)
    parts = []
    for k in range(5):
        p = logp[logp.index.minute % 5 == k]
        r2 = p.diff() ** 2
        r2.index = r2.index + pd.Timedelta(minutes=1)
        parts.append(r2.resample("1h", label="right", closed="right").sum(min_count=1))
    return pd.concat(parts, axis=1).mean(axis=1)


def build_targets(close_1m: pd.Series) -> pd.DataFrame:
    """Hourly frame with measures (known at t) and future targets y_1h, y_24h."""
    h = hourly_measures(close_1m)
    h["rv5"] = hourly_rv5_subsampled(close_1m)
    h["y_1h"] = h["rv"].shift(-1)
    fut24 = h["rv5"][::-1].rolling(24, min_periods=24).sum()[::-1]
    h["y_24h"] = fut24.shift(-1)
    return h


def annualised_vol(var_per_hour: float | pd.Series) -> float | pd.Series:
    return np.sqrt(var_per_hour * 24 * 365)
