"""Popular trader tracker (13D): scorecard maths on synthetic fills, the copy test's history check and
no-look-ahead rules, the liquidation map, the two alerts and what the page may say."""
import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from regimecast import db
from regimecast.alerts import engine as alert_engine
from regimecast.api import accounts
from regimecast.api import traders as traders_api
from regimecast.api.main import app
from regimecast.db.schema import metadata, whale_events
from regimecast.traders import copytest as ct
from regimecast.traders import fills as fl
from regimecast.traders import liqmap, published, rules
from regimecast.traders import scorecard as sc
from regimecast.traders.hl import Budget, info_weight, select_pool
from regimecast.traders.live import TraderWatch, merge_trades

T0 = pd.Timestamp("2026-01-01", tz="UTC")


def fill(minutes, coin="BTC", side="B", sz=1.0, start=0.0, pnl=0.0, fee=0.0, px=100.0, tid=None):
    return {"time": T0 + pd.Timedelta(minutes=minutes), "coin": coin, "px": px, "sz": sz, "side": side, "dir": "",
            "start_position": start, "closed_pnl": pnl, "fee": fee, "fee_token": "USDC", "crossed": True,
            "tid": tid if tid is not None else int(minutes), "twap_id": "", "liquidated": False}


def frame(rows):
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ pool and rate limit
def lb(addr, value, month=0.0):
    return {"ethAddress": addr, "accountValue": str(value), "displayName": None,
            "windowPerformances": [["month", {"pnl": str(month)}], ["allTime", {"pnl": "0"}]]}


def test_pool_is_whale_watch_plus_everyone_above_1m():
    rows = [lb("0xA", 5e6, 10), lb("0xB", 2e6, 5), lb("0xC", 5e5, 99)]
    pool = select_pool(rows)
    assert [p["address"] for p in pool] == ["0xa", "0xb"]    # under $1M is out
    assert all(p["whale_watch"] for p in pool)
    many = [lb(f"0x{i}", 2e6 + i) for i in range(150)] + [lb("0xz", 1.5e6)]
    assert len(select_pool(many)) == 151                     # above $1M counts even if Whale Watch skips it
    assert sum(p["whale_watch"] for p in select_pool(many)) < 151


def test_request_weights_follow_hyperliquids_table():
    assert info_weight({"type": "clearinghouseState"}) == 2
    assert info_weight({"type": "metaAndAssetCtxs"}) == 20
    assert info_weight({"type": "userFillsByTime"}, 2000) == 120


def test_budget_waits_once_the_minute_is_spent():
    now = [0.0]
    naps = []

    def sleep(s):
        naps.append(s)
        now[0] += s

    b = Budget(100, clock=lambda: now[0], sleep=sleep)
    b.wait(60)
    b.wait(40)
    assert naps == []
    b.wait(20)
    assert naps and now[0] >= 60


def test_download_stops_at_a_short_page_and_removes_duplicates():
    def f(t, tid):
        return {"time": t, "tid": tid, "coin": "BTC", "px": "1", "sz": "1", "side": "B"}

    class Fake:
        def __init__(self):
            self.bodies = []

        def post(self, body):
            self.bodies.append(body)
            if len(self.bodies) == 1:
                return [f(1000 + i, i) for i in range(fl.FILLS_PAGE)]
            return [f(1000 + fl.FILLS_PAGE - 1, fl.FILLS_PAGE - 1), f(5000, 99999)]

    fake = Fake()
    out = fl.download_account(fake, "0xa")
    assert len(out) == fl.FILLS_PAGE + 1 and len(fake.bodies) == 2
    assert fake.bodies[1]["startTime"] == 1000 + fl.FILLS_PAGE - 1


def test_cheap_depth_check_defers_accounts_without_old_fills():
    class Empty:
        def post(self, body):
            assert "endTime" in body
            return []

    assert fl.download_account(Empty(), "0xa", older_than_ms=5000) is None

    class Old:
        def __init__(self):
            self.bodies = []

        def post(self, body):
            self.bodies.append(body)
            if "endTime" in body:
                return [{"time": 100, "tid": 1}]
            return [{"time": 6000, "tid": 2}]

    old = Old()
    got = fl.download_account(old, "0xa", older_than_ms=5000)
    assert [x["tid"] for x in got] == [1, 2]
    assert old.bodies[1]["startTime"] == 5000      # carries on after the old part, nothing lost


def test_fills_table_and_depth_row():
    raw = [{"time": 1000 * 86400 * 365, "coin": "BTC", "px": "100", "sz": "2", "side": "B", "startPosition": "0",
            "closedPnl": "0", "fee": "0.1", "tid": 7, "dir": "Open Long", "_user": "0xa"}]
    df = fl.to_frame(raw)
    assert df["px"].iloc[0] == 100.0 and df["fee"].iloc[0] == 0.1 and str(df["time"].dt.tz) == "UTC"
    row = fl.depth_row("0xa", df, df["time"].iloc[0] + pd.Timedelta(days=10))
    assert row["n_fills"] == 1 and row["depth_days"] == 10 and row["truncated"] is False
    assert fl.depth_row("0xb", fl.to_frame([]), T0)["depth_days"] == 0.0


# ------------------------------------------------------------------ scorecard maths
def test_spot_fills_are_left_out():
    df = frame([fill(0), fill(1, coin="@107"), fill(2, coin="PURR/USDC"), fill(3, coin="xyz:GOLD")])
    assert list(sc.perp_fills(df)["coin"]) == ["BTC", "xyz:GOLD"]


def test_daily_pnl_counts_fees_and_fills_quiet_days_with_zero():
    df = frame([fill(0, pnl=10, fee=1), fill(5, pnl=5, fee=1), fill(2 * 1440, pnl=-3, fee=0)])
    d = sc.daily_pnl(df, T0, T0 + pd.Timedelta(days=3))
    assert list(d) == [13.0, 0.0, -3.0, 0.0]


def test_round_trips_including_a_flip_and_a_trade_older_than_the_history():
    df = frame([
        fill(0, side="A", sz=1, start=2, pnl=5),                # began before the history: not "inside"
        fill(1, side="A", sz=1, start=1, pnl=5, tid=2),         # ...and closes here
        fill(10, side="B", sz=1, start=0, tid=3),               # opens long
        fill(20, side="A", sz=3, start=1, pnl=7, fee=1, tid=4),  # flips: closes the long, opens a short of 2
        fill(30, side="B", sz=2, start=-2, pnl=-4, tid=5),      # closes the short
    ])
    rt = sc.round_trips(df)
    assert list(rt["inside"]) == [False, True, True]
    assert list(rt["pnl"]) == [10.0, 6.0, -4.0]
    assert rt["hours"].iloc[1] == pytest.approx(10 / 60)
    assert rt["notional"].iloc[1] == 100.0 and rt["notional"].iloc[2] == 200.0


def test_track_record_numbers_on_a_known_series():
    rows, tid = [], 0
    pnl = [10.0, -5.0] * 20      # 40 days, 20 wins of 10 and 20 losses of 5
    for i, p in enumerate(pnl):
        rows.append(fill(i * 1440, side="B", tid=tid))
        rows.append(fill(i * 1440 + 60, side="A", start=1, pnl=p, tid=tid + 1))
        tid += 2
    tr = sc.track_record(frame(rows), T0 + pd.Timedelta(days=39), account_value=1000.0, n_boot=200)
    x = np.array(pnl)
    assert tr["n_trades"] == 40 and tr["n_days"] == 40 and tr["tested"]
    assert tr["win_rate"] == 0.5 and tr["profit_factor"] == 2.0
    assert tr["net_pnl"] == 100.0 and tr["return_on_account"] == 0.1
    assert tr["sharpe"] == pytest.approx(x.mean() / x.std(ddof=1) * math.sqrt(365))
    assert tr["max_drawdown"] == 5.0
    assert tr["median_hold_hours"] == 1.0
    assert tr["avg_leverage"] == pytest.approx(100.0 / 1000.0)
    assert tr["coins"] == [{"coin": "BTC", "share": 1.0}]


def test_too_little_history_is_not_tested():
    rows = [fill(0), fill(60, side="A", start=1, pnl=100, tid=99)]
    tr = sc.track_record(frame(rows), T0 + pd.Timedelta(days=2), n_boot=50)
    assert tr["p"] == 1.0 and tr["tested"] is False


def test_max_drawdown_counts_a_fall_from_the_start():
    assert sc.max_drawdown(np.array([-5.0, -8.0, -2.0])) == 8.0
    assert sc.max_drawdown(np.array([5.0, 2.0, 9.0, 1.0])) == 8.0


def test_skill_test_finds_a_real_edge_and_not_luck():
    rng = np.random.default_rng(1)
    assert sc.skill_test(rng.normal(1.0, 1.0, 300), 500)["p"] < 0.01
    assert sc.skill_test(rng.normal(-1.0, 1.0, 300), 500)["p"] > 0.9
    # about 5% of no-skill accounts look significant at 5%: the test is honest about luck
    ps = [sc.skill_test(rng.normal(0, 1, 200), 300, seed=i)["p"] for i in range(200)]
    assert 0.01 <= np.mean(np.array(ps) < 0.05) <= 0.12


def test_bootstrap_blocks_are_consecutive_days():
    idx = sc._blocks(100, 5, 4, np.random.default_rng(0))
    assert idx.shape == (5, 100)
    assert np.all((idx[0, 1:4] - idx[0, :3]) % 100 == 1)


def test_benjamini_hochberg_against_a_worked_example():
    p = np.array([0.01, 0.04, 0.03, 0.005])
    assert list(sc.benjamini_hochberg(p, 0.05)) == [True, True, True, True]
    assert list(sc.benjamini_hochberg(np.array([0.01, 0.2, 0.3, 0.4]), 0.05)) == [True, False, False, False]
    assert list(sc.benjamini_hochberg(np.array([0.2, 0.3]), 0.05)) == [False, False]


def test_correction_stops_the_luckiest_of_a_big_pool_being_called_skilled():
    rng = np.random.default_rng(2)
    ps = [sc.skill_test(rng.normal(0, 1, 120), 400, seed=i)["p"] for i in range(300)]
    assert min(ps) < 0.05                              # plenty look lucky one at a time
    assert sc.benjamini_hochberg(np.array(ps), 0.05).sum() <= 3


def test_labels():
    cards = pd.DataFrame([
        {"address": "a", "n_fills": 50, "p": 0.0001, "mean_daily_pnl": 5.0, "net_pnl": 500.0},
        {"address": "b", "n_fills": 50, "p": 0.6, "mean_daily_pnl": 1.0, "net_pnl": 50.0},
        {"address": "c", "n_fills": 50, "p": 0.99, "mean_daily_pnl": -1.0, "net_pnl": -50.0},
        {"address": "d", "n_fills": 0, "p": math.nan, "mean_daily_pnl": math.nan, "net_pnl": math.nan},
    ])
    out = sc.label(cards).set_index("address")
    assert list(out["label"]) == ["skilled", "unproven", "losing", "no_trades"]
    assert out["bh_family"].iloc[0] == 3    # the family is every account with perp fills


# ------------------------------------------------------------------ copy test: history check
def depth_row(a, first, last, n=10, dl="2026-10-31"):
    return {"address": a, "n_fills": n, "first_fill": pd.Timestamp(first, tz="UTC").isoformat(),
            "last_fill": pd.Timestamp(last, tz="UTC").isoformat(), "downloaded_at": pd.Timestamp(dl, tz="UTC").isoformat()}


def test_history_gate_needs_60_accounts_spanning_six_months():
    deep = [depth_row(f"d{i}", "2026-01-01", "2026-10-01") for i in range(60)]
    assert ct.history_gate(pd.DataFrame(deep))["historical_test"] is True
    g = ct.history_gate(pd.DataFrame(deep[:59]))
    assert g["historical_test"] is False and g["accounts_6m"] == 59


def test_old_one_off_trades_and_busy_short_histories_do_not_count():
    rows = [depth_row("oneoff", "2026-03-05", "2026-03-05"),   # traded on one day, 8 months ago
            depth_row("busy", "2026-10-29", "2026-10-30", n=17830),
            depth_row("edge", "2026-01-01", pd.Timestamp("2026-01-01") + pd.Timedelta(days=181)),
            depth_row("deep", "2026-01-01", pd.Timestamp("2026-01-01") + pd.Timedelta(days=182))]
    g = ct.history_gate(pd.DataFrame(rows))
    assert g["eligible"] == ["deep"]


def test_periods_are_two_thirds_and_one_third_of_the_common_history():
    d = pd.DataFrame([depth_row("a", "2026-01-01", "2026-10-01", dl="2026-10-31"),
                      depth_row("b", "2026-02-10", "2026-10-01", dl="2026-11-02")])
    p = ct.periods(d, ["a", "b"])
    assert p["a_start"] == pd.Timestamp("2026-02-10", tz="UTC")    # the latest first fill
    assert p["end"] == pd.Timestamp("2026-10-31", tz="UTC")        # the earliest download
    assert abs((p["b_start"] - p["a_start"]) / (p["end"] - p["a_start"]) - 2 / 3) < 0.01


# ------------------------------------------------------------------ copy test: simulation
def minutes(n=4 * 1440, start=100.0, step=0.0):
    idx = pd.date_range(T0, periods=n, freq="min")
    return pd.Series(start + step * np.arange(n), index=idx)


NO_FUNDING = pd.Series(dtype=float)


def sim(f, px, funding=NO_FUNDING, t1=None, **kw):
    return ct.simulate_coin(frame(f), px, funding, T0, t1 or T0 + pd.Timedelta(days=4), **kw)


def test_copier_trades_five_minutes_after_the_fill():
    px = minutes(step=1.0)       # rises 1 a minute
    out = sim([fill(100)], px, fee=0.0)
    held = len(px) - 1 - 105     # from the close of minute 105 to the last close
    assert out.sum() == pytest.approx(held)


def test_a_price_jump_inside_the_delay_is_missed():
    px = minutes()
    px.iloc[103:] += 50          # jumps 3 minutes after the fill; the copier buys after it
    assert sim([fill(100)], px, fee=0.0).sum() == pytest.approx(0.0)
    assert sim([fill(100)], px, fee=0.0, delay=pd.Timedelta(0)).sum() == pytest.approx(50.0)


def test_taker_fee_on_every_change_of_position():
    px = minutes(start=1000.0)
    out = sim([fill(100, sz=2), fill(200, side="A", sz=2, start=2, tid=5)], px)
    assert out.sum() == pytest.approx(-(2 * 1000 + 2 * 1000) * 0.00045)


def test_funding_is_paid_by_longs_when_positive_and_received_by_shorts():
    px = minutes()
    fr = pd.Series([0.001], index=[T0 + pd.Timedelta(hours=10)])
    assert sim([fill(0)], px, fr, fee=0.0).sum() == pytest.approx(-0.1)
    assert sim([fill(0, side="A")], px, fr, fee=0.0).sum() == pytest.approx(0.1)


def test_position_older_than_the_window_is_held_from_the_start():
    px = minutes(step=1.0)
    # the first fill in the data closes a long of 1: the copier held it until then, with no entry fee
    out = sim([fill(100, side="A", start=1)], px, fee=0.0)
    assert out.sum() == pytest.approx(105)


def test_later_prices_and_fills_cannot_change_earlier_days():
    px = minutes(step=0.5)
    early = sim([fill(10)], px)
    px2 = px.copy()
    px2.iloc[3 * 1440:] *= 3
    late = sim([fill(10), fill(3 * 1440 + 10, side="A", start=1, tid=77)], px2)
    pd.testing.assert_series_equal(early.iloc[:3], late.iloc[:3])
    assert early.iloc[3] != late.iloc[3]


def test_buy_and_hold_is_a_third_in_each_coin():
    px = {c: minutes(start=100.0, step=0.0) for c in ct.COPY_COINS}
    t1 = T0 + pd.Timedelta(days=4)
    assert ct.buy_and_hold(px, {}, T0, t1).sum() == pytest.approx(0.0)
    px["BTC"] = pd.Series(np.linspace(100, 200, len(px["BTC"])), index=px["BTC"].index)
    assert ct.buy_and_hold(px, {}, T0, t1).add(1).prod() - 1 == pytest.approx(1 / 3, rel=1e-3)


# ------------------------------------------------------------------ copy test: no look-ahead, the bar
def edge_fills(direction, seed, days=60, base="BTC"):
    """A trader who opens at each day's start and closes it after 12 hours, winning ``direction`` x 10."""
    rng = np.random.default_rng(seed)
    rows, tid = [], 0
    for d in range(days):
        win = direction * 10 + rng.normal(0, 5)
        rows.append(fill(d * 1440 + 1, coin=base, tid=tid))
        rows.append(fill(d * 1440 + 721, coin=base, side="A", start=1, pnl=win, tid=tid + 1))
        tid += 2
    return frame(rows)


def test_selection_refuses_a_fill_from_period_b():
    b = T0 + pd.Timedelta(days=10)
    with pytest.raises(ValueError):
        ct.select({"x": frame([fill(11 * 1440)])}, T0, b)


def test_selection_ignores_everything_in_period_b():
    """The pre-registered rule: changing what happens in B cannot change who is copied."""
    b0 = T0 + pd.Timedelta(days=40)
    fills = {f"w{i}": edge_fills(1, i) for i in range(5)} | {f"l{i}": edge_fills(-1, 10 + i) for i in range(5)}

    def chosen(data):
        before = {a: f[f["time"] < b0] for a, f in data.items()}
        return ct.select(before, T0, b0, top_share=0.2, n_boot=100)

    base = chosen(fills)
    shuffled = {a: f.assign(closed_pnl=np.where(f["time"] >= b0, -f["closed_pnl"] * 100, f["closed_pnl"]))
                for a, f in fills.items()}
    pd.testing.assert_frame_equal(base, chosen(shuffled))
    assert set(base.loc[base["selected"], "address"]) <= {f"w{i}" for i in range(5)}


def test_universe_needs_a_copy_coin_and_some_risk_in_period_a():
    b0 = T0 + pd.Timedelta(days=40)
    f = {"alt": edge_fills(1, 1, days=40, base="DOGE"), "btc": edge_fills(1, 2, days=39),
         "flat": edge_fills(1, 3, days=39)}
    s = ct.select(f, T0, b0, n_boot=50, can_size={"btc", "alt"}).set_index("address")
    assert s.loc["btc", "in_universe"] and not s.loc["alt", "in_universe"] and not s.loc["flat", "in_universe"]


def bar(copy_mean, random_mean, bh_mean, days=120, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(T0, periods=days, freq="D")
    daily = {"pick": pd.Series(rng.normal(copy_mean, 1, days), index=idx)}
    uni = ["pick"]
    for i in range(30):
        daily[f"r{i}"] = pd.Series(rng.normal(random_mean, 1, days), index=idx)
        uni.append(f"r{i}")
    bh = pd.Series(rng.normal(bh_mean, 1, days), index=idx)
    return ct.compare(daily, {a: 1.0 for a in uni}, ["pick"], uni, bh, n_random=50, n_boot=400, seed=seed)


def test_bar_passes_only_when_all_three_parts_pass():
    r = bar(1.5, 0.0, 0.0)
    assert r["beats_random"] and r["beats_buy_and_hold"] and r["ci_above_zero"] and r["passed"]
    assert r["sharpe_copy"] > r["sharpe_random_median"]
    assert not bar(1.5, 0.0, 3.0)["passed"]    # loses to buy-and-hold
    assert not bar(0.0, 0.0, -1.0)["passed"]   # no better than random: the interval includes 0


def test_full_run_selects_on_a_and_scores_on_b():
    end = T0 + pd.Timedelta(days=60)
    prices = {c: minutes(n=60 * 1440, start=100.0, step=0.001) for c in ct.COPY_COINS}
    fills = {f"w{i}": edge_fills(1, i) for i in range(6)} | {f"l{i}": edge_fills(-1, 10 + i) for i in range(6)}
    rows = []
    for a, f in fills.items():
        rows.append({"address": a, "n_fills": len(f), "first_fill": T0.isoformat(),
                     "last_fill": f["time"].max().isoformat(), "downloaded_at": end.isoformat()})
    out = ct.run(fills, pd.DataFrame(rows), prices, {}, n_random=20, n_boot=100, score_boot=100)
    assert out["b_start"] == (T0 + pd.Timedelta(days=40)).isoformat()
    assert out["universe"] == 12 and len(out["selected"]) == 2
    assert out["result"]["days"] == 20
    assert set(out["selected"]) <= {f"w{i}" for i in range(6)}


# ------------------------------------------------------------------ liquidation map
def pos(coin="BTC", size=1.0, usd=100.0, liq=95.0, address="0xa"):
    return {"coin": coin, "size": size, "position_usd": usd, "liquidation": liq, "address": address}


def test_map_adds_up_per_band_and_side():
    p = [pos(usd=100, liq=98.5), pos(usd=50, liq=98.1, address="0xb"), pos(size=-1, usd=70, liq=101.5, address="0xc"),
         pos(size=-1, usd=30, liq=98.5, address="0xd"), pos(coin="ETH", usd=999, liq=98.5)]
    m = liqmap.build(p, "BTC", 100.0)
    by = {b["from_pct"]: b for b in m["bands"]}
    assert by[-2.0]["long_usd"] == 150 and by[-2.0]["n_long"] == 2 and by[-2.0]["short_usd"] == 30
    assert by[1.0]["short_usd"] == 70 and by[1.0]["n_short"] == 1
    assert by[-2.0]["price_low"] == 98.0 and by[-2.0]["price_high"] == 99.0
    assert m["n_positions"] == 4 and m["n_accounts"] == 4
    assert [b["from_pct"] for b in m["bands"]] == sorted(by)   # lowest price first
    assert "tracked Hyperliquid accounts only" in m["label"] and "not the whole market" in m["label"]


def test_map_reports_what_it_leaves_out():
    m = liqmap.build([pos(usd=10, liq=None), pos(usd=20, liq=float("nan")), pos(usd=40, liq=50.0)], "BTC", 100.0)
    assert m["bands"] == [] and m["no_liquidation_price_usd"] == 30 and m["n_no_liquidation_price"] == 2
    assert m["outside_span_usd"] == 40


def test_band_width_changes_the_grouping_not_the_total():
    p = [pos(usd=10, liq=98.5), pos(usd=20, liq=97.5), pos(usd=30, liq=96.5)]
    for w in (0.5, 1.0, 2.0):
        assert sum(b["long_usd"] for b in liqmap.build(p, "BTC", 100.0, band_pct=w)["bands"]) == 60


# ------------------------------------------------------------------ alerts
CARD = {"address": "0xabc0000000000000000000000000000000000def", "label": "skilled", "n_days": 200, "n_trades": 150}


def test_skilled_trader_alert_opened_closed_flipped():
    big = {"size": 10.0, "position_usd": 2e6, "leverage": 5.0}
    short = {"size": -10.0, "position_usd": 3e6}
    e = rules.skilled_move(CARD, "BTC", None, big, T0)
    assert e["kind"] == "skilled_trader" and "opened a $2.0M BTC long" in e["title"]
    assert "200 days" in e["reason"] and "150 finished trades" in e["reason"]     # carries its sample size
    assert "closed" in rules.skilled_move(CARD, "BTC", big, None, T0)["title"]
    assert "flipped" in rules.skilled_move(CARD, "BTC", big, short, T0)["title"]
    assert rules.skilled_move(CARD, "BTC", {"size": 1.0, "position_usd": 5e5}, None, T0) is None   # too small
    assert rules.skilled_move(CARD, "BTC", big, {"size": 5.0, "position_usd": 1e6}, T0) is None   # only trimmed


def test_alert_never_says_following_has_worked_unless_told():
    e = rules.skilled_move(CARD, "BTC", None, {"size": 10.0, "position_usd": 2e6}, T0)
    assert published.NOT_SHOWN in e["reason"] and "has worked" not in e["reason"]


def test_cluster_fires_once_and_rearms_after_clearing():
    r = rules.ClusterRule(cluster_usd=100, near_pct=1.5)
    near_ = [pos(usd=120, liq=99.0)]
    assert r.check(near_, "BTC", 100.0, T0)[0]["kind"] == "liq_cluster"
    assert r.check(near_, "BTC", 100.0, T0) == []                 # still there: no repeat
    assert r.check([pos(usd=60, liq=99.0)], "BTC", 100.0, T0) == []    # half-cleared: still waits
    assert r.check([pos(usd=40, liq=99.0)], "BTC", 100.0, T0) == []    # cleared: re-armed
    e = r.check(near_, "BTC", 100.0, T0 + pd.Timedelta(hours=1))
    assert len(e) == 1 and "tracked Hyperliquid accounts only" in e[0]["reason"]


def test_cluster_only_counts_the_side_that_would_be_hit():
    r = rules.ClusterRule(cluster_usd=100, near_pct=1.5)
    # a long whose liquidation is above the price, or far below it, is not near
    assert r.check([pos(usd=500, liq=101.0), pos(usd=500, liq=90.0)], "BTC", 100.0, T0) == []
    e = r.check([pos(size=-1, usd=500, liq=101.0)], "BTC", 100.0, T0)
    assert e[0]["detail"]["side"] == "short"


def test_watch_first_snapshot_is_only_the_starting_point():
    w = TraderWatch(cards=[CARD])
    row = {"coin": "BTC", "size": 10.0, "position_usd": 2e6}
    w.apply(CARD, CARD["address"], [row], T0)
    assert w.pending == []
    w.apply(CARD, CARD["address"], [], T0 + pd.Timedelta(minutes=2))
    assert len(w.pending) == 1 and "closed" in w.pending[0]["title"]
    assert w.positions() == []


def test_watch_only_alerts_on_skilled_accounts():
    other = {**CARD, "label": "unproven"}
    w = TraderWatch(cards=[other])
    w.apply(other, other["address"], [{"coin": "BTC", "size": 10.0, "position_usd": 2e6}], T0)
    w.apply(other, other["address"], [], T0)
    assert w.pending == []


def test_trades_merge_fills_of_the_same_minute():
    t = int(T0.timestamp() * 1000)
    fills = [{"time": t, "coin": "BTC", "side": "B", "dir": "Open Long", "sz": "1", "px": "100", "closedPnl": "0", "fee": "1"},
             {"time": t + 500, "coin": "BTC", "side": "B", "dir": "Open Long", "sz": "3", "px": "200", "closedPnl": "0", "fee": "1"},
             {"time": t + 500, "coin": "DOGE", "side": "B", "dir": "Open Long", "sz": "3", "px": "1"}]
    out = merge_trades(fills, "0xa")
    assert len(out) == 1 and out[0]["qty"] == 4 and out[0]["price"] == 175 and out[0]["closed_pnl"] == -2 and out[0]["n_fills"] == 2


# ------------------------------------------------------------------ what the page may say
def test_only_a_passed_test_may_say_it_has_worked():
    assert published.headline(None)["worked"] is False
    for status in ("not_run", "pending_forward", "failed"):
        h = published.headline({"status": status, "result": {"passed": True}})
        assert h["worked"] is False and h["sentence"] == "Past results; following them was not shown to pay."
    assert published.headline({"status": "passed", "result": {"passed": False}})["worked"] is False
    h = published.headline({"status": "passed", "result": {"passed": True}})
    assert h["worked"] is True and "has worked" in h["sentence"]


CARDS = {"summary": {"generated_at": "2026-11-02T00:00:00+00:00", "pool_size": 3, "downloaded": 3,
                     "accounts_with_perp_fills": 2, "counts": {"skilled": 1, "unproven": 0, "losing": 1, "no_trades": 1},
                     "fdr": 0.05, "limits": ["a limit"]},
         "cards": [{**CARD, "n_fills": 300, "p": 0.0001, "score": 4.2, "tested": True, "net_pnl": 5.0, "win_rate": 0.6,
                    "profit_factor": None, "coins": [{"coin": "BTC", "share": 1.0}], "display_name": None},
                   {"address": "0xlose", "label": "losing", "n_fills": 3, "tested": False, "p": 1.0, "score": 0.0,
                    "coins": []}]}


@pytest.fixture
def client(monkeypatch):
    caches = (published.scorecards, published.copy_test)
    for f in caches:
        f.cache_clear()
    monkeypatch.setattr(published, "SCORECARDS", published.HERE / "does-not-exist.gz")
    monkeypatch.setattr(published, "COPY_TEST", published.HERE / "does-not-exist.json")
    w = TraderWatch(cards=CARDS["cards"])
    monkeypatch.setattr(traders_api, "watch", w)
    monkeypatch.setattr(db, "_engine", None)
    yield TestClient(app), w
    for f in caches:
        f.cache_clear()


def test_endpoints_say_not_ready_before_the_files_exist(client):
    c, _ = client
    assert c.get("/traders/scorecards").status_code == 503
    assert c.get("/traders/skilled").status_code == 503
    assert c.get("/traders/liquidation-map/BTCUSDT").status_code == 503
    body = c.get("/traders/copy-test").json()
    assert body["status"] == "not_run" and body["worked"] is False and "not shown to pay" in body["headline"]


def test_scorecards_and_skilled_endpoints(client, monkeypatch):
    c, w = client
    monkeypatch.setattr(published, "scorecards", lambda: CARDS)
    all_ = c.get("/traders/scorecards").json()
    assert all_["total"] == 2 and all_["summary"]["accounts_with_perp_fills"] == 2
    one = c.get("/traders/scorecards", params={"label": "skilled"}).json()
    assert [x["address"] for x in one["cards"]] == [CARD["address"]] and one["cards"][0]["n_days"] == 200
    assert one["cards"][0]["profit_factor"] is None
    w.apply(CARD, CARD["address"], [{"coin": "BTC", "size": 2.0, "position_usd": 1000.0, "entry": 400.0, "liquidation": 300.0,
                                     "leverage": 5.0, "leverage_type": "cross", "unrealized_pnl": 10.0}], T0)
    s = c.get("/traders/skilled").json()
    assert s["n_skilled"] == 1 and s["bh_family"] == 2
    t = s["traders"][0]
    assert t["positions"][0]["side"] == "long" and t["positions"][0]["liquidation"] == 300.0 and t["read_at"]
    net = {n["coin"]: n for n in s["net"]}
    assert net["BTC"]["net_usd"] == 1000 and net["BTC"]["n_long"] == 1 and net["BTC"]["n_read"] == 1
    assert net["ETH"]["n_flat"] == 1 and net["ETH"]["net_usd"] == 0


def test_net_position_of_longs_and_shorts():
    p = [{"coin": "BTC", "address": "a", "size": 1.0, "position_usd": 100.0},
         {"coin": "BTC", "address": "b", "size": -1.0, "position_usd": 300.0},
         {"coin": "BTC", "address": "zzz", "size": 1.0, "position_usd": 9999.0}]     # not skilled
    n = traders_api.net_positions(["a", "b", "c"], p, {"a", "b", "c"}, ["BTC"])[0]
    assert n["net_usd"] == -200 and (n["n_long"], n["n_short"], n["n_flat"], n["n_read"]) == (1, 1, 1, 3)


def test_liquidation_map_endpoint_carries_its_label(client):
    c, w = client
    w.marks["BTC"] = 100.0
    w.apply(None, "0xa", [{"coin": "BTC", "size": 1.0, "position_usd": 500.0, "liquidation": 98.5}], T0)
    m = c.get("/traders/liquidation-map/BTCUSDT").json()
    assert m["bands"][0]["long_usd"] == 500 and m["accounts_read"] == 1 and m["n_positions"] == 1
    assert m["label"] == "tracked Hyperliquid accounts only, not the whole market"


def test_copy_test_endpoint_wording_follows_the_result(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(published, "copy_test", lambda: {"status": "failed", "plain": "no", "result": {"passed": False}})
    assert c.get("/traders/copy-test").json()["worked"] is False
    monkeypatch.setattr(published, "copy_test", lambda: {"status": "passed", "plain": "yes", "result": {"passed": True}})
    b = c.get("/traders/copy-test").json()
    assert b["worked"] is True and "has worked" in b["headline"]


# ------------------------------------------------------------------ alerts through the shared table and inbox
@pytest.fixture
def eng(monkeypatch):
    e = db.make_engine(os.environ.get("TEST_DATABASE_URL") or "sqlite://")
    with e.begin() as c:
        metadata.drop_all(c)
        c.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    db.upgrade(e)
    monkeypatch.setattr(db, "_engine", e)
    accounts.throttle._fails.clear()
    yield e
    e.dispose()


def add_event(eng, key, kind, sym="BTCUSDT", when=None):
    when = when or datetime.now(timezone.utc) + timedelta(seconds=1)
    with eng.begin() as x:
        x.execute(sa.insert(whale_events).values(key=key, ts=when, symbol=sym, kind=kind, address="", title=key,
                                                 reason="why", detail={}, created_at=when))


def test_each_alert_kind_only_gets_its_own_events(eng):
    c = TestClient(app, base_url="https://testserver")
    assert c.post("/auth/signup", json={"email": "a@example.test", "password": "correct-horse-battery"}).status_code == 201
    assert c.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "trader_watch"}).status_code == 201
    add_event(eng, "skilled", "skilled_trader")
    add_event(eng, "cluster", "liq_cluster")
    add_event(eng, "whale", "whale_move")
    add_event(eng, "other-coin", "liq_cluster", "ETHUSDT")
    view = alert_engine.MarketView(lambda s: None, lambda s: None, lambda s: None, lambda s, h: None, {})
    with eng.begin() as x:
        assert alert_engine.run_once(x, view, datetime.now(timezone.utc)) == 2
    with eng.begin() as x:
        assert alert_engine.run_once(x, view, datetime.now(timezone.utc)) == 0     # delivered once
    items = c.get("/me/inbox").json()["items"]
    assert {i["title"] for i in items} == {"skilled", "cluster"} and {i["kind"] for i in items} == {"trader_watch"}


def test_whale_watch_page_does_not_show_trader_events(eng):
    add_event(eng, "whale", "whale_move")
    add_event(eng, "skilled", "skilled_trader")
    c = TestClient(app)
    assert [e["title"] for e in c.get("/whales/events").json()["events"]] == ["whale"]
    assert [e["title"] for e in c.get("/traders/events").json()["events"]] == ["skilled"]
    assert c.get("/traders/events").json()["events"][0]["explorer_url"] is None


def test_download_resumes_and_takes_accounts_with_old_fills_first(tmp_path, monkeypatch):
    monkeypatch.setattr(fl.config, "DATA_DIR", tmp_path)
    r = fl.root()
    r.mkdir(parents=True)
    pd.DataFrame([{"address": "0xyoung", "whale_watch": True}, {"address": "0xold", "whale_watch": False}]).to_parquet(r / "pool.parquet")
    order = []

    class Fake:
        def __init__(self, *a):
            pass

        def post(self, body):
            if "endTime" in body:
                if body["user"] == "0xyoung":
                    return []
                order.append(body["user"])
                return [{"time": 100, "tid": 1, "coin": "BTC", "px": "1", "sz": "1", "side": "B"}]
            if body["user"] == "0xyoung":
                order.append(body["user"])
            return []

    monkeypatch.setattr(fl, "Info", Fake)
    commits = []
    d = fl.download(checkpoint=lambda: commits.append(1))
    assert order == ["0xold", "0xyoung"] and set(d["address"]) == {"0xold", "0xyoung"} and commits
    order.clear()
    assert len(fl.download()) == 2 and order == []      # a rerun has nothing left to do


def test_account_with_no_finished_trade_still_gets_a_card():
    tr = sc.track_record(frame([fill(0)]), T0 + pd.Timedelta(days=40), n_boot=50)   # opened and never closed
    assert tr["n_trades"] == 0 and tr["tested"] is False and tr["p"] == 1.0 and math.isnan(tr["win_rate"])


def test_restart_does_not_ask_again_about_accounts_already_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(fl.config, "DATA_DIR", tmp_path)
    r = fl.root()
    r.mkdir(parents=True)
    pd.DataFrame([{"address": "0xa", "whale_watch": True}]).to_parquet(r / "pool.parquet")
    (r / "no_fills_older_than_182d.txt").write_text("0xa\n")
    asked = []

    class Fake:
        def __init__(self, *a):
            pass

        def post(self, body):
            asked.append(body.get("endTime") is not None)
            return []

    monkeypatch.setattr(fl, "Info", Fake)
    fl.download()
    assert len(asked) == 2 and "0xa" in fl.load_checked(30)    # only the 30-day question and the final download


def test_trader_modal_jobs_have_no_schedule():
    from pathlib import Path

    text = (Path(__file__).parents[1] / "regimecast" / "jobs" / "modal_traders.py").read_text(encoding="utf-8")
    assert "modal.Cron" not in text and "modal.Period" not in text


def test_history_check_reads_times_with_and_without_fractions_of_a_second():
    rows = [{"address": "a", "n_fills": 5, "first_fill": "2025-01-30T19:33:57+00:00",
             "last_fill": "2026-10-01T00:00:00.123000+00:00", "downloaded_at": "2026-11-02T01:00:00.5+00:00"},
            {"address": "b", "n_fills": 5, "first_fill": "2025-02-01T00:00:00.250000+00:00",
             "last_fill": "2026-10-01T00:00:00+00:00", "downloaded_at": "2026-11-03T01:00:00+00:00"}]
    d = pd.DataFrame(rows)
    assert ct.history_gate(d, min_accounts=2)["historical_test"] is True
    assert ct.periods(d, ["a", "b"])["end"] == pd.Timestamp("2026-11-02", tz="UTC")
