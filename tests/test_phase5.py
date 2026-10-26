"""Phase 5: calibration tools, liquidation-risk models and the volatility forecaster."""
import numpy as np
import pandas as pd
import pytest

from regimecast.models import calibration as C
from regimecast.models import liquidation_risk as LR
from regimecast.models import volatility as V
from regimecast.risk import liquidation as liq


# ------------------------------------------------------------------ calibration
def test_isotonic_is_monotone_and_fixes_a_biased_forecast():
    rng = np.random.default_rng(0)
    p_true = rng.uniform(0, 1, 20_000)
    y = (rng.uniform(0, 1, len(p_true)) < p_true).astype(int)
    p_bad = p_true ** 0.5                       # systematically too high
    iso = C.Isotonic().fit(p_bad, y)
    out = iso.predict(np.linspace(0, 1, 101))
    assert np.all(np.diff(out) >= -1e-12)
    assert C.ece(iso.predict(p_bad), y) < C.ece(p_bad, y) / 3
    assert C.brier(iso.predict(p_bad), y) < C.brier(p_bad, y)


def test_scores_on_known_cases():
    y = np.array([0, 0, 1, 1])
    assert C.brier([0, 0, 1, 1], y) == 0 and C.brier([1, 1, 0, 0], y) == 1
    rng = np.random.default_rng(1)
    p = rng.uniform(0, 1, 50_000)
    hit = (rng.uniform(0, 1, len(p)) < p).astype(int)
    assert C.ece(p, hit) < 0.01                 # a calibrated forecast has ~0 calibration error
    r = C.reliability(p, hit)
    assert r["n"].sum() == len(p) and (r["observed"] - r["predicted"]).abs().max() < 0.05


def test_block_bootstrap_interval_contains_the_estimate():
    idx = pd.date_range("2024-01-01", periods=24 * 120, freq="1h", tz="UTC")
    df = pd.DataFrame({"x": np.random.default_rng(2).normal(0, 1, len(idx))}, index=idx)
    lo, hi = C.block_bootstrap(df, lambda d: d["x"].mean(), n=100)
    assert lo < df["x"].mean() < hi and hi - lo < 0.2


# ------------------------------------------------------------------ liquidation risk
def test_distances_match_the_liquidation_formula():
    d = LR.distances("BTCUSDT").set_index(["leverage", "side"])["d"]
    pos = liq.Position("BTCUSDT", "long", entry=100.0, qty=10.0, leverage=10)
    assert d[(10, "long")] == pytest.approx(liq.adverse_log_distance(pos, liq.liquidation_price(pos)))
    assert d[(10, "long")] == pytest.approx(-np.log((1 - 1 / 10) / (1 - 0.004)))
    assert (d.xs("long", level="side").diff().dropna() < 0).all()   # more leverage, closer liquidation


def test_fhs_is_calibrated_on_its_own_distribution():
    rng = np.random.default_rng(3)
    z = pd.DataFrame({"long": np.abs(rng.standard_normal(20_000)) * 1.3, "short": np.abs(rng.standard_normal(20_000))})
    f = LR.FHS().fit(z)
    sigma = np.full(5, 0.02)
    d = np.array([0.0, 0.01, 0.02, 0.04, 0.08])
    expected = [(z["long"] >= x / 0.02).mean() for x in d]
    assert f.predict("long", d, sigma) == pytest.approx(expected, abs=1e-3)


def test_quantile_survival_is_monotone_and_matches_known_quantiles():
    from scipy.stats import norm
    probs = np.array(LR.QUANTILES)
    q = np.tile(norm.ppf(probs), (3, 1))         # log z ~ N(0, 1)
    targets = np.array([-3.0, 0.0, 1.645])
    s = LR.QuantileGBM.survival(q, targets)
    assert s[1] == pytest.approx(0.5, abs=1e-6) and s[2] == pytest.approx(0.05, abs=0.01)
    grid = np.linspace(-4, 5, 200)
    sg = LR.QuantileGBM.survival(np.tile(norm.ppf(probs), (len(grid), 1)), grid)
    assert np.all(np.diff(sg) <= 1e-12) and sg[-1] < 1e-3


# ------------------------------------------------------------------ volatility forecaster
def hourly_frames(n_hours=24 * 400, seed=4):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2023-01-01", periods=n_hours, freq="1h", tz="UTC")
    lv = np.zeros(n_hours)
    for i in range(1, n_hours):                  # persistent log-variance process
        lv[i] = 0.98 * lv[i - 1] + rng.normal(0, 0.2)
    rv = np.exp(-10 + lv) * rng.lognormal(0, 0.3, n_hours)
    h = pd.DataFrame({"ts": ts, "rv": rv, "rv5": rv, "rq": rv ** 2, "jump": 0.0, "rs_neg": rv / 2,
                      "ret": rng.normal(0, 0.003, n_hours), "ret_24h": 0.0, "range_max_1m": 0.001,
                      "volume_usd": 1e8, "taker_buy_share": 0.5, "premium_mean": 0.0, "mark_index_gap": 0.0,
                      "funding_rate": 1e-4, "oi_chg_24h": 0.0, "ls_top": 1.0, "ls_accounts": 1.0, "taker_ls": 1.0,
                      "ob_spread_bps_mean": np.nan, "ob_spread_bps_max": np.nan, "ob_imbalance_top50_mean": np.nan,
                      "ob_range_bps_mean": np.nan, "ob_depth_top50_usd_mean": np.nan, "ob_depth_2bps_usd_min": np.nan,
                      "ob_ofi": np.nan, "close": 100.0})
    t = pd.DataFrame({"ts": ts, "y_1h": pd.Series(rv).shift(-1), "y_24h": pd.Series(rv).shift(-1).rolling(24).sum().shift(-23)})
    return h, t


def test_gbm_learns_persistent_volatility_and_dvol_join_has_no_lookahead():
    h, t = hourly_frames()
    dvol = pd.DataFrame({"ts": h["ts"], "open": 50.0, "high": 50.0, "low": 50.0, "close": np.arange(len(h), dtype=float)})
    X, y = V.dataset(h, t, 1, dvol)
    # the DVOL candle that OPENS at H is only known at H + 1h
    assert X.loc[h["ts"].iloc[100], "log_iv_h"] == pytest.approx(np.log((99 / 100) ** 2 / (24 * 365)))
    split = X.index[int(len(X) * 0.7)]
    m = V.GBMVol(1).fit(X[X.index < split], y[X.index < split])
    test = (X.index >= split + pd.Timedelta(hours=1)) & y.notna().to_numpy()
    pred = m.predict(X[test])
    corr = np.corrcoef(np.log(pred), np.log(y[test]))[0, 1]
    assert corr > 0.5
    shap = m.contributions(X[test].head(5))
    assert np.allclose(shap.sum(axis=1), np.log(pred.head(5) / m.smear))   # SHAP values add up to the prediction


def test_direct_model_is_monotone_in_distance_and_learns_direction():
    rng = np.random.default_rng(9)
    n = 20_000
    F = pd.DataFrame({c: rng.normal(0, 1, n) for c in LR.DIRECT_FEATURES})
    k = rng.normal(0, 1, n)
    # crowded longs (high ls_top) make long liquidations more likely at the same distance
    logit = -2.5 * k + 1.2 * F["ls_top"].to_numpy()
    y = (rng.uniform(0, 1, n) < 1 / (1 + np.exp(-logit))).astype(float)
    m = LR.DirectModel(rounds=150).fit(F.assign(k=k, y=y))
    grid = F.iloc[[0] * 50].assign(k=np.linspace(-3, 3, 50))
    p = m.predict(grid)
    assert np.all(np.diff(p) <= 1e-12)                       # farther away is never riskier
    lo, hi = F.iloc[[0, 0]].assign(k=0.0), F.iloc[[0, 0]].assign(k=0.0)
    lo["ls_top"], hi["ls_top"] = -2.0, 2.0
    assert m.predict(hi)[0] > m.predict(lo)[0] + 0.2        # crowding raises the risk
