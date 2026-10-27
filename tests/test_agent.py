"""Phase 9 paper agent: costs, funding sign, no look-ahead, the closed final test period, the live log."""
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from regimecast.agent import backtest as B, live as L, rules as R
from regimecast.agent.final import load_frozen, run_final

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def hours(n, start="2023-03-01 01:00"):
    return pd.date_range(start, periods=n, freq="1h", tz="UTC")


def flat_market(n=10, price=100.0, start="2023-03-01 01:00"):
    ix = hours(n, start)
    return pd.DataFrame(price, index=ix, columns=COINS), pd.DataFrame(np.nan, index=ix, columns=COINS)


def signals(ix, vol=0.5, p=0.0, regime=0.0):
    return {c: pd.DataFrame({"vol_ann": vol, "p_stress": p, "p_stressed": 0.0, "regime": regime,
                             "trail_vol_ann": vol}, index=ix) for c in COINS}


# ------------------------------------------------------------------ costs
def test_fee_is_taker_fee_on_every_change():
    mark, fund = flat_market(4)
    t = pd.DataFrame([[1.0] * 3, [1.0] * 3, [0.5] * 3, [0.5] * 3], index=mark.index, columns=COINS)
    r = B.simulate(mark, fund, t, band=0.01)
    # buy the whole account (notional 1), then sell back to half: fee 0.05% of each notional traded
    assert r.hourly["fee"].iloc[0] == pytest.approx(0.0005)
    assert r.hourly["fee"].iloc[1] == 0.0
    sold = 1 - 0.5 * (1 - 0.0005)
    assert r.hourly["fee"].iloc[2] == pytest.approx(0.0005 * sold / (1 - 0.0005), rel=1e-9)
    assert r.equity.iloc[-1] == pytest.approx(1 - r.hourly["fee"].sum() * 1, rel=1e-6)
    assert r.trades == 6


def test_no_trade_band_skips_small_changes():
    mark, fund = flat_market(3)
    t = pd.DataFrame([[0.50] * 3, [0.53] * 3, [0.70] * 3], index=mark.index, columns=COINS)
    r = B.simulate(mark, fund, t, band=0.05)
    assert r.hourly["fee"].iloc[1] == 0.0                       # 3 points < 5-point band
    assert r.hourly["fee"].iloc[2] > 0.0                        # 20 points: traded
    assert r.hourly["exposure_BTCUSDT"].iloc[2] == pytest.approx(0.70, rel=1e-3)


def test_never_short_never_above_1x():
    mark, fund = flat_market(2)
    t = pd.DataFrame([[2.0, -1.0, 0.5]] * 2, index=mark.index, columns=COINS)
    r = B.simulate(mark, fund, t)
    e = r.hourly.filter(like="exposure_").iloc[0]
    assert e.tolist() == pytest.approx([1.0, 0.0, 0.5], rel=1e-3)


def test_buy_and_hold_keeps_quantities():
    ix = hours(3)
    mark = pd.DataFrame({"BTCUSDT": [100, 200, 200], "ETHUSDT": [100, 100, 50], "SOLUSDT": [100, 100, 100.0]}, index=ix)
    r = B.simulate(mark, mark * np.nan, R.buy_and_hold_targets(COINS, ix))
    fee = 0.0005
    # a third in each at 100 (fee paid in cash); then BTC doubles and ETH halves: (2 + 0.5 + 1) / 3
    assert r.equity.iloc[-1] == pytest.approx(3.5 / 3 - fee, rel=1e-9)
    assert r.trades == 3


# ------------------------------------------------------------------ funding
@pytest.mark.parametrize("rate", [0.001, -0.001])
def test_funding_sign_long_pays_positive_receives_negative(rate):
    mark, fund = flat_market(3, start="2023-03-01 07:00")     # hours 07, 08, 09
    fund.loc[pd.Timestamp("2023-03-01 08:00", tz="UTC")] = rate
    t = pd.DataFrame(1.0, index=mark.index, columns=COINS)
    r = B.simulate(mark, fund, t, fee=0.0)
    h = r.hourly
    assert h["funding"].iloc[1] == pytest.approx(rate)          # paid = notional (1) x rate
    assert h["equity"].iloc[1] == pytest.approx(1 - rate)       # positive: long pays; negative: receives
    assert h["funding"].iloc[[0, 2]].abs().sum() == 0.0


def test_funding_charged_on_position_held_at_settlement():
    mark, fund = flat_market(3, start="2023-03-01 07:00")
    fund.loc[pd.Timestamp("2023-03-01 08:00", tz="UTC")] = 0.01
    # flat into 08:00, buys AT 08:00 (after the settlement): pays nothing
    t = pd.DataFrame([[0.0] * 3, [1.0] * 3, [1.0] * 3], index=mark.index, columns=COINS)
    r = B.simulate(mark, fund, t, fee=0.0)
    assert r.hourly["funding"].sum() == 0.0


# ------------------------------------------------------------------ rule
def test_exposure_rule():
    ix = hours(4)
    sig = pd.DataFrame({"vol_ann": [1.0, 0.25, 1.0, 1.0], "p_stress": [0.0, 0.0, 0.15, 0.6],
                        "regime": [2.0, 0.0, 0.0, 0.0]}, index=ix)
    s = R.Settings(target_vol=0.5, stress_elevated=0.1, stress_high=0.3, regime_mult=0.5, band=0.0)
    assert R.exposure(sig, s).tolist() == pytest.approx([0.25, 1.0, 0.25, 0.0])
    off = R.Settings(target_vol=0.5, stress_elevated=R.NO_CUT, stress_high=R.NO_CUT, regime_mult=1.0)
    assert R.exposure(sig, off).tolist() == pytest.approx([0.5, 1.0, 0.5, 0.5])


# ------------------------------------------------------------------ no look-ahead
def test_decision_at_h_uses_only_signals_stamped_at_or_before_h():
    rng = np.random.default_rng(1)
    ix = hours(200)
    mark = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (200, 3)), 0)), index=ix, columns=COINS)
    fund = pd.DataFrame(np.nan, index=ix, columns=COINS)
    fund[ix.hour.isin(B.FUNDING_HOURS)] = 1e-4
    sig = {c: pd.DataFrame({"vol_ann": rng.uniform(0.3, 1.2, 200), "p_stress": rng.uniform(0, 0.6, 200),
                            "p_stressed": 0.0, "regime": rng.integers(0, 3, 200).astype(float)}, index=ix) for c in COINS}
    s = R.Settings(0.5, 0.1, 0.3, 0.5, 0.05)
    base = B.simulate(mark, fund, R.agent_targets(sig, s, ix), s.band).hourly
    H = 120
    sig2 = {c: v.copy() for c, v in sig.items()}
    mark2, fund2 = mark.copy(), fund.copy()
    for v in sig2.values():
        v.iloc[H + 1:] = v.iloc[H + 1:].to_numpy()[::-1]       # scramble everything after H
    mark2.iloc[H + 1:] *= 3.0
    fund2.iloc[H + 1:] = 0.05
    alt = B.simulate(mark2, fund2, R.agent_targets(sig2, s, ix), s.band).hourly
    pd.testing.assert_frame_equal(base.iloc[:H + 1], alt.iloc[:H + 1])
    assert not base.iloc[H + 1:].equals(alt.iloc[H + 1:])


def test_final_test_period_is_closed():
    mark, fund = flat_market(3, start="2025-09-30 23:00")
    t = pd.DataFrame(1.0, index=mark.index, columns=COINS)
    with pytest.raises(ValueError, match="final test period"):
        B.simulate(mark, fund, t)
    assert len(B.simulate(mark, fund, t, final=True).hourly) == 3
    with pytest.raises(ValueError, match="final=True"):
        run_final()


# ------------------------------------------------------------------ metrics
def test_metrics_and_bootstrap():
    ix = hours(5)
    eq = pd.Series([1.0, 1.2, 0.6, 0.9, 1.1], index=ix)
    assert B.max_drawdown(eq) == pytest.approx(-0.5)
    rng = np.random.default_rng(0)
    a = pd.Series(rng.normal(2e-4, 0.01, 3000), index=hours(3000))
    b = pd.Series(rng.normal(0, 0.01, 3000), index=a.index)
    out = B.sharpe_diff_bootstrap(a, b, n=400)
    assert out["ci_lo"] < out["sharpe_diff"] < out["ci_hi"]
    assert out["sharpe_diff"] == pytest.approx(B.sharpe(a) - B.sharpe(b))


def test_frozen_settings_load():
    s, d = load_frozen()
    assert 0 < s.target_vol <= 2 and 0 <= s.regime_mult <= 1 and s.band >= 0
    assert d["frozen_at"] and d["settings_tried"] > 1


# ------------------------------------------------------------------ live paper log
def live_inputs(hour, mark=100.0, vol=1.0, p=0.0, regime=0, funding=1e-4):
    st = {"forecast": hour, "stress": hour, "regime": hour}
    return {c: {"mark": mark, "funding_rate": funding, "vol_ann": vol, "p_stress": p, "regime": regime,
                "p_stressed": 0.0, "stamps": st} for c in COINS}


def test_live_step_logs_decision_before_outcome_and_marks_it_next_hour(tmp_path):
    s = R.Settings(0.5, R.NO_CUT, R.NO_CUT, 1.0, 0.05)
    state = L.new_state(COINS, s)
    t1 = datetime(2026, 11, 2, 7, 12, tzinfo=timezone.utc)
    state, l1 = L.step(state, live_inputs("2026-11-02T07:00:00+00:00"), t1, s)
    assert l1["hour"] == "2026-11-02T07:00:00+00:00" and l1["decided_at"].startswith("2026-11-02T07:12")
    assert l1["coins"]["BTCUSDT"]["exposure"] == pytest.approx(0.5)
    assert l1["equity"] == pytest.approx(1 - 0.5 * 0.0005)           # half the account bought
    # same hour again: nothing new
    assert L.step(state, live_inputs("2026-11-02T07:00:00+00:00"), t1, s)[1] is None
    # next hour, after the 08:00 settlement: price +10%, funding 0.01% paid on what was held
    t2 = datetime(2026, 11, 2, 8, 11, tzinfo=timezone.utc)
    state, l2 = L.step(state, live_inputs("2026-11-02T08:00:00+00:00", mark=110.0), t2, s)
    gain = 0.5 * 0.1                                                 # half the account, +10%
    assert l2["funding"]["agent"] == pytest.approx(0.5 * 1.1 * 1e-4, rel=1e-9)
    assert l2["equity"] == pytest.approx(1 - 0.5 * 0.0005 + gain - l2["funding"]["agent"] - l2["fee"], rel=1e-9)
    assert l2["bh_equity"] > l2["equity"]                            # buy-and-hold was fully in


def test_live_step_keeps_position_when_inputs_are_stale():
    s = R.Settings(0.5, R.NO_CUT, R.NO_CUT, 1.0, 0.05)
    state = L.new_state(COINS, s)
    t = datetime(2026, 11, 2, 12, 10, tzinfo=timezone.utc)
    state, line = L.step(state, live_inputs("2026-11-02T06:00:00+00:00"), t, s)
    c = line["coins"]["ETHUSDT"]
    assert c["target"] is None and c["exposure"] == 0.0 and "kept" in c["reasons"][0]


def test_live_run_step_writes_state_log_and_api(tmp_path):
    t = datetime(2026, 11, 2, 9, 10, tzinfo=timezone.utc)
    line = L.run_step(tmp_path, live_inputs("2026-11-02T09:00:00+00:00"), t)
    assert line is not None
    assert (tmp_path / "log.jsonl").read_text().count("\n") == 1
    import json
    api = json.loads((tmp_path / "api" / "latest.json").read_text())
    assert api["hours_logged"] == 1 and api["latest"]["hour"] == "2026-11-02T09:00:00+00:00"


def test_api_agent_serves_live_log_and_backtest(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from regimecast.api import agent as A
    from regimecast.api.main import app

    t = datetime(2026, 11, 2, 9, 10, tzinfo=timezone.utc)
    L.run_step(tmp_path, live_inputs("2026-11-02T09:00:00+00:00"), t)
    import json
    live = json.loads((tmp_path / "api" / "latest.json").read_text())
    monkeypatch.setattr(A, "_live", lambda: live)
    r = TestClient(app).get("/agent")
    assert r.status_code == 200
    body = r.json()
    assert body["live"]["latest"]["coins"]["BTCUSDT"]["reasons"]
    assert body["backtest"]["settings"] == load_frozen()[1]["settings"]
    assert {x["strategy"] for x in body["backtest"]["results"]} == {"agent", "buy_and_hold", "vol_target"}
    monkeypatch.setattr(A, "_live", lambda: None)
    assert TestClient(app).get("/agent").json()["live"] is None
