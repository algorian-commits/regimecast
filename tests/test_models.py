"""Walk-forward harness, Gaussian HMM and regimes."""
import numpy as np
import pandas as pd
import pytest

from regimecast import config
from regimecast.models import harness, regimes
from regimecast.models.hmm import GaussianHMM
from regimecast.vol import evaluate as ev
from regimecast.vol import har, realized


def regime_series(n=3000, seed=0):
    """Hidden 3-state chain with sticky transitions and well separated means."""
    rng = np.random.default_rng(seed)
    A = np.array([[0.97, 0.02, 0.01], [0.03, 0.94, 0.03], [0.02, 0.05, 0.93]])
    means = np.array([[0.0, -2.0], [0.0, 0.0], [0.0, 2.5]])
    s = np.zeros(n, int)
    for t in range(1, n):
        s[t] = rng.choice(3, p=A[s[t - 1]])
    X = means[s] + rng.normal(0, 0.5, (n, 2))
    return X, s


# ------------------------------------------------------------------ HMM
def test_hmm_recovers_states_and_filter_is_causal():
    X, s = regime_series()
    m = GaussianHMM(3, n_init=3, seed=1).fit(X).order_by(1)
    assert m.means_[:, 1] == pytest.approx([-2.0, 0.0, 2.5], abs=0.15)
    assert np.diag(m.transmat_).min() > 0.85
    acc = (m.filter(X).argmax(1) == s).mean()
    assert acc > 0.9
    # filtered probabilities at t never depend on rows after t
    Y = X.copy()
    Y[2000:] = np.random.default_rng(5).normal(0, 3, Y[2000:].shape)
    assert np.allclose(m.filter(X)[:2000], m.filter(Y)[:2000])


def test_hmm_missing_rows_carry_the_state_forward():
    X, _ = regime_series(500)
    m = GaussianHMM(3, n_init=2, seed=0).fit(X)
    Y = X.copy()
    Y[100] = np.nan
    p = m.filter(Y)
    assert np.allclose(p[100], p[99] @ m.transmat_)


# ------------------------------------------------------------------ harness
def hourly(n_days=400, seed=3):
    rng = np.random.default_rng(seed)
    n = n_days * 24 * 60
    vol = 0.0008 * np.exp(0.5 * np.sin(np.arange(n) / (60 * 24 * 5)))
    close = 30000 * np.exp(np.cumsum(rng.standard_normal(n) * vol))
    ts = pd.date_range("2024-06-01", periods=n, freq="1min", tz="UTC")
    return realized.build_targets(realized.minute_close(pd.DataFrame({"ts": ts, "close": close})))


def test_folds_purge_and_closed_final_period():
    idx = pd.date_range("2025-01-01", "2026-03-01", freq="1h", tz="UTC")
    fs = harness.folds(idx, "2025-06-01", horizon_hours=24, embargo_hours=6)
    hold = pd.Timestamp(config.HOLDOUT_START, tz="UTC")
    assert fs and all(f.fit_end == f.block_start - pd.Timedelta(hours=30) for f in fs)
    assert max(f.block_end for f in fs) <= hold - pd.Timedelta(hours=24) + pd.Timedelta(microseconds=1)
    with pytest.raises(ValueError):
        harness.folds(idx, "2025-11-01", horizon_hours=1)
    assert max(f.block_end for f in harness.folds(idx, "2025-06-01", 24, final=True)) > hold


def test_harness_matches_phase1_walk_forward_and_runs_baselines():
    h = hourly()
    X, y = har.har_features(h, 24), h["y_24h"]
    a = harness.walk_forward(X, y, lambda: har.HARModel("har_s"), "2025-03-01", 24)
    b = ev.walk_forward(X, y, lambda: har.HARModel("har_s"), "2025-03-01", 24, end=a.index[-1])
    pd.testing.assert_series_equal(a, b, check_names=False)
    y, preds = harness.run_baselines(h, 1, "2025-03-01", names=["har", "no_change", "hist_vol"])
    t = ev.summary_table(y, preds, "har", 1)
    assert set(t.index) == {"har", "no_change", "hist_vol"} and (t["qlike"] > 0).all()


# ------------------------------------------------------------------ regimes
def test_regimes_walk_forward_frozen_after_holdout():
    X, s = regime_series(n=24 * 200, seed=2)
    idx = pd.date_range(pd.Timestamp(config.HOLDOUT_START, tz="UTC") - pd.Timedelta(days=150),
                        periods=len(X), freq="1h")
    x = pd.DataFrame({"ret": X[:, 0] * 0.01, "log_rv": X[:, 1], "log_rv_24h": X[:, 1]}, index=idx)
    reg = regimes.fit_walk_forward(x, ["ret", "log_rv", "log_rv_24h"], refit_every_hours=24 * 30,
                                   min_train_hours=24 * 30)
    hold = pd.Timestamp(config.HOLDOUT_START, tz="UTC")
    assert reg.loc[reg.index < hold, "frozen"].eq(False).all()
    assert reg.loc[reg.index >= hold, "frozen"].eq(True).all()
    # never fitted on the final test period: hourly rows are labelled by the hour END, so the
    # row stamped 2025-10-01 00:00 holds 30 Sep 23:00-24:00 data; nothing later is used
    assert (reg.loc[reg.index >= hold, "fit_end"] <= hold).all()
    p = reg[[f"p_{n}" for n in regimes.NAMES]]
    assert np.allclose(p.sum(1), 1)
    # stressed regime lines up with the high-volatility hidden state
    truth = pd.Series(s, index=idx).reindex(reg.index)
    assert (reg["regime"] == truth).mean() > 0.8
