"""Stress early warning (M10, part 1): the probability of a stress event in the next hour.

Label (features/build.py, independent of every model): a stress event is 5+ minutes in a
row of 15-minute realised volatility above the 99th percentile of the previous 30 days.
y(t) = 1 if any stress-event minute falls in (t, t + 1 h].

Classifier: LightGBM on what is known at t: the M7 volatility inputs (realised volatility,
funding, open interest, basis, DVOL, order book), the M4 regime probabilities (forward-
filtered, out of sample) and recent stress history. Walk-forward (models/harness.py),
refitted every 90 days, then isotonic calibration on earlier out-of-sample predictions.

Simple rules it must beat, each turned into a probability by past frequencies:
``persist`` (stress in the last hour), ``vol_rule`` (last hour's volatility above its
trailing 95th percentile) and ``regime_rule`` (the regime says stressed).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from regimecast.models import harness, volatility as V
from regimecast.models.calibration import Isotonic

PARAMS = dict(objective="binary", learning_rate=0.03, num_leaves=31, min_data_in_leaf=200,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=13)
EXTRA = ["stress_last_1h", "stress_last_6h", "hours_since_stress", "log_rv_vs_week", "p_calm", "p_active", "p_stressed"]
FEATURES = [*V.FEATURES, *EXTRA]


def dataset(h: pd.DataFrame, t: pd.DataFrame, regimes: pd.DataFrame | None = None,
            dvol: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.Series]:
    X, _ = V.dataset(h, t, 1, dvol)
    hh = h.set_index("ts") if "ts" in h else h
    sm = hh["stress_minutes"].reindex(X.index)
    X["stress_last_1h"] = (sm > 0).astype(float).where(sm.notna())
    X["stress_last_6h"] = sm.rolling(6, min_periods=1).sum()
    last = pd.Series(np.where(sm > 0, np.arange(len(sm)), np.nan), index=X.index).ffill()
    X["hours_since_stress"] = (np.arange(len(sm)) - last).clip(upper=24 * 30)
    X["log_rv_vs_week"] = np.log(hh["rv"].clip(lower=1e-12) / hh["rv_168h"].clip(lower=1e-12)).reindex(X.index)
    if regimes is not None and not regimes.empty:
        r = regimes[["p_calm", "p_active", "p_stressed"]]
        X = X.join(r, how="left")
    else:
        X[["p_calm", "p_active", "p_stressed"]] = np.nan
    X["vol_hot"] = vol_signal(X)            # for the vol_rule baseline (not a model input)
    y = (sm.shift(-1) > 0).astype(float).where(sm.shift(-1).notna())
    return X, y


class StressGBM:
    name = "gbm"

    def __init__(self, features: list[str] = FEATURES, valid_share: float = 0.2):
        self.features, self.valid_share = features, valid_share

    def fit(self, X, y):
        import lightgbm as lgb

        ok = y.notna()
        X, y = X.loc[ok, self.features], y[ok]
        cut = X.index[int(len(X) * (1 - self.valid_share))]
        tr, va = X.index < cut - pd.Timedelta(hours=1), X.index >= cut
        dtr = lgb.Dataset(X[tr], y[tr])
        self.model = lgb.train(PARAMS, dtr, 2000, valid_sets=[lgb.Dataset(X[va], y[va], reference=dtr)],
                               callbacks=[lgb.early_stopping(100, verbose=False)])
        return self

    def predict(self, X):
        return pd.Series(self.model.predict(X[self.features], num_iteration=self.model.best_iteration),
                         index=X.index, name=self.name)

    def contributions(self, X):
        c = self.model.predict(X[self.features], num_iteration=self.model.best_iteration, pred_contrib=True)
        return pd.DataFrame(c, index=X.index, columns=[*self.features, "base"])


class RateRule:
    """Turns a yes/no (or categorical) signal into a probability: the past frequency of
    next-hour stress for each value of the signal."""

    def __init__(self, signal, name):
        self.signal, self.name = signal, name

    def fit(self, X, y):
        s = self.signal(X)
        ok = y.notna() & s.notna()
        self.rates = y[ok].groupby(s[ok]).mean()
        self.base = float(y[ok].mean())
        return self

    def predict(self, X):
        return self.signal(X).map(self.rates).fillna(self.base).rename(self.name)


def regime_signal(X: pd.DataFrame) -> pd.Series:
    """Current regime (0 calm, 1 active, 2 stressed); missing before the first regime fit."""
    p = X[["p_calm", "p_active", "p_stressed"]]
    return pd.Series(p.fillna(-1).to_numpy().argmax(axis=1), index=X.index, dtype=float).where(p["p_calm"].notna())


def vol_signal(X: pd.DataFrame) -> pd.Series:
    """1 if last hour's volatility is above its trailing 30-day 95th percentile (past only)."""
    lv = X["log_rv_short"]
    thr = lv.rolling(24 * 30, min_periods=24 * 7).quantile(0.95).shift(1)
    return (lv > thr).astype(float).where(thr.notna())


MODELS = {
    "persist": lambda: RateRule(lambda X: X["stress_last_1h"], "persist"),
    "vol_rule": lambda: RateRule(lambda X: X["vol_hot"], "vol_rule"),
    "regime_rule": lambda: RateRule(regime_signal, "regime_rule"),
    "gbm": StressGBM,
}


def compare(X, y, test_start="2022-01-01", refit_every_hours: int = 24 * 90, final: bool = False) -> pd.DataFrame:
    out = pd.DataFrame({n: harness.walk_forward(X, y, f, test_start, 1, refit_every_hours=refit_every_hours, final=final)
                        for n, f in MODELS.items()})
    out["gbm_cal"] = calibrate(out["gbm"], y)
    out["y"] = y.reindex(out.index)
    return out


def calibrate(p: pd.Series, y: pd.Series, step: str = "90D", min_rows: int = 24 * 120) -> pd.Series:
    """Isotonic map refitted every `step` on earlier out-of-sample predictions only."""
    out = pd.Series(np.nan, index=p.index)
    yy = y.reindex(p.index)
    for a in pd.date_range(p.index.min(), p.index.max(), freq=step):
        b = a + pd.Timedelta(step)
        past = (p.index <= a - pd.Timedelta(hours=1)) & yy.notna()
        blk = (p.index >= a) & (p.index < b)
        if past.sum() >= min_rows and blk.any():
            out[blk] = Isotonic().fit(p[past], yy[past]).predict(p[blk])
    return out


# ------------------------------------------------------------------ scores
def auc(p, y) -> float:
    """Area under the ROC curve (rank formulation)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    r = pd.Series(p).rank().to_numpy()
    n1, n0 = y.sum(), len(y) - y.sum()
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def average_precision(p, y) -> float:
    o = np.argsort(-np.asarray(p, float))
    yy = np.asarray(y, float)[o]
    tp = np.cumsum(yy)
    prec = tp / np.arange(1, len(yy) + 1)
    return float((prec * yy).sum() / yy.sum())


def alarms(p: pd.Series, y: pd.Series, threshold: float) -> dict:
    a = p >= threshold
    days = (p.index.max() - p.index.min()).total_seconds() / 86400
    tp, fp = int((a & (y == 1)).sum()), int((a & (y == 0)).sum())
    return {"threshold": threshold, "precision": tp / max(tp + fp, 1), "recall": tp / max(int((y == 1).sum()), 1),
            "alarms_per_day": float(a.sum() / days), "false_alarms_per_day": fp / days}


# ------------------------------------------------------------------ live use (the app)
LIVE_DIR = Path(__file__).with_name("stress_live")
LIVE_FEATURES = [f for f in FEATURES if not f.startswith(("ob_", "log_ob_"))]   # no live order book
LEVELS = [(0.30, "high"), (0.10, "elevated"), (0.0, "low")]
DRIVER_NAMES = {"log_rv_vs_week": "volatility vs the past week", "hours_since_stress": "time since the last stress",
                "stress_last_1h": "stress in the last hour", "stress_last_6h": "stress in the last 6 hours",
                "hour": "time of day", "dow": "day of week", "oi_chg_24h": "open interest change (24 h)",
                "ls_top": "top traders' long/short ratio", "ls_accounts": "accounts' long/short ratio",
                "taker_ls": "taker buy/sell ratio", "log_volume_usd": "trading volume", "ret": "last hour's return",
                "ret_24h": "last day's return", "log_iv_h": "options-implied volatility (DVOL)",
                "funding_rate": "funding rate", "p_stressed": "regime: stressed", "p_calm": "regime: calm",
                "log_rv_short": "recent volatility", "log_range_max_1m": "largest 1-minute range",
                "mark_index_gap": "mark vs index price", "premium_mean": "futures premium",
                "taker_buy_share": "share of aggressive buying", "log_rv_mid": "volatility over the last day",
                "log_rv_long": "volatility over the last week", "neg_share": "share of downside volatility",
                "jump_share": "share of price jumps", "p_active": "regime: active"}


def fit_live(symbol: str, X: pd.DataFrame, y: pd.Series) -> dict:
    """Fit on the development period only and save the model the app uses."""
    dev = X.index <= harness.holdout_start() - pd.Timedelta(hours=1)
    m = StressGBM(LIVE_FEATURES).fit(X[dev], y[dev])
    LIVE_DIR.mkdir(exist_ok=True)
    m.model.save_model(str(LIVE_DIR / f"{symbol}.txt"), num_iteration=m.model.best_iteration)
    meta = {"features": LIVE_FEATURES, "fitted_through": "2025-09-30", "base_rate": float(y[dev].mean()),
            "n_train": int(y[dev].notna().sum())}
    (LIVE_DIR / f"{symbol}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def load_live(symbol: str):
    import lightgbm as lgb

    f = LIVE_DIR / f"{symbol}.txt"
    if not f.exists():
        return None
    return lgb.Booster(model_file=str(f)), json.loads((LIVE_DIR / f"{symbol}.json").read_text(encoding="utf-8"))


def live_gauge(booster, meta: dict, X: pd.DataFrame) -> dict:
    """P(stress in the next hour) for the last row of X, its level and the top drivers (SHAP)."""
    row = X[meta["features"]].iloc[[-1]]
    p = float(booster.predict(row)[0])
    c = pd.Series(booster.predict(row, pred_contrib=True)[0][:-1], index=meta["features"])
    top = c.reindex(c.abs().sort_values(ascending=False).index)[:4]
    return {"p_stress_next_hour": p, "level": next(name for th, name in LEVELS if p >= th),
            "base_rate": meta["base_rate"], "inputs_until": X.index[-1].isoformat(),
            "model": f"stress classifier (fitted to {meta['fitted_through']})",
            "drivers": [{"name": DRIVER_NAMES.get(k, k), "direction": "up" if v > 0 else "down"} for k, v in top.items()]}
