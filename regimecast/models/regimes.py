"""Market regimes (calm, active, stressed) from a 3-state Gaussian HMM, walk-forward.

* Fitted only on each fold's past (models/harness.py folds, refit monthly), on
  standardised hourly features; the scaler is fitted on the same past.
* States are ordered by mean log realised volatility: 0 calm, 1 active, 2 stressed.
* Probabilities are *filtered*: the regime at hour t uses hours up to t only.
* From the final test period on (2025-10-01), the last model fitted before it is
  frozen and only filters: the chart can show the October 2025 crash without the
  model ever being fitted on it.

Feature sets (hourly, from features/build.py):
    base       return, log realised variance (1 h), log realised variance (24 h avg)
    orderbook  base + log spread and log top-50 depth (Bybit, from May 2025)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from regimecast.models import harness
from regimecast.models.hmm import GaussianHMM

NAMES = ["calm", "active", "stressed"]
FEATURE_SETS = {
    "base": ["ret", "log_rv", "log_rv_24h"],
    "orderbook": ["ret", "log_rv", "log_rv_24h", "log_spread", "log_depth"],
}
# Dev-period crashes (the October 2025 crash is in the closed final test period)
EVENTS = {"COVID crash": "2020-03-12", "China mining ban": "2021-05-19",
          "FTX collapse": "2022-11-08", "Yen carry unwind": "2024-08-05"}


def regime_inputs(h: pd.DataFrame) -> pd.DataFrame:
    """Hourly HMM inputs from the hourly feature table (index = hour end)."""
    x = pd.DataFrame(index=pd.DatetimeIndex(h["ts"]) if "ts" in h else h.index)
    src = h.set_index("ts") if "ts" in h else h
    x["ret"] = src["ret"]
    x["log_rv"] = np.log(src["rv"].clip(lower=1e-12))
    x["log_rv_24h"] = np.log(src["rv_24h"].clip(lower=1e-12))
    if "ob_spread_bps_mean" in src:
        x["log_spread"] = np.log(src["ob_spread_bps_mean"].clip(lower=1e-6))
        x["log_depth"] = np.log(src["ob_depth_top50_usd_mean"].clip(lower=1.0))
    return x


def fit_walk_forward(x: pd.DataFrame, cols: list[str], test_start=None, refit_every_hours: int = 24 * 30,
                     min_train_hours: int = 24 * 45, freeze_after_holdout: bool = True, seed: int = 0) -> pd.DataFrame:
    """Filtered regime probabilities for every hour from test_start on."""
    X = x[cols]
    valid = X.dropna()
    if test_start is None:
        test_start = valid.index[0] + pd.Timedelta(hours=min_train_hours)
    folds = harness.folds(X.index, test_start, horizon_hours=0, refit_every_hours=refit_every_hours,
                          min_train_hours=min_train_hours)
    ilv = cols.index("log_rv")
    out, model, warm = [], None, None

    def block(model, mu, sd, lo, hi, frozen):
        hist = X[X.index < hi]
        p = model.filter(((hist - mu) / sd).to_numpy())
        keep = hist.index >= lo
        df = pd.DataFrame(p[keep], index=hist.index[keep], columns=[f"p_{n}" for n in NAMES])
        df["fit_end"], df["frozen"] = fit_end, frozen
        return df

    for f in folds:
        tr = valid[valid.index <= f.fit_end]
        if len(tr) < min_train_hours // 2:
            continue
        mu, sd = tr.mean(), tr.std()
        model = GaussianHMM(3, n_init=5 if warm is None else 2, seed=seed).fit(((tr - mu) / sd).to_numpy(), warm=warm)
        model.order_by(ilv)
        warm, fit_end = model, f.fit_end
        out.append(block(model, mu, sd, f.block_start, min(f.block_end, harness.holdout_start()), False))
    if freeze_after_holdout and model is not None and X.index[-1] >= harness.holdout_start():
        out.append(block(model, mu, sd, harness.holdout_start(), X.index[-1] + pd.Timedelta(seconds=1), True))
    res = pd.concat(out)
    res = res[~res.index.duplicated(keep="first")]
    res["regime"] = res[[f"p_{n}" for n in NAMES]].to_numpy().argmax(axis=1)
    res.index.name = "ts"
    return res


def hourly_close(minute: pd.DataFrame) -> pd.Series:
    """Last price of each hour (index = hour end), from the minute feature table."""
    return minute.set_index("ts")["close"].resample("1h", label="right", closed="right").last()


def crash_study(close: pd.Series, reg: pd.DataFrame, events: dict = EVENTS, drop: float = 0.10,
                stressed_p: float = 0.5) -> pd.DataFrame:
    """For each crash: when it started, and how long the regime had been 'stressed' by then.

    onset = first hour in [day - 3 d, day + 2 d] at least `drop` below the trailing 24 h high.
    lead = length of the unbroken run of p_stressed >= `stressed_p` ending at the onset hour
    (0 if the regime was not stressed at onset).
    """
    rows = []
    trail_max = close.rolling(24, min_periods=12).max()
    for name, day in events.items():
        d = pd.Timestamp(day, tz="UTC")
        w = close[d - pd.Timedelta(days=3): d + pd.Timedelta(days=2)]
        hit = w[w <= (1 - drop) * trail_max.reindex(w.index)]
        if hit.empty or hit.index[0] not in reg.index:
            rows.append({"event": name, "date": day, "onset": None})
            continue
        onset = hit.index[0]
        p = reg["p_stressed"]
        before = p[:onset]
        run = 0
        for v in before[::-1]:
            if v >= stressed_p:
                run += 1
            else:
                break
        prior = p[onset - pd.Timedelta(hours=24): onset]
        rows.append({"event": name, "date": day, "onset": onset, "price_at_onset": float(close[onset]),
                     "p_stressed_at_onset": float(p[onset]), "lead_hours": run,
                     "max_p_stressed_24h_before": float(prior.max()),
                     "regime_24h_before": NAMES[int(reg["regime"].get(onset - pd.Timedelta(hours=24), 0))]})
    return pd.DataFrame(rows)


def regime_summary(reg: pd.DataFrame, h: pd.DataFrame) -> pd.DataFrame:
    """Share of time in each regime and the volatility that FOLLOWED (next 24 h), annualised.
    Uses filtered regimes and future outcomes: a fair test that regimes mean something."""
    src = h.set_index("ts") if "ts" in h else h
    fut24 = src["rv5"][::-1].rolling(24, min_periods=20).sum()[::-1].shift(-1)  # realised variance over (t, t+24h]
    df = reg[["regime", "frozen"]].join(fut24.rename("next_24h_var"))
    df = df[~df["frozen"]]
    g = df.groupby("regime")
    out = pd.DataFrame({"share_of_hours": g.size() / len(df),
                        "next_24h_vol_annualised": np.sqrt(g["next_24h_var"].mean() * 365)})
    out.index = [NAMES[i] for i in out.index]
    return out
