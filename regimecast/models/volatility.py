"""Volatility forecaster (M7): gradient-boosted trees on the M4 features, compared with HAR.

Target: realised variance over the next 1 h or 24 h (features/targets.py), forecast at
every hour end t. Inputs at t (all known at t):

* the HAR components (last hour/day/week or day/week/month of realised variance, logged),
  hour of day and day of week of the period ahead, jump and downside shares;
* recent return and range, volume and taker buy share, basis (premium, mark vs index),
  funding, open-interest change, long/short ratios;
* order book (spread, depth, imbalance, order-flow imbalance; from May 2025, else empty);
* DVOL, Deribit's implied volatility, scaled to the horizon (BTC and ETH, from Mar 2021).

LightGBM fits log variance (missing inputs are fine), with early stopping on the latest
20% of each training window (after a purge gap), and Duan's smearing turns it back into
variance, as for HAR. Settings are fixed in advance; nothing is tuned on test data.
``Combo`` averages the HAR-S and LightGBM variance forecasts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.models import harness
from regimecast.vol import har

FEATURES = ["log_rv_short", "log_rv_mid", "log_rv_long", "hour", "dow", "jump_share", "neg_share",
            "ret", "ret_24h", "log_range_max_1m", "log_volume_usd", "taker_buy_share", "premium_mean",
            "mark_index_gap", "funding_rate", "oi_chg_24h", "ls_top", "ls_accounts", "taker_ls",
            "ob_spread_bps_mean", "ob_spread_bps_max", "log_ob_depth_top50", "log_ob_depth_2bps_min",
            "ob_imbalance_top50_mean", "ob_ofi_norm", "ob_range_bps_mean", "log_iv_h"]
PARAMS = dict(objective="regression", learning_rate=0.03, num_leaves=31, min_data_in_leaf=200,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=7)
MAX_ROUNDS = 2000


def dataset(h: pd.DataFrame, t: pd.DataFrame, horizon: int, dvol: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.Series]:
    """X (index = hour end t) and the target y over (t, t + horizon]."""
    h = h.set_index("ts") if "ts" in h else h
    X = har.har_features(h, horizon)
    for c in ("short", "mid", "long"):
        X[f"log_rv_{c}"] = np.log(X[f"rv_{c}"].clip(lower=1e-12))
    X["ret"], X["ret_24h"] = h["ret"], h["ret_24h"]
    X["log_range_max_1m"] = np.log(h["range_max_1m"].clip(lower=1e-6))
    X["log_volume_usd"] = np.log1p(h["volume_usd"])
    for c in ("taker_buy_share", "premium_mean", "mark_index_gap", "funding_rate", "oi_chg_24h",
              "ls_top", "ls_accounts", "taker_ls", "ob_spread_bps_mean", "ob_spread_bps_max",
              "ob_imbalance_top50_mean", "ob_range_bps_mean"):
        X[c] = h[c]
    X["log_ob_depth_top50"] = np.log(h["ob_depth_top50_usd_mean"].clip(lower=1.0))
    X["log_ob_depth_2bps_min"] = np.log1p(h["ob_depth_2bps_usd_min"].clip(lower=0))
    X["ob_ofi_norm"] = h["ob_ofi"] / h["ob_depth_top50_usd_mean"].replace(0, np.nan) * h["close"]  # OFI in coins -> share of depth
    if dvol is not None and not dvol.empty:
        d = dvol.set_index("ts")["close"]
        d.index = d.index + pd.Timedelta(hours=1)          # candle opened at H-1h is known at H
        iv = d.reindex(X.index)
        X["log_iv_h"] = np.log((iv / 100) ** 2 * horizon / config.HOURS_PER_YEAR)
    else:
        X["log_iv_h"] = np.nan
    t = t.set_index("ts") if "ts" in t else t
    y = t["y_1h" if horizon == 1 else "y_24h"].reindex(X.index)
    return X, y


class GBMVol:
    """LightGBM on log variance with early stopping on the latest part of the training window."""
    name = "gbm"

    def __init__(self, horizon: int, features: list[str] = FEATURES, valid_share: float = 0.2):
        self.horizon, self.features, self.valid_share = horizon, features, valid_share

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "GBMVol":
        import lightgbm as lgb

        ok = y.notna() & (y > 0)
        X, ly = X.loc[ok, self.features], np.log(y[ok])
        cut = X.index[int(len(X) * (1 - self.valid_share))]
        gap = pd.Timedelta(hours=self.horizon)                 # purge: validation targets don't overlap training
        tr, va = X.index < cut - gap, X.index >= cut
        dtr = lgb.Dataset(X[tr], ly[tr])
        dva = lgb.Dataset(X[va], ly[va], reference=dtr)
        self.model = lgb.train(PARAMS, dtr, MAX_ROUNDS, valid_sets=[dva],
                               callbacks=[lgb.early_stopping(100, verbose=False)])
        resid = ly - self.model.predict(X, num_iteration=self.model.best_iteration)
        self.smear = float(np.mean(np.exp(resid)))
        return self

    def predict(self, X: pd.DataFrame) -> pd.Series:
        p = self.model.predict(X[self.features], num_iteration=self.model.best_iteration)
        return pd.Series(np.exp(p) * self.smear, index=X.index, name=self.name)

    def contributions(self, X: pd.DataFrame) -> pd.DataFrame:
        """SHAP values (TreeSHAP, built into LightGBM) on the log-variance scale; last column = base."""
        c = self.model.predict(X[self.features], num_iteration=self.model.best_iteration, pred_contrib=True)
        return pd.DataFrame(c, index=X.index, columns=[*self.features, "base"])


class Combo:
    """Average of the HAR-S and LightGBM variance forecasts (forecast combination)."""
    name = "combo"

    def __init__(self, horizon: int):
        self.har, self.gbm = har.HARModel("har_s"), GBMVol(horizon)

    def fit(self, X, y):
        self.har.fit(X, y)
        self.gbm.fit(X, y)
        return self

    def predict(self, X):
        return ((self.har.predict(X) + self.gbm.predict(X)) / 2).rename(self.name)


def compare(X: pd.DataFrame, y: pd.Series, horizon: int, test_start="2023-01-01",
            refit_every_hours: int = 24 * 90, final: bool = False) -> dict[str, pd.Series]:
    """Out-of-sample forecasts of every model, same folds, final test period closed (unless final=True)."""
    makers = {**{n: harness.BASELINES[n] for n in ("har", "har_s", "no_change", "hist_vol")},
              "gbm": lambda: GBMVol(horizon), "combo": lambda: Combo(horizon)}
    return {n: harness.walk_forward(X, y, f, test_start, horizon, refit_every_hours=refit_every_hours,
                                    final=final).rename(n)
            for n, f in makers.items()}
