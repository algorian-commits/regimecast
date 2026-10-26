"""How good is the news mood? Two checks.

1. Accuracy against hand labels (``accuracy``): accuracy, macro F1 and a confusion
   matrix per model.
2. Against price (``event_study``, ``lead_lag``): around big BTC moves, does the mood change
   in the hours BEFORE the move, in the SAME hour, or only AFTER it?

Every mood measure at hour end t uses only headlines known before t, and "abnormal"
values compare an hour with the hours before it (never after). Big moves are defined
with volatility from the past only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CLASSES = ["positive", "negative", "neutral"]


# ------------------------------------------------------------------ 1. accuracy
def accuracy(truth: pd.Series, pred: pd.Series) -> dict:
    t, p = truth.reset_index(drop=True), pred.reset_index(drop=True)
    out = {"n": int(len(t)), "accuracy": float((t == p).mean())}
    f1s = []
    for c in CLASSES:
        tp = int(((p == c) & (t == c)).sum())
        prec = tp / max(int((p == c).sum()), 1)
        rec = tp / max(int((t == c).sum()), 1)
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[f"recall_{c}"], out[f"precision_{c}"] = rec, prec
        f1s.append(f1)
    out["macro_f1"] = float(np.mean(f1s))
    return out


def confusion(truth: pd.Series, pred: pd.Series) -> pd.DataFrame:
    return (pd.crosstab(truth.rename("label"), pred.rename("model"))
              .reindex(index=CLASSES, columns=CLASSES, fill_value=0))


# ------------------------------------------------------------------ 2. against price
def hourly_close(bars: pd.DataFrame) -> pd.Series:
    """1-minute bars (ts = bar open) -> close at each hour END (the last minute's close)."""
    s = bars.set_index("ts")["close"]
    return s.resample("h", label="right", closed="left").last()


def abnormal(x: pd.Series, window: int = 168, min_periods: int = 48) -> pd.Series:
    """(x - mean of the previous ``window`` hours) / their std. Uses the past only."""
    past = x.shift(1).rolling(window, min_periods=min_periods)
    sd = past.std().replace(0, np.nan)
    return (x - past.mean()) / sd


def big_moves(close: pd.Series, z: float = 4.0, window: int = 720) -> pd.DataFrame:
    """Hours whose log return is at least ``z`` times the past 30-day hourly volatility.
    Returns ts (hour end), ret, zscore, direction. Hours right after a bigger move in the
    same direction (within 3 hours) are folded into it, so one crash counts once."""
    r = np.log(close).diff()
    sd = r.shift(1).rolling(window, min_periods=window // 2).std()
    zs = r / sd
    ev = pd.DataFrame({"ret": r, "zscore": zs}).dropna()
    ev = ev[ev["zscore"].abs() >= z].copy()
    ev["direction"] = np.where(ev["ret"] > 0, "up", "down")
    keep, last = [], {}
    for t, d in zip(ev.index, ev["direction"]):
        if d in last and t - last[d] <= pd.Timedelta(hours=3):
            last[d] = t
            continue
        last[d] = t
        keep.append(t)
    return ev.loc[keep].rename_axis("ts").reset_index()


def event_study(features: pd.DataFrame, events: pd.Series, lags=range(-6, 7), n_boot: int = 2000,
                seed: int = 0) -> pd.DataFrame:
    """Average of each feature at hour end (event + lag hours). Lag 0 = the hour of the move,
    -1 = the hour before it, +1 = the hour after. With a bootstrap 90% interval over events,
    and the average over ALL hours as the baseline."""
    rng = np.random.default_rng(seed)
    rows = []
    idx = features.index
    for lag in lags:
        at = pd.DatetimeIndex(events) + pd.Timedelta(hours=lag)
        sub = features.reindex(at)
        for col in features.columns:
            v = sub[col].dropna().to_numpy()
            if len(v) == 0:
                continue
            boots = rng.choice(v, size=(n_boot, len(v))).mean(axis=1)
            rows.append({"lag_hours": lag, "feature": col, "mean": v.mean(), "n": len(v),
                         "lo90": np.quantile(boots, 0.05), "hi90": np.quantile(boots, 0.95),
                         "baseline": features.loc[idx, col].mean()})
    return pd.DataFrame(rows)


def lead_lag(feature: pd.Series, target: pd.Series, lags=range(-6, 7)) -> pd.DataFrame:
    """corr(feature at t + lag, target at t). Negative lags = the feature moves first."""
    df = pd.concat({"f": feature, "y": target}, axis=1)
    return pd.DataFrame([{"lag_hours": k, "corr": df["f"].shift(-k).corr(df["y"])} for k in lags])


def predictive_test(features: pd.DataFrame, absret: pd.Series, n_lags: int = 24) -> pd.DataFrame:
    """Does the mood at hour end t help predict |return| in the NEXT hour, beyond the past
    24 hours of |return|? OLS with Newey-West errors. One row per mood feature."""
    import statsmodels.api as sm

    base = pd.DataFrame({"y": absret.shift(-1), "abs_r": absret,
                         "abs_r_24h": absret.rolling(n_lags).mean()})
    rows = []
    for col in features.columns:
        d = base.join(features[col]).dropna()
        x = sm.add_constant(d[["abs_r", "abs_r_24h", col]])
        fit = sm.OLS(d["y"], x).fit(cov_type="HAC", cov_kwds={"maxlags": n_lags})
        r0 = sm.OLS(d["y"], x.drop(columns=[col])).fit()
        rows.append({"feature": col, "coef": fit.params[col], "t_stat": fit.tvalues[col],
                     "p_value": fit.pvalues[col], "r2_without": r0.rsquared, "r2_with": fit.rsquared,
                     "n_hours": int(fit.nobs)})
    return pd.DataFrame(rows)
