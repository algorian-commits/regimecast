"""Liquidation probability (M7): how likely the mark price reaches a liquidation price in time.

For each hourly origin t, side (long/short) and leverage L (isolated margin, small
position, the coin's first margin tier), the liquidation price sits a distance d(L)
away (risk/liquidation.py; e.g. 10x long BTC: 9.6%). The position is liquidated if
the mark price's largest adverse move over the horizon reaches d (features/targets.py).

Models of P(worst move >= d), using the volatility forecast sigma (std of log return
over the horizon) made at t:

* ``gauss``: random walk, 2 * Phi(-d / sigma) (the app's baseline so far);
* ``fhs``: filtered historical simulation: the share of past origins whose scaled
  worst move z = mae / sigma reached d / sigma (fitted on past origins only);
* ``qgbm``: LightGBM quantile regression of log z on the M4 features, turned into a
  full distribution, so the tail can widen or narrow with market conditions;
* ``*_cal``: the same after isotonic calibration, fitted on earlier out-of-sample
  predictions only (models/calibration.py).

Every model is fitted walk-forward on the past only (models/harness.py folds).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from regimecast.models import harness
from regimecast.models.calibration import Isotonic
from regimecast.risk import liquidation as liq

LEVERAGES = [2, 3, 5, 10, 15, 20, 25, 50, 75, 100]
QUANTILES = [0.05, 0.25, 0.5, 0.75, 0.9, 0.95, 0.98, 0.99, 0.995, 0.999]
QPARAMS = dict(learning_rate=0.05, num_leaves=15, min_data_in_leaf=300, feature_fraction=0.8,
               bagging_fraction=0.8, bagging_freq=1, verbose=-1, seed=11)


def distances(symbol: str) -> pd.DataFrame:
    """Adverse log distance to the liquidation price per side and leverage (first margin tier)."""
    rows = []
    for L in LEVERAGES:
        for side in ("long", "short"):
            pos = liq.Position(symbol, side, entry=100.0, qty=10.0, leverage=L)  # $1,000 notional at 100
            lp = liq.liquidation_price(pos)
            rows.append({"leverage": L, "side": side, "d": liq.adverse_log_distance(pos, lp)})
    return pd.DataFrame(rows)


def gauss(d: np.ndarray, sigma: np.ndarray) -> np.ndarray:
    return np.clip(2 * norm.cdf(-np.asarray(d) / np.asarray(sigma)), 0, 1)


class FHS:
    """Empirical survival of z = mae / sigma, per side, from past origins."""

    def fit(self, z: pd.DataFrame) -> "FHS":
        self.z = {s: np.sort(z[s].dropna().to_numpy()) for s in ("long", "short")}
        return self

    def predict(self, side: str, d: np.ndarray, sigma: np.ndarray) -> np.ndarray:
        zs = self.z[side]
        k = len(zs) - np.searchsorted(zs, np.asarray(d) / np.asarray(sigma), side="left")
        return (k + 0.5) / (len(zs) + 1)


class QuantileGBM:
    """Quantiles of log z given the features; P(z >= k) by interpolating between them."""

    def __init__(self, features: list[str]):
        self.features = features

    def fit(self, X: pd.DataFrame, z: pd.DataFrame) -> "QuantileGBM":
        import lightgbm as lgb

        self.models = {}
        for side in ("long", "short"):
            ok = z[side].notna() & (z[side] > 0)
            Xs, lz = X.loc[ok, self.features], np.log(z.loc[ok, side])
            self.models[side] = [lgb.train({**QPARAMS, "objective": "quantile", "alpha": q},
                                           lgb.Dataset(Xs, lz), 300) for q in QUANTILES]
        return self

    def quantiles(self, side: str, X: pd.DataFrame) -> np.ndarray:
        q = np.column_stack([m.predict(X[self.features]) for m in self.models[side]])
        return np.maximum.accumulate(q, axis=1)                  # quantiles must not cross

    @staticmethod
    def survival(q: np.ndarray, target: np.ndarray) -> np.ndarray:
        """P(log z >= target) per row from that row's predicted quantiles of log z."""
        probs = np.array(QUANTILES)
        t = np.asarray(target, float)
        k = (q <= t[:, None]).sum(axis=1)                       # number of quantiles below target
        inner = (k > 0) & (k < len(probs))
        kk = np.clip(k, 1, len(probs) - 1)
        q0, q1 = np.take_along_axis(q, (kk - 1)[:, None], 1)[:, 0], np.take_along_axis(q, kk[:, None], 1)[:, 0]
        p0, p1 = probs[kk - 1], probs[kk]
        cdf = p0 + (p1 - p0) * np.clip((t - q0) / np.maximum(q1 - q0, 1e-9), 0, 1)
        out = 1 - cdf
        below = k == 0                                          # under the lowest quantile
        out[below] = 1 - probs[0] * np.exp(np.minimum(t[below] - q[below, 0], 0))
        above = k == len(probs)                                 # beyond the top: exponential tail
        tail = 1 - probs[-1]
        slope = np.maximum((q[:, -1] - q[:, -3]) / np.log((1 - probs[-3]) / tail), 1e-6)
        out[above] = tail * np.exp(-(t[above] - q[above, -1]) / slope[above])
        out[~(inner | below | above)] = out[~(inner | below | above)]
        return np.clip(out, 1e-6, 1)


def evaluate(symbol: str, horizon: int, X: pd.DataFrame, sigma: pd.Series, targets: pd.DataFrame,
             eval_start="2022-01-01", refit_every_hours: int = 24 * 90, features: list[str] | None = None,
             with_qgbm: bool = True, final: bool = False) -> pd.DataFrame:
    """Out-of-sample P(liquidation) of every model for every origin, side and leverage.

    sigma: out-of-sample volatility forecasts (variance over the horizon) from earlier folds,
    so the z history used for fitting is itself out of sample."""
    h = f"{horizon}h" if horizon == 1 else "24h"
    t = targets.set_index("ts") if "ts" in targets else targets
    s = np.sqrt(sigma.dropna())
    mae = t[[f"mae_long_{h}", f"mae_short_{h}"]].reindex(s.index)
    mae.columns = ["long", "short"]
    z = mae.div(s, axis=0)
    feats = features or [c for c in X.columns if c not in ("hour", "dow")]
    Xs = X.reindex(s.index).assign(log_sigma=np.log(s))
    feats = [*feats, "log_sigma"]
    dist = distances(symbol)
    rows = []
    for f in harness.folds(s.index, eval_start, horizon, refit_every_hours=refit_every_hours, min_train_hours=24 * 180,
                           final=final):
        train = (s.index <= f.fit_end)
        test = (s.index >= f.block_start) & (s.index < f.block_end) & mae.notna().all(axis=1)
        if not test.any():
            continue
        fhs = FHS().fit(z[train])
        qg = QuantileGBM(feats).fit(Xs[train], z[train]) if with_qgbm else None
        st = s[test]
        for side in ("long", "short"):
            q = qg.quantiles(side, Xs[test]) if qg else None
            for _, r in dist[dist["side"] == side].iterrows():
                d = np.full(test.sum(), r["d"])
                rows.append(pd.DataFrame({
                    "ts": st.index, "side": side, "leverage": r["leverage"], "d": r["d"], "sigma": st.to_numpy(),
                    "hit": (mae.loc[test, side] >= r["d"]).astype(int).to_numpy(),
                    "gauss": gauss(d, st), "fhs": fhs.predict(side, d, st),
                    "qgbm": QuantileGBM.survival(q, np.log(d / st.to_numpy())) if q is not None else np.nan}))
    out = pd.concat(rows, ignore_index=True)
    return calibrate_walk_forward(out, horizon, models=("gauss", "fhs", "qgbm") if with_qgbm else ("gauss", "fhs"))


def calibrate_walk_forward(p: pd.DataFrame, horizon: int, models=("gauss", "fhs", "qgbm"),
                           step: str = "90D") -> pd.DataFrame:
    """Add <model>_cal: isotonic maps fitted on earlier out-of-sample predictions only
    (origins whose outcome was known before the block starts)."""
    p = p.sort_values("ts").reset_index(drop=True)
    for m in models:
        p[f"{m}_cal"] = np.nan
    starts = pd.date_range(p["ts"].min(), p["ts"].max() + pd.Timedelta(step), freq=step)
    for a, b in zip(starts[:-1], starts[1:]):
        past = p[p["ts"] <= a - pd.Timedelta(hours=horizon)]
        blk = (p["ts"] >= a) & (p["ts"] < b)
        if len(past) < 5000 or not blk.any():
            continue
        for m in models:
            iso = Isotonic().fit(past[m], past["hit"])
            p.loc[blk, f"{m}_cal"] = iso.predict(p.loc[blk, m])
    return p


# ------------------------------------------------------------------ direction-aware model (Phase 6b)
DIRECT_FEATURES = ["ret", "ret_24h", "funding_rate", "oi_chg_24h", "ls_top", "ls_accounts", "taker_ls",
                   "taker_buy_share", "premium_mean", "mark_index_gap", "log_rv_vs_week", "p_stressed",
                   "p_stress", "iv_minus_sigma", "hour", "dow"]
DPARAMS = dict(objective="binary", learning_rate=0.05, num_leaves=31, min_data_in_leaf=500, feature_fraction=0.8,
               bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1, seed=17)


def _stack(F: pd.DataFrame, k_by_lev: dict, hit: pd.DataFrame | None, side: str) -> pd.DataFrame:
    rows = []
    for L, k in k_by_lev.items():
        r = F.copy()
        r["k"] = k
        r["leverage"] = L
        if hit is not None:
            r["y"] = hit[L]
        rows.append(r)
    return pd.concat(rows)


class DirectModel:
    """P(liquidated within the horizon) per side from k = log(d / sigma) and directional state.
    LightGBM with a monotone constraint: a larger k (liquidation farther away) never raises
    the probability."""

    def __init__(self, features: list[str] = DIRECT_FEATURES, rounds: int = 400):
        self.features, self.rounds = features, rounds

    def fit(self, stacked: pd.DataFrame) -> "DirectModel":
        import lightgbm as lgb

        cols = ["k", *self.features]
        ok = stacked["y"].notna() & stacked["k"].notna()
        mono = [-1] + [0] * len(self.features)
        self.model = lgb.train({**DPARAMS, "monotone_constraints": mono}, lgb.Dataset(stacked.loc[ok, cols],
                               stacked.loc[ok, "y"]), self.rounds)
        return self

    def predict(self, stacked: pd.DataFrame) -> np.ndarray:
        return self.model.predict(stacked[["k", *self.features]])


def evaluate_direct(symbol: str, horizon: int, F: pd.DataFrame, sigma: pd.Series, targets: pd.DataFrame,
                    eval_start="2022-01-01", refit_every_hours: int = 24 * 90) -> pd.DataFrame:
    """Walk-forward out-of-sample predictions of DirectModel for every hour, side and leverage.
    F: directional state per hour (DIRECT_FEATURES), sigma: out-of-sample variance forecasts."""
    h = "1h" if horizon == 1 else "24h"
    t = targets.set_index("ts") if "ts" in targets else targets
    s = np.sqrt(sigma.dropna())
    F = F.reindex(s.index)
    dist = distances(symbol)
    out = []
    for f in harness.folds(s.index, eval_start, horizon, refit_every_hours=refit_every_hours, min_train_hours=24 * 180):
        tr = s.index <= f.fit_end
        te = (s.index >= f.block_start) & (s.index < f.block_end)
        for side in ("long", "short"):
            mae = t[f"mae_{side}_{h}"].reindex(s.index)
            d = dist[dist["side"] == side].set_index("leverage")["d"]
            k = {L: np.log(dL / s) for L, dL in d.items()}
            hit = pd.DataFrame({L: (mae >= dL).astype(float).where(mae.notna()) for L, dL in d.items()})
            train = _stack(F[tr], {L: v[tr] for L, v in k.items()}, hit[tr], side)
            m = DirectModel().fit(train)
            test = _stack(F[te], {L: v[te] for L, v in k.items()}, hit[te], side)
            out.append(pd.DataFrame({"ts": test.index, "side": side, "leverage": test["leverage"].to_numpy(),
                                     "direct": m.predict(test), "hit_d": test["y"].to_numpy()}))
    p = pd.concat(out, ignore_index=True).dropna(subset=["hit_d"])
    p = p.rename(columns={"hit_d": "hit"})
    return calibrate_walk_forward(p, horizon, models=("direct",))
