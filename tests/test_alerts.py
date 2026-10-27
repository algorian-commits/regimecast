"""Alert engine (Phase 11, M12): each alert fires once, re-arms when its condition clears, and the
fired alerts reach the right user's inbox. Same database setup as tests/test_accounts.py."""
import os
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from regimecast import db
from regimecast.alerts import engine, rules
from regimecast.api import accounts, main
from regimecast.db.schema import alert_events, alerts, metadata, whale_events
from regimecast.risk import fhs_live

PW = "correct horse battery"   # test-only password
T0 = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def eng():
    e = db.make_engine(os.environ.get("TEST_DATABASE_URL") or "sqlite://")
    with e.begin() as c:
        metadata.drop_all(c)
        c.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    db.upgrade(e)
    yield e
    e.dispose()


@pytest.fixture(autouse=True)
def fresh(eng, monkeypatch):
    with eng.begin() as c:
        for t in reversed(metadata.sorted_tables):
            c.execute(t.delete())
    monkeypatch.setattr(db, "_engine", eng)
    accounts.throttle._fails.clear()
    yield


def user(email="alice@example.test"):
    c = TestClient(main.app, base_url="https://testserver")
    assert c.post("/auth/signup", json={"email": email, "password": PW}).status_code == 201
    return c


class Market:
    """A market whose values the test sets by hand."""

    def __init__(self):
        self.price = {"BTCUSDT": 70_000.0}
        self.stress = {"BTCUSDT": {"level": "low", "p_stress_next_hour": 0.03, "base_rate": 0.04}}
        self.regime = {"BTCUSDT": {"regime": "calm", "p_stressed": 0.02}}
        self.sigma = 0.02   # std of the 24 h log return

    def view(self):
        return engine.MarketView(self.price.get, self.stress.get, self.regime.get, lambda s, h: self.sigma,
                                 fhs_live.load())


def run(eng, m: Market, minutes=0):
    with eng.begin() as c:
        return engine.run_once(c, m.view(), T0 + timedelta(minutes=minutes))


# ------------------------------------------------------------------ the state machine
def test_step_fires_once_then_rearms_after_clearing():
    armed, fired = True, []
    for state in [False, True, True, None, True, False, None, True]:
        f, armed = rules.step(armed, state, "stress_high")
        fired.append(f)
    assert fired == [False, True, False, False, False, False, False, True]


def test_first_check_price_waits_for_a_crossing_state_fires_now():
    assert rules.step(None, True, "price_above") == (False, False)   # already above: wait for a real crossing
    assert rules.step(None, False, "price_above") == (False, True)
    assert rules.step(None, True, "regime_stressed") == (True, False)  # already stressed: tell the user now
    assert rules.step(None, None, "stress_high") == (False, None)     # no data: nothing decided yet


def test_price_band_stops_flapping_around_the_level():
    lvl = 70_000
    assert rules.price_check("price_above", lvl, 70_000, "BTCUSDT").state is True
    assert rules.price_check("price_above", lvl, 69_960, "BTCUSDT").state is None   # inside the 0.1% band
    assert rules.price_check("price_above", lvl, 69_900, "BTCUSDT").state is False
    assert rules.price_check("price_below", lvl, 70_050, "BTCUSDT").state is None
    assert rules.price_check("price_below", lvl, 70_100, "BTCUSDT").state is False
    assert rules.price_check("price_below", lvl, None, "BTCUSDT").state is None


def test_position_risk_grows_as_price_nears_liquidation():
    trade = {"symbol": "BTCUSDT", "side": "long", "entry": 70_000, "leverage": 10, "margin": 1000}
    fhs = fhs_live.load()
    p_far, lp, _ = rules.position_risk(trade, 72_000, 0.02, fhs)
    p_near, _, pct = rules.position_risk(trade, 64_000, 0.02, fhs)
    assert 60_000 < lp < 64_000 and 0 < pct < 5
    assert p_near > p_far
    assert rules.position_risk(trade, lp * 0.99, 0.02, fhs)[0] == 1.0   # past the liquidation price


# ------------------------------------------------------------------ the engine, end to end
def test_price_alert_fires_once_and_again_after_clearing(eng):
    c = user()
    assert c.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "price_above", "threshold": 71_000}).status_code == 201
    m = Market()
    assert run(eng, m, 0) == 0                 # first check, below the level: armed
    m.price["BTCUSDT"] = 71_200
    assert run(eng, m, 1) == 1                 # crossed: fires
    assert run(eng, m, 2) == 0                 # still above: silent
    m.price["BTCUSDT"] = 70_960
    assert run(eng, m, 3) == 0                 # inside the band: still waiting to clear
    m.price["BTCUSDT"] = 70_500
    assert run(eng, m, 4) == 0                 # cleared: re-armed
    m.price["BTCUSDT"] = 71_050
    assert run(eng, m, 5) == 1                 # crossed again: fires again
    box = c.get("/me/inbox").json()
    assert box["unread"] == 2 and [i["title"] for i in box["items"]] == ["BTC crossed above 71,000"] * 2
    assert "71,050" in box["items"][0]["message"]
    a = c.get("/me/alerts").json()[0]
    assert a["fired_count"] == 2 and a["armed"] is False and a["last_value"] == 71_050


def test_alert_created_past_its_level_waits_for_a_real_crossing(eng):
    c = user()
    c.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "price_above", "threshold": 60_000})
    m = Market()
    assert run(eng, m, 0) == 0 and run(eng, m, 1) == 0
    m.price["BTCUSDT"] = 59_000
    run(eng, m, 2)
    m.price["BTCUSDT"] = 60_100
    assert run(eng, m, 3) == 1


def test_stress_and_regime_alerts(eng):
    c = user()
    for k in ("stress_elevated", "stress_high", "regime_stressed"):
        assert c.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": k}).status_code == 201
    m = Market()
    assert run(eng, m, 0) == 0
    m.stress["BTCUSDT"] = {"level": "elevated", "p_stress_next_hour": 0.15, "base_rate": 0.04}
    assert run(eng, m, 10) == 1               # elevated: only the "elevated or high" alert
    m.stress["BTCUSDT"] = {"level": "high", "p_stress_next_hour": 0.42, "base_rate": 0.04}
    m.regime["BTCUSDT"] = {"regime": "stressed", "p_stressed": 0.9}
    assert run(eng, m, 20) == 2               # high + regime; "elevated or high" is still on, so silent
    assert run(eng, m, 30) == 0
    titles = [i["title"] for i in c.get("/me/inbox").json()["items"]]
    assert titles == ["BTC regime turned stressed", "BTC stress is high", "BTC stress is elevated"]   # newest first
    msg = c.get("/me/inbox").json()["items"][-1]["message"]
    assert "15% chance" in msg and "normally 4%" in msg and "not a direction" in msg


def test_position_alert_uses_the_saved_check_and_the_profile_limit(eng):
    c = user()
    c.patch("/me/profile", json={"risk_limit": 0.02})
    trade = {"symbol": "BTCUSDT", "side": "long", "entry": 70_000, "leverage": 10, "margin": 1000, "horizon_hours": 24}
    chk = c.post("/me/checks", json={"symbol": "BTCUSDT", "side": "long", "trade": trade,
                                     "result": {"p_liquidation": 0.001}}).json()
    r = c.post("/me/alerts", json={"symbol": "ETHUSDT", "kind": "position_risk", "check_id": chk["id"]})
    assert r.status_code == 201 and r.json()["symbol"] == "BTCUSDT"   # the position's coin
    m = Market()
    assert run(eng, m, 0) == 0                # far from liquidation
    m.price["BTCUSDT"], m.sigma = 64_500, 0.04
    assert run(eng, m, 1) == 1
    item = c.get("/me/inbox").json()["items"][0]
    assert item["kind"] == "position_risk" and item["value"] > 0.02
    assert "above your limit of 2.0%" in item["message"] and "Liquidation at" in item["message"]
    # deleting the saved check removes its alert
    assert c.delete(f"/me/checks/{chk['id']}").status_code == 200
    assert c.get("/me/alerts").json() == []


def test_position_alert_needs_your_own_saved_check(eng):
    a = user()
    chk = a.post("/me/checks", json={"symbol": "BTCUSDT", "side": "long",
                                     "trade": {"symbol": "BTCUSDT", "side": "long", "entry": 1, "leverage": 2,
                                               "margin": 1}, "result": {}}).json()
    b = user("bob@example.test")
    assert b.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "position_risk",
                                      "check_id": chk["id"]}).status_code == 404
    assert b.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "position_risk"}).status_code == 422


def test_whale_watch_alert_delivers_each_event_once(eng):
    c = user()
    c.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "whale_watch"})
    later = datetime.now(timezone.utc) + timedelta(seconds=1)

    def add(key, sym="BTCUSDT"):
        with eng.begin() as x:
            x.execute(sa.insert(whale_events).values(key=key, ts=later, symbol=sym, kind="whale_move", address="0xabc",
                                                     title=f"Whale move {key}", reason="why", detail={},
                                                     created_at=later))
    add("a")
    add("b", "ETHUSDT")
    m = Market()
    assert run(eng, m, 0) == 1 and run(eng, m, 1) == 0
    add("c")
    assert run(eng, m, 2) == 1
    assert [i["title"] for i in c.get("/me/inbox").json()["items"]] == ["Whale move c", "Whale move a"]


def test_inbox_is_private_and_can_be_marked_read(eng):
    a, b = user(), user("bob@example.test")
    a.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "regime_stressed"})
    m = Market()
    m.regime["BTCUSDT"] = {"regime": "stressed", "p_stressed": 0.8}
    assert run(eng, m, 0) == 1
    assert b.get("/me/inbox").json() == {"items": [], "unread": 0}
    box = a.get("/me/inbox").json()
    assert box["unread"] == 1
    b.post("/me/inbox/read", json={"ids": [box["items"][0]["id"]]})   # someone else's id: no effect
    assert a.get("/me/inbox").json()["unread"] == 1
    a.post("/me/inbox/read", json={})
    assert a.get("/me/inbox").json()["unread"] == 0
    assert TestClient(main.app).get("/me/inbox").status_code == 401


def test_one_failing_source_does_not_stop_the_others(eng):
    c = user()
    c.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "price_below", "threshold": 69_000})
    c.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "regime_stressed"})

    def boom(_):
        raise RuntimeError("Binance down")
    v = engine.MarketView(boom, lambda s: None, lambda s: {"regime": "stressed", "p_stressed": 0.7},
                          lambda s, h: None, {})
    with eng.begin() as x:
        assert engine.run_once(x, v, T0) == 1
    assert c.get("/me/inbox").json()["items"][0]["kind"] == "regime_stressed"


def test_engine_status_endpoint():
    r = TestClient(main.app).get("/alerts/engine").json()
    assert r["every_seconds"] == 60 and "running" in r
