"""The live HAR-S + LightGBM combination (vol/combo_live.py): 13A's pass mark that the live forecast equals the offline
model on the same inputs, its drivers, and how the risk check uses it."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from regimecast.features import build
from regimecast.models import stress, volatility as V
from regimecast.risk import fhs_live, liquidation as liq
from regimecast.vol import combo_live as C, live
from tests.test_live_vol import bars

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def now():
    return pd.Timestamp.now(tz="UTC")


def fake_inputs(symbol, last):
    """Everything features/live.py fetches, without the network."""
    bb = last[["ts", "open", "high", "low", "close"]]
    idx = pd.date_range(end=now().floor("5min"), periods=500, freq="5min")
    rng = np.random.default_rng(3)
    m = pd.DataFrame({"available_at": idx, "sum_open_interest": 1000 + np.cumsum(rng.normal(0, 1, 500)),
                      "sum_open_interest_value": 8e7, "sum_toptrader_long_short_ratio": 1.5 + rng.normal(0, 0.1, 500),
                      "count_long_short_ratio": 2.0, "sum_taker_long_short_vol_ratio": 1.0 + rng.normal(0, 0.2, 500)})
    m["period_start"] = m["ts"] = m["available_at"] - pd.Timedelta(minutes=5)
    f = pd.DataFrame({"ts": pd.date_range(end=now(), periods=3, freq="8h"), "funding_rate": 1e-4, "interval_hours": 8})
    return {"last": last, "mark": bb, "index": bb, "premium": bb.assign(close=0.0), "funding": f,
            "metrics": m, "ob": pd.DataFrame()}


def live_bars():
    b = bars(end=now())
    b["quote_volume"], b["taker_buy_volume"], b["count"] = b["close"] * b["volume"], b["volume"] / 2, 10
    return b


@pytest.fixture
def cache():
    c = live.ForecastCache(symbols=["BTCUSDT"], fetch=lambda s: live_bars())
    c.fetch_inputs = fake_inputs
    c.refresh_all()
    return c


def synthetic(n=4000, seed=1):
    """A frame with every model input and a target that depends on recent volatility."""
    rng = np.random.default_rng(seed)
    ix = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    rv = np.exp(rng.normal(-9, 0.6, n))
    X = pd.DataFrame({c: rng.normal(0, 1, n) for c in C.LIVE_FEATURES}, index=ix)
    X["rv_short"], X["rv_mid"], X["rv_long"] = rv, rv * 1.1, rv * 0.9
    X["log_rv_short"], X["log_rv_mid"], X["log_rv_long"] = np.log(rv), np.log(rv * 1.1), np.log(rv * 0.9)
    X["hour"], X["dow"] = ix.hour, ix.dayofweek
    y = pd.Series(rv * np.exp(rng.normal(0, 0.3, n)), index=ix)
    return X, y


# ------------------------------------------------------------------ the shipped models
def test_shipped_models_exist_for_every_coin_and_horizon_without_the_order_book():
    assert not [f for f in C.LIVE_FEATURES if "ob_" in f] and len(C.LIVE_FEATURES) == 20
    for s in SYMBOLS:
        models = C.load(s)
        assert set(models) == {"1h", "24h"}
        for key, m in models.items():
            assert m.fitted_through == "2026-09-30" and m.gbm["features"] == C.LIVE_FEATURES
            assert m.booster.num_trees() == m.gbm["best_iteration"] and m.har.variant == "har_s"


@pytest.mark.parametrize("symbol", SYMBOLS)
@pytest.mark.parametrize("key", ["1h", "24h"])
def test_shipped_files_equal_the_offline_model_on_real_hours(symbol, key):
    """The saved files reproduce, on real archive hours, what the fitted model gave in memory before saving."""
    X, expected = C.check_frame(symbol, key)
    assert len(X) == 24 and X.index.max() <= pd.Timestamp("2026-10-01", tz="UTC")
    np.testing.assert_array_equal(C.load(symbol)[key].predict(X).to_numpy(), expected)


# ------------------------------------------------------------------ live = offline
def test_saved_model_equals_the_offline_combination(tmp_path):
    X, y = synthetic()
    end = str((X.index[-200]).date())
    models = {"1h": C.fit(X, y, 1, end=end), "24h": C.fit(X, y, 24, end=end)}
    C.save("BTCUSDT", models, {"1h": X, "24h": X}, {"1h": y, "24h": y}, out=tmp_path)
    loaded = C.load("BTCUSDT", tmp_path)
    for key, (offline, _) in models.items():
        np.testing.assert_array_equal(loaded[key].predict(X).to_numpy(), offline.predict(X).to_numpy())
        parts = loaded[key].parts(X)
        np.testing.assert_array_equal(parts["combo"], (offline.har.predict(X) + offline.gbm.predict(X)) / 2)


def test_live_forecast_equals_the_offline_model_on_the_same_inputs(cache):
    """Pass mark: from the live inputs, through the training feature code, the cache serves exactly what the
    model files give when the same rows are built offline by volatility.dataset."""
    inp = fake_inputs("BTCUSDT", live_bars())
    h = build.hourly_features(inp)
    h = h[h["ts"] <= now().floor("1h")]
    t = pd.DataFrame({"ts": h["ts"], "y_1h": np.nan, "y_24h": np.nan})
    models = C.load("BTCUSDT")
    for key, H in C.HORIZONS.items():
        X = V.dataset(h, t, H, None)[0]
        fc = cache.get("BTCUSDT", H)
        assert fc["model"].startswith("HAR-S + LightGBM") and fc["inputs_until"] == X.index[-1].isoformat()
        assert fc["variance"] == pytest.approx(float(models[key].predict(X.iloc[[-1]]).iloc[0]), rel=1e-12)


def test_fit_never_uses_a_target_ending_after_the_cutoff():
    X, y = synthetic()
    end = str((X.index[-200]).date())
    _, ok = C.fit(X, y, 24, end=end)
    assert X.index[ok].max() + pd.Timedelta(hours=24) <= pd.Timestamp(end, tz="UTC")
    a, _ = C.fit(X, y, 24, end=end)
    y2 = y.copy()
    y2[X.index > pd.Timestamp(end, tz="UTC") - pd.Timedelta(hours=24)] *= 1000
    b, _ = C.fit(X, y2, 24, end=end)
    np.testing.assert_array_equal(a.predict(X.iloc[:50]), b.predict(X.iloc[:50]))


def test_forecast_reads_only_complete_hours():
    b = live_bars()
    n = now()
    first = live.live_rows(b, "BTCUSDT", n, fake_inputs)
    assert first[0]["ts"].iloc[-1] == n.floor("1h")
    fc = C.forecasts(first[0], first[1], C.load("BTCUSDT"))
    later = pd.concat([first[0], first[0].tail(1).assign(ts=n.floor("1h") + pd.Timedelta(hours=1), rv=1.0)])
    again = C.forecasts(later[later["ts"] <= n.floor("1h")], first[1], C.load("BTCUSDT"))
    assert fc["24h"]["variance"] == again["24h"]["variance"]


# ------------------------------------------------------------------ drivers
def test_gbm_drivers_are_shap_values_that_add_up_to_the_forecast(cache, monkeypatch):
    monkeypatch.setattr(C, "TOP_DRIVERS", 100)
    m = C.load("BTCUSDT")["24h"]
    inp = fake_inputs("BTCUSDT", live_bars())
    h = build.hourly_features(inp)
    X = C.live_X(h[h["ts"] <= now().floor("1h")], None, 24).iloc[[-1]]
    raw = m.booster.predict(X[m.gbm["features"]], pred_contrib=True)[0]
    assert raw.sum() == pytest.approx(float(m.booster.predict(X[m.gbm["features"]])[0]), abs=1e-6)
    d = m.gbm_drivers(X)
    # the % effects, turned back into log variance, add up to the forecast against LightGBM's average
    total = sum(2 * np.log(1 + e["effect_pct"] / 100) for e in d)
    assert total == pytest.approx(raw[:-1].sum(), abs=0.05)     # each effect is rounded to 1 decimal
    assert {"time of day", "day of week", "volatility in the last day"} <= {e["name"] for e in d}
    assert not [e for e in d if e["name"].startswith(("log_", "ob_"))]   # plain names only


def test_forecast_shows_what_moves_each_part(cache):
    fc = cache.get("BTCUSDT", 24)
    assert fc["drivers"] and fc["drivers_gbm"] and len(fc["drivers_gbm"]) <= C.TOP_DRIVERS
    assert fc["sigma_pct"] == pytest.approx(np.sqrt((fc["parts"]["har_s_sigma_pct"] ** 2 + fc["parts"]["gbm_sigma_pct"] ** 2) / 2))


# ------------------------------------------------------------------ the API
def test_risk_check_shows_the_combination_and_keeps_fhs_on_har_s(cache, monkeypatch):
    from regimecast.api import main

    monkeypatch.setattr(main.vol_live, "cache", cache)
    c = TestClient(main.app)
    f = c.get("/forecast", params={"symbol": "BTCUSDT"}).json()
    assert f["model"].startswith("HAR-S + LightGBM") and f["drivers_gbm"]
    r = c.post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 80000, "leverage": 50,
                                    "margin": 100, "horizon_hours": 24}).json()
    assert r["sigma_horizon_pct"] == pytest.approx(f["sigma_pct"], abs=1e-3)
    assert r["volatility_source"].startswith("HAR-S + LightGBM") and r["drivers"] and r["drivers_gbm"]
    # the liquidation chance is still FHS on HAR-S, the pairing the final evaluation tested
    har = cache.get_har_s("BTCUSDT", 24)
    assert har["model"].startswith("HAR-S (") and r["model"] == "fhs_har_s"
    assert r["liquidation_sigma_pct"] == pytest.approx(har["sigma_pct"], abs=1e-3)
    pos = liq.Position("BTCUSDT", "long", entry=80000, qty=100 * 50 / 80000, leverage=50)
    d = liq.adverse_log_distance(pos, liq.liquidation_price(pos))
    assert r["p_liquidation"] == pytest.approx(fhs_live.survival(fhs_live.load()["BTCUSDT"]["24h"]["long"], d, np.sqrt(har["variance"])), abs=2e-5)
    assert any("FHS on the HAR-S forecast" in e for e in r["explanation"])
    assert any("LightGBM part" in e for e in r["explanation"]) or not [x for x in r["drivers_gbm"] if abs(x["effect_pct"]) >= 3]


def test_har_s_serves_while_the_combination_has_no_fresh_inputs(monkeypatch):
    from regimecast.api import main

    def down(symbol, last):
        raise RuntimeError("Binance statistics unavailable")

    c = live.ForecastCache(symbols=["BTCUSDT"], fetch=lambda s: live_bars())
    c.fetch_inputs = down
    c.refresh_all()
    assert c.get("BTCUSDT", 24)["model"].startswith("HAR-S (")      # still a tested forecast
    monkeypatch.setattr(main.vol_live, "cache", c)
    r = TestClient(main.app).post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 80000, "leverage": 10,
                                                       "margin": 100}).json()
    assert r["model"] == "fhs_har_s" and r["sigma_horizon_pct"] == r["liquidation_sigma_pct"] and r["drivers_gbm"] == []


def test_a_combination_forecast_never_reaches_fhs_without_har_s(monkeypatch):
    """FHS was fitted on HAR-S only: with the combination alone, the odds fall back to the random walk."""
    from regimecast.api import main

    c = live.ForecastCache(symbols=["BTCUSDT"], fetch=None, models={})
    monkeypatch.setattr(c, "get", lambda s, h: {"variance": 4e-4, "sigma_pct": 2.0, "model": "HAR-S + LightGBM (fitted to 2026-09-30)",
                                                "inputs_until": "2026-11-02T10:00:00+00:00", "drivers": [], "drivers_gbm": []})
    monkeypatch.setattr(main.vol_live, "cache", c)
    r = TestClient(main.app).post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 80000, "leverage": 10,
                                                       "margin": 100}).json()
    assert r["model"] == "first_passage_baseline" and r["sigma_horizon_pct"] == pytest.approx(2.0)


def test_the_stress_gauge_still_gets_its_inputs_from_the_same_rows(cache):
    g = cache.get_stress("BTCUSDT")
    assert g and g["inputs_until"] == cache.get("BTCUSDT", 1)["inputs_until"] and stress.LIVE_DIR.exists()
    assert "crowd" not in " ".join(cache.get_state("BTCUSDT"))     # the crowd rule's 14-day ranks are no longer fetched
