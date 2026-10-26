"""One walk-forward harness for every model in RegimeCast.

Each model is fitted only on the past and tested on the block right after it:

    train origins ......... | gap | test block | next refit ...
                       fit_end      block_start  block_end

* **purge**: a training origin must have its whole target known before the test
  block starts, so the gap is at least the forecast horizon;
* **embargo**: an extra gap on top, against slow-decaying dependence;
* **final test period closed**: unless ``final=True``, the last test origin is
  ``config.HOLDOUT_START`` minus the horizon, so no target reaches into it, and a
  test may not start inside it. The final evaluation passes ``final=True`` once.

Baselines every new model is compared with (all on vol.har.har_features columns):
``har``, ``har_s``, ``har_j``, ``harq`` (vol/har.py), ``no_change`` (the last
period's realised variance) and ``hist_vol`` (the long-window average).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from regimecast import config
from regimecast.vol import har


def _utc(t) -> pd.Timestamp:
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def holdout_start() -> pd.Timestamp:
    return _utc(config.HOLDOUT_START)


@dataclass(frozen=True)
class Fold:
    fit_end: pd.Timestamp       # last training origin allowed (inclusive)
    block_start: pd.Timestamp   # first test origin (inclusive)
    block_end: pd.Timestamp     # end of the test block (exclusive)


def folds(index: pd.DatetimeIndex, test_start, horizon_hours: int, refit_every_hours: int = 24 * 30,
          embargo_hours: int = 0, min_train_hours: int = 24 * 90, end=None, final: bool = False) -> list[Fold]:
    t0 = _utc(test_start)
    last = index[-1] if end is None else min(_utc(end), index[-1])
    if not final:
        if t0 >= holdout_start():
            raise ValueError(f"test starts in the final test period ({config.HOLDOUT_START}); pass final=True")
        last = min(last, holdout_start() - pd.Timedelta(hours=horizon_hours))
    gap = pd.Timedelta(hours=horizon_hours + embargo_hours)
    out, start = [], t0
    while start <= last:
        stop = start + pd.Timedelta(hours=refit_every_hours)
        fit_end = start - gap
        if (index <= fit_end).sum() >= min_train_hours:
            out.append(Fold(fit_end, start, min(stop, last + pd.Timedelta(microseconds=1))))
        start = stop
    return out


def walk_forward(X: pd.DataFrame, y: pd.Series, make_model: Callable[[], object], test_start,
                 horizon_hours: int, **kw) -> pd.Series:
    """Out-of-sample predictions: refit on each fold's past, predict its test block."""
    preds = []
    for f in folds(X.index, test_start, horizon_hours, **kw):
        train = X.index <= f.fit_end
        test = (X.index >= f.block_start) & (X.index < f.block_end)
        if test.any():
            preds.append(make_model().fit(X[train], y[train]).predict(X[test]))
    return pd.concat(preds) if preds else pd.Series(dtype=float)


# ------------------------------------------------------------------ baselines
class NoChange:
    """Forecast = realised variance of the last period, scaled to the horizon."""
    name = "no_change"

    def fit(self, X, y):
        return self

    def predict(self, X):
        return X["rv_short"].rename(self.name)


class HistVol:
    """Forecast = long-window average realised variance (a week for 1 h, a month for 24 h)."""
    name = "hist_vol"

    def fit(self, X, y):
        return self

    def predict(self, X):
        return X["rv_long"].rename(self.name)


BASELINES: dict[str, Callable[[], object]] = {
    "har": lambda: har.HARModel("har"),
    "har_s": lambda: har.HARModel("har_s"),
    "har_j": lambda: har.HARModel("har_j"),
    "harq": lambda: har.HARModel("harq"),
    "no_change": NoChange,
    "hist_vol": HistVol,
}


def run_baselines(h: pd.DataFrame, horizon: int, test_start, names=None, **kw) -> tuple[pd.Series, dict[str, pd.Series]]:
    """Target and out-of-sample forecasts of the registered baselines.

    h: hourly frame from vol.realized.build_targets (index = hour end)."""
    X = har.har_features(h, horizon)
    y = h["y_1h" if horizon == 1 else "y_24h"]
    preds = {n: walk_forward(X, y, BASELINES[n], test_start, horizon, **kw).rename(n)
             for n in (names or BASELINES)}
    return y, preds
