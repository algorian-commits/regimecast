"""Walk-forward evaluation for volatility forecasts.

* ``walk_forward``  expanding-window refits with a purge gap (no look-ahead)
* ``qlike``, ``mse`` loss functions (QLIKE on VARIANCE, not log variance)
* ``dm_test``       Diebold-Mariano with Newey-West (HAC) errors and the
                    Harvey-Leybourne-Newbold small-sample correction
* ``model_confidence_set``  Hansen-Lunde-Nason MCS with a block bootstrap
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd
from scipy import stats


def qlike(y: pd.Series | np.ndarray, f: pd.Series | np.ndarray) -> np.ndarray:
    y, f = np.asarray(y, float), np.asarray(f, float)
    ratio = y / f
    return ratio - np.log(ratio) - 1.0


def mse(y, f) -> np.ndarray:
    return (np.asarray(y, float) - np.asarray(f, float)) ** 2


def walk_forward(X: pd.DataFrame, y: pd.Series, make_model: Callable[[], object], test_start: str,
                 horizon_hours: int, refit_every_hours: int = 24 * 30, min_train_hours: int = 24 * 90,
                 end: str | None = None) -> pd.Series:
    """Out-of-sample forecasts.

    For each block of `refit_every_hours` test origins we fit on all origins
    whose TARGET was fully known before the block starts (origin <= block_start
    - horizon). That gap is the purge that stops overlapping targets leaking.
    """
    idx = X.index
    t0 = pd.Timestamp(test_start, tz="UTC") if pd.Timestamp(test_start).tzinfo is None else pd.Timestamp(test_start)
    t_end = idx[-1] if end is None else pd.Timestamp(end, tz="UTC")
    preds = []
    block_start = t0
    while block_start <= t_end:
        block_end = block_start + pd.Timedelta(hours=refit_every_hours)
        train_mask = idx <= block_start - pd.Timedelta(hours=horizon_hours)
        if train_mask.sum() >= min_train_hours:
            m = make_model().fit(X[train_mask], y[train_mask])
            test_mask = (idx >= block_start) & (idx < block_end) & (idx <= t_end)
            if test_mask.any():
                preds.append(m.predict(X[test_mask]))
        block_start = block_end
    return pd.concat(preds) if preds else pd.Series(dtype=float)


def _nw_var(d: np.ndarray, lag: int) -> float:
    d = d - d.mean()
    n = len(d)
    v = d @ d / n
    for k in range(1, lag + 1):
        w = 1 - k / (lag + 1)
        v += 2 * w * (d[k:] @ d[:-k]) / n
    return v


def dm_test(loss_a: np.ndarray, loss_b: np.ndarray, horizon: int = 1, lag: int | None = None) -> dict:
    """H0: equal accuracy. Negative stat = model A has LOWER loss (better).

    lag defaults to horizon-1 (at least 1). For 24h-ahead forecasts made every
    hour, use horizon=24 so overlapping errors are handled.
    """
    d = np.asarray(loss_a, float) - np.asarray(loss_b, float)
    d = d[np.isfinite(d)]
    n = len(d)
    lag = max(1, horizon - 1) if lag is None else lag
    var = _nw_var(d, lag)
    stat = d.mean() / np.sqrt(var / n)
    h = horizon
    hln = np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)  # small-sample correction
    stat_c = stat * hln
    p = 2 * (1 - stats.t.cdf(abs(stat_c), df=n - 1))
    return {"stat": float(stat_c), "p_value": float(p), "mean_diff": float(d.mean()), "n": n,
            "better": "A" if stat_c < 0 else "B"}


def _block_bootstrap_idx(n: int, block: int, rng) -> np.ndarray:
    starts = rng.integers(0, n, size=int(np.ceil(n / block)))
    idx = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
    return idx % n


def model_confidence_set(losses: pd.DataFrame, alpha: float = 0.1, B: int = 1000, block: int = 24,
                         seed: int = 0) -> dict:
    """Hansen, Lunde & Nason (2011) MCS, T_max statistic.

    losses: one column per model, one row per time. Returns the surviving
    models and the MCS p-value of every model.
    """
    L = losses.dropna().to_numpy()
    names = list(losses.columns)
    n = L.shape[0]
    rng = np.random.default_rng(seed)
    boot = [_block_bootstrap_idx(n, block, rng) for _ in range(B)]
    alive = list(range(len(names)))
    pvals: dict[str, float] = {}
    running = 0.0
    while len(alive) > 1:
        Ls = L[:, alive]
        dbar_i = Ls.mean(axis=1, keepdims=True)
        d = Ls - dbar_i  # loss relative to average of alive models
        dbar = d.mean(axis=0)
        bmeans = np.stack([d[b].mean(axis=0) for b in boot])  # B x m
        se = np.sqrt(((bmeans - dbar) ** 2).mean(axis=0)) + 1e-18
        t = dbar / se
        tmax = t.max()
        tmax_boot = ((bmeans - dbar) / se).max(axis=1)
        p = float((tmax_boot >= tmax).mean())
        worst = int(np.argmax(t))
        running = max(running, p)
        pvals[names[alive[worst]]] = running
        if p >= alpha:
            break
        alive.pop(worst)
    for i in alive:
        pvals.setdefault(names[i], 1.0)
    return {"included": [names[i] for i in alive], "p_values": pvals, "alpha": alpha}


def summary_table(y: pd.Series, forecasts: dict[str, pd.Series], baseline: str, horizon: int) -> pd.DataFrame:
    """QLIKE/MSE of every model on the SAME test rows, plus DM test vs baseline."""
    df = pd.concat({"y": y, **forecasts}, axis=1).dropna()
    df = df[(df["y"] > 0) & (df.drop(columns="y") > 0).all(axis=1)]
    rows = []
    base_loss = qlike(df["y"], df[baseline])
    for name in forecasts:
        ql = qlike(df["y"], df[name])
        row = {"model": name, "qlike": ql.mean(), "mse": mse(df["y"], df[name]).mean(), "n": len(df),
               "qlike_vs_base_pct": 100 * (ql.mean() / base_loss.mean() - 1)}
        if name != baseline:
            dm = dm_test(ql, base_loss, horizon)
            row.update(dm_stat=dm["stat"], dm_p=dm["p_value"])
        rows.append(row)
    return pd.DataFrame(rows).set_index("model")
