"""HAR-family baselines (Corsi 2009 and friends) at hourly origins.

Models (all fitted by OLS):
* ``har``    log-HAR:  log y = a + b1 log RV_short + b2 log RV_mid + b3 log RV_long
* ``har_s``  log-HAR + hour-of-day and day-of-week dummies (seasonality)
* ``har_j``  har_s + log(1 + jump share)
* ``harq``   levels HARQ (Bollerslev, Patton & Quaedvlieg 2016)
* ``har_x``  har_s + any extra columns you pass (funding, OI, spread, ...)

Log models are turned back into variance with Duan's smearing factor, so the
forecast is not biased low (important for QLIKE).

Component windows (in hours):
    1h target : 1, 24, 168        (hour, day, week)
    24h target: 24, 168, 720      (day, week, month)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOWS = {1: (1, 24, 168), 24: (24, 168, 720)}
EPS = 1e-12


def har_features(h: pd.DataFrame, horizon: int, rv_col: str | None = None) -> pd.DataFrame:
    """Build HAR inputs at each hourly origin. Uses only data known at t."""
    rv_col = rv_col or ("rv" if horizon == 1 else "rv5")
    w = WINDOWS[horizon]
    X = pd.DataFrame(index=h.index)
    for name, win in zip(("short", "mid", "long"), w):
        X[f"rv_{name}"] = h[rv_col].rolling(win, min_periods=win).mean() * horizon  # per-horizon scale
    X["rq_short"] = h["rq"].rolling(w[0], min_periods=w[0]).mean() * horizon if "rq" in h else np.nan
    if "jump" in h:
        X["jump_share"] = (h["jump"].rolling(w[0]).sum() / (h["rv"].rolling(w[0]).sum() + EPS)).clip(0, 1)
    if "rs_neg" in h:
        X["neg_share"] = (h["rs_neg"].rolling(w[0]).sum() / (h["rv"].rolling(w[0]).sum() + EPS)).clip(0, 1)
    # seasonality of the period we forecast (the hour right after t)
    nxt = h.index + pd.Timedelta(hours=1)
    X["hour"] = nxt.hour
    X["dow"] = nxt.dayofweek
    return X


def _design(X: pd.DataFrame, variant: str, extra: list[str]) -> pd.DataFrame:
    D = pd.DataFrame(index=X.index)
    D["const"] = 1.0
    if variant == "harq":
        D["rv_short"] = X["rv_short"]
        D["rv_short_q"] = X["rv_short"] * np.sqrt(X["rq_short"].clip(lower=0))
        D["rv_mid"] = X["rv_mid"]
        D["rv_long"] = X["rv_long"]
        return D
    for c in ("rv_short", "rv_mid", "rv_long"):
        D[f"log_{c}"] = np.log(X[c] + EPS)
    if variant in ("har_s", "har_j", "har_x"):
        for k in range(1, 24):
            D[f"h{k}"] = (X["hour"] == k).astype(float)
        for k in range(1, 7):
            D[f"d{k}"] = (X["dow"] == k).astype(float)
    if variant == "har_j":
        D["log1p_jump"] = np.log1p(X["jump_share"])
    if variant == "har_x":
        for c in extra:
            D[c] = X[c]
    return D


class HARModel:
    def __init__(self, variant: str = "har_s", extra: list[str] | None = None):
        if variant not in ("har", "har_s", "har_j", "harq", "har_x"):
            raise ValueError(variant)
        self.variant = variant
        self.extra = extra or []
        self.name = variant if variant != "har_x" else "har_x"

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "HARModel":
        D = _design(X, self.variant, self.extra)
        ok = D.notna().all(axis=1) & y.notna() & (y > 0)
        D, yy = D[ok], y[ok]
        self.cols = list(D.columns)
        if self.variant == "harq":
            self.beta, *_ = np.linalg.lstsq(D.to_numpy(), yy.to_numpy(), rcond=None)
            self.lo, self.hi = float(yy.min()), float(yy.max())  # "insanity filter"
            return self
        ly = np.log(yy.to_numpy())
        self.beta, *_ = np.linalg.lstsq(D.to_numpy(), ly, rcond=None)
        resid = ly - D.to_numpy() @ self.beta
        self.smear = float(np.mean(np.exp(resid)))  # Duan (1983) smearing
        return self

    def predict(self, X: pd.DataFrame) -> pd.Series:
        D = _design(X, self.variant, self.extra)[self.cols]
        raw = D.to_numpy() @ self.beta
        if self.variant == "harq":
            out = np.clip(raw, self.lo, self.hi)
        else:
            out = np.exp(raw) * self.smear
        return pd.Series(out, index=X.index, name=self.name)
