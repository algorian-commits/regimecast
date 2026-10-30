"""Phase 12 trade verdict: every rule, the closed final test period, no look-ahead, the dev pipeline and the API."""
import copy
import json
import math

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from regimecast import config
from regimecast.risk import fhs_live
from regimecast.verdict import crowd as C, devrun, evaluate as E, fix as FX, grade as G, settings, similar as S
from regimecast.verdict import timing as T, whatif as WI
from regimecast.verdict.final import run_final

CFG = settings.DEFAULTS["grade"]
HOLD = pd.Timestamp(config.HOLDOUT_START, tz="UTC")


def facts(**kw):
    base = dict(coin="BTC", horizon_hours=24, p_knockout=0.05, p_liquidation=0.001, risk_limit=0.01, has_stop=True,
                stop_past_liquidation=False, stop_moves=2.0, cost_share=0.03)
    return G.Facts(**{**base, **kw})


def grade(**kw):
    return G.assess(facts(**kw), CFG).grade


# ------------------------------------------------------------------ Trade Grade
def test_well_built_trade_is_A_with_three_reasons():
    v = G.assess(facts(regime="calm"), CFG)
    assert v.grade == "A" and len(v.reasons) == 3
    assert any("outside normal noise" in r for r in v.reasons)


@pytest.mark.parametrize("p,g", [(0.05, "A"), (0.15, "B"), (0.30, "C"), (0.50, "D"), (0.70, "F")])
def test_knockout_bands(p, g):
    assert grade(p_knockout=p) == g


@pytest.mark.parametrize("ratio,g", [(1.0, "A"), (1.5, "B"), (3, "C"), (7, "D"), (11, "F")])
def test_liquidation_against_the_users_limit(ratio, g):
    assert grade(p_liquidation=0.01 * ratio) == g


def test_stop_rules():
    assert grade(has_stop=False, stop_moves=None) == "B"                     # liquidation is the only exit
    v = G.assess(facts(stop_past_liquidation=True), CFG)
    assert v.grade == "F" and "liquidated before it triggers" in v.reasons[0]


@pytest.mark.parametrize("cost,g", [(0.05, "A"), (0.2, "B"), (0.3, "C"), (0.6, "D")])
def test_costs(cost, g):
    assert grade(cost_share=cost) == g


def test_market_steps_add_up_to_two_and_follow_the_weights():
    bad = dict(regime="stressed", stress_level="high", p_stress=0.4, stress_base=0.04, crowd_on_side=True,
               crowd_text="crowded", timing=["CPI soon"])
    assert grade(**bad) == "C"                                               # A + capped at 2 steps
    assert grade(p_knockout=0.50, **bad) == "F"
    off = {**CFG, "weights": {k: 0 for k in G.MARKET}}
    assert G.assess(facts(**bad), off).grade == "A"
    assert G.assess(facts(stress_level="elevated", p_stress=0.15), CFG).grade == "B"


def test_a_riskier_setup_never_gets_a_better_grade():
    rng = np.random.default_rng(0)
    n = 5000
    base = dict(p_knockout=rng.random(n), p_liquidation=rng.random(n) * 0.2, risk_limit=0.01, has_stop=rng.random(n) > 0.2,
                stop_past_liquidation=rng.random(n) > 0.8, cost_share=rng.random(n) * 0.8, stressed=rng.random(n) > 0.5,
                stress_steps=rng.integers(0, 3, n), crowd_on_side=rng.random(n) > 0.5, bad_timing=rng.random(n) > 0.5)
    g0 = G.grade_index(G.scores(**base, cfg=CFG))
    for k, bump in (("p_knockout", 0.1), ("p_liquidation", 0.02), ("cost_share", 0.2)):
        g1 = G.grade_index(G.scores(**{**base, k: base[k] + bump}, cfg=CFG))
        assert (g1 >= g0).all()
    for k in ("stressed", "crowd_on_side", "bad_timing"):
        assert (G.grade_index(G.scores(**{**base, k: np.ones(n, bool)}, cfg=CFG)) >= g0).all()


def test_reasons_put_the_worst_first():
    v = G.assess(facts(p_liquidation=0.2, has_stop=False, stop_moves=None, cost_share=0.3), CFG)
    assert v.grade == "F" and len(v.reasons) == 3
    assert v.reasons[0].startswith("20% chance of liquidation") and "20 times your 1% limit" in v.reasons[0]
    assert "No stop-loss" in " ".join(v.reasons) and "Fees and funding take 30%" in " ".join(v.reasons)


def test_the_app_and_the_evaluation_grid_grade_alike():
    rng = np.random.default_rng(1)
    for _ in range(200):
        f = facts(p_knockout=rng.random(), p_liquidation=rng.random() * 0.1, cost_share=rng.random() * 0.6,
                  stop_past_liquidation=bool(rng.random() > 0.8), regime=rng.choice(["calm", "stressed"]),
                  stress_level=rng.choice(["low", "elevated", "high"]), p_stress=0.2,
                  crowd_on_side=bool(rng.random() > 0.5), timing=["x"] if rng.random() > 0.5 else [])
        row = pd.DataFrame([{"p_knockout": f.p_knockout, "p_liquidation": f.p_liquidation,
                             "stop_past_liquidation": f.stop_past_liquidation, "cost_share": f.cost_share,
                             "stressed": f.regime == "stressed", "stress_steps": G.STRESS_STEPS[f.stress_level],
                             "crowd_on_side": f.crowd_on_side, "bad_timing": bool(f.timing)}])
        assert E.grade_trades(row, CFG)[0] == G.assess(f, CFG).index


def test_break_even_win_rate():
    assert G.break_even_win_rate(100, 200, 10) == pytest.approx(110 / 300)
    assert G.break_even_win_rate(100, 100, 0) == pytest.approx(0.5)
    assert G.break_even_win_rate(100, 5, 10) > 1                             # costs eat the gain: never profitable
    assert G.break_even_win_rate(100, 0, 1) is None


# ------------------------------------------------------------------ timing
def test_shipped_macro_calendar():
    c = T.load_calendar()
    years = c["time"].dt.year
    for y in range(2021, 2028):
        n = c[years == y]["event"].value_counts()
        assert n.get("FOMC") == 8 and n.get("CPI", 0) >= 11 and n.get("JOBS", 0) >= 11, y
    assert set(c["time"].dt.strftime("%H:%M")) <= {"12:30", "13:30", "18:00", "19:00"}   # 8:30 / 14:00 ET
    assert c["time"].is_monotonic_increasing


def test_events_count_inside_the_horizon_only():
    t = pd.Timestamp("2024-06-12 12:30", tz="UTC")
    cal = pd.DataFrame({"time": [t], "event": ["CPI"]})
    assert T.check(t - pd.Timedelta(hours=24), 24, cal).events            # right at the end of the horizon
    assert not T.check(t - pd.Timedelta(hours=25), 24, cal).events
    assert not T.check(t, 24, cal).events                                 # already out
    w = T.check(t - pd.Timedelta(hours=5), 24, cal).warnings(t - pd.Timedelta(hours=5))
    assert w == ["US inflation data (CPI) comes out in 5 hours, inside your trade's time: prices often jump on it."]


def test_weekend_thin_hours_and_stress_rising():
    sat = pd.Timestamp("2024-06-15 03:00", tz="UTC")
    assert T.check(sat, 24, T.load_calendar().iloc[:0]).weekend
    assert T.check(sat + pd.Timedelta(days=2), 1, T.load_calendar().iloc[:0], thin_hours=[3]).thin
    assert T.stress_rising(0.12, 0.07) and not T.stress_rising(0.12, 0.09) and not T.stress_rising(0.09, 0.01)


def test_timing_flags_match_the_single_check():
    cal = T.load_calendar()
    ix = pd.date_range("2024-01-01", "2024-03-01", freq="1h", tz="UTC")
    p = pd.Series(np.random.default_rng(0).random(len(ix)) * 0.3, index=ix)
    f = T.flags(ix, 24, cal, [2, 3], p)
    for t in ix[::37]:
        c = T.check(t, 24, cal, [2, 3], p[t], p.get(t - pd.Timedelta(hours=3)))
        assert f.loc[t, "event"] == bool(c.events) and f.loc[t, "weekend"] == c.weekend
        assert f.loc[t, "thin"] == c.thin and f.loc[t, "rising"] == c.rising


# ------------------------------------------------------------------ crowd warning
TH = C.fit(None, 0.9, 0.75)


def test_crowd_warning_needs_all_three_extremes():
    P = pd.DataFrame({"funding_rank": [0.95, 0.95, 0.05, 0.05, np.nan], "ls_rank": [0.97, 0.97, 0.02, 0.5, 0.99],
                      "oi_rank": [0.8, 0.5, 0.9, 0.9, 0.9]})
    assert C.warning(P, TH).tolist() == [1, 0, -1, 0, 0]
    assert C.lean(P, TH).tolist() == [1, 1, -1, -1, 0]


def test_crowd_ranks_use_only_the_last_14_days():
    rng = np.random.default_rng(1)
    ix = pd.date_range("2023-01-01 01:00", periods=1500, freq="1h", tz="UTC")
    x = pd.Series(rng.normal(size=len(ix)), index=ix)
    r = C.trailing_rank(x)
    assert r.iloc[: C.MIN_HOURS - 1].isna().all() and r.iloc[C.MIN_HOURS:].notna().all()
    t = 1200
    later = x.copy()
    later.iloc[t + 1:] = 99.0                                             # the future changes...
    assert C.trailing_rank(later).iloc[: t + 1].equals(r.iloc[: t + 1])   # ...nothing up to t does
    w = x.iloc[t - C.WINDOW_HOURS + 1: t + 1]
    assert r.iloc[t] == pytest.approx(((w < x.iloc[t]).sum() + 0.5 * (w == x.iloc[t]).sum()) / len(w))
    # a change of level that happened more than 14 days ago no longer matters
    shifted = x.where(x.index >= ix[t - C.WINDOW_HOURS + 1], x + 50)
    assert C.trailing_rank(shifted).iloc[t:].equals(r.iloc[t:])


def test_live_ranks_match_the_dev_definition():
    ix = pd.date_range("2024-01-01 01:00", periods=500, freq="1h", tz="UTC")
    oi = pd.Series(np.linspace(100, 200, 500), index=ix)
    ls = pd.Series(np.r_[np.full(499, 1.5), 3.0], index=ix)
    funding = pd.Series([1e-4, 2e-4], index=[ix[0], ix[250] - pd.Timedelta(minutes=1)])
    h = C.rank_frame(oi, ls, funding)
    assert h.index[-1] == ix[-1] and h["funding_rate"].iloc[-1] == 2e-4
    assert h["oi_chg_24h"].iloc[-1] == pytest.approx(math.log(oi.iloc[-1] / oi.iloc[-25]))
    assert h["ls_rank"].iloc[-1] > 0.99 and h["funding_rank"].iloc[-1] > 0.5 and 0 <= h["oi_rank"].iloc[-1] <= 1


def test_against_the_crowd_is_a_5_percent_move_within_24h():
    P = pd.DataFrame({"mae_long_24h": [C.AGAINST_LONG, C.AGAINST_LONG - 1e-9, 0.0, np.nan],
                      "mae_short_24h": [0.0, 0.0, C.AGAINST_SHORT, 0.0]})
    a = C.against(P, pd.Series([1, 1, -1, 1]))
    assert a.iloc[:3].tolist() == [1.0, 0.0, 1.0] and np.isnan(a.iloc[3])
    assert math.isclose(math.exp(-C.AGAINST_LONG), 0.95) and math.isclose(math.exp(C.AGAINST_SHORT), 1.05)


def test_crowd_ratio_and_its_bootstrap_interval():
    rng = np.random.default_rng(0)
    ix = pd.date_range("2022-01-01", periods=24 * 700, freq="1h", tz="UTC")
    warn = rng.random(len(ix)) < 0.05
    against = (rng.random(len(ix)) < np.where(warn, 0.4, 0.1)).astype(float)
    o = pd.DataFrame({"warning": warn, "side": 1, "against": against}, index=ix)
    a, b, r, n = C.ratio(o)
    assert r == pytest.approx(4, rel=0.15) and n == warn.sum()
    lo, hi = E.ratio_bootstrap(o)
    assert 1 < lo < r < hi
    same = o.assign(against=(rng.random(len(ix)) < 0.1).astype(float))
    lo, hi = E.ratio_bootstrap(same)
    assert lo < 1 < hi


# ------------------------------------------------------------------ similar moments
@pytest.fixture(scope="module")
def panel():
    return E.synthetic_panel(seed=3, start="2021-01-01", end="2021-09-01")


def test_known_by_counts_only_finished_next_days(panel):
    sc = S.fit_scaling(S.features(panel))
    lib = S.Library.from_panel(panel, sc)
    t = pd.Timestamp(lib.ts[10], tz="UTC")
    assert lib.known_by(t + pd.Timedelta(hours=24)) == 11
    assert lib.known_by(t + pd.Timedelta(hours=24) - pd.Timedelta(seconds=1)) == 10


def test_similar_moments_never_look_ahead(panel):
    sc = S.fit_scaling(S.features(panel))
    cfg = {"k": 100, "shrink": 1.0, "scale": True}
    now = pd.Timestamp("2021-06-15 12:00", tz="UTC")
    row = panel.loc[now]
    x = {c: row[c] for c in S.FEATURES[1:]}
    m1 = S.query(S.Library.from_panel(panel, sc), x, "active", row["sigma"], now, cfg, sc)
    late = panel.index > now - pd.Timedelta(hours=24)                   # outcome not finished at now, or the future
    P2 = panel.copy()
    P2.loc[late, ["fwd_24h", "sigma", "funding_rate", "ls_top"]] *= -7
    m2 = S.query(S.Library.from_panel(P2, sc), x, "active", row["sigma"], now, cfg, sc)
    m3 = S.query(S.Library.from_panel(panel[~late], sc), x, "active", row["sigma"], now, cfg, sc)
    assert m1 == m2 == m3 and m1.n == 100


def test_evaluation_and_live_query_agree(panel):
    sc = S.fit_scaling(S.features(panel))
    lib = S.Library.from_panel(panel, sc)
    cfg = {"k": 50, "shrink": 1.0, "scale": True}
    Q = panel.loc["2021-07-01":"2021-07-02"]
    pred = S.predict(lib, Q, S.standardize(S.features(Q), sc), [cfg])
    for _, p in pred.iloc[::7].iterrows():
        q = panel.loc[p["ts"]]
        m = S.query(lib, {c: q[c] for c in S.FEATURES[1:]}, S.REGIMES[int(q["regime"])], q["sigma"], p["ts"], cfg, sc)
        assert (m.lo, m.hi, m.p_up) == pytest.approx((p["lo"], p["hi"], p["p_up"]))


def test_up_chance_is_pulled_toward_the_given_dev_up_share(panel):
    sc = S.fit_scaling(S.features(panel))
    lib = S.Library.from_panel(panel, sc)
    cfg = {"k": 50, "shrink": 1000.0, "scale": True}
    Q = panel.loc["2021-07-01":"2021-07-02"]
    pred = S.predict(lib, Q, S.standardize(S.features(Q), sc), [cfg], base=0.52)
    assert np.allclose(pred["p_up"], 0.52, atol=0.001)                   # heavy shrinkage: the base itself
    q = panel.loc[pred["ts"].iloc[0]]
    m = S.query(lib, {c: q[c] for c in S.FEATURES[1:]}, S.REGIMES[int(q["regime"])], q["sigma"], pred["ts"].iloc[0],
                cfg, sc, base=0.52)
    assert m.p_up == pytest.approx(pred["p_up"].iloc[0])


def test_walk_forward_fhs_uses_only_finished_outcomes():
    P = E.synthetic_panel(seed=4, start="2021-01-01", end="2022-03-01")
    tabs = E.walk_forward_fhs(P)
    b = tabs[0][0]
    P2 = P.copy()
    P2.loc[P2.index > b - pd.Timedelta(hours=24), "mae_long_24h"] = 5.0  # outcomes not finished by the block start
    assert E.walk_forward_fhs(P2)[0][1]["long"] == tabs[0][1]["long"]
    tr = E.example_trades(P, "BTCUSDT", tabs, None, T.load_calendar(), period=("2021-01-01", "2022-03-01"))
    assert tr["ts"].min() >= b                                              # no grade before the first table
    first = tuple(x.strftime("%Y-%m-%d %H:%M") for x in (b, tabs[1][0]))
    one = E.example_trades(P, "BTCUSDT", tabs[0][1], None, T.load_calendar(), period=first)
    same = tr[tr["ts"] < tabs[1][0]].reset_index(drop=True)
    assert np.allclose(one["p_knockout"].to_numpy(), same["p_knockout"].to_numpy())


def test_up_chance_shrinks_to_the_base_rate_and_says_coin_flip():
    lib = S.Library(np.arange(10, dtype=np.int64), np.zeros(10, np.int8), np.zeros((10, 6), np.float32),
                    np.ones(10, np.float32), np.array([1, 1, 1, 1, 1, 1, 1, 1, -1, -1], np.float32))
    m = S._summary(lib, np.arange(8), 1.0, 0.0, True, 0.5, True)
    assert m.p_up == 1.0 and not m.coin_flip
    m = S._summary(lib, np.arange(8), 1.0, 4.0, True, 0.5, True)        # (8 + 32 x 0.5) / 40
    assert m.p_up == pytest.approx(0.6) and m.coin_flip and "coin flip" in m.message("BTC")


def test_coverage_and_brier_scoring():
    ix = pd.date_range("2022-01-01", periods=4, freq="1h", tz="UTC")
    pred = pd.DataFrame({"ts": ix, "lo": -0.01, "hi": 0.01, "p_up": 0.5})
    fwd = pd.Series([0.0, 0.02, -0.005, -0.02], index=ix)
    s = S.score(pred, fwd, 0.5)
    assert s["coverage"] == 0.5 and s["brier"] == pytest.approx(0.25) and s["brier_base"] == pytest.approx(0.25)


# ------------------------------------------------------------------ what-if
def test_what_if_dollars():
    kw = dict(entry=100.0, qty=10.0, fees=1.0, stop=95.0, liquidation=90.0, liquidation_loss=100.0)
    assert WI.pnl("long", move=math.log(1.1), **kw) == (pytest.approx(99.0), None)
    assert WI.pnl("long", move=math.log(0.97), **kw) == (pytest.approx(-31.0), None)
    assert WI.pnl("long", move=math.log(0.8), **kw) == (pytest.approx(-51.0), "stop")       # stop before liquidation
    assert WI.pnl("long", move=math.log(0.8), **{**kw, "stop": 85.0}) == (-100.0, "liquidation")
    assert WI.pnl("short", move=math.log(0.8), **{**kw, "stop": 105.0, "liquidation": 110.0}) == (pytest.approx(199.0), None)


def test_what_if_strip_has_normal_bad_and_crash_days():
    rows = WI.strip("SOLUSDT", "short", 100.0, 10.0, 1.0, None, 110.0, 100.0, "calm", 0.03,
                    {"calm": {"normal": 0.04, "bad": 0.09, "n": 500}})
    assert [r.scenario for r in rows] == ["normal", "bad", "crash"]
    assert rows[0].move_pct == pytest.approx(100 * (math.exp(0.04) - 1)) and "calm market" in rows[0].basis
    assert rows[2].up_usdt is None and rows[2].down_usdt == pytest.approx(10 * 100 * 0.307 - 1)  # a short gains
    fallback = WI.strip("BTCUSDT", "long", 100.0, 1.0, 0.0, None, 0.0, 100.0, None, 0.02, None)
    assert fallback[0].move_pct == pytest.approx(100 * (math.exp(1.6449 * 0.02) - 1))


def test_what_if_table_uses_quantiles_per_regime():
    P = pd.DataFrame({"fwd_24h": np.r_[np.linspace(-0.1, 0.1, 201), np.linspace(-0.3, 0.3, 201)],
                      "regime": np.r_[np.zeros(201), np.full(201, 2)]})
    t = WI.move_table(P)
    assert t["calm"]["normal"] == pytest.approx(0.09) and t["stressed"]["bad"] == pytest.approx(0.297)
    assert "active" not in t and t["all"]["n"] == 402


# ------------------------------------------------------------------ fix my trade
def opt(lev, stop, g, p, liq_d=0.1, loss=50.0):
    return FX.Option(lev, stop, g, p, liq_d, loss)


def test_fix_picks_the_smallest_change_that_qualifies():
    orig = opt(25, None, 4, 0.2, 0.04, 100.0)
    options = [opt(25, 0.02, 2, 0.2, 0.04, 60), opt(20, 0.02, 1, 0.009, 0.05, 50), opt(10, 0.02, 1, 0.001, 0.1, 25),
               opt(20, 0.03, 0, 0.009, 0.05, 70), opt(15, 0.04, 1, 0.005, 0.06, 70)]
    best = FX.choose(orig, options, 0.01, 0.02)
    assert (best.leverage, best.stop_distance) == (20, 0.02)          # within the limit, stop inside, grade B, closest


def test_fix_respects_limit_stop_buffer_and_loss_cap():
    orig = opt(10, 0.01, 3, 0.05, 0.09, 40.0)
    assert FX.choose(orig, [opt(10, 0.02, 1, 0.02, 0.09, 30)], 0.01, 0.02) is None      # over the limit
    assert FX.choose(orig, [opt(10, 0.08, 1, 0.005, 0.09, 30)], 0.01, 0.02) is None     # stop past 80% of the way
    assert FX.choose(orig, [opt(5, 0.02, 1, 0.005, 0.18, 41)], 0.01, 0.02) is None      # could lose more
    assert FX.choose(opt(5, 0.03, 0, 0.001, 0.18, 20), [], 0.01, 0.02) is None          # already fine: no fix


def test_fix_candidates_never_raise_leverage_or_tighten_the_stop():
    c = FX.candidates(10, 0.015, 0.01)
    assert max(l for l, _ in c) == 10 and min(d for _, d in c) == pytest.approx(0.015)


# ------------------------------------------------------------------ evaluation and the closed final test period
def test_grid_liquidation_chances_match_the_app_model():
    fhs = fhs_live.load()["BTCUSDT"]["24h"]["long"]
    rng = np.random.default_rng(0)
    d, sig = rng.random(300) * 0.2, rng.random(300) * 0.05 + 0.001
    d[:5] = [0, -0.1, np.inf, 0.5, 1e-9]
    assert E.survival(fhs, d, sig) == pytest.approx([fhs_live.survival(fhs, a, b) for a, b in zip(d, sig)])


def test_liquidation_distance_is_the_same_at_any_entry():
    from regimecast.risk import liquidation as liq
    for side in ("long", "short"):
        for lev in E.LEVERAGES:
            p = liq.Position("ETHUSDT", side, 3777.7, E.GRID_MARGIN * lev / 3777.7, lev)
            assert E.liquidation_distance("ETHUSDT", side, lev) == pytest.approx(
                liq.adverse_log_distance(p, liq.liquidation_price(p)), rel=1e-9)


def test_small_grades_merge_with_the_next():
    g = E.merge_grades([150, 300, 0, 150, 60], [15, 60, 0, 60, 40])
    assert [x["group"] for x in g] == ["A+B", "D+F"] and [x["n"] for x in g] == [450, 210]
    assert g[0]["hit_share"] == pytest.approx(75 / 450)
    assert [x["group"] for x in E.merge_grades([500, 10, 10, 100, 100], [1] * 5)] == ["A", "B+C+D+F"]
    assert [x["group"] for x in E.merge_grades([500, 10, 10, 10, 10], [1] * 5)] == ["A+B+C+D+F"]


def test_grade_bar_rises_and_ratio():
    t = pd.DataFrame({"symbol": "BTCUSDT", "group": ["A", "B+C", "D", "F"], "n": 300,
                      "hit_share": [0.05, 0.10, 0.12, 0.2]})
    b = E.grade_bar(t).iloc[0]
    assert b["rises"] and b["ratio_last_first"] == pytest.approx(4) and b["passes"]
    t.loc[2, "hit_share"] = 0.09
    assert not E.grade_bar(t).iloc[0]["passes"]


def test_dev_panel_ends_before_the_final_test_period():
    P0 = E.synthetic_panel(seed=1, start="2025-08-01", end="2025-10-10")
    h = P0.assign(ret=np.log(P0["mark"]).diff())
    t = P0[["mae_long_24h", "mae_short_24h"]].assign(y_24h=0.001)
    P = E.panel_from(h, t, P0["p_stress"], P0["regime"], sigma=P0["sigma"])
    assert P.index.max() < HOLD
    late = P.index > HOLD - pd.Timedelta(hours=24)
    assert P.loc[late, E.OUTCOME_COLS].isna().all().all() and P.loc[~late, "mae_long_24h"].notna().any()
    with pytest.raises(ValueError, match="final test period"):
        E.example_trades(P0, "BTCUSDT", E.synthetic_fhs()["24h"], None, T.load_calendar())
    assert len(E.example_trades(P0, "BTCUSDT", E.synthetic_fhs()["24h"], None, T.load_calendar(), final=True))


def test_dev_queries_stop_before_the_final_test_period(monkeypatch, tmp_path):
    from regimecast.data import store
    seen = {}
    P0 = E.synthetic_panel(seed=1, start="2025-08-01", end="2025-10-01")

    def features(symbol, freq="1h", start=None, end=None, columns=None):
        seen["features"] = end
        return P0.assign(ret=0.0).reset_index()

    def targets(symbol, start=None, end=None, columns=None):
        seen["targets"] = end
        return P0[["mae_long_24h", "mae_short_24h"]].assign(y_24h=0.001).reset_index()

    monkeypatch.setattr(store, "features", features)
    monkeypatch.setattr(store, "targets", targets)
    ix = pd.date_range("2025-08-01", "2025-10-20", freq="1h", tz="UTC", name="ts")
    pd.DataFrame({"gbm_cal": 0.05, "gbm": 0.05}, index=ix).to_parquet(tmp_path / "phase6_pred_BTCUSDT.parquet")
    pd.DataFrame({"regime": 1, "p_stressed": 0.1}, index=ix).to_parquet(tmp_path / "regimes_base_BTCUSDT.parquet")
    monkeypatch.setattr(E, "hars_sigma", lambda h, y, final=False: pd.Series(0.02, index=h.index))
    P = E.load("BTCUSDT", reports=tmp_path)
    assert seen["features"] == HOLD and seen["targets"] == HOLD - pd.Timedelta(hours=23)
    assert P.index.max() < HOLD and P["p_stress"].notna().all()
    assert E._read(tmp_path / "regimes_base_BTCUSDT.parquet", final=False).index.max() < HOLD


def test_final_entry_point_needs_final_true_and_frozen_settings(monkeypatch):
    with pytest.raises(ValueError, match="final=True"):
        run_final()
    monkeypatch.setattr(settings, "load", lambda path=None: copy.deepcopy(settings.DEFAULTS))
    with pytest.raises(RuntimeError, match="frozen.json is missing"):
        run_final(final=True)


# ------------------------------------------------------------------ the dev pipeline on synthetic data
def test_dev_pipeline_chooses_checks_and_freezes(monkeypatch, tmp_path):
    periods = {"choose": ("2021-04-01", "2021-07-01"), "check": ("2021-07-01", "2021-08-15"),
               "dev": ("2021-04-01", "2021-08-15")}
    monkeypatch.setattr(E, "CHOOSE", periods["choose"])
    monkeypatch.setattr(E, "DEV", periods["dev"])
    monkeypatch.setattr(devrun, "PERIODS", {f"{k} x": v for k, v in periods.items()})
    monkeypatch.setattr(S, "KS", (50, 100))
    syms = ["BTCUSDT", "ETHUSDT"]
    panels = {s: E.synthetic_panel(seed=i, start="2021-01-01", end="2021-08-20") for i, s in enumerate(syms)}
    fhs = {s: E.synthetic_fhs() for s in syms}
    frozen_path = tmp_path / "frozen.json"
    f = devrun.run(panels, fhs, T.load_calendar(), tmp_path, frozen_path, tmp_path / "similar", every_h=6)
    saved = json.loads(frozen_path.read_text())
    assert set(saved) >= {"grade", "crowd", "similar", "whatif", "thin_hours", "rules", "check"}
    assert saved["similar"]["k"] in (50, 100) and set(saved["similar"]["scaling"]) == set(syms)
    assert len(saved["thin_hours"]["BTCUSDT"]) == 4 and "odds" in saved["crowd"]["BTCUSDT"]
    for name in ("grade", "grade_bar", "grade_grid", "crowd", "crowd_grid", "similar", "similar_grid", "whatif"):
        assert (tmp_path / f"phase12_{name}.csv").exists(), name
    assert S.Library.load(tmp_path / "similar" / "BTCUSDT.npz") is not None
    # the crowd setting is chosen on the choose period only: changing later hours leaves it as it was
    later = {s: P.assign(funding_rank=P["funding_rank"].where(P.index < pd.Timestamp("2021-07-01", tz="UTC"), 1.0))
             for s, P in panels.items()}
    again = devrun.run(later, fhs, T.load_calendar(), tmp_path / "b", tmp_path / "b.json", tmp_path / "b", every_h=6)
    assert again["crowd"]["BTCUSDT"]["level"] == f["crowd"]["BTCUSDT"]["level"]
    # a second run with the same choice keeps the freeze date; different settings are refused
    saved["frozen_at"] = "2026-01-01"
    frozen_path.write_text(json.dumps(saved))
    assert devrun.run(panels, fhs, T.load_calendar(), tmp_path, frozen_path, tmp_path / "similar",
                      every_h=6)["frozen_at"] == "2026-01-01"
    saved["grade"]["knockout_cuts"] = [0.01, 0.02, 0.03, 0.04]
    frozen_path.write_text(json.dumps(saved))
    with pytest.raises(SystemExit, match="different settings"):
        devrun.run(panels, fhs, T.load_calendar(), tmp_path, frozen_path, tmp_path / "similar", every_h=6)


# ------------------------------------------------------------------ the API
@pytest.fixture
def api(monkeypatch):
    from regimecast.api import main, verdict as V
    cfg = copy.deepcopy(settings.DEFAULTS)
    cfg["grade"]["weights"]["timing"] = 0                     # the real clock must not move the grade in tests
    monkeypatch.setattr(V, "CFG", cfg)
    return TestClient(main.app), V, cfg


TRADE = {"symbol": "BTCUSDT", "side": "long", "entry": 100000, "leverage": 25, "margin": 100, "sigma_annual": 0.6}


def test_risk_check_returns_grade_fix_and_what_if(api):
    c, _, _ = api
    r = c.post("/risk-check", json=TRADE).json()
    v, fx = r["verdict"], r["fix"]
    assert v["grade"] == "F" and len(v["reasons"]) == 3 and "not which way the price will go" in v["not_direction"]
    assert v["settings_frozen_at"] is None and v["break_even_win_rate"] > 0.5
    assert fx["needed"] and fx["original"]["grade"] == "F" and fx["fixed"]["grade"] in ("A", "B")
    f = fx["fixed"]
    assert f["p_liquidation"] <= 0.01 and f["stop"] < 100000 and f["max_loss_usdt"] <= fx["original"]["max_loss_usdt"]
    assert f["leverage"] < 25 and f["margin_usdt"] == fx["original"]["margin_usdt"]
    assert [w["scenario"] for w in r["what_if"]] == ["normal", "bad", "crash"]
    crash = r["what_if"][2]
    assert crash["down_usdt"] == -100.0 and crash["down_exit"] == "liquidation"


def test_a_well_built_trade_needs_no_fix(api):
    c, _, _ = api
    r = c.post("/risk-check", json={**TRADE, "leverage": 2, "stop": 93000, "take_profit": 110000}).json()
    assert r["verdict"]["grade"] == "A" and not r["fix"]["needed"] and r["fix"]["fixed"] is None
    assert r["verdict"]["break_even_note"].startswith("With this stop and take-profit")
    assert r["verdict"]["break_even_win_rate"] == pytest.approx((0.002 * 7000 + 2 * 0.0005 * 200) / (0.002 * 17000), rel=1e-3)   # $200 position


def test_a_sound_setup_held_down_by_the_market_is_not_sent_to_a_smaller_margin(api, monkeypatch):
    from regimecast.api import main
    c, _, _ = api
    stress = {"level": "high", "p_stress_next_hour": 0.45, "base_rate": 0.04}
    monkeypatch.setattr(main.vol_live.cache, "get_stress", lambda s: stress)
    monkeypatch.setattr(main.vol_live.cache, "get_regime", lambda s: {"regime": "stressed", "p_calm": 0, "p_active": 0, "p_stressed": 1})
    r = c.post("/risk-check", json={**TRADE, "leverage": 2, "stop": 93000}).json()
    assert r["verdict"]["grade"] == "C" and not r["fix"]["needed"]
    assert "Market conditions hold the grade at C" in r["fix"]["message"]


def test_take_profit_must_be_on_the_winning_side(api):
    c, _, _ = api
    assert c.post("/risk-check", json={**TRADE, "take_profit": 90000}).status_code == 422


def test_a_verdict_failure_never_breaks_the_risk_check(api, monkeypatch):
    c, V, _ = api
    monkeypatch.setattr(V, "Context", lambda *a, **k: 1 / 0)
    r = c.post("/risk-check", json=TRADE)
    assert r.status_code == 200 and r.json()["verdict"] is None and r.json()["p_liquidation"] > 0


def test_crowd_and_similar_moments_in_the_api(api, monkeypatch, tmp_path):
    from regimecast.api import main
    c, V, cfg = api
    P = E.synthetic_panel(seed=2, start="2021-01-01", end="2021-06-01")
    sc = S.fit_scaling(S.features(P))
    S.Library.from_panel(P, sc).save(tmp_path / "BTCUSDT.npz")
    monkeypatch.setattr(settings, "SIMILAR_DIR", tmp_path)
    monkeypatch.setattr(V, "_LIBRARIES", {})
    cfg["crowd"]["BTCUSDT"] = {**TH, "odds": {"long": {"after_warning": 0.21, "n": 300}, "short": {"after_warning": None, "n": 0},
                                              "other": 0.07}}
    cfg["similar"].update({"k": 100, "scaling": {"BTCUSDT": sc}})
    state = {"funding_rate": 5e-4, "ls_top": 3.0, "oi_chg_24h": 0.08, "ret_24h": 0.01, "ret_168h": -0.02,
             "funding_rank": 0.97, "ls_rank": 0.99, "oi_rank": 0.95, "p_stress_3h_ago": 0.02}
    monkeypatch.setattr(main.vol_live.cache, "get_state", lambda s: state)
    v = c.post("/risk-check", json={**TRADE, "leverage": 2, "stop": 93000}).json()["verdict"]
    assert v["crowd"]["warning"] and v["crowd"]["side"] == "long" and "21% of the time, vs 7%" in v["crowd"]["message"]
    assert v["grade"] == "B" and any(r.startswith("Traders are crowded on the long side") for r in v["reasons"])
    s = v["similar"]
    assert s["n"] == 100 and s["range_low_pct"] < s["range_high_pct"] and "past hours" in s["message"]
    short = c.post("/risk-check", json={**TRADE, "side": "short", "leverage": 2, "stop": 107000}).json()["verdict"]
    assert short["grade"] == "A"                                           # against the crowd: no crowding step


def test_live_cache_keeps_the_crowding_inputs(monkeypatch):
    from regimecast.vol import live
    from tests.test_live_vol import bars

    b = bars(end=pd.Timestamp.now(tz="UTC"))
    b["quote_volume"], b["taker_buy_volume"], b["count"] = b["close"] * b["volume"], b["volume"] / 2, 10

    def fake_inputs(symbol, last):
        bb = last[["ts", "open", "high", "low", "close"]]
        idx = pd.date_range(end=pd.Timestamp.now(tz="UTC").floor("5min"), periods=500, freq="5min")
        m = pd.DataFrame({"available_at": idx, "sum_open_interest": np.linspace(1000, 1100, 500),
                          "sum_open_interest_value": 8e7, "sum_toptrader_long_short_ratio": 1.7,
                          "count_long_short_ratio": 2.0, "sum_taker_long_short_vol_ratio": 1.0})
        m["period_start"] = m["ts"] = m["available_at"] - pd.Timedelta(minutes=5)
        f = pd.DataFrame({"ts": pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=3, freq="8h"),
                          "funding_rate": 2e-4, "interval_hours": 8})
        return {"last": last, "mark": bb, "index": bb, "premium": bb.assign(close=0.0), "funding": f,
                "metrics": m, "ob": pd.DataFrame()}

    cache = live.ForecastCache(symbols=["BTCUSDT"], fetch=lambda s: b)
    cache.fetch_inputs = fake_inputs
    cache.refresh_all()
    s = cache.get_state("BTCUSDT")
    assert s["funding_rate"] == pytest.approx(2e-4) and s["ls_top"] == pytest.approx(1.7)
    assert s["oi_chg_24h"] > 0 and s["p_stress_3h_ago"] is not None and s["ret_168h"] is not None
    assert "state" not in cache.get_stress("BTCUSDT")
