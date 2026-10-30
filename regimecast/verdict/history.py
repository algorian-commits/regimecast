"""The verdict's inputs hour by hour on past data, as the app would have known them (no look-ahead).

One row per hour end t, per coin:

* market: ``mark``, ``close``, ``ret_24h`` (traded price, as in the live features), ``ret_168h``,
  ``funding_rate``, ``ls_top``, ``oi_chg_24h``, ``volume_usd`` (features/build.py, all known at t);
* ``sigma24``: the 24 h volatility forecast made at t by HAR-S, the app's model, refitted every 90 days
  on the past only (models/harness.py, walk-forward from 2021-01);
* ``p_stress``, ``p_stress_3h``: Phase 6's walk-forward stress probability now and 3 hours earlier
  (calibrated; uncalibrated before its calibrator has a past, as in Phase 9);
* ``regime``, ``p_calm``, ``p_active``, ``p_stressed``: Phase 4's forward-filtered regimes;
* the crowd ranks (crowd.py);
* outcomes, never used as inputs: ``mae_long``, ``mae_short`` (worst mark-price move against a long /
  short over (t, t + 24 h], features/targets.py) and ``r24`` (log mark-price return over the next 24 h).

Without ``final=True`` everything stops before the final test period and outcomes that would reach into
it are blanked (t + 24 h must be before 2025-10-01).

Liquidation and stop probabilities use FHS with the same walk-forward schedule: every 90 days the table
of past worst moves / forecast (z) is rebuilt from hours whose outcome was complete before the block.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.agent.backtest import check_closed, holdout_start
from regimecast.data import store
from regimecast.models import harness, regimes
from regimecast.models.stress import LEVELS
from regimecast.risk import fhs_live
from regimecast.verdict import crowd
from regimecast.vol import har

REPORTS = config.DATA_DIR / "reports"
VOL_START = "2021-01-01"
REFIT_HOURS = 24 * 90
DEV_FILES = {"stress": "phase6_pred_{s}.parquet", "regime": "regimes_base_{s}.parquet"}
FINAL_FILES = {"stress": "final_stress_pred_{s}.parquet", "regime": "final_regimes_{s}.parquet"}
COLUMNS = ["ts", "mark", "close", "ret_24h", "funding_rate", "ls_top", "oi_chg_24h", "volume_usd", "rv", "rs_pos",
           "rs_neg", "bpv", "rq", "ret", "n", "jump", "rv5", "rv_24h", "rv_168h"]


def har_s_sigma(h: pd.DataFrame, t: pd.DataFrame, final: bool = False) -> pd.Series:
    """Walk-forward HAR-S 24 h forecast (std of the log return), as in the live app (vol/live.py)."""
    X = har.har_features(h, 24)
    y = t["y_24h"].reindex(X.index)
    var = harness.walk_forward(X, y, harness.BASELINES["har_s"], VOL_START, 24, refit_every_hours=REFIT_HOURS,
                               final=final)
    return np.sqrt(var.clip(lower=1e-12)).rename("sigma24")


def _stress(symbol: str, final: bool, rep) -> pd.Series:
    parts = [pd.read_parquet(rep / DEV_FILES["stress"].format(s=symbol))]
    if final:
        parts.append(pd.read_parquet(rep / FINAL_FILES["stress"].format(s=symbol)))
    st = pd.concat(parts)
    st = st[~st.index.duplicated(keep="last")].sort_index()
    return (st["gbm_cal"].fillna(st["gbm"]) if "gbm_cal" in st else st["p_stress"]).rename("p_stress")


def _regimes(symbol: str, final: bool, rep) -> pd.DataFrame:
    parts = [pd.read_parquet(rep / DEV_FILES["regime"].format(s=symbol))]
    if final:
        parts.append(pd.read_parquet(rep / FINAL_FILES["regime"].format(s=symbol)))
    rg = pd.concat(parts)
    rg = rg[~rg.index.duplicated(keep="last")].sort_index()
    out = rg[["p_calm", "p_active", "p_stressed"]].copy()
    out["regime"] = rg["regime"].map(lambda i: regimes.NAMES[int(i)] if pd.notna(i) else None)
    return out


def stress_level(p: pd.Series) -> pd.Series:
    return p.map(lambda v: next(name for th, name in LEVELS if v >= th) if pd.notna(v) else None)


def hourly(symbol: str, final: bool = False, reports=None) -> pd.DataFrame:
    rep = REPORTS if reports is None else reports
    h = store.features(symbol, "1h", columns=COLUMNS).drop_duplicates("ts").set_index("ts").sort_index()
    t = store.targets(symbol).drop_duplicates("ts").set_index("ts").sort_index().reindex(h.index)
    if not final:
        h, t = h[h.index < holdout_start()], t[t.index < holdout_start()]
    out = h[["mark", "close", "ret_24h", "funding_rate", "ls_top", "oi_chg_24h", "volume_usd"]].copy()
    out["ret_168h"] = np.log(h["close"] / h["close"].shift(168))
    out["sigma24"] = har_s_sigma(h, t, final).reindex(out.index)
    p = _stress(symbol, final, rep).reindex(out.index)
    out["p_stress"], out["p_stress_3h"] = p, p.shift(3)
    out["stress_level"] = stress_level(p)
    out = out.join(_regimes(symbol, final, rep))
    out = out.join(crowd.ranks(out))
    out["mae_long"], out["mae_short"] = t["mae_long_24h"], t["mae_short_24h"]
    nxt = out["mark"].reindex(out.index + pd.Timedelta(hours=24)).to_numpy()
    out["r24"] = np.log(nxt / out["mark"].to_numpy())
    if not final:
        late = out.index > holdout_start() - pd.Timedelta(hours=24)
        out.loc[late, ["mae_long", "mae_short", "r24"]] = np.nan
    out.index.name = "ts"
    check_closed(out.index, final)
    return out


# ------------------------------------------------------------------ walk-forward FHS
def fhs_tables(h: pd.DataFrame, start=VOL_START, refit_hours: int = REFIT_HOURS) -> list[tuple[pd.Timestamp, dict]]:
    """[(block start, {"long": entry, "short": entry})]: FHS z-quantiles from hours whose 24 h outcome was
    complete before the block (t + 24 h <= block start), in fhs_live's format."""
    z_ok = h["sigma24"].notna() & (h["sigma24"] > 0)
    out, b = [], pd.Timestamp(start, tz="UTC") + pd.Timedelta(hours=refit_hours)
    while b <= h.index.max():
        past = z_ok & (h.index <= b - pd.Timedelta(hours=24))
        if past.sum() >= 24 * 60:
            out.append((b, {side: fhs_live.fit(h.loc[past, f"mae_{side}"] / h.loc[past, "sigma24"])
                            for side in ("long", "short")}))
        b += pd.Timedelta(hours=refit_hours)
    return out


def survival_vec(entry: dict, d: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    """fhs_live.survival for arrays."""
    d, sigma = np.asarray(d, float), np.asarray(sigma, float)
    k = np.where(sigma > 0, d / np.where(sigma > 0, sigma, 1), np.inf)
    z, lv = np.array(entry["z"]), np.array(entry["levels"])
    floor = 0.5 / (entry["n"] + 1)
    p = np.maximum(1 - np.interp(k, z, lv), floor)
    p = np.where(k >= z[-1], floor, p)
    p = np.where(d <= 0, 1.0, p)
    return np.where(np.isfinite(d) & (sigma > 0), p, np.where(d <= 0, 1.0, 0.0))


def block_of(index: pd.DatetimeIndex, tables: list) -> np.ndarray:
    """Which table each hour uses (-1 = none yet): the last block that started at or before it."""
    starts = np.array([b.value for b, _ in tables])
    return np.searchsorted(starts, index.as_unit("ns").asi8, side="right") - 1
