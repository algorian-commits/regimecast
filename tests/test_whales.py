"""Whale Watch rules (M12) on a recorded sample of the recorder's Hyperliquid data.

tests/data/hl_sample.parquet: the recorder's rows from 19-20 Oct 2026 (the first watchlist,
the positions of 10 accounts and their big orders), kept small. Public on-chain data.
"""
from pathlib import Path

import pandas as pd
import pytest
import sqlalchemy as sa

from regimecast import db
from regimecast.db.schema import metadata, whale_events, whale_seen
from regimecast.whales import live, rules

SAMPLE = Path(__file__).with_name("data") / "hl_sample.parquet"
ORDER = {"hl_watchlist": 0, "hl_orders": 1, "hl_positions": 2}


def rows():
    df = pd.read_parquet(SAMPLE)
    df["k"] = df["stream"].map(ORDER)
    df = df.sort_values(["event_ts", "k"], kind="stable")
    # one table for three streams: drop the other streams' empty columns from each row
    return [{k: v for k, v in r.items() if not (isinstance(v, float) and pd.isna(v))} for r in df.to_dict("records")]


def replay(r: rules.WhaleRules, until=None):
    out = []
    for row in rows():
        if until is not None and row["event_ts"] > until:
            break
        if row["stream"] == "hl_watchlist":
            r.watchlist(row)
        elif row["stream"] == "hl_orders":
            r.order(row)
        else:
            out += r.position(row)
    return out


@pytest.fixture(scope="module")
def events():
    return replay(rules.WhaleRules())


def test_recorded_sample_gives_the_expected_events(events):
    kinds = sorted((e["kind"], e["address"][:10]) for e in events)
    assert kinds == [("fresh_wallet", "0x3ff54fd2"), ("fresh_wallet", "0x4b62652d"), ("fresh_wallet", "0x598f9efb"),
                     ("whale_move", "0x3bcae23e"), ("whale_move", "0x3bcae23e"),
                     ("whale_move", "0x77eeda19"), ("whale_move", "0x77eeda19")]
    assert len({e["key"] for e in events}) == len(events)


def test_whale_move_reasons_say_what_changed(events):
    moves = [e for e in events if e["kind"] == "whale_move"]
    opened, closed = moves[0], moves[1]
    assert "opened a $6.2M BTC short" in opened["title"]
    assert "closed its" in closed["title"] and "BTC short" in closed["title"]
    assert "top Hyperliquid account" in opened["reason"] and "size +0 -> -" in opened["reason"]
    assert any("flipped its BTC position" in m["title"] for m in moves)
    for m in moves:
        assert abs(m["detail"]["change_usd"]) >= rules.MOVE_USD


def test_no_events_from_the_first_watchlist_or_quiet_accounts(events):
    # the 200 accounts of the first watchlist are the starting point, never "fresh"
    first = {r["address"].lower() for r in rows() if r["stream"] == "hl_watchlist"}
    assert not [e for e in events if e["kind"] == "fresh_wallet" and e["address"] in first]
    # five tracked accounts in the sample never changed by $5M: no events for them
    assert len({e["address"] for e in events}) == 5


def test_fresh_wallet_fires_once_per_account_with_its_order(events):
    fresh = [e for e in events if e["kind"] == "fresh_wallet"]
    assert len({e["address"] for e in fresh}) == len(fresh)
    assert all(e["detail"]["position_usd"] >= rules.FRESH_USD for e in fresh)
    assert all("had not seen before" in e["reason"] for e in fresh)
    assert any("spotted placing a" in e["reason"] for e in fresh)


def test_a_known_account_is_never_fresh_again():
    seen = {(r.get("address") or r["taker"]).lower() for r in rows()}
    assert not [e for e in replay(rules.WhaleRules(known=seen)) if e["kind"] == "fresh_wallet"]


def test_rules_only_use_rows_up_to_now(events):
    # cutting the feed at any time gives exactly the events up to that time (nothing revised later)
    for cut in (pd.Timestamp("2026-10-20 00:10", tz="UTC"), pd.Timestamp("2026-10-20 14:40", tz="UTC")):
        part = replay(rules.WhaleRules(), until=cut)
        assert [e["key"] for e in part] == [e["key"] for e in events if e["ts"] <= cut]


def _timing_rules():
    r = rules.WhaleRules(known={"0xseen"})
    r.watchlist({"address": "0xbig", "reason": "largest_account", "event_ts": pd.Timestamp("2026-10-20 08:00", tz="UTC")})
    snap = lambda ts, size: {"event_ts": pd.Timestamp(ts, tz="UTC"), "address": "0xbig", "coin": "BTC",
                             "source": "watchlist", "size": size, "position_usd": abs(size) * 100_000.0}
    assert r.position(snap("2026-10-20 09:00", 0.0)) == []
    assert [e["kind"] for e in r.position(snap("2026-10-20 10:00", -120.0))] == ["whale_move"]   # $12M short
    return r, snap


def test_suspicious_timing_is_a_pattern_not_an_accusation():
    r, _ = _timing_rules()
    assert r.stress("BTC", "low", "2026-10-20 11:00Z") == []
    ev = r.stress("BTC", "elevated", "2026-10-20 12:00Z", 0.18)
    assert [e["kind"] for e in ev] == ["suspicious_timing"]
    e = ev[0]
    assert e["title"].startswith("Pattern:") and "2.0 h before a stress flag" in e["title"]
    assert "not evidence of wrongdoing" in e["reason"] and "18% chance" in e["reason"]
    assert e["detail"]["lead_hours"] == 2.0 and e["detail"]["side"] == "short"
    # staying elevated, or another coin, gives nothing new
    assert r.stress("BTC", "high", "2026-10-20 13:00Z") == []
    assert r.stress("ETH", "low", "2026-10-20 11:00Z") == [] and r.stress("ETH", "high", "2026-10-20 12:00Z") == []


def test_suspicious_timing_needs_the_open_before_the_flag_and_within_the_window():
    # the open is at 10:00
    for flag_at, n in (("09:30", 0),     # flag before the open: no hindsight
                       ("17:00", 0),     # 7 h later: outside the 6 h window
                       ("15:30", 1)):    # 5.5 h later: inside
        r, _ = _timing_rules()
        r.stress("BTC", "low", "2026-10-20 09:00Z")
        assert len(r.stress("BTC", "high", f"2026-10-20 {flag_at}Z")) == n, flag_at


def test_first_stress_reading_is_not_a_change():
    r, _ = _timing_rules()
    assert r.stress("BTC", "high", "2026-10-20 11:00Z") == []


def test_events_are_stored_once():
    eng = db.make_engine("sqlite://")
    db.upgrade(eng)
    r = rules.WhaleRules()
    evs = replay(r)
    now = pd.Timestamp("2026-10-27", tz="UTC").to_pydatetime()
    with eng.begin() as c:
        assert live.store_events(c, evs, now) == len(evs)
        assert live.store_events(c, evs, now) == 0
        live.store_seen(c, r.newly_known)
        live.store_seen(c, r.newly_known)
        assert c.execute(sa.select(sa.func.count()).select_from(whale_events)).scalar_one() == len(evs)
        n_seen = c.execute(sa.select(sa.func.count()).select_from(whale_seen)).scalar_one()
    assert n_seen == len({a for a, _ in r.newly_known})
    metadata.drop_all(eng)
