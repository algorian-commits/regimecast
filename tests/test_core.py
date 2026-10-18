"""Core tests. All run offline on small synthetic data."""
import io
import json
import math
import zipfile

import numpy as np
import pandas as pd
import pytest

from regimecast.data import binance_archive as ba
from regimecast.data import validate as val
from regimecast.recorder import recorder as rec
from regimecast.risk import liquidation as liq
from regimecast.vol import evaluate as ev
from regimecast.vol import har, realized


# ------------------------------------------------------------------ helpers
def gbm_minutes(n_min=60 * 24 * 60, sigma_annual=0.6, seed=1, start="2024-01-01"):
    rng = np.random.default_rng(seed)
    s = sigma_annual / math.sqrt(365 * 24 * 60)
    # time-varying vol so HAR has something to learn
    vol = s * np.exp(0.5 * np.sin(np.arange(n_min) / (60 * 24 * 3)))
    r = rng.standard_normal(n_min) * vol
    close = 30000 * np.exp(np.cumsum(r))
    ts = pd.date_range(start, periods=n_min, freq="1min", tz="UTC")
    noise = np.abs(rng.standard_normal(n_min)) * vol * close * 0.5
    return pd.DataFrame({"ts": ts, "open": np.r_[close[0], close[:-1]], "close": close,
                         "high": np.maximum(close, np.r_[close[0], close[:-1]]) + noise,
                         "low": np.minimum(close, np.r_[close[0], close[:-1]]) - noise})


# ------------------------------------------------------------------ archive
def test_urls_and_plan():
    ds = ba.DATASETS["markPriceKlines"]
    assert ba.archive_url(ds, "BTCUSDT", "2024-01", "monthly").endswith(
        "/data/futures/um/monthly/markPriceKlines/BTCUSDT/1m/BTCUSDT-1m-2024-01.zip")
    assert ba.archive_url(ba.DATASETS["fundingRate"], "BTCUSDT", "2024-01", "monthly").endswith(
        "/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2024-01.zip")
    from datetime import date
    plan = ba.plan_periods(ds, date(2026, 8, 1), date(2026, 10, 3))
    assert ("monthly", "2026-08") in plan and ("monthly", "2026-09") in plan
    assert ("daily", "2026-10-03") in plan and not any(p == "2026-10" for _, p in plan)
    plan_m = ba.plan_periods(ba.DATASETS["metrics"], date(2026, 9, 29), date(2026, 10, 2))
    assert [p for _, p in plan_m] == ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"]


def test_parse_klines_with_and_without_header():
    row = "1704067200000,42000.1,42100,41900,42050,12.5,1704067259999,525000,100,6,252000,0\n"
    hdr = ",".join(ba.KLINE_COLS) + "\n"
    a = ba.parse_csv("klines", (hdr + row).encode())
    b = ba.parse_csv("klines", row.encode())
    assert a.ts[0] == pd.Timestamp("2024-01-01", tz="UTC") == b.ts[0]
    assert a.close[0] == 42050 == b.close[0]
    us = row.replace("1704067200000", "1704067200000000", 1)
    assert ba.parse_csv("klines", us.encode()).ts[0] == pd.Timestamp("2024-01-01", tz="UTC")


def test_parse_funding_and_metrics():
    f = "calc_time,funding_interval_hours,last_funding_rate\n1704067200000,8,0.0001\n"
    df = ba.parse_csv("fundingRate", f.encode())
    assert df.last_funding_rate[0] == 0.0001
    m = ("create_time,symbol,sum_open_interest,sum_open_interest_value,count_toptrader_long_short_ratio,"
         "sum_toptrader_long_short_ratio,count_long_short_ratio,sum_taker_long_short_vol_ratio\n"
         "2024-01-01 00:05:00,BTCUSDT,80000,3.3e9,1.2,1.1,1.3,0.9\n")
    dm = ba.parse_csv("metrics", m.encode())
    assert dm.ts[0] == pd.Timestamp("2024-01-01 00:05", tz="UTC")


def test_download_one_verifies_checksum(tmp_path, monkeypatch):
    import hashlib
    monkeypatch.setattr(ba.config, "DATA_DIR", tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("x.csv", "1704067200000,1,2,0.5,1.5,1,1704067259999,1,1,1,1,0\n")
    blob = buf.getvalue()

    class R:
        def __init__(self, c): self.content, self.status_code = c, 200
        def raise_for_status(self): pass

    good = hashlib.sha256(blob).hexdigest().encode() + b"  x.zip"

    class Client:
        def __init__(self, chk): self.chk = chk
        def get(self, url, timeout=60): return R(self.chk if url.endswith("CHECKSUM") else blob)

    assert ba.download_one(Client(good), "klines", "BTCUSDT", "monthly", "2024-01").startswith("ok")
    assert ba.download_one(Client(good), "klines", "BTCUSDT", "monthly", "2024-01") == "skip"
    with pytest.raises(IOError):
        ba.download_one(Client(b"0" * 64), "klines", "BTCUSDT", "monthly", "2024-02")


# ------------------------------------------------------------------ validation
def test_gap_report_and_metrics_fix(tmp_path, monkeypatch):
    ts = pd.Series(pd.date_range("2024-01-01", periods=10, freq="1min", tz="UTC"))
    ts = pd.concat([ts.drop([3, 4]), ts.iloc[[7]]]).reset_index(drop=True)
    r = val.gap_report(ts)
    assert r["duplicates"] == 1 and r["missing_steps"] == 2

    monkeypatch.setattr(val.config, "DATA_DIR", tmp_path)
    d = tmp_path / "parquet" / "metrics" / "BTCUSDT"
    d.mkdir(parents=True)
    pd.DataFrame({"ts": pd.to_datetime(["2026-06-24 10:05", "2026-06-25 10:00"], utc=True),
                  "sum_open_interest": [1.0, 2.0]}).to_parquet(d / "a.parquet")
    m = val.load_metrics("BTCUSDT")
    # old end-labelled row 10:05 -> period starts 10:00, known at 10:05
    assert m.period_start[0] == pd.Timestamp("2026-06-24 10:00", tz="UTC")
    assert m.available_at[0] == pd.Timestamp("2026-06-24 10:05", tz="UTC")
    # new start-labelled row 10:00 -> known at 10:05
    assert m.available_at[1] == pd.Timestamp("2026-06-25 10:05", tz="UTC")


# ------------------------------------------------------------------ recorder
def test_recorder_parsing_and_store(tmp_path):
    fo = {"stream": "!forceOrder@arr", "data": {"e": "forceOrder", "E": 1760126100000, "o": {
        "s": "BTCUSDT", "S": "SELL", "o": "LIMIT", "f": "IOC", "q": "0.5", "p": "105000", "ap": "105100",
        "X": "FILLED", "l": "0.5", "z": "0.5", "T": 1760126100000}}}
    s, row = rec.parse_message(fo)
    assert s == "liquidations" and row["notional_usd"] == pytest.approx(52550)
    mp = {"data": {"e": "markPriceUpdate", "E": 1760126100000, "s": "BTCUSDT", "p": "105000.1",
                   "i": "105010.2", "P": "105000", "r": "0.0001", "T": 1760140800000}}
    assert rec.parse_message(mp)[1]["funding_rate"] == 0.0001
    dp = {"data": {"e": "depthUpdate", "E": 1760126100000, "T": 1760126100000, "s": "BTCUSDT", "U": 1, "u": 2,
                   "pu": 0, "b": [["105000", "1.5"]] * 20, "a": [["105001", "2"]] * 20}}
    s, row = rec.parse_message(dp)
    assert s == "depth" and row["b_p19"] == 105000 and row["a_q0"] == 2

    r = rec.Recorder(["BTCUSDT"], tmp_path)
    r.handle(json.dumps(fo)); r.handle(json.dumps(dp)); r.handle(json.dumps(dp))  # 2nd depth sampled out
    assert r.store.counts["depth"] == 1 and r.store.counts["liquidations"] == 1
    assert r.store.flush() == 2
    assert rec.compact(tmp_path, today="2030-01-01") == 2
    files = list(tmp_path.rglob("*.parquet"))
    assert all(f.name == "day.parquet" for f in files)
    assert "!forceOrder@arr" in r.stream_url() and "btcusdt@depth20@500ms" in r.stream_url()


def test_recorder_http(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(rec.config, "DATA_DIR", tmp_path)
    monkeypatch.setenv("RECORDER_TOKEN", "secret")
    app = rec.create_app(start_tasks=False)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/files").status_code == 401
        assert c.get("/files?token=secret").json() == []
        assert c.get("/files/../../etc/passwd?token=secret").status_code == 404


# ------------------------------------------------------------------ liquidation
def test_liquidation_price_matches_binance_examples():
    # 10x isolated long, MMR 0.5%, no cum  ->  about 9.55% below entry
    tiers = {"BTCUSDT": liq.build_tiers([(0, 0.005, 125)])}
    p = liq.Position("BTCUSDT", "long", entry=100.0, qty=1.0, leverage=10, brackets=tiers)
    lp = liq.liquidation_price(p)
    assert lp == pytest.approx(90 / 0.995, rel=1e-9)
    s = liq.Position("BTCUSDT", "short", entry=100.0, qty=1.0, leverage=10, brackets=tiers)
    assert liq.liquidation_price(s) == pytest.approx(110 / 1.005, rel=1e-9)
    # at the liquidation price, equity == maintenance margin
    equity = p.initial_margin + (lp - p.entry) * p.qty
    assert equity == pytest.approx(0.005 * lp * p.qty, rel=1e-9)
    # funding paid moves the liquidation price closer
    assert liq.liquidation_price(p, funding_paid=1.0) > lp
    # 1x long cannot be liquidated (price would have to go below 0)... nearly
    one = liq.Position("BTCUSDT", "long", entry=100.0, qty=1.0, leverage=1, brackets=tiers)
    assert liq.liquidation_price(one) < 1.0


def test_tier_cum_continuity():
    t = liq.DEFAULT_BRACKETS["BTCUSDT"]
    for a, b in zip(t, t[1:]):
        mm_a = b.floor * a.mmr - a.cum
        mm_b = b.floor * b.mmr - b.cum
        assert mm_a == pytest.approx(mm_b)


def test_first_passage_matches_monte_carlo():
    rng = np.random.default_rng(0)
    steps, paths, sigma = 1440, 4000, 0.03
    r = rng.standard_normal((paths, steps)) * sigma / math.sqrt(steps)
    worst = (-np.cumsum(r, axis=1)).max(axis=1)
    for d in (0.02, 0.04, 0.06):
        mc = (worst >= d).mean()
        assert liq.first_passage_prob(d, sigma) == pytest.approx(mc, abs=0.02)


def test_fhs_model_and_safe_leverage():
    df = gbm_minutes(n_min=60 * 24 * 40)
    mae = liq.max_adverse_excursion(df, 60 * 24)
    sigma = pd.Series(0.6 * math.sqrt(1 / 365), index=mae.index)
    m = liq.FHSLiquidationModel(24).fit(mae, sigma)
    p_near, p_far = m.prob("long", 0.01, sigma.iloc[0]), m.prob("long", 0.2, sigma.iloc[0])
    assert p_near > p_far and 0 < p_far < 0.05
    tiers = {"BTCUSDT": liq.build_tiers([(0, 0.004, 125)])}
    pos = liq.Position("BTCUSDT", "long", entry=30000, qty=0.1, leverage=10, brackets=tiers)
    sig = 0.6 * math.sqrt(1 / 365)
    fn = lambda p: liq.first_passage_prob(liq.adverse_log_distance(p, liq.liquidation_price(p)), sig)
    lev = liq.max_safe_leverage(fn, pos, 0.01)
    assert 1 < lev < 125
    hi = liq.Position(**{**pos.__dict__, "leverage": lev + 1, "qty": pos.initial_margin * (lev + 1) / 30000})
    assert fn(hi) > 0.01


# ------------------------------------------------------------------ volatility
def test_targets_alignment():
    df = gbm_minutes(n_min=60 * 24 * 5)
    close = realized.minute_close(df)
    h = realized.build_targets(close)
    # y_1h at t equals rv at t+1h
    t = h.index[30]
    assert h.loc[t, "y_1h"] == pytest.approx(h.loc[t + pd.Timedelta(hours=1), "rv"])
    # rv at hour-end t uses returns ending in (t-1h, t]
    r = np.log(close).diff()
    r.index = r.index + pd.Timedelta(minutes=1)
    manual = (r[(r.index > t - pd.Timedelta(hours=1)) & (r.index <= t)] ** 2).sum()
    assert h.loc[t, "rv"] == pytest.approx(manual)
    assert h.loc[t, "y_24h"] == pytest.approx(h.loc[t + pd.Timedelta(hours=1): t + pd.Timedelta(hours=24), "rv5"].sum())


def test_har_walk_forward_and_tests():
    df = gbm_minutes(n_min=60 * 24 * 150)
    h = realized.build_targets(realized.minute_close(df))
    X = har.har_features(h, 1)
    y = h["y_1h"]
    preds = {}
    for v in ("har", "har_s", "harq"):
        preds[v] = ev.walk_forward(X, y, lambda v=v: har.HARModel(v), "2024-04-15", horizon_hours=1,
                                   refit_every_hours=24 * 15, min_train_hours=24 * 30)
    # naive "yesterday's average" forecast should lose to HAR
    preds["naive"] = X["rv_mid"].reindex(preds["har"].index) / 24
    tab = ev.summary_table(y, preds, baseline="har", horizon=1)
    assert tab.loc["har", "qlike"] < tab.loc["naive", "qlike"]
    assert tab.loc["har", "qlike"] < 0.5
    losses = pd.DataFrame({k: ev.qlike(y.reindex(v.index), v) for k, v in preds.items()}).dropna()
    mcs = ev.model_confidence_set(losses, B=200)
    assert "naive" not in mcs["included"]


def test_dm_test_detects_difference():
    rng = np.random.default_rng(0)
    a = rng.standard_normal(2000) ** 2
    b = a + 0.3 + 0.1 * rng.standard_normal(2000)
    r = ev.dm_test(a, b, horizon=24)
    assert r["better"] == "A" and r["p_value"] < 0.01
    assert ev.dm_test(a, a + 0.1 * rng.standard_normal(2000), 1)["p_value"] > 0.01


# ------------------------------------------------------------------ API
def test_api_risk_check():
    from fastapi.testclient import TestClient
    from regimecast.api.main import app
    c = TestClient(app)
    r = c.post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 100000, "leverage": 10,
                                    "margin": 1000, "stop": 85000, "horizon_hours": 24})
    assert r.status_code == 200, r.text
    d = r.json()
    assert 89000 < d["liquidation_price"] < 91000
    assert d["stop"]["stop_inside_liquidation"] is True
    assert 0 <= d["p_liquidation"] <= 1 and d["max_safe_leverage"] > 0
    assert any("placeholder" in w.lower() for w in d["warnings"])
    assert c.post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 1, "leverage": 10}).status_code == 422
    assert c.get("/forecast").status_code == 503
