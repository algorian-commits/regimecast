"""The live volatility forecast (Phase 13A): the HAR-S + LightGBM combination that passed the final evaluation.

The final evaluation found the combination's error (QLIKE) 6-27% lower than HAR on all 6 coin x horizon cases
(significant on 5), where HAR-S alone, the app's model until now, gained 1-14%. This module is that model for the app:

* **Same model, same settings.** ``models/volatility.py``'s ``Combo``: the average of the HAR-S and LightGBM
  variance forecasts, with LightGBM's fixed parameters, early stopping on the latest 20% of the training rows and
  Duan's smearing. Fitted once per coin and horizon on every hour whose target ended by 2026-09-30 (the end of the
  final test period), by ``scripts/fit_live_combo.py``, and shipped in ``combo_live/`` (as the stress model is),
  so the API image needs no training.
* **No order book.** The API has no live order book, so the order-book inputs are left out (``LIVE_FEATURES``);
  they only exist from May 2025 in the archive anyway.
* **Same inputs as training.** The live rows come from the stress gauge's live inputs (features/live.py) through
  the training feature code (features/build.py, then ``volatility.dataset``), so each input means exactly what it
  meant in training. ``scripts/p13a_live_check.py`` checks this against the archive, hour by hour.
* **Explained.** Each part has its own drivers, both as "% more or less volatility than a typical hour": HAR-S's
  exact SHAP values (linear model, vol/live.py ``drivers``) and LightGBM's TreeSHAP values.

The liquidation and stop chances keep using FHS on HAR-S (risk/fhs_live.py): that is the pairing the final
evaluation's liquidation bar and the Trade Grade bar were passed with, so the risk check's numbers stay the tested ones.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.models import volatility as V
from regimecast.vol import har

MODEL_DIR = Path(__file__).with_name("combo_live")
LIVE_FEATURES = [f for f in V.FEATURES if not f.startswith(("ob_", "log_ob_"))]   # no live order book
HAR_COLS = ["rv_short", "rv_mid", "rv_long", "hour", "dow"]                     # what HAR-S reads
FIT_END = "2026-10-01"          # targets must end by then: everything through 2026-09-30
HORIZONS = {"1h": 1, "24h": 24}
TOP_DRIVERS = 8

GBM_NAMES = {"hour": "time of day", "dow": "day of week", "log_iv_h": "options-implied volatility (DVOL)",
             "jump_share": "share of price jumps", "neg_share": "share of downside volatility",
             "ret": "last hour's return", "ret_24h": "last day's return",
             "log_range_max_1m": "largest 1-minute range", "log_volume_usd": "trading volume",
             "taker_buy_share": "share of aggressive buying", "premium_mean": "futures premium",
             "mark_index_gap": "mark vs index price", "funding_rate": "funding rate",
             "oi_chg_24h": "open interest change (24 h)", "ls_top": "top traders' long/short ratio",
             "ls_accounts": "accounts' long/short ratio", "taker_ls": "taker buy/sell ratio"}


def model_inputs(X: pd.DataFrame) -> pd.DataFrame:
    """Every column the combination reads (HAR-S's and LightGBM's), in a fixed order."""
    return X[HAR_COLS + [c for c in LIVE_FEATURES if c not in HAR_COLS]]


def _effect(log_var: float) -> float:
    """A change in log variance as the % change in volatility it makes."""
    return round(float(100 * (np.exp(log_var / 2) - 1)), 1)


# ------------------------------------------------------------------ the model
class LiveCombo:
    """The combination as the app runs it: HAR-S coefficients and a LightGBM booster, read from files."""

    def __init__(self, horizon: int, har_model: dict, booster, gbm: dict, fitted_through: str):
        from regimecast.vol import live

        self.horizon, self.har_dict, self.booster, self.gbm = horizon, har_model, booster, gbm
        self.har = live.model_from_dict(har_model)
        self.fitted_through = fitted_through

    def parts(self, X: pd.DataFrame) -> pd.DataFrame:
        """Variance forecasts of both parts and their average (the combination)."""
        v_har = self.har.predict(X).to_numpy()
        v_gbm = np.exp(self.booster.predict(X[self.gbm["features"]])) * self.gbm["smear"]
        return pd.DataFrame({"har_s": v_har, "gbm": v_gbm, "combo": (v_har + v_gbm) / 2}, index=X.index)

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return self.parts(X)["combo"].rename("combo")

    def gbm_drivers(self, X_row: pd.DataFrame) -> list[dict]:
        """TreeSHAP values of LightGBM for one forecast (log variance), as % more or less volatility than its
        average forecast. Time of day and day of week are kept as one driver each, as for HAR-S."""
        from regimecast.vol import live

        c = pd.Series(self.booster.predict(X_row[self.gbm["features"]], pred_contrib=True)[0][:-1],
                      index=self.gbm["features"])
        names = {**GBM_NAMES, **live.DRIVER_NAMES[self.horizon]}
        g = c.groupby(c.index.map(lambda k: names.get(k, k))).sum()
        out = [{"name": k, "effect_pct": _effect(v)} for k, v in g.items()]
        return sorted(out, key=lambda d: -abs(d["effect_pct"]))[:TOP_DRIVERS]

    def forecast(self, X: pd.DataFrame) -> dict:
        """The forecast for the last row of X, in the shape /forecast serves."""
        from regimecast.vol import live

        row = X.iloc[[-1]]
        p = self.parts(row).iloc[0]
        var = float(p["combo"])
        sig = lambda v: 100 * np.sqrt(v)                                   # noqa: E731
        return {"variance": var, "sigma_pct": sig(var),
                "annualised_vol_pct": 100 * np.sqrt(var * config.HOURS_PER_YEAR / self.horizon),
                "model": f"HAR-S + LightGBM (fitted to {self.fitted_through})",
                "inputs_until": row.index[0].isoformat(),
                "parts": {"har_s_sigma_pct": sig(float(p["har_s"])), "gbm_sigma_pct": sig(float(p["gbm"]))},
                "drivers": live.drivers(self.har_dict, row, self.horizon),
                "drivers_gbm": self.gbm_drivers(row)}


def fit(X: pd.DataFrame, y: pd.Series, horizon: int, end: str = FIT_END) -> tuple[V.Combo, pd.Series]:
    """Fit the combination exactly as the final evaluation did, without the order book, on the hours whose
    target (t, t + horizon] ended by ``end``."""
    ok = X.index <= pd.Timestamp(end, tz="UTC") - pd.Timedelta(hours=horizon)
    m = V.Combo(horizon)
    m.gbm = V.GBMVol(horizon, LIVE_FEATURES)
    return m.fit(X[ok], y[ok]), ok


def save(symbol: str, models: dict[str, tuple[V.Combo, pd.Series]], X: dict[str, pd.DataFrame],
         y: dict[str, pd.Series], out: Path = MODEL_DIR, check_rows: int = 24) -> dict:
    """Write the boosters and one JSON per coin: HAR-S coefficients (with training means, for its drivers),
    LightGBM's smearing factor, and the last ``check_rows`` real training rows with the in-memory model's
    forecasts, which tests/test_combo_live.py replays through the saved files."""
    from regimecast.vol import live

    out.mkdir(exist_ok=True)
    meta = {}
    for key, (m, ok) in models.items():
        horizon = HORIZONS[key]
        Xk, yk = X[key][ok], y[key][ok]
        m.gbm.model.save_model(str(out / f"{symbol}_{key}.txt"), num_iteration=m.gbm.model.best_iteration)
        D = har._design(Xk, "har_s", [])[m.har.cols].dropna()
        used = Xk.index[yk.notna() & (yk > 0)]
        rows = model_inputs(Xk).dropna(subset=["rv_short", "rv_mid", "rv_long"]).iloc[-check_rows:]
        meta[key] = {
            "horizon_hours": horizon, "fitted_through": "2026-09-30",
            "train_from": used.min().isoformat(), "train_to": used.max().isoformat(), "n_train": int(len(used)),
            "har_s": {**live.model_to_dict(m.har, "2026-09-30", int(len(D))), "means": [float(v) for v in D.mean()]},
            "gbm": {"features": LIVE_FEATURES, "smear": m.gbm.smear, "best_iteration": int(m.gbm.model.best_iteration),
                    "params": V.PARAMS, "valid_share": m.gbm.valid_share},
            "check": {"ts": [t.isoformat() for t in rows.index], "columns": list(rows.columns),
                      "X": [[None if pd.isna(v) else float(v) for v in r] for r in rows.to_numpy()],
                      "variance": [float(v) for v in m.predict(rows)]},
        }
    (out / f"{symbol}.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return meta


def load(symbol: str, path: Path = MODEL_DIR) -> dict[str, LiveCombo] | None:
    import lightgbm as lgb

    f = path / f"{symbol}.json"
    if not f.exists():
        return None
    meta = json.loads(f.read_text(encoding="utf-8"))
    return {key: LiveCombo(m["horizon_hours"], m["har_s"], lgb.Booster(model_file=str(path / f"{symbol}_{key}.txt")),
                           m["gbm"], m["fitted_through"])
            for key, m in meta.items()}


def check_frame(symbol: str, key: str, path: Path = MODEL_DIR) -> tuple[pd.DataFrame, np.ndarray]:
    """The real training rows saved with a model and the forecasts the in-memory model gave for them."""
    c = json.loads((path / f"{symbol}.json").read_text(encoding="utf-8"))[key]["check"]
    X = pd.DataFrame([[np.nan if v is None else v for v in r] for r in c["X"]], columns=c["columns"],
                     index=pd.DatetimeIndex(pd.to_datetime(c["ts"], utc=True)))
    return X, np.array(c["variance"])


# ------------------------------------------------------------------ live rows
def live_X(h: pd.DataFrame, dvol: pd.DataFrame | None, horizon: int) -> pd.DataFrame:
    """Model inputs at every hour end of the live feature rows (features/build.py), by the training code."""
    t = pd.DataFrame({"ts": h["ts"], "y_1h": np.nan, "y_24h": np.nan})
    X, _ = V.dataset(h, t, horizon, dvol if dvol is not None and not dvol.empty else None)
    return X


def forecasts(h: pd.DataFrame, dvol: pd.DataFrame | None, models: dict[str, LiveCombo]) -> dict:
    """Both horizons' forecasts for the last row of the live feature rows."""
    out = {}
    for key, m in models.items():
        X = live_X(h, dvol, m.horizon)
        fc = m.forecast(X)
        if np.isfinite(fc["variance"]) and fc["variance"] > 0:
            out[key] = fc
    return out
