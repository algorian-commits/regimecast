"""Live HAR-S forecasts (vol/live.py) and how the API uses them."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from regimecast.vol import live

NOW = pd.Timestamp("2026-10-26 14:37:20", tz="UTC")


def bars(days=31, seed=0, end=NOW):
    rng = np.random.default_rng(seed)
    ts = pd.date_range(end=end.floor("1min"), periods=days * 1440, freq="1min")
    close = 80_000 * np.exp(np.cumsum(rng.normal(0, 0.0008, len(ts))))
    return pd.DataFrame({"ts": ts, "open": close, "high": close, "low": close, "close": close,
                         "volume": 1.0})


def test_shipped_models_exist_for_every_coin_and_are_fitted_before_the_final_period():
    m = live.load_models()
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        assert set(m[s]) == {"1h", "24h"} and m[s]["24h"]["variant"] == "har_s"
        assert m[s]["1h"]["fitted_through"] <= "2025-09-30"


def test_forecast_uses_complete_hours_only():
    models = live.load_models()["BTCUSDT"]
    fc = live.forecast_from_bars(bars(), models, NOW)
    assert fc["1h"]["inputs_until"].startswith("2026-10-26T14:00")   # 14:00-14:37 is not complete
    assert 0 < fc["1h"]["variance"] < fc["24h"]["variance"]
    # random walk with 0.08% per minute -> about 0.08% * sqrt(60) per hour
    assert fc["1h"]["sigma_pct"] == pytest.approx(0.08 * np.sqrt(60), rel=0.5)
    # the open candle and anything after "now" must not matter
    later = pd.concat([bars(), bars(days=1, seed=9, end=NOW + pd.Timedelta(hours=3)).assign(close=1e9)])
    later = later.drop_duplicates("ts").sort_values("ts")
    assert live.forecast_from_bars(later[later["ts"] <= NOW + pd.Timedelta(hours=3)], models, NOW) == fc


def test_fetch_pages_backwards():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def get(self, path, params):
            self.calls.append(params)
            end = params.get("endTime", 10_000 * 60_000)
            opens = [end - (i + 1) * 60_000 + 1 for i in range(params["limit"])][::-1]

            class R:
                def raise_for_status(self):
                    pass

                def json(self):
                    return [[o, "1", "1", "1", "1", "2", o + 59_999, "2", 1, "1", "1", "0"] for o in opens]
            return R()

    c = FakeClient()
    df = live.fetch_bars("BTCUSDT", minutes=3200, client=c)
    assert len(df) == 3200 and df["ts"].is_monotonic_increasing and df["ts"].is_unique
    assert len(c.calls) == 3


def test_api_uses_live_forecast_and_drops_placeholder_warning(monkeypatch):
    from regimecast.api import main

    cache = live.ForecastCache(symbols=["BTCUSDT"], fetch=lambda s: bars(end=pd.Timestamp.now(tz="UTC")))
    cache.refresh_all()
    monkeypatch.setattr(main.vol_live, "cache", cache)
    c = TestClient(main.app)
    f = c.get("/forecast", params={"symbol": "BTCUSDT"}).json()
    assert f["horizon_hours"] == 24 and f["model"].startswith("HAR-S") and f["variance"] > 0
    r = c.post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 80000, "leverage": 10,
                                    "margin": 100, "horizon_hours": 24}).json()
    assert "HAR-S" in r["volatility_source"] and not any("placeholder" in w.lower() for w in r["warnings"])
    assert r["sigma_horizon_pct"] == pytest.approx(f["sigma_pct"], abs=1e-3)   # API rounds to 3 decimals
    # no forecast -> placeholder, clearly flagged
    monkeypatch.setattr(main.vol_live, "cache", live.ForecastCache(symbols=["BTCUSDT"], fetch=None, models={}))
    r = c.post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 80000, "leverage": 10,
                                    "margin": 100, "horizon_hours": 24}).json()
    assert r["volatility_source"].startswith("PLACEHOLDER") and any("placeholder" in w.lower() for w in r["warnings"])
