"""Feature builder: time conventions and leakage tests.

Every model has to pass the same two checks on its inputs:
* changing any input that becomes known after T leaves every row at or before T unchanged;
* dropping those inputs altogether does the same.
"""
import numpy as np
import pandas as pd
import pytest

from regimecast.features import build

START = pd.Timestamp("2025-01-01", tz="UTC")
MIN = pd.Timedelta(minutes=1)


def synthetic_inputs(days: int = 3, seed: int = 0) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    n = days * 24 * 60
    ts = pd.date_range(START, periods=n, freq="1min")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    open_ = np.r_[close[0], close[:-1]]
    hi = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0005, n))
    lo = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0005, n))
    vol = rng.uniform(1, 10, n)
    vol[600:610] = 0  # a 10-minute "halt": frozen candles with zero volume
    last = pd.DataFrame({"ts": ts, "open": open_, "high": hi, "low": lo, "close": close, "volume": vol,
                         "quote_volume": vol * close, "count": 10, "taker_buy_volume": vol * rng.uniform(0.3, 0.7, n)})
    bars = lambda x: pd.DataFrame({"ts": ts, "open": x, "high": x, "low": x, "close": x})  # noqa: E731
    mark = bars(close * (1 + rng.normal(0, 1e-4, n)))
    index = bars(close * (1 + rng.normal(0, 1e-4, n)))
    premium = bars(rng.normal(0, 1e-4, n))
    f_ts = pd.date_range(START, periods=days * 3, freq="8h")
    funding = pd.DataFrame({"ts": f_ts, "funding_rate": rng.normal(1e-4, 5e-5, len(f_ts)), "interval_hours": 8})
    m_start = pd.date_range(START, periods=days * 288, freq="5min")
    metrics = pd.DataFrame({"period_start": m_start, "available_at": m_start + pd.Timedelta(minutes=5),
                            "sum_open_interest": 1000 + np.cumsum(rng.normal(0, 1, len(m_start))),
                            "sum_open_interest_value": 1e5, "sum_toptrader_long_short_ratio": 1.2,
                            "count_long_short_ratio": 1.1, "sum_taker_long_short_vol_ratio": 0.9})
    ob_ts = pd.date_range(START + pd.Timedelta(days=1), periods=(days - 1) * 1440, freq="1min") + MIN
    ob = pd.DataFrame({"ts": ob_ts, **{c: rng.uniform(0.5, 2.0, len(ob_ts)) for c in build.OB_COLS}})
    return {"last": last, "mark": mark, "index": index, "premium": premium,
            "funding": funding, "metrics": metrics, "ob": ob}


# When each input row becomes known
KNOWN_AT = {"last": lambda d: d["ts"] + MIN, "mark": lambda d: d["ts"] + MIN, "index": lambda d: d["ts"] + MIN,
            "premium": lambda d: d["ts"] + MIN, "funding": lambda d: d["ts"],
            "metrics": lambda d: d["available_at"], "ob": lambda d: d["ts"]}


def after(inp: dict, cut: pd.Timestamp, mode: str, seed: int = 1) -> dict:
    """Copy of the inputs with every row known after `cut` scrambled or dropped."""
    rng = np.random.default_rng(seed)
    out = {}
    for k, d in inp.items():
        late = KNOWN_AT[k](d) > cut
        d = d.copy()
        if mode == "drop":
            d = d[~late]
        else:
            for c in d.columns:
                if c not in ("ts", "available_at", "period_start") and pd.api.types.is_float_dtype(d[c]):
                    d.loc[late, c] = d.loc[late, c] * rng.uniform(0.5, 2.0, int(late.sum()))
        out[k] = d.reset_index(drop=True)
    return out


def assert_same_up_to(a: pd.DataFrame, b: pd.DataFrame, cut: pd.Timestamp) -> None:
    a = a[a["ts"] <= cut].set_index("ts")
    b = b[b["ts"] <= cut].set_index("ts").reindex(a.index)
    assert len(a) > 20
    pd.testing.assert_frame_equal(a, b[a.columns], check_exact=False, rtol=1e-12)


@pytest.mark.parametrize("mode", ["scramble", "drop"])
@pytest.mark.parametrize("cut", ["2025-01-02 13:00", "2025-01-02 13:37"])
def test_no_lookahead_minute_and_hour(mode, cut):
    inp = synthetic_inputs()
    cut = pd.Timestamp(cut, tz="UTC")
    changed = after(inp, cut, mode)
    assert_same_up_to(build.minute_features(inp), build.minute_features(changed), cut)
    assert_same_up_to(build.hourly_features(inp), build.hourly_features(changed), cut)


def test_scrambling_the_future_does_change_later_rows():
    # guard against a test that passes because nothing depends on the inputs at all
    inp = synthetic_inputs()
    cut = pd.Timestamp("2025-01-02 13:00", tz="UTC")
    a, b = build.hourly_features(inp), build.hourly_features(after(inp, cut, "scramble"))
    later = a["ts"] > cut + pd.Timedelta(hours=2)
    assert not np.allclose(a.loc[later, "rv"], b.loc[later, "rv"], equal_nan=True)


def test_time_conventions():
    inp = synthetic_inputs()
    m = build.minute_features(inp).set_index("ts")
    bar = inp["last"].set_index("ts")
    t = pd.Timestamp("2025-01-01 10:00", tz="UTC")
    # the candle that opens at 09:59 is known at 10:00
    assert m.loc[t, "close"] == bar.loc[t - MIN, "close"]
    # open interest for 10:00-10:05 is known at 10:05, not before
    met = inp["metrics"].set_index("available_at")
    assert m.loc[pd.Timestamp("2025-01-01 10:04", tz="UTC"), "oi"] == met.loc[pd.Timestamp("2025-01-01 10:00", tz="UTC"), "sum_open_interest"]
    assert m.loc[pd.Timestamp("2025-01-01 10:05", tz="UTC"), "oi"] == met.loc[pd.Timestamp("2025-01-01 10:05", tz="UTC"), "sum_open_interest"]
    # zero-volume (halt) minutes carry no return information
    assert m["ret_1m"].iloc[606:611].isna().all() or (m["ret_1m"].iloc[606:611] == 0).all()
    # order-book features exist only where Bybit data exists (from day 2)
    h = build.hourly_features(inp, m.reset_index()).set_index("ts")
    assert h.loc[:"2025-01-01 23:00", "ob_spread_bps_mean"].isna().all()
    assert h.loc["2025-01-02 01:00":, "ob_spread_bps_mean"].notna().all()
    # hourly rows are labelled by the hour END: the 10:00 row sums the 09:00-10:00 minutes
    assert h.loc[t, "volume_usd"] == pytest.approx(m.loc[t - pd.Timedelta(minutes=59): t, "volume_usd"].sum())


def test_stress_minutes_need_a_run_and_use_only_the_past():
    idx = pd.date_range("2025-01-01", periods=60 * 24 * 40, freq="1min", tz="UTC")
    rng = np.random.default_rng(5)
    rv = pd.Series(rng.uniform(1, 2, len(idx)), index=idx)
    burst = idx[len(idx) - 2000: len(idx) - 1990]       # 10 very high minutes
    rv[burst] = 100.0
    s = build.stress_minutes(rv)
    assert s[burst[:4]].sum() == 0                       # first 4 minutes of the run don't count (yet)
    assert s[burst[4:]].all()                            # from the 5th minute it is a stress event
    assert s[: idx[len(idx) - 2001]].sum() < 20          # quiet data: almost never
    # changing anything later never changes earlier flags
    later = rv.copy()
    later[idx[-500:]] = 1000.0
    assert (build.stress_minutes(later)[: idx[-501]] == s[: idx[-501]]).all()
