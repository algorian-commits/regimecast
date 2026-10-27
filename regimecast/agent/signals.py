"""What the agent reads: market data for the account, and RegimeCast's signals at each hour.

Every signal is a walk-forward, out-of-sample output of an earlier phase, stamped at the
hour end H when it was known, and never fitted on the hours it is used for:

* ``vol_ann``: the 24 h volatility forecast made at H (HAR-S + LightGBM combination, M7,
  ``phase5_liq_<coin>_24h.parquet`` column ``sigma``), annualised: sigma x sqrt(365);
* ``p_stress``: the calibrated probability of a stress event in (H, H + 1 h] (M10 part 1,
  ``phase6_pred_<coin>.parquet``; before its calibrator has a past, Jan-Jun 2022, the
  uncalibrated out-of-sample probability);
* ``p_stressed`` and ``regime``: the forward-filtered regime at H (M4, ``regimes_base_<coin>.parquet``);
* ``trail_vol_ann``: the last week's realised volatility, for the plain volatility-targeting
  baseline (no RegimeCast model).

A signal missing for a few hours is carried forward from its last value (never backward).
The final evaluation reads the same columns from the files Phase 10 writes for the final test
period (``final_liq_<coin>_24h.parquet``, ``final_stress_pred_<coin>.parquet``,
``final_regimes_<coin>.parquet``); see final.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.agent.backtest import FUNDING_HOURS, check_closed, holdout_start
from regimecast.data import store

REPORTS = config.DATA_DIR / "reports"
DEV_FILES = {"vol": "phase5_liq_{s}_24h.parquet", "stress": "phase6_pred_{s}.parquet", "regime": "regimes_base_{s}.parquet"}
FINAL_FILES = {"vol": "final_liq_{s}_24h.parquet", "stress": "final_stress_pred_{s}.parquet",
               "regime": "final_regimes_{s}.parquet"}
FILL_HOURS = {"vol_ann": 24, "p_stress": 3, "p_stressed": 3, "regime": 3, "trail_vol_ann": 3}


def _cut(df: pd.DataFrame, final: bool) -> pd.DataFrame:
    return df if final else df[df.index < holdout_start()]


def market(symbols: list[str], final: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Hourly mark price at each hour end, and the funding rate charged at each settlement.

    Funding comes from Binance's archived settlements (store.funding), stamped a few
    milliseconds after the hour, so each is charged at its hour. This includes the
    off-schedule ones (SOL settled every 2 hours from 10 to 18 Nov 2022). Without the raw
    file, the hourly feature table is used: it joins funding "as of" each hour end, so the
    rate charged at T shows in the row for T + 1 h (equal to the raw BTC file at 99.99% of
    settlements); that fallback only sees the 00/08/16 UTC settlements."""
    marks, funds = {}, {}
    for s in symbols:
        h = store.features(s, "1h", columns=["ts", "mark", "funding_rate"]).set_index("ts").sort_index()
        h = _cut(h, final)
        marks[s] = h["mark"]
        rate_at = h["funding_rate"].shift(-1)        # value known one row later = rate charged at T
        f = rate_at.where(h.index.hour.isin(FUNDING_HOURS))
        raw = store.funding(s)
        if len(raw):
            r = raw.assign(hour=raw["ts"].dt.floor("1h")).groupby("hour")["funding_rate"].last()
            covered = h.index <= r.index.max()       # after the raw file ends: the feature table
            f = r.reindex(h.index).where(covered, f)
        funds[s] = f
    mark = pd.DataFrame(marks)
    check_closed(mark.index, final)
    return mark, pd.DataFrame(funds)


def load(symbol: str, final: bool = False, reports=None) -> pd.DataFrame:
    """Signals for one coin, one row per hour end (see the module docstring)."""
    rep = REPORTS if reports is None else reports
    names = {k: v.format(s=symbol) for k, v in (FINAL_FILES if final else DEV_FILES).items()}
    v = pd.read_parquet(rep / names["vol"], columns=["ts", "sigma"]).drop_duplicates("ts").set_index("ts")["sigma"]
    st = pd.read_parquet(rep / names["stress"])
    p = st["gbm_cal"].fillna(st["gbm"]) if "gbm_cal" in st else st["p_stress"]
    rg = pd.read_parquet(rep / names["regime"])
    h = store.features(symbol, "1h", columns=["ts", "rv_168h"]).set_index("ts")["rv_168h"]
    out = pd.DataFrame({"vol_ann": np.sqrt(365.0) * v, "p_stress": p,
                        "p_stressed": rg["p_stressed"], "regime": rg["regime"].astype(float),
                        "trail_vol_ann": np.sqrt(h * config.HOURS_PER_YEAR)}).sort_index()
    out = _cut(out, final)
    if len(out):
        grid = pd.date_range(out.index.min(), out.index.max(), freq="1h")
        out = out.reindex(grid)
        for c, lim in FILL_HOURS.items():
            out[c] = out[c].ffill(limit=lim)
    out.index.name = "ts"
    check_closed(out.index, final)
    return out


def load_all(symbols: list[str], final: bool = False, reports=None) -> dict[str, pd.DataFrame]:
    return {s: load(s, final, reports) for s in symbols}
