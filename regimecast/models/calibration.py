"""Probability calibration and its scores (Guo et al. 2017; Niculescu-Mizil and Caruana 2005).

* ``Isotonic``: pool-adjacent-violators fit of outcome on predicted probability, a
  non-decreasing step map from raw to calibrated probability (no extra dependency).
* ``brier``, ``log_loss``, ``ece`` (expected calibration error) and ``reliability``
  (predicted vs observed frequency per bin), the numbers behind a reliability diagram.
* ``block_bootstrap``: confidence intervals that respect overlapping windows: whole
  blocks of consecutive days are resampled, not single rows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


class Isotonic:
    def fit(self, p: np.ndarray, y: np.ndarray, weight: np.ndarray | None = None) -> "Isotonic":
        p, y = np.asarray(p, float), np.asarray(y, float)
        w = np.ones_like(p) if weight is None else np.asarray(weight, float)
        o = np.argsort(p, kind="mergesort")
        xs, ys, ws = p[o], y[o], w[o]
        # pool adjacent violators on (value, weight) blocks
        val, wt, lo = [], [], []
        for i in range(len(xs)):
            val.append(ys[i]); wt.append(ws[i]); lo.append(xs[i])
            while len(val) > 1 and val[-2] > val[-1]:
                v = (val[-2] * wt[-2] + val[-1] * wt[-1]) / (wt[-2] + wt[-1])
                w2 = wt[-2] + wt[-1]
                val.pop(); wt.pop(); lo.pop()
                val[-1], wt[-1] = v, w2
        self.x_, self.y_ = np.array(lo), np.array(val)
        return self

    def predict(self, p: np.ndarray) -> np.ndarray:
        p = np.asarray(p, float)
        i = np.searchsorted(self.x_, p, side="right") - 1
        return self.y_[np.clip(i, 0, len(self.y_) - 1)]


def brier(p, y) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def log_loss(p, y, eps: float = 1e-6) -> float:
    p = np.clip(np.asarray(p, float), eps, 1 - eps)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


BINS = np.array([0, 0.001, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 1.0000001])


def reliability(p, y, bins: np.ndarray = BINS) -> pd.DataFrame:
    """Per bin of predicted probability: count, mean prediction, observed frequency.
    Bins are finer near zero, where most liquidation probabilities are."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    b = np.digitize(p, bins) - 1
    df = pd.DataFrame({"bin": b, "p": p, "y": y}).groupby("bin").agg(n=("p", "size"), predicted=("p", "mean"),
                                                                     observed=("y", "mean"))
    df.index = [f"{bins[i]:.3f}-{min(bins[i + 1], 1):.3f}" for i in df.index]
    return df


def ece(p, y, bins: np.ndarray = BINS) -> float:
    r = reliability(p, y, bins)
    return float((r["n"] * (r["predicted"] - r["observed"]).abs()).sum() / r["n"].sum())


def block_bootstrap(df: pd.DataFrame, stat, block: str = "7D", n: int = 300, seed: int = 0) -> tuple[float, float]:
    """95% interval of stat(df) resampling whole time blocks (df indexed by time)."""
    rng = np.random.default_rng(seed)
    keys = df.index.floor(block) if block.endswith("D") else df.index.floor(block)
    groups = {k: g for k, g in df.groupby(keys)}
    names = list(groups)
    vals = []
    for _ in range(n):
        pick = rng.choice(len(names), len(names), replace=True)
        vals.append(stat(pd.concat([groups[names[i]] for i in pick])))
    lo, hi = np.percentile(vals, [2.5, 97.5])
    return float(lo), float(hi)
