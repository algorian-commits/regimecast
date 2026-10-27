"""Live volatility forecasts for the app: HAR-S on the latest Binance candles.

HAR-S (HAR with hour-of-day and day-of-week effects, vol/har.py) is our best tested
baseline: on real BTC data it beat plain HAR by 9.7% (1 h) and 19.3% (24 h) in QLIKE,
walk-forward, 2024-01 to 2025-09. It stands in for the placeholder volatility until
the Phase 5 model replaces it.

* Coefficients are fitted once per coin and horizon on the development period (up to
  config.HOLDOUT_START) by ``scripts/fit_live_har.py`` and shipped in
  ``har_live.json``, like the margin tiers.
* Inputs are the last ~31 days of 1-minute traded-price candles from Binance, turned
  into hourly realised variance exactly as in training (vol/realized.py); only
  complete hours are used.
* A background thread refreshes every coin every REFRESH_SECONDS, so a risk check
  never waits on Binance. Until the first refresh succeeds, callers get None and the
  API falls back to the placeholder (with its warning).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.vol import har, realized

log = logging.getLogger("vol.live")

MODELS_FILE = Path(__file__).with_name("har_live.json")
HISTORY_MINUTES = 31 * 24 * 60   # the 24 h model's longest window is 720 h (30 days)
REFRESH_SECONDS = 600.0
KLINE_LIMIT = 1500


# ------------------------------------------------------------------ models
def model_to_dict(m: har.HARModel, fitted_through: str, n: int) -> dict:
    return {"variant": m.variant, "cols": m.cols, "beta": [float(b) for b in m.beta],
            "smear": float(getattr(m, "smear", 1.0)), "fitted_through": fitted_through, "n_train": n}


def model_from_dict(d: dict) -> har.HARModel:
    m = har.HARModel(d["variant"])
    m.cols, m.beta, m.smear = d["cols"], np.array(d["beta"]), d["smear"]
    return m


def fit_models(symbol: str, variant: str = "har_s") -> dict:
    """Fit the live model for both horizons on the development period only.

    Reads the hourly feature and target tables (features/build.py, features/targets.py),
    which hold the same realised measures as vol/realized.py. Also stores the average of
    every model input over the training rows: the baseline for the explanation (drivers)."""
    from regimecast.data import store

    end = pd.Timestamp(config.HOLDOUT_START, tz="UTC")
    h = store.features(symbol, "1h", end=end + pd.Timedelta(hours=1)).set_index("ts")
    t = store.targets(symbol, end=end + pd.Timedelta(hours=1)).set_index("ts").reindex(h.index)
    out = {}
    for key, horizon, target in (("1h", 1, "y_1h"), ("24h", 24, "y_24h")):
        X, y = har.har_features(h, horizon), t[target]
        ok = (y.index <= end - pd.Timedelta(hours=horizon)).astype(bool)   # targets fully before the final test period
        m = har.HARModel(variant).fit(X[ok], y[ok])
        D = har._design(X[ok], variant, [])[m.cols].dropna()
        out[key] = {**model_to_dict(m, (end - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), int(ok.sum())),
                    "means": [float(v) for v in D.mean()]}
    return out


DRIVER_NAMES = {1: {"log_rv_short": "volatility in the last hour", "log_rv_mid": "volatility in the last day",
                    "log_rv_long": "volatility in the last week"},
                24: {"log_rv_short": "volatility in the last day", "log_rv_mid": "volatility in the last week",
                     "log_rv_long": "volatility in the last month"}}


def drivers(model: dict, X_last: pd.DataFrame, horizon: int) -> list[dict]:
    """Exact SHAP values of the linear HAR-S model for one forecast: beta * (x - training mean),
    grouped (seasonal dummies -> time of day, day of week) and shown as the % change in
    volatility versus a typical hour."""
    if "means" not in model:
        return []
    D = har._design(X_last, model["variant"], [])[model["cols"]].iloc[0]
    c = pd.Series(np.array(model["beta"]) * (D.to_numpy() - np.array(model["means"])), index=model["cols"])
    groups = {**DRIVER_NAMES[horizon],
              **{col: "time of day" for col in c.index if col.startswith("h") and col[1:].isdigit()},
              **{col: "day of week" for col in c.index if col.startswith("d") and col[1:].isdigit()}}
    g = c.groupby(c.index.map(lambda k: groups.get(k))).sum()   # const has no group: dropped
    out = [{"name": k, "effect_pct": round(float(100 * (np.exp(v / 2) - 1)), 1)} for k, v in g.items()]
    return sorted(out, key=lambda d: -abs(d["effect_pct"]))


def load_models(path: Path = MODELS_FILE) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


# ------------------------------------------------------------------ forecast from candles
def forecast_from_bars(bars: pd.DataFrame, models: dict, now: pd.Timestamp) -> dict:
    """Forecasts for both horizons from 1-minute candles (``ts`` = open time).

    Uses the last complete hour H <= now. Returns {"1h": {...}, "24h": {...}}."""
    bars = bars[bars["ts"] + pd.Timedelta(minutes=1) <= now]          # drop the open candle
    close = realized.minute_close(bars)
    h = realized.hourly_measures(close)
    h["rv5"] = realized.hourly_rv5_subsampled(close).where(h["n"] >= realized.MIN_RETURNS_PER_HOUR)
    last_hour = now.floor("1h")
    h = h[h.index <= last_hour]
    out = {}
    for key, horizon in (("1h", 1), ("24h", 24)):
        if key not in models:
            continue
        X = har.har_features(h, horizon).iloc[[-1]]
        var = float(model_from_dict(models[key]).predict(X).iloc[0])
        why = drivers(models[key], X, horizon)
        if not np.isfinite(var) or var <= 0:
            continue
        out[key] = {"variance": var, "sigma_pct": 100 * np.sqrt(var),
                    "annualised_vol_pct": 100 * np.sqrt(var * config.HOURS_PER_YEAR / horizon),
                    "model": f"HAR-S (fitted to {models[key]['fitted_through']})",
                    "inputs_until": X.index[0].isoformat(), "drivers": why}
    return out


def regime_history(bars: pd.DataFrame, model: dict, now: pd.Timestamp) -> pd.DataFrame:
    """Hourly regime probabilities (index = hour END) with the saved HMM (models/regimes.py).

    Same inputs as in training (hourly return, log realised variance, its 24 h average),
    forward-filtered over the window up to the last complete hour: the value at hour H
    uses only candles before H."""
    from regimecast.models import regimes

    bars = bars[bars["ts"] + pd.Timedelta(minutes=1) <= now]
    h = realized.hourly_measures(realized.minute_close(bars))
    h = h[h.index <= now.floor("1h")]
    x = pd.DataFrame({"ret": h["ret"], "log_rv": np.log(h["rv"].clip(lower=1e-12)),
                      "log_rv_24h": np.log(h["rv"].rolling(24, min_periods=18).mean().clip(lower=1e-12))})
    x = x.iloc[24:]  # the first day has no 24 h average yet
    return regimes.live_probabilities(x, model) if not x.empty else pd.DataFrame()


def regime_from_bars(bars: pd.DataFrame, model: dict, now: pd.Timestamp) -> dict | None:
    """Current market regime from 1-minute candles: the last row of regime_history."""
    from regimecast.models import regimes

    hist = regime_history(bars, model, now)
    if hist.empty:
        return None
    p = hist.iloc[-1]
    name = regimes.NAMES[int(p["regime"])]
    return {"regime": name, **{f"p_{n}": float(p[f"p_{n}"]) for n in regimes.NAMES},
            "inputs_until": hist.index[-1].isoformat(), "model": f"HMM regimes (fitted to {model['fitted_through']})",
            "typical_next_24h_vol_pct": {k: round(100 * v, 1) for k, v in model.get("next_24h_vol_by_regime", {}).items()}}


def hourly_candles(bars: pd.DataFrame, now: pd.Timestamp, hist: pd.DataFrame | None = None) -> list[dict]:
    """Complete hourly candles (traded price) from 1-minute candles, oldest first.

    ``t`` is the hour's START (as on exchange charts). With a regime history, each candle
    carries the regime known at its close (hist row at t + 1 h), never a later one."""
    from regimecast.models import regimes

    b = bars[bars["ts"] + pd.Timedelta(minutes=1) <= now].set_index("ts")
    g = b.resample("1h", label="left", closed="left")
    c = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                      "close": g["close"].last(), "volume": g["volume"].sum(), "n": g["close"].count()})
    c = c[(c["n"] == 60) & (c.index + pd.Timedelta(hours=1) <= now)]   # full hours only
    out = []
    for t, r in c.iterrows():
        row = {"t": t.isoformat(), "open": float(r["open"]), "high": float(r["high"]), "low": float(r["low"]),
               "close": float(r["close"]), "volume": float(r["volume"]), "regime": None, "p_stressed": None}
        end = t + pd.Timedelta(hours=1)
        if hist is not None and not hist.empty and end in hist.index:
            row["regime"] = regimes.NAMES[int(hist.at[end, "regime"])]
            row["p_stressed"] = round(float(hist.at[end, "p_stressed"]), 4)
        out.append(row)
    return out


def stress_from_bars(bars: pd.DataFrame, symbol: str, stress_model, regime_model: dict, now: pd.Timestamp,
                     fetch_inputs=None) -> dict | None:
    """Stress gauge for the last complete hour: the live inputs go through the same feature
    code as training (features/build.py), then the saved classifier (models/stress.py)."""
    from regimecast.features import build, live as flive
    from regimecast.models import regimes, stress

    inp = (fetch_inputs or flive.fetch_inputs)(symbol, last=bars)
    h = build.hourly_features(inp)
    h = h[h["ts"] <= now.floor("1h")]
    if h.empty or h["ts"].iloc[-1] != now.floor("1h"):
        return None
    reg = regimes.live_probabilities(regimes.regime_inputs(h).iloc[24:], regime_model)
    dv = flive.fetch_dvol(symbol) if fetch_inputs is None else pd.DataFrame()
    t = pd.DataFrame({"ts": h["ts"], "y_1h": np.nan, "y_24h": np.nan})
    X, _ = stress.dataset(h, t, reg, dv if not dv.empty else None)
    booster, meta = stress_model
    return {**stress.live_gauge(booster, meta, X), "state": market_state(h, X, booster, meta)}


def market_state(h: pd.DataFrame, X: pd.DataFrame, booster, meta: dict) -> dict:
    """The last hour's crowding and trend inputs for Phase 12 (crowd warning, similar moments), from the same
    feature rows as the stress gauge, and the stress probability three hours earlier (stress rising)."""
    num = lambda v: float(v) if pd.notna(v) else None                    # noqa: E731
    last = h.iloc[-1]
    before = X.index[-1] - pd.Timedelta(hours=3)
    p_before = float(booster.predict(X.loc[[before], meta["features"]])[0]) if before in X.index else None
    return {"funding_rate": num(last["funding_rate"]), "ls_top": num(last["ls_top"]),
            "oi_chg_24h": num(last["oi_chg_24h"]), "ret_24h": num(last["ret_24h"]),
            "ret_168h": num(h["ret"].rolling(168, min_periods=120).sum().iloc[-1]),
            "p_stress_3h_ago": p_before, "inputs_until": X.index[-1].isoformat()}


def fetch_bars(symbol: str, minutes: int = HISTORY_MINUTES, client=None) -> pd.DataFrame:
    """Last `minutes` 1-minute candles from Binance USD-M, oldest first."""
    import httpx

    own = client is None
    client = client or httpx.Client(base_url=config.BINANCE_FAPI_URL, timeout=10.0)
    try:
        rows, end = [], None
        while len(rows) < minutes:
            params = {"symbol": symbol, "interval": "1m", "limit": KLINE_LIMIT}
            if end is not None:
                params["endTime"] = end
            r = client.get("/fapi/v1/klines", params=params)
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            rows = batch + rows
            end = batch[0][0] - 1
        rows = rows[-minutes:]
    finally:
        if own:
            client.close()
    df = pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close", "volume", "close_time",
                                     "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"])
    out = pd.DataFrame({"ts": pd.to_datetime(df["open_time"], unit="ms", utc=True)})
    for c in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume"):
        out[c] = df[c].astype(float)
    out["count"] = df["count"].astype(int)
    return out.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


# ------------------------------------------------------------------ background cache
class ForecastCache:
    def __init__(self, symbols=None, fetch=fetch_bars, models=None):
        self.symbols = symbols or config.SYMBOLS
        self.fetch = fetch
        self.models = load_models() if models is None else models
        self.regime_models = None   # loaded on first refresh (models/regimes_live.json)
        self.data: dict[str, dict] = {}
        self.regimes: dict[str, dict] = {}
        self.candles: dict[str, dict] = {}   # hourly candles + regime, for the dashboard chart
        self.stress: dict[str, dict] = {}
        self.state: dict[str, dict] = {}     # crowding and trend inputs of the last hour (Phase 12)
        self.stress_models = None   # loaded on first refresh (models/stress_live/)
        self.fetch_inputs = None    # None -> features.live.fetch_inputs; tests inject a fake
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def refresh(self, symbol: str) -> None:
        if self.regime_models is None:
            from regimecast.models import regimes
            self.regime_models = regimes.load_live()
        if symbol not in self.models and symbol not in self.regime_models:
            return
        now = pd.Timestamp.now(tz="UTC")
        bars = self.fetch(symbol)
        made = datetime.now(timezone.utc).isoformat()
        if symbol in self.models:
            fc = forecast_from_bars(bars, self.models[symbol], now)
            with self._lock:
                self.data[symbol] = {k: {**v, "made_at": made} for k, v in fc.items()}
        hist = None
        if symbol in self.regime_models:
            rg = regime_from_bars(bars, self.regime_models[symbol], now)
            hist = regime_history(bars, self.regime_models[symbol], now)
            if rg:
                with self._lock:
                    self.regimes[symbol] = {**rg, "made_at": made}
        candles = hourly_candles(bars, now, hist)
        with self._lock:
            self.candles[symbol] = {"candles": candles, "made_at": made}
        if self.stress_models is None:
            from regimecast.models import stress
            self.stress_models = {s: m for s in self.symbols if (m := stress.load_live(s)) is not None}
        if symbol in self.stress_models and symbol in self.regime_models:
            g = stress_from_bars(bars, symbol, self.stress_models[symbol], self.regime_models[symbol], now,
                                 self.fetch_inputs)
            if g:
                state = g.pop("state")
                with self._lock:
                    self.stress[symbol] = {**g, "made_at": made}
                    self.state[symbol] = {**state, "made_at": made}

    def refresh_all(self) -> None:
        for s in self.symbols:
            try:
                self.refresh(s)
            except Exception as e:  # Binance hiccup: keep the last forecast, try again later
                log.warning("forecast refresh %s failed: %r", s, e)

    def get(self, symbol: str, horizon: int, max_age_s: float = 3 * 3600) -> dict | None:
        with self._lock:
            fc = self.data.get(symbol, {}).get(f"{horizon}h")
        if not fc:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(fc["made_at"])).total_seconds()
        return fc if age <= max_age_s else None

    def get_stress(self, symbol: str, max_age_s: float = 3 * 3600) -> dict | None:
        with self._lock:
            g = self.stress.get(symbol)
        if not g:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(g["made_at"])).total_seconds()
        return g if age <= max_age_s else None

    def get_state(self, symbol: str, max_age_s: float = 3 * 3600) -> dict | None:
        with self._lock:
            s = self.state.get(symbol)
        if not s:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(s["made_at"])).total_seconds()
        return s if age <= max_age_s else None

    def get_regime(self, symbol: str, max_age_s: float = 3 * 3600) -> dict | None:
        with self._lock:
            rg = self.regimes.get(symbol)
        if not rg:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(rg["made_at"])).total_seconds()
        return rg if age <= max_age_s else None

    def get_candles(self, symbol: str, max_age_s: float = 3 * 3600) -> dict | None:
        with self._lock:
            c = self.candles.get(symbol)
        if not c:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(c["made_at"])).total_seconds()
        return c if age <= max_age_s else None

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop():
            while True:
                self.refresh_all()
                time.sleep(REFRESH_SECONDS)

        self._thread = threading.Thread(target=loop, name="har-live", daemon=True)
        self._thread.start()


cache = ForecastCache()
