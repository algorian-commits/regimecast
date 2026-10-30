"""Phase 13B: crash mode for the long side's liquidation probability."""
import numpy as np
import pandas as pd

from regimecast.models import crash_mode as CM
from regimecast.models import liquidation_risk as LR


def _synthetic(seed=0, hours=24 * 900):
    """Hourly origins with a stress probability; in stressed hours longs fall much further."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2020-01-01 01:00", periods=hours, freq="1h", tz="UTC")
    p = pd.Series(np.clip(rng.beta(0.5, 6, hours), 0, 1), index=idx)
    var = pd.Series(np.full(hours, 0.02 ** 2), index=idx)
    scale = np.where(p >= 0.1, 4.0, 1.0)
    t = pd.DataFrame({"ts": idx,
                      "mae_long_24h": np.abs(rng.normal(0, 0.02, hours)) * scale,
                      "mae_short_24h": np.abs(rng.normal(0, 0.02, hours))})
    return var, t, p


def test_plain_fhs_is_reproduced_and_short_side_is_never_changed():
    var, t, p = _synthetic()
    cands = CM.candidates()
    out = CM.evaluate("BTCUSDT", 24, var, t, p, cands, eval_start="2021-01-01")
    ref = LR.evaluate("BTCUSDT", 24, pd.DataFrame(index=var.index), var, t, eval_start="2021-01-01",
                      features=[], with_qgbm=False)
    j = out.merge(ref[["ts", "side", "leverage", "fhs"]], on=["ts", "side", "leverage"], suffixes=("", "_ref"))
    assert len(j) == len(out) == len(ref)
    assert np.allclose(j["fhs"], j["fhs_ref"])
    short = out[out["side"] == "short"]
    for c in cands:
        assert np.array_equal(short[CM.name(c)], short["fhs"])


def test_crash_mode_raises_long_risk_in_stressed_hours_only_and_fixes_the_gap():
    var, t, p = _synthetic()
    c = {"train_cut": 0.10, "combine": "replace", "floor": False}
    out = CM.evaluate("BTCUSDT", 24, var, t, p, [c], eval_start="2021-01-01")
    m = CM.name(c)
    long = out[out["side"] == "long"]
    calm = long[long["p_stress"] < CM.SWITCH_ON]
    assert np.array_equal(calm[m], calm["fhs"])
    r = CM.score(out, [m])
    assert r[f"{m}_hot_long_gap"] < r["fhs_hot_long_gap"] / 3
    assert r[f"{m}_hot_long_mean"] > r["fhs_hot_long_mean"]


def test_no_lookahead_future_outcomes_and_stress_do_not_move_past_predictions():
    var, t, p = _synthetic()
    cands = CM.candidates()
    a = CM.evaluate("BTCUSDT", 24, var, t, p, cands, eval_start="2021-01-01")
    T = pd.Timestamp("2021-09-01", tz="UTC")
    t2, p2 = t.copy(), p.copy()
    t2.loc[t2["ts"] > T - pd.Timedelta(hours=24), ["mae_long_24h", "mae_short_24h"]] *= 7   # outcomes known after T
    p2[p2.index > T] = 0.9
    b = CM.evaluate("BTCUSDT", 24, var, t2, p2, cands, eval_start="2021-01-01")
    cols = ["fhs", *[CM.name(c) for c in cands]]
    early_a, early_b = a[a["ts"] <= T], b[b["ts"] <= T]
    assert len(early_a) == len(early_b) > 0
    assert np.allclose(early_a[cols].to_numpy(), early_b[cols].to_numpy())


def test_inactive_without_enough_past_stress_and_combine_floor_rules():
    rng = np.random.default_rng(3)
    z = pd.Series(np.abs(rng.normal(0, 1, 2000)))
    mae = z * 0.02
    few = pd.Series(np.where(np.arange(2000) < 100, 0.5, 0.0))
    cm = CM.CrashMode(0.10, "replace", False).fit(z, mae, few)
    assert not cm.active
    d, sig, plain = np.full(5, 0.05), np.full(5, 0.02), np.full(5, 0.01)
    assert np.array_equal(cm.predict(d, sig, np.full(5, 0.9), plain), plain)
    many = pd.Series(np.full(2000, 0.5))
    mx = CM.CrashMode(0.10, "max", True).fit(z, mae, many)
    big = np.full(5, 0.99)
    assert np.all(mx.predict(d, sig, np.full(5, 0.9), big) >= big)
    fl = CM.CrashMode(0.10, "replace", True).fit(z, mae * 10, many)       # raw moves larger than scaled ones
    nofl = CM.CrashMode(0.10, "replace", False).fit(z, mae * 10, many)
    assert np.all(fl.predict(d, sig, np.full(5, 0.9), plain) >= nofl.predict(d, sig, np.full(5, 0.9), plain))
    assert np.array_equal(fl.predict(d, sig, np.array([0.0, 0.05, np.nan, 0.09, 0.0]), plain), plain)
