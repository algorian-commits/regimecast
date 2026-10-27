"""Crash replay (Phase 11, M12): no look-ahead, the final test period stays closed, and the FTX replay
plays through the API."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from regimecast.api import main
from regimecast.models import regimes, stress
from regimecast.replay import build, engine
from regimecast.vol import live as vol_live


def test_each_minute_uses_the_last_complete_hour():
    ends = pd.DatetimeIndex(pd.to_datetime(["2022-11-08 09:00", "2022-11-08 10:00"], utc=True))
    closes = pd.DatetimeIndex(pd.to_datetime(["2022-11-08 08:59", "2022-11-08 09:00", "2022-11-08 09:59",
                                              "2022-11-08 10:00"], utc=True))
    assert engine.hour_index(closes, ends).tolist() == [-1, 0, 0, 1]


def _cut(replay: dict, symbol: str, n: int) -> dict:
    """The replay as it would look if the data stopped after minute n (closes at first_close + n - 1)."""
    c = replay["coins"][symbol]
    last_close = pd.Timestamp(c["minute_start"]) + pd.Timedelta(minutes=n)
    hours = [h for h in c["hours"] if pd.Timestamp(h["t"]) <= last_close]
    coin = {**c, "n_minutes": n, "mark": {k: v[:n] for k, v in c["mark"].items()}, "hours": hours}
    return {**replay, "coins": {symbol: coin}}


@pytest.fixture(scope="module")
def ftx():
    r = engine.load("ftx_2022")
    assert r is not None, "regimecast/replay/events/ftx_2022.json.gz is missing (python scripts/make_replay.py ftx_2022)"
    return r


@pytest.mark.parametrize("side,lev", [("long", 10), ("short", 20)])
def test_replay_frames_never_use_later_minutes(ftx, side, lev):
    full = engine.frames(ftx, "BTCUSDT", side, lev)
    for n in (1, 61, 3000, 4321):
        part = engine.frames(_cut(ftx, "BTCUSDT", n), "BTCUSDT", side, lev, entry=full["entry"])
        for k, v in part["minutes"].items():
            assert v == full["minutes"][k][:n], (k, n)
        assert part["hours"] == full["hours"][:len(part["hours"])]


def test_hourly_model_outputs_never_use_later_hours():
    """The builder's hourly values at H are the same whether the data stops at H or goes on."""
    rng = np.random.default_rng(3)
    ts = pd.date_range("2022-09-01 01:00", periods=24 * 75, freq="1h", tz="UTC")
    n = len(ts)
    rv = np.exp(rng.normal(-11, 0.6, n))
    h = pd.DataFrame({"ts": ts, "rv": rv, "rs_pos": rv / 2, "rs_neg": rv / 2, "bpv": rv * 0.9, "rq": rv ** 2,
                      "ret": rng.normal(0, 0.004, n), "n": 60, "jump": rv * 0.1, "rv5": rv,
                      "close": 20_000 * np.exp(np.cumsum(rng.normal(0, 0.004, n))), "volume_usd": 1e8,
                      "stress_minutes": (rng.random(n) < 0.04) * 5.0, "taker_buy_share": 0.5, "premium_mean": 0.0,
                      "mark_index_gap": 0.0, "range_max_1m": 0.002, "funding_rate": 1e-4, "oi": 1e5, "oi_usd": 2e9,
                      "ls_top": 1.1, "ls_accounts": 1.5, "taker_ls": 1.0, "oi_chg_24h": rng.normal(0, 0.02, n)})
    h["rv_24h"] = h["rv"].rolling(24, min_periods=1).mean()
    h["rv_168h"] = h["rv"].rolling(168, min_periods=1).mean()
    h["ret_24h"] = h["ret"].rolling(24, min_periods=1).sum()
    h["mark"] = h["close"]
    for c in ("ob_spread_bps_mean", "ob_spread_bps_max", "ob_depth_top50_usd_mean", "ob_depth_2bps_usd_min",
              "ob_imbalance_top50_mean", "ob_ofi", "ob_range_bps_mean"):
        h[c] = np.nan
    s = "BTCUSDT"
    har_m, reg_m, st_m = vol_live.load_models()[s], regimes.load_live()[s], stress.load_live(s)
    start, end = ts[24 * 40], ts[-1]
    full = build.hourly_states(h, s, start, end, har_m, reg_m, st_m)
    assert full["p_stress"].notna().all() and full["sigma_24h"].notna().all() and full["regime"].notna().all()
    for cut in (start + pd.Timedelta(hours=5), start + pd.Timedelta(hours=200)):
        part = build.hourly_states(h[h["ts"] <= cut], s, start, cut, har_m, reg_m, st_m)
        cols = ["sigma_1h", "sigma_24h", "p_calm", "p_active", "p_stressed", "regime", "p_stress", "stress_level"]
        pd.testing.assert_frame_equal(part[cols], full.loc[part.index, cols])


def test_the_october_2025_replay_waits_for_the_final_evaluation():
    if build.FINAL_EVAL_DOC.exists():
        pytest.skip("the final evaluation has run")
    with pytest.raises(build.FinalTestPeriodClosed):
        build.build("oct_2025")
    with pytest.raises(build.FinalTestPeriodClosed):
        build.check_window(pd.Timestamp("2025-09-29", tz="UTC"), pd.Timestamp("2025-10-02", tz="UTC"))
    build.check_window(pd.Timestamp("2022-11-06", tz="UTC"), pd.Timestamp("2022-11-11", tz="UTC"))
    assert not (build.EVENTS_DIR / "oct_2025.json.gz").exists()


def test_ftx_replay_through_the_api():
    c = TestClient(main.app)
    ev = {e["id"]: e for e in c.get("/replay/events").json()}
    assert ev["ftx_2022"]["available"] and not ev["oct_2025"]["available"]
    assert c.get("/replay/oct_2025").status_code == 404
    r = c.get("/replay/ftx_2022", params={"symbol": "BTCUSDT", "side": "long", "leverage": 10}).json()
    m = r["minutes"]
    assert r["n_minutes"] == len(m["price"]) == len(m["p_liquidation"]) == 5 * 24 * 60
    assert 19_000 < r["liquidation_price"] < 19_500
    assert r["liquidated_at"].startswith("2022-11-08")        # BTC fell through 19,240 on 8 Nov
    i = m["price"].index(next(p for p in m["price"] if p is not None and p < r["liquidation_price"]))
    assert m["p_liquidation"][i] == 1.0 and m["p_liquidation"][0] < 0.05
    stressed = [h for h in r["hours"] if h["regime"] == "stressed"]
    assert stressed and min(h["t"] for h in stressed) >= "2022-11-07"   # calm before the crash
    assert max(h["p_stress"] for h in r["hours"]) > 0.3
    assert all(h["safe_leverage"] is not None for h in r["hours"][1:])
    short = c.get("/replay/ftx_2022", params={"symbol": "SOLUSDT", "side": "short", "leverage": 3}).json()
    assert short["liquidated_at"] is None and short["minutes"]["p_liquidation"][-1] < 0.5
