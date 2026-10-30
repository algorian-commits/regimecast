"""Phase 12: trade verdict for beginners. Every rule, no look-ahead in similar moments and the crowd ranks,
and the API's verdict."""
import math

import numpy as np
import pandas as pd
import pytest

from regimecast.risk import fhs_live, liquidation as liq
from regimecast.verdict import calendar, crowd, evaluate as E, history as H, rules, similar


# ------------------------------------------------------------------ calendar
def test_macro_calendar_file_is_sane():
    ev = calendar.load_events()
    assert ev["time_utc"].is_monotonic_increasing and ev["time_utc"].is_unique
    assert set(ev["kind"]) == {"fomc", "cpi", "jobs"}
    per_year = ev[ev["time_utc"].dt.year.between(2021, 2024)].groupby([ev["time_utc"].dt.year, "kind"]).size()
    assert (per_year.xs("fomc", level=1) == 8).all() and (per_year.xs("cpi", level=1) == 12).all()
    assert (per_year.xs("jobs", level=1) == 12).all()
    # 14:00 New York for the Fed, 08:30 for the data, in summer and winter time
    fomc = ev[(ev["kind"] == "fomc") & (ev["time_utc"].dt.year == 2024)]["time_utc"].dt.hour
    assert set(fomc) == {18, 19}
    cpi = ev[(ev["kind"] == "cpi")]["time_utc"]
    assert set(cpi.dt.strftime("%H:%M")) == {"12:30", "13:30"}
    assert pd.Timestamp("2022-11-10 13:30", tz="UTC") in set(cpi)   # the Nov 2022 CPI print


def test_events_within_window_and_vector_version_agree():
    now = pd.Timestamp("2024-09-18 10:00", tz="UTC")       # FOMC at 18:00 UTC that day
    names = [e["kind"] for e in calendar.events_within(now, 24)]
    assert names == ["fomc"]
    assert calendar.events_within(now, 1) == []
    assert calendar.events_within(pd.Timestamp("2024-09-18 18:30", tz="UTC"), 1)        # just happened: counts
    assert not calendar.events_within(pd.Timestamp("2024-09-18 19:30", tz="UTC"), 1)
    idx = pd.date_range("2023-01-01", "2023-12-31", freq="7h", tz="UTC")
    vec = calendar.event_flags(idx, 24)
    assert vec.tolist() == [bool(calendar.events_within(t, 24)) for t in idx]


def test_weekend_and_thin_hours():
    assert calendar.is_weekend("2024-09-21 12:00") and not calendar.is_weekend("2024-09-20 12:00")
    idx = pd.date_range("2024-01-01 01:00", periods=24 * 30, freq="1h", tz="UTC")   # hour ends
    vol = pd.Series(100.0, index=idx)
    vol[(idx - pd.Timedelta(hours=1)).hour.isin([2, 3, 4, 22])] = 1.0
    assert calendar.thin_hours_from_volume(vol) == [2, 3, 4, 22]
    assert calendar.in_thin_hours("2024-01-01 03:00", [2, 3]) and not calendar.in_thin_hours("2024-01-01 05:00", [2, 3])


# ------------------------------------------------------------------ grade
def test_step_tables_and_letters():
    assert rules.step(0.5, rules.LIQ_EDGES, rules.LIQ_POINTS, inclusive=True) == 0       # p = L/2: still 0
    assert rules.step(0.51, rules.LIQ_EDGES, rules.LIQ_POINTS, inclusive=True) == 1
    assert rules.step(1.0, rules.LIQ_EDGES, rules.LIQ_POINTS, inclusive=True) == 1       # p = L: within the limit
    assert rules.step(10.0, rules.LIQ_EDGES, rules.LIQ_POINTS, inclusive=True) == 3
    assert rules.step(10.1, rules.LIQ_EDGES, rules.LIQ_POINTS, inclusive=True) == 4
    assert rules.step(0.149, rules.KNOCK_EDGES, rules.KNOCK_POINTS) == 0
    assert rules.step(0.15, rules.KNOCK_EDGES, rules.KNOCK_POINTS) == 2
    assert rules.step(0.5, rules.KNOCK_EDGES, rules.KNOCK_POINTS) == 6
    cuts = (2, 5, 8, 11)
    assert [rules.letter(p, cuts) for p in (0, 1, 2, 4, 5, 7, 8, 10, 11, 20)] == list("AABBCCDDFF")
    assert rules.letter(np.array([0, 11]), cuts).tolist() == ["A", "F"]


def test_stress_rising_rule():
    assert rules.stress_rising(0.12, 0.05) and not rules.stress_rising(0.12, 0.07)
    assert not rules.stress_rising(0.08, 0.01)                    # below 10%: not a warning
    assert not rules.stress_rising(0.5, float("nan"))             # no past value: not rising
    assert rules.stress_rising(np.array([0.2, 0.2]), np.array([0.1, 0.11])).tolist() == [True, False]


def test_grade_safe_trade_and_reckless_trade():
    safe = rules.Facts(side="long", p_liq=0.001, risk_limit=0.01, p_stop=0.08, cost_share=0.04, regime="calm",
                       stress_level="low")
    g = rules.grade(safe, (2, 5, 8, 11))
    assert g["grade"] == "A" and g["points"] == 0 and len(g["reasons"]) == 3 and all(r["good"] for r in g["reasons"])
    bad = rules.Facts(side="long", p_liq=0.25, risk_limit=0.01, p_stop=None, cost_share=0.04, regime="stressed",
                      stress_level="high", crowd="long", crowd_odds=(0.3, 0.1),
                      events=[{"name": "US inflation report (CPI)", "time_utc": "2024-09-11T12:30:00+00:00"}])
    g = rules.grade(bad, (2, 5, 8, 11))
    # knock-out 25% (2) + 25x the limit (4) + no stop (2) + stressed (1) + high stress (2) + crowd (2) + CPI (1)
    assert g["points"] == 14 and g["grade"] == "F"
    assert g["reasons"][0]["text"].startswith("Leverage is too high") and not g["reasons"][0]["good"]
    assert "crowd" in " ".join(r["text"] for r in g["all_reasons"]).lower()
    # the crowd on the OTHER side adds nothing
    assert rules.grade(rules.Facts(**{**bad.__dict__, "crowd": "short"}), (2, 5, 8, 11))["points"] == 12


def test_stop_past_liquidation_counts_liquidation_as_the_knockout():
    f = rules.Facts(side="long", p_liq=0.2, risk_limit=0.01, p_stop=0.05, stop_past_liq=True)
    assert rules.knockout(f) == 0.2
    g = rules.grade(f)
    assert any("past the liquidation price" in r["text"] for r in g["all_reasons"])
    assert g["points"] == 2 + 4 + rules.STOP_PAST_LIQ


def test_vectorised_points_equal_single_trade_points():
    rng = np.random.default_rng(0)
    n = 400
    side = rng.choice([1, -1], n)
    p_liq = rng.uniform(0, 0.4, n)
    p_stop = np.where(rng.random(n) < 0.2, np.nan, rng.uniform(0, 0.9, n))
    past = rng.random(n) < 0.2
    cost = rng.uniform(0, 0.4, n)
    stressed = rng.random(n) < 0.3
    level = rng.choice(["low", "elevated", "high"], n)
    p_now, p_3h = rng.uniform(0, 0.4, n), rng.uniform(0, 0.4, n)
    crowd_s = rng.choice([1, -1, 0], n)
    ev, thin = rng.random(n) < 0.2, rng.random(n) < 0.3
    vec = rules.points_array(side, p_liq, 0.01, p_stop, past, cost, stressed, [rules.STRESS_POINTS[x] for x in level],
                             rules.stress_rising(p_now, p_3h), crowd_s, ev, thin)
    for i in range(n):
        sd = "long" if side[i] == 1 else "short"
        f = rules.Facts(side=sd, p_liq=p_liq[i], risk_limit=0.01,
                        p_stop=None if np.isnan(p_stop[i]) else p_stop[i], stop_past_liq=bool(past[i]),
                        cost_share=cost[i], regime="stressed" if stressed[i] else "active", stress_level=level[i],
                        stress_rising=bool(rules.stress_rising(p_now[i], p_3h[i])),
                        crowd={1: "long", -1: "short", 0: None}[crowd_s[i]],
                        events=[{"name": "x", "time_utc": "2024-01-01T00:00"}] if ev[i] else [],
                        weekend=bool(thin[i]))
        assert rules.grade(f)["points"] == vec[i], i


def test_cost_share():
    # fees 0.1% round trip, no funding, sigma 1% -> typical move 0.798%: 12.5%
    assert rules.cost_share("long", 0.0005, 0.0, 0.01) == pytest.approx(0.001 / (0.01 * math.sqrt(2 / math.pi)))
    # positive funding: longs pay three settlements a day, shorts receive them
    assert rules.cost_share("long", 0.0005, 0.0001, 0.01) > rules.cost_share("short", 0.0005, 0.0001, 0.01)


# ------------------------------------------------------------------ break-even, what-if, fix
def test_break_even_arithmetic():
    # long 1 coin at 100, stop 95, target 110, fees 0.05% per side, no funding
    be = rules.break_even("long", 100, 1, 95, 110, fee_rate=0.0005)
    win = 10 - 0.0005 * (100 + 110)
    loss = 5 + 0.0005 * (100 + 95)
    assert be["win_rate"] == pytest.approx(loss / (win + loss), abs=1e-4)
    assert be["win_usdt"] == pytest.approx(win, abs=0.01) and be["reward_to_risk"] == pytest.approx(win / loss, abs=0.01)
    # without fees and funding a 2:1 target needs a third of the trades
    assert rules.break_even("short", 100, 2, 105, 90, fee_rate=0.0)["win_rate"] == pytest.approx(1 / 3, abs=1e-4)
    # funding paid makes it harder
    assert rules.break_even("long", 100, 1, 95, 110, funding_usdt=0.5)["win_rate"] > be["win_rate"]
    assert rules.break_even("long", 100, 1, 95, None) is None
    assert rules.break_even("long", 100, 1, 99.99, 100.05)["win_rate"] == 1.0   # target below costs
    with pytest.raises(ValueError):
        rules.break_even("long", 100, 1, 95, 90)


def test_what_if_outcomes():
    sc = [{"key": "a", "label": "", "move": -0.02}, {"key": "b", "label": "", "move": -0.06},
          {"key": "c", "label": "", "move": -0.2}, {"key": "d", "label": "", "move": 0.05}]
    # long 10 coins at 100, liquidation at 90, stop at 95, margin 100
    out = rules.what_if("long", 100, 10, 100, 90, 95, sc, fee_rate=0.0)
    assert [o["outcome"] for o in out] == ["loss", "stopped out", "stopped out", "gain"]
    assert out[0]["pnl_usdt"] == -20 and out[1]["pnl_usdt"] == -50 and out[3]["pnl_usdt"] == 50
    no_stop = rules.what_if("long", 100, 10, 100, 90, None, sc, fee_rate=0.0)
    assert no_stop[2]["outcome"] == "liquidated" and no_stop[2]["pnl_usdt"] == -100
    # a stop past the liquidation price never fills first
    assert rules.what_if("long", 100, 10, 100, 90, 85, sc, fee_rate=0.0)[2]["outcome"] == "liquidated"
    s = rules.scenarios("short", {"p50": 0.01, "p90": 0.03, "p99": 0.08}, -0.15)
    assert [x["move"] for x in s] == [0.01, 0.03, 0.08, -0.15]
    assert rules.what_if("short", 100, 1, 50, 150, None, s, fee_rate=0.0)[3]["outcome"] == "gain"


def _evaluator(side, entry, sigma, symbol="BTCUSDT"):
    def evaluate(lev, q, st):
        p = liq.Position(symbol=symbol, side=side, entry=entry, qty=q, leverage=lev, stop=st)
        lp = liq.liquidation_price(p)
        p_liq = liq.first_passage_prob(liq.adverse_log_distance(p, lp), sigma)
        p_stop = liq.first_passage_prob(liq.adverse_log_distance(p, st), sigma) if st else None
        return rules.Candidate(lev, q, st, p_liq, p_stop, lp if math.isfinite(lp) and lp > 0 else None)
    return evaluate


def test_fix_my_trade_lowers_leverage_and_adds_a_stop_outside_noise():
    sigma, entry = 0.025, 100_000.0
    ev = _evaluator("long", entry, sigma)
    qty = 1000 * 25 / entry                                      # $1,000 margin at 25x, no stop
    fix = rules.fix_trade("long", entry, qty, 25, None, 0.01, sigma, ev)
    assert fix is not None and fix.leverage < 25 and fix.stop < entry
    assert fix.p_liq <= 0.01 and fix.p_stop < rules.GOOD_STOP and fix.stop > fix.liq_price
    assert fix.qty * entry / fix.leverage == pytest.approx(1000)  # same margin, smaller position
    # the closest version: one step up the ladder breaks the limit or puts liquidation before the stop
    higher = [x for x in rules.LEVERAGE_LADDER if x > fix.leverage and x <= 25]
    if higher:
        c = ev(min(higher), qty * min(higher) / 25, fix.stop)
        assert c.p_liq > 0.01 or c.liq_price >= c.stop
    # a trade that already meets both conditions needs no fix
    good = ev(2, 1000 * 2 / entry, entry * math.exp(-3 * sigma))
    assert rules.fix_trade("long", entry, good.qty, 2, good.stop, 0.01, sigma, ev) is None


def test_fix_keeps_a_good_stop_and_works_for_shorts():
    sigma, entry = 0.03, 50.0
    ev = _evaluator("short", entry, sigma, "SOLUSDT")
    stop = entry * math.exp(3 * sigma)
    fix = rules.fix_trade("short", entry, 1000 * 50 / entry, 50, stop, 0.01, sigma, ev)
    assert fix.stop == stop and fix.leverage < 50 and fix.liq_price > fix.stop


# ------------------------------------------------------------------ crowd
def _hourly(n=2000, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01 01:00", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"funding_rate": rng.normal(1e-4, 5e-5, n), "ls_top": rng.normal(1.5, 0.2, n),
                         "oi_chg_24h": rng.normal(0, 0.03, n)}, index=idx)


def test_trailing_rank_uses_only_the_past():
    h = _hourly()
    r = crowd.trailing_rank(h["ls_top"])
    assert r.iloc[: crowd.MIN_HOURS - 1].isna().all() and r.iloc[crowd.MIN_HOURS:].notna().all()
    t = 1500
    changed = h["ls_top"].copy()
    changed.iloc[t + 1:] = 99.0                                   # the future changes...
    assert crowd.trailing_rank(changed).iloc[: t + 1].equals(r.iloc[: t + 1])   # ...nothing up to t does
    # by hand: rank among the last 336 values, ties counted half
    w = h["ls_top"].iloc[t - crowd.WINDOW_HOURS + 1: t + 1]
    x = h["ls_top"].iloc[t]
    assert r.iloc[t] == pytest.approx(((w < x).sum() + 0.5 * (w == x).sum()) / len(w))


def test_crowd_warning_rules():
    rk = pd.DataFrame({"funding_rate_rank": [0.95, 0.02, 0.95, 0.95, np.nan, 0.5],
                       "ls_top_rank": [0.97, 0.05, 0.96, 0.5, 0.99, 0.95],
                       "oi_chg_24h_rank": [0.92, 0.93, 0.5, 0.95, 0.99, 0.95]})
    assert crowd.warning(rk, 0.9, "all3").tolist() == [1, -1, 0, 0, 0, 0]
    assert crowd.warning(rk, 0.9, "no_oi").tolist() == [1, -1, 1, 0, 0, 0]
    assert crowd.warning(rk, 0.9, "2of3").tolist() == [1, -1, 1, 1, 0, 1]


def test_crowd_events_ratio_and_bootstrap():
    mae_long = pd.Series([0.06, 0.01, 0.06, 0.01, 0.01, 0.01, np.nan])
    mae_short = pd.Series([0.01, 0.06, 0.01, 0.01, 0.01, 0.06, 0.01])
    drop, rise = crowd.events(mae_long, mae_short)
    assert drop.tolist()[:6] == [1.0, 0.0, 1.0, 0.0, 0.0, 0.0] and np.isnan(drop.iloc[6])
    warn = np.array([1, 0, 0, 0, 0, -1, 1])
    r = crowd.ratio(warn, drop, rise)
    # warnings (rows 0 and 5; row 6 has no outcome): 2 hits; other hours: drop rate 1/4, rise rate 1/4
    assert r["n_warnings"] == 2 and r["hits"] == 2 and r["expected"] == pytest.approx(0.5) and r["ratio"] == 4
    rng = np.random.default_rng(3)
    n = 5000
    w = np.where(rng.random(n) < 0.05, 1, 0)
    d = (rng.random(n) < np.where(w == 1, 0.3, 0.1)).astype(float)
    lo, hi = crowd.block_bootstrap(w, d, np.zeros(n), block_hours=24, reps=200)
    assert lo < crowd.ratio(w, d, np.zeros(n))["ratio"] < hi and lo > 1


# ------------------------------------------------------------------ similar moments
def _pool(n=3000, seed=2):
    rng = np.random.default_rng(seed)
    ts = pd.date_range("2022-01-01", periods=n, freq="1h", tz="UTC")
    Z = rng.normal(size=(n, len(similar.FEATURES))).astype(np.float32)
    return ts, Z, np.full(n, 0.02), rng.normal(0, 0.02, n)


def test_similar_moments_use_only_hours_with_a_complete_next_day():
    ts, Z, sig, r = _pool()
    pool = similar.Pool(ts, Z, sig, r)
    now = ts[2000]
    n = pool.known_at(now)
    assert pd.Timestamp(int(pool.ts[n - 1]), tz="UTC") + pd.Timedelta(hours=24) <= now
    assert pd.Timestamp(int(pool.ts[n]), tz="UTC") + pd.Timedelta(hours=24) > now
    z = Z[2000]
    idx = pool.neighbours(z, now, 50)
    assert len(idx) == 50 and all(pool.time(i) + pd.Timedelta(hours=24) <= now for i in idx)
    # the current hour itself (distance 0) is NOT allowed: its next day is not over
    assert 2000 not in idx
    # changing everything after now - 24 h changes nothing
    Z2, r2 = Z.copy(), r.copy()
    Z2[1977:], r2[1977:] = 0.0, 9.0
    pool2 = similar.Pool(ts, Z2, sig, r2)
    s1 = similar.summarise(pool, idx, 0.02, 50, True, 1.0, 0.5)
    s2 = similar.summarise(pool2, pool2.neighbours(z, now, 50), 0.02, 50, True, 1.0, 0.5)
    assert s1 == s2


def test_similar_summary_maths_and_text():
    ts, Z, sig, r = _pool()
    r = np.where(np.arange(len(r)) % 2 == 0, 0.01, -0.03)
    pool = similar.Pool(ts, Z, sig * 2, r)
    idx = np.arange(100)
    s = similar.summarise(pool, idx, 0.02, 100, False, 0.0, 0.5)
    assert s["share_up"] == 0.5 and s["p_up"] == 0.5 and s["lo"] == pytest.approx(-0.03) and s["hi"] == pytest.approx(0.01)
    scaled = similar.summarise(pool, idx, 0.02, 100, True, 0.0, 0.5)      # past sigma 0.04, now 0.02: halved
    assert scaled["lo"] == pytest.approx(-0.015)
    pulled = similar.summarise(pool, idx[::2], 0.02, 50, False, 3.0, 0.5)  # all up, pulled toward 0.5
    assert pulled["share_up"] == 1.0 and pulled["p_up"] == pytest.approx((50 + 150 * 0.5) / 200)
    assert "coin flip" in " ".join(similar.plain_text(s))
    ex = similar.examples(pool, np.arange(100), 3)
    assert len(ex) == 3 and pd.Timestamp(ex[1]["time"]) - pd.Timestamp(ex[0]["time"]) >= pd.Timedelta(hours=24)


def test_similar_runs_never_use_the_future():
    rng = np.random.default_rng(4)
    n = 24 * 120
    idx = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    h = pd.DataFrame({"p_stressed": rng.random(n), "p_calm": rng.random(n), "sigma24": 0.02 + 0.01 * rng.random(n),
                      "ret_24h": rng.normal(0, 0.02, n), "ret_168h": rng.normal(0, 0.05, n),
                      "funding_rate_rank": rng.random(n), "ls_top_rank": rng.random(n), "oi_chg_24h_rank": rng.random(n),
                      "r24": rng.normal(0, 0.02, n)}, index=idx)
    sc = similar.scaling(similar.describe(h))
    a = E.similar_runs(h, sc, "2023-03-01", "2023-03-05", k_max=200)
    h2 = h.copy()
    h2.loc[h2.index >= pd.Timestamp("2023-03-05", tz="UTC"), "r24"] = 5.0     # the future after the window
    b = E.similar_runs(h2, sc, "2023-03-01", "2023-03-05", k_max=200)
    pd.testing.assert_frame_equal(a, b)


# ------------------------------------------------------------------ history helpers and evaluation
def test_vector_fhs_matches_fhs_live():
    rng = np.random.default_rng(5)
    entry = fhs_live.fit(pd.Series(np.abs(rng.standard_t(4, 5000))))
    d = np.array([0.0, 0.005, 0.02, 0.05, 0.3, np.inf])
    sig = np.array([0.02, 0.02, 0.02, 0.02, 0.02, 0.02])
    vec = H.survival_vec(entry, d, sig)
    assert vec.tolist() == pytest.approx([fhs_live.survival(entry, x, 0.02) for x in d])


def test_fhs_tables_are_walk_forward():
    rng = np.random.default_rng(6)
    idx = pd.date_range("2021-01-01", periods=24 * 400, freq="1h", tz="UTC")
    h = pd.DataFrame({"sigma24": 0.02, "mae_long": np.abs(rng.normal(0, 0.02, len(idx))),
                      "mae_short": np.abs(rng.normal(0, 0.02, len(idx)))}, index=idx)
    t1 = H.fhs_tables(h)
    b = t1[0][0]
    h2 = h.copy()
    h2.loc[h2.index > b - pd.Timedelta(hours=24), "mae_long"] = 1.0     # outcomes not complete by the block start
    assert H.fhs_tables(h2)[0][1]["long"] == t1[0][1]["long"]
    blk = H.block_of(pd.DatetimeIndex([b - pd.Timedelta(hours=1), b, t1[1][0]]), t1)
    assert blk.tolist() == [-1, 0, 1]


def test_merge_small_and_grade_table():
    counts = pd.Series({"A": 500, "B": 50, "C": 300, "D": 100, "F": 120})
    assert E.merge_small(counts) == [["A"], ["B", "C"], ["D", "F"]]
    assert E.merge_small(pd.Series({"A": 500, "B": 300, "C": 300, "D": 300, "F": 20})) == [["A"], ["B"], ["C"],
                                                                                         ["D", "F"]]
    tr = pd.DataFrame({"points": [0] * 300 + [3] * 300 + [6] * 300 + [9] * 300 + [12] * 300,
                       "hit": [1] * 30 + [0] * 270 + [1] * 60 + [0] * 240 + [1] * 90 + [0] * 210
                       + [1] * 120 + [0] * 180 + [1] * 150 + [0] * 150})
    tab, chk = E.grade_table(tr, (2, 5, 8, 11))
    assert tab["grade"].tolist() == list("ABCDF") and chk["rises_A_to_F"] and chk["F_over_A"] == pytest.approx(5)
    assert chk["passes"]


def test_grid_liquidation_distance():
    # 10x long in the first tier: about a 10% move minus the maintenance margin
    d = E.liq_distance("BTCUSDT", "long", 10)
    assert 0.08 < 1 - math.exp(-d) < 0.1
    assert E.liq_distance("BTCUSDT", "long", 50) < E.liq_distance("BTCUSDT", "long", 2)


def test_final_entry_point_refuses_without_final():
    from regimecast.verdict.final import run_final

    with pytest.raises(ValueError):
        run_final()


def test_frozen_settings_are_complete():
    from regimecast.verdict.final import load_frozen

    fz = load_frozen()
    if not fz:
        pytest.skip("frozen.json not written yet")
    assert tuple(fz["grading"]["cuts"]) in rules.CUT_CANDIDATES
    assert (fz["crowd"]["q"], fz["crowd"]["rule"]) in crowd.CANDIDATES
    assert fz["similar"]["k"] in similar.K_CANDIDATES and fz["similar"]["prior"] in similar.PRIOR_CANDIDATES
    for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        assert set(fz["similar"]["scaling"][s]["mean"]) == set(similar.FEATURES)
        q = fz["what_if"]["quantiles"][s]["all"]["long"]
        assert 0 < q["p50"] < q["p90"] < q["p99"] < 1
        assert -0.6 < fz["what_if"]["crash_day"][s] < 0


# ------------------------------------------------------------------ live pieces and the API
def test_live_crowd_frame_and_trend():
    from regimecast.verdict import live

    idx = pd.date_range("2024-01-01 01:00", periods=500, freq="1h", tz="UTC")
    oi = pd.Series(np.linspace(100, 200, 500), index=idx)
    ls = pd.Series(1.5, index=idx)
    funding = pd.Series([1e-4, 2e-4], index=[idx[0], idx[250] - pd.Timedelta(minutes=1)])
    h = live.crowd_frame(oi, ls, funding)
    assert h.index[-1] == idx[-1] and h["funding_rate"].iloc[-1] == 2e-4
    assert h["oi_chg_24h"].iloc[-1] == pytest.approx(math.log(oi.iloc[-1] / oi.iloc[-25]))
    assert h["oi_chg_24h_rank"].iloc[-1] > 0 and h["ls_top_rank"].iloc[-1] == 0.5
    c = [{"t": (idx[0] + pd.Timedelta(hours=i)).isoformat(), "close": 100.0 * (1.001 ** i)} for i in range(200)]
    tr = live.trend(c)
    assert tr["ret_24h"] == pytest.approx(24 * math.log(1.001)) and tr["ret_168h"] == pytest.approx(168 * math.log(1.001))


def test_api_risk_check_returns_the_verdict():
    from fastapi.testclient import TestClient
    from regimecast.api.main import app

    c = TestClient(app)
    r = c.post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 100000, "leverage": 25,
                                    "margin": 200, "sigma_annual": 0.55}).json()
    v = r["verdict"]
    assert v["grade"] in rules.LETTERS and len(v["reasons"]) == 3
    assert v["fix"] is not None and v["fix"]["leverage"] < 25 and v["fix"]["stop"] < 100000
    assert v["fix"]["p_liquidation_24h"] <= 0.01 and v["fix"]["changes"]
    assert v["break_even"] is None and v["break_even_hint"]
    if v["what_if"]:
        assert [w["key"] for w in v["what_if"]] == ["normal", "bad", "very_bad", "crash"]
        assert v["what_if"][3]["outcome"] == "liquidated" and v["what_if"][3]["pnl_usdt"] == pytest.approx(-200)
    ok = c.post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 100000, "leverage": 2,
                                     "margin": 200, "stop": 92000, "target": 110000, "sigma_annual": 0.55}).json()
    assert ok["verdict"]["break_even"]["win_rate"] < 0.5
    assert rules.LETTERS.index(ok["verdict"]["grade"]) < rules.LETTERS.index(v["grade"])
    bad_target = c.post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 100000, "leverage": 2,
                                             "margin": 200, "target": 90000})
    assert bad_target.status_code == 422
