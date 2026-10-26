"""Stress early warning (models/stress.py)."""
import numpy as np
import pandas as pd
import pytest

from regimecast.models import stress as S
from tests.test_phase5 import hourly_frames


def stress_frames(seed=6):
    h, t = hourly_frames(seed=seed)
    rng = np.random.default_rng(seed)
    # stress follows high volatility, and tends to repeat
    hot = (h["rv"] > h["rv"].quantile(0.93)).astype(int)
    h["stress_minutes"] = hot * rng.integers(5, 40, len(h))
    h["rv_168h"] = h["rv"].rolling(168, min_periods=1).mean()
    return h, t


def test_label_is_stress_in_the_next_hour():
    h, t = stress_frames()
    X, y = S.dataset(h, t)
    sm = h.set_index("ts")["stress_minutes"]
    i = 500
    assert y.iloc[i] == float(sm.iloc[i + 1] > 0)                 # y at t = stress in (t, t+1h]
    assert X["stress_last_1h"].iloc[i] == float(sm.iloc[i] > 0)   # feature at t = stress up to t
    assert np.isnan(y.iloc[-1])                                   # the last hour has no future yet
    assert X["hours_since_stress"].iloc[i] >= 0


def test_rate_rule_uses_past_frequencies():
    idx = pd.date_range("2024-01-01", periods=1000, freq="1h", tz="UTC")
    X = pd.DataFrame({"stress_last_1h": np.r_[np.ones(100), np.zeros(900)]}, index=idx)
    y = pd.Series(np.r_[np.ones(60), np.zeros(40), np.ones(90), np.zeros(810)], index=idx)
    r = S.RateRule(lambda X: X["stress_last_1h"], "persist").fit(X, y)
    assert r.predict(X.iloc[[0]]).iloc[0] == pytest.approx(0.6)
    assert r.predict(X.iloc[[500]]).iloc[0] == pytest.approx(0.1)


def test_scores_on_known_cases():
    y = np.array([0, 0, 1, 1])
    assert S.auc([0.1, 0.2, 0.8, 0.9], y) == 1.0 and S.auc([0.9, 0.8, 0.2, 0.1], y) == 0.0
    assert S.auc([0.5, 0.5, 0.5, 0.5], y) == 0.5
    assert S.average_precision([0.1, 0.2, 0.8, 0.9], y) == 1.0
    idx = pd.date_range("2024-01-01", periods=48, freq="1h", tz="UTC")
    a = S.alarms(pd.Series(np.r_[np.ones(4), np.zeros(44)], index=idx), pd.Series(np.r_[1, 1, 0, 0, np.zeros(44)], index=idx), 0.5)
    assert a["precision"] == 0.5 and a["recall"] == 1.0 and a["false_alarms_per_day"] == pytest.approx(2 / (47 / 24))


def test_classifier_beats_base_rate_out_of_sample():
    h, t = stress_frames()
    X, y = S.dataset(h, t)
    split = X.index[int(len(X) * 0.7)]
    m = S.StressGBM().fit(X[X.index < split], y[X.index < split])
    test = (X.index > split) & y.notna().to_numpy()
    assert S.auc(m.predict(X[test]), y[test]) > 0.75
