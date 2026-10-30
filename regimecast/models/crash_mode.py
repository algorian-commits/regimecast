"""Crash mode for the liquidation odds (Phase 13B).

The weak spot it targets: FHS (models/liquidation_risk.py) learns how far the price moves
against a position, in units of the volatility forecast, from ALL past hours. In a crash
the moves against longs are far larger than on an ordinary day, so the long side is
under-warned (final evaluation, 10 Oct 2025: BTC 10x long, FHS 1.3%, liquidated 46%).

Crash mode: when the stress gauge (models/stress.py, P(stress in the next hour)) is at
"elevated" or above (10%+, the gauge's own level), the LONG side's probability comes from
an FHS fitted only on past hours whose stress probability was high. The short side is
never changed. Below 10%, or before enough stressed hours have been seen, it is plain FHS.

Three settings, and only three (fixed in ``crash_mode.json`` by ``scripts/crash_mode_dev.py``):

* ``train_cut``: the stress probability that makes a past hour a "high-stress hour" for
  fitting (0.10 = elevated, 0.30 = high);
* ``combine``: ``replace`` (crash FHS instead of plain FHS) or ``max`` (whichever is
  larger: crash mode can only raise the warning);
* ``floor``: the one optional extra rule. The probability is at least the share of past
  high-stress hours whose worst fall over the horizon (raw, not scaled by volatility)
  reached the liquidation distance. It is there for crashes the volatility forecast has
  not caught up with.

Fixed by the plan, not tuned: the switch-on level ``SWITCH_ON`` (10%) and the minimum
number of past high-stress hours ``MIN_PAST`` before crash mode is used at all.

Walk-forward exactly as Phase 5: the same folds (refit every 90 days, purge = horizon,
at least 180 days of past), fitted on past origins only, using the out-of-sample volatility
forecast and the out-of-sample stress probability known at each origin.
"""
from __future__ import annotations

import json
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

from regimecast.models import harness
from regimecast.models import liquidation_risk as LR

SWITCH_ON = 0.10          # the stress gauge's "elevated" (models/stress.LEVELS)
MIN_PAST = 500            # past high-stress origins needed before crash mode is used
GRID = {"train_cut": [0.10, 0.30], "combine": ["replace", "max"], "floor": [False, True]}
FROZEN_FILE = Path(__file__).with_name("crash_mode.json")


def candidates() -> list[dict]:
    """Every setting tried, in a fixed order (simplest first)."""
    return [dict(zip(GRID, v)) for v in product(*GRID.values())]


def name(s: dict) -> str:
    return f"crash_cut{int(round(s['train_cut'] * 100))}_{s['combine']}_{'floor' if s['floor'] else 'nofloor'}"


def _survival(sorted_vals: np.ndarray, x: np.ndarray) -> np.ndarray:
    """(k + 0.5) / (n + 1), k = past values >= x: the same estimate as LR.FHS."""
    k = len(sorted_vals) - np.searchsorted(sorted_vals, np.asarray(x), side="left")
    return (k + 0.5) / (len(sorted_vals) + 1)


class CrashMode:
    """Long-side liquidation probability fitted on past high-stress origins only."""

    def __init__(self, train_cut: float, combine: str, floor: bool, switch_on: float = SWITCH_ON,
                 min_past: int = MIN_PAST):
        if combine not in ("replace", "max"):
            raise ValueError(combine)
        self.train_cut, self.combine, self.floor = train_cut, combine, floor
        self.switch_on, self.min_past = switch_on, min_past

    def fit(self, z_long: pd.Series, mae_long: pd.Series, p_stress: pd.Series) -> "CrashMode":
        """z_long = worst long move / volatility forecast, mae_long = the raw worst move, p_stress =
        the stress probability known at each past origin (all on the same index)."""
        hot = (p_stress.reindex(z_long.index) >= self.train_cut) & z_long.notna() & mae_long.notna()
        self.n = int(hot.sum())
        self.active = self.n >= self.min_past
        self.z = np.sort(z_long[hot].to_numpy())
        self.mae = np.sort(mae_long[hot].to_numpy())
        return self

    def predict(self, d: np.ndarray, sigma: np.ndarray, p_now: np.ndarray, fhs_plain: np.ndarray) -> np.ndarray:
        """Long side only. p_now: the stress probability at the origin (NaN = unknown -> plain FHS)."""
        fhs_plain = np.asarray(fhs_plain, float)
        if not self.active:
            return fhs_plain.copy()
        crash = _survival(self.z, np.asarray(d) / np.asarray(sigma))
        if self.combine == "max":
            crash = np.maximum(crash, fhs_plain)
        if self.floor:
            crash = np.maximum(crash, _survival(self.mae, np.broadcast_to(np.asarray(d, float), crash.shape)))
        on = np.nan_to_num(np.asarray(p_now, float), nan=-1.0) >= self.switch_on
        return np.where(on, crash, fhs_plain)


def evaluate(symbol: str, horizon: int, sigma: pd.Series, targets: pd.DataFrame, p_stress: pd.Series,
             settings: list[dict], eval_start="2022-01-01", refit_every_hours: int = 24 * 90,
             final: bool = False) -> pd.DataFrame:
    """Out-of-sample plain FHS and crash mode for every origin, side and leverage.

    Same folds, rows and FHS as LR.evaluate (its ``fhs`` column is reproduced exactly).
    sigma: out-of-sample variance forecasts over the horizon (HAR-S, the app's), p_stress:
    out-of-sample stress probabilities (index = hour end, known then). Adds ``p_stress``
    and one column per setting (``name(s)``)."""
    h = "1h" if horizon == 1 else "24h"
    t = targets.set_index("ts") if "ts" in targets else targets
    s = np.sqrt(sigma.dropna())
    mae = t[[f"mae_long_{h}", f"mae_short_{h}"]].reindex(s.index)
    mae.columns = ["long", "short"]
    z = mae.div(s, axis=0)
    ps = p_stress.reindex(s.index)
    dist = LR.distances(symbol)
    rows = []
    for f in harness.folds(s.index, eval_start, horizon, refit_every_hours=refit_every_hours, min_train_hours=24 * 180,
                           final=final):
        train = (s.index <= f.fit_end)
        test = (s.index >= f.block_start) & (s.index < f.block_end) & mae.notna().all(axis=1)
        if not test.any():
            continue
        fhs = LR.FHS().fit(z[train])
        cms = [CrashMode(**c).fit(z.loc[train, "long"], mae.loc[train, "long"], ps[train]) for c in settings]
        st, pn = s[test], ps[test].to_numpy()
        for side in ("long", "short"):
            for _, r in dist[dist["side"] == side].iterrows():
                d = np.full(test.sum(), r["d"])
                plain = fhs.predict(side, d, st)
                row = {"ts": st.index, "side": side, "leverage": r["leverage"], "d": r["d"], "sigma": st.to_numpy(),
                       "hit": (mae.loc[test, side] >= r["d"]).astype(int).to_numpy(), "fhs": plain, "p_stress": pn}
                for c, cm in zip(settings, cms):
                    row[name(c)] = cm.predict(d, st.to_numpy(), pn, plain) if side == "long" else plain
                rows.append(pd.DataFrame(row))
    return pd.concat(rows, ignore_index=True).sort_values(["ts", "side", "leverage"]).reset_index(drop=True)


def gap(p: pd.Series, hit: pd.Series) -> float:
    """Calibration gap: |mean predicted - observed| (percentage points / 100)."""
    return float(abs(p.mean() - hit.mean())) if len(p) else float("nan")


def score(p: pd.DataFrame, models: list[str], switch_on: float = SWITCH_ON) -> dict:
    """The two numbers the crash-mode bar uses, for plain FHS and each model:
    the long side's calibration gap in hours with stress >= switch_on, and ECE over all hours
    (pooled over long and short and the leverage grid)."""
    from regimecast.models import calibration as C

    hot = p[(p["side"] == "long") & (p["p_stress"] >= switch_on)]
    out = {"n_rows": len(p), "n_hot_long_rows": len(hot), "n_hot_hours": int(hot["ts"].nunique()),
           "observed_hot_long": float(hot["hit"].mean()) if len(hot) else float("nan")}
    for m in ["fhs", *models]:
        out[f"{m}_hot_long_mean"] = float(hot[m].mean()) if len(hot) else float("nan")
        out[f"{m}_hot_long_gap"] = gap(hot[m], hot["hit"])
        out[f"{m}_ece"] = C.ece(p[m], p["hit"])
        out[f"{m}_brier"] = C.brier(p[m], p["hit"])
    return out


def load_frozen(path: Path = FROZEN_FILE) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def from_frozen(frozen: dict) -> CrashMode:
    s = frozen["settings"]
    return CrashMode(s["train_cut"], s["combine"], s["floor"], frozen["switch_on"], frozen["min_past"])
