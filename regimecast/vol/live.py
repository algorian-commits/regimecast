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
    """Fit the live model for both horizons on the development period only."""
    from regimecast.data import store

    end = pd.Timestamp(config.HOLDOUT_START, tz="UTC")
    h = realized.build_targets(realized.minute_close(store.minute_bars(symbol, "last", end=end)))
    out = {}
    for key, horizon, target in (("1h", 1, "y_1h"), ("24h", 24, "y_24h")):
        X, y = har.har_features(h, horizon), h[target]
        ok = y.index <= end - pd.Timedelta(hours=horizon)   # every target fully before the final test period
        m = har.HARModel(variant).fit(X[ok], y[ok])
        out[key] = model_to_dict(m, (end - pd.Timedelta(days=1)).strftime("%Y-%m-%d"), int(ok.sum()))
    return out


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
        if not np.isfinite(var) or var <= 0:
            continue
        out[key] = {"variance": var, "sigma_pct": 100 * np.sqrt(var),
                    "annualised_vol_pct": 100 * np.sqrt(var * config.HOURS_PER_YEAR / horizon),
                    "model": f"HAR-S (fitted to {models[key]['fitted_through']})",
                    "inputs_until": X.index[0].isoformat()}
    return out


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
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = df[c].astype(float)
    return out.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


# ------------------------------------------------------------------ background cache
class ForecastCache:
    def __init__(self, symbols=None, fetch=fetch_bars, models=None):
        self.symbols = symbols or config.SYMBOLS
        self.fetch = fetch
        self.models = load_models() if models is None else models
        self.data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def refresh(self, symbol: str) -> None:
        if symbol not in self.models:
            return
        now = pd.Timestamp.now(tz="UTC")
        fc = forecast_from_bars(self.fetch(symbol), self.models[symbol], now)
        made = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.data[symbol] = {k: {**v, "made_at": made} for k, v in fc.items()}

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

    def start(self) -> None:
        if self._thread is not None or not self.models:
            return

        def loop():
            while True:
                self.refresh_all()
                time.sleep(REFRESH_SECONDS)

        self._thread = threading.Thread(target=loop, name="har-live", daemon=True)
        self._thread.start()


cache = ForecastCache()
