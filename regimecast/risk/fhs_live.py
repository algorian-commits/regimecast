"""Liquidation probability for the app: filtered historical simulation (FHS).

Phase 5 found FHS the best calibrated model out of sample (models/liquidation_risk.py,
scripts/run_phase5.py): scale each past worst move of the mark price by the volatility
forecast made at the time, z = worst move / sigma, and read the chance of reaching a
liquidation price d as the share of past z at or above d / sigma.

Shipped in ``fhs_live.json``: per coin, horizon and side, quantiles of z from the
development period (to 2025-09-30), with sigma from the same live model the app uses
(HAR-S, out of sample, walk-forward from 2021). ``scripts/fit_live_fhs.py`` rebuilds it.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

FILE = Path(__file__).with_name("fhs_live.json")
# CDF levels kept: fine in the tail, where liquidations live
LEVELS = np.unique(np.concatenate([np.linspace(0, 0.99, 100), np.linspace(0.99, 0.999, 10),
                                   np.linspace(0.999, 0.9999, 10), [1.0]]))


def fit(z: pd.Series) -> dict:
    z = np.sort(z.dropna().to_numpy())
    return {"levels": LEVELS.round(6).tolist(), "z": np.quantile(z, LEVELS).round(6).tolist(), "n": int(len(z))}


def survival(entry: dict, d: float, sigma: float) -> float:
    """P(worst move >= d) given the horizon volatility sigma (std of log return)."""
    if d <= 0:
        return 1.0
    if not np.isfinite(d) or sigma <= 0:
        return 0.0
    k = d / sigma
    z, lv = np.array(entry["z"]), np.array(entry["levels"])
    if k >= z[-1]:
        return 0.5 / (entry["n"] + 1)        # beyond everything seen: never exactly 0
    cdf = np.interp(k, z, lv)
    return float(max(1 - cdf, 0.5 / (entry["n"] + 1)))


def load(path: Path = FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
