"""Position Guardian and Telegram (Phase 13C): the security tests the plan asks for (encryption round-trip, keys
never in logs or API responses, trading keys refused), the alert rules on a scripted price path, and Telegram
linking and delivery through a fake client. Same database setup as tests/test_alerts.py."""
import json
import logging
import os
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from regimecast import db
from regimecast.alerts import engine
from regimecast.api import accounts, main
from regimecast.db.schema import (alert_events, alerts, guardian_positions, guardian_sources, metadata,
                                  notify_log, telegram_codes, telegram_links)
from regimecast.guardian import binance as bn, crypto, hyperliquid as hl, loop, risk
from regimecast.notify import dispatch, linking, redact, telegram as tg
from regimecast.risk import fhs_live

PW = "correct horse battery"                               # test-only password
KEY = "TESTKEYaaaaBBBBccccDDDD1111222233334444"             # made-up Binance-style key and secret (not real)
SECRET = "TESTSECRETzzzzYYYYxxxxWWWW99998888777766"
WALLET = "0x" + "ab" * 20
T0 = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def eng():
    e = db.make_engine(os.environ.get("TEST_DATABASE_URL") or "sqlite://")
    with e.begin() as c:
        metadata.drop_all(c)
        c.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    db.upgrade(e)
    yield e
    e.dispose()


class Fakes:
    def __init__(self):
        self.hl = hl.FakeHyperliquid()
        self.binance_keys: dict[tuple[str, str], bn.FakeBinance] = {}

    def binance(self, key, secret):
        return self.binance_keys[(key, secret)]


@pytest.fixture(autouse=True)
def fresh(eng, monkeypatch):
    with eng.begin() as c:
        for t in reversed(metadata.sorted_tables):
            if not t.name.startswith("track_"):   # the track tables refuse DELETE (and these tests write none)
                c.execute(t.delete())
    monkeypatch.setattr(db, "_engine", eng)
    monkeypatch.setenv("GUARDIAN_SECRET", "test-only-guardian-secret-" + "x" * 20)
    accounts.throttle._fails.clear()
    fakes = Fakes()
    monkeypatch.setattr(loop.guardian, "clients", loop.Clients(hyperliquid=fakes.hl, binance=fakes.binance))
    bot = tg.FakeTelegram()
    monkeypatch.setattr(linking.poller, "offset", None)   # each fake bot numbers its updates from 1
    tg.set_client(bot)
    yield fakes, bot
    tg.set_client(None)


def user(email="alice@example.test"):
    c = TestClient(main.app, base_url="https://testserver")
    assert c.post("/auth/signup", json={"email": email, "password": PW}).status_code == 201
    return c


def uid_of(eng, email="alice@example.test"):
    with eng.begin() as c:
        return c.execute(sa.text("SELECT id FROM users WHERE email = :e"), {"e": email}).scalar_one()


class Market:
    def __init__(self, price=70_000.0):
        self.price = {"BTCUSDT": price}
        self.stress = {"BTCUSDT": {"level": "low", "p_stress_next_hour": 0.03, "base_rate": 0.04}}
        self.regime = {"BTCUSDT": {"regime": "calm", "p_stressed": 0.02}}
        self.sigma24 = 0.02

    def view(self):
        return engine.MarketView(self.price.get, self.stress.get, self.regime.get,
                                 lambda s, h: self.sigma24 * (1 if h == 24 else (1 / 24) ** 0.5), fhs_live.load())


def run(eng, m: Market, fakes, minutes=0):
    with eng.begin() as c:
        return loop.run_once(c, m.view(), loop.guardian.clients, T0 + timedelta(minutes=minutes))


def events(eng, kind_prefix="guardian"):
    with eng.begin() as c:
        rows = c.execute(sa.select(alert_events).order_by(alert_events.c.id)).mappings().all()
    return [r for r in rows if r["kind"].startswith(kind_prefix)]


# ------------------------------------------------------------------ encryption
def test_encryption_round_trip_and_wrong_secret(monkeypatch):
    token = crypto.encrypt(SECRET)
    assert SECRET not in token and crypto.decrypt(token) == SECRET
    assert crypto.encrypt(SECRET) != token                     # fresh IV every time
    monkeypatch.setenv("GUARDIAN_SECRET", "another-server-secret-" + "y" * 20)
    with pytest.raises(crypto.NotConfigured):
        crypto.decrypt(token)
    monkeypatch.setenv("GUARDIAN_SECRET", "")
    assert not crypto.enabled()
    with pytest.raises(crypto.NotConfigured):
        crypto.encrypt("x")


# ------------------------------------------------------------------ permission check
@pytest.mark.parametrize("flags,ok", [
    ({"enableReading": True}, True),
    ({"enableReading": True, "enableFutures": True}, False),
    ({"enableReading": True, "enableSpotAndMarginTrading": True}, False),
    ({"enableReading": True, "enableWithdrawals": True}, False),
    ({"enableReading": True, "enableInternalTransfer": True}, False),
    ({"enableReading": False}, False),
])
def test_permission_check_live_binance(flags, ok):
    p = bn.check_permissions(bn.FakeBinance(restrictions=flags))
    assert p.read_only is ok and (p.refusal() is None) is ok


def test_permission_check_testnet_probe():
    assert bn.check_permissions(bn.FakeBinance(sapi="", trade_ok=False)).read_only
    assert not bn.check_permissions(bn.FakeBinance(sapi="", trade_ok=True)).read_only


def test_trading_key_is_refused_and_nothing_is_stored(fresh, eng):
    fakes, _ = fresh
    fakes.binance_keys[(KEY, SECRET)] = bn.FakeBinance(restrictions={"enableReading": True, "enableFutures": True})
    c = user()
    r = c.post("/me/guardian/sources/binance", json={"api_key": KEY, "api_secret": SECRET})
    assert r.status_code == 400 and "can trade" in r.json()["detail"]
    fakes.binance_keys[(KEY, SECRET)] = bn.FakeBinance(restrictions={"enableReading": True, "enableWithdrawals": True})
    assert c.post("/me/guardian/sources/binance", json={"api_key": KEY, "api_secret": SECRET}).status_code == 400
    with eng.begin() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(guardian_sources)).scalar_one() == 0


def test_without_guardian_secret_binance_keys_are_off(fresh, monkeypatch):
    monkeypatch.setenv("GUARDIAN_SECRET", "")
    r = user().post("/me/guardian/sources/binance", json={"api_key": KEY, "api_secret": SECRET})
    assert r.status_code == 503 and r.json()["status"] == "not_ready"


# ------------------------------------------------------------------ keys never in logs or responses
def test_keys_never_in_logs_or_api_responses(fresh, eng, caplog):
    fakes, _ = fresh
    caplog.set_level(logging.DEBUG)
    fakes.binance_keys[(KEY, SECRET)] = bn.FakeBinance(positions=[
        {"symbol": "BTCUSDT", "positionAmt": "0.5", "entryPrice": "70000", "markPrice": "69000",
         "liquidationPrice": "63500", "leverage": "10", "marginType": "isolated", "positionSide": "BOTH"}])
    c = user()
    seen = []
    r = c.post("/me/guardian/sources/binance", json={"api_key": KEY, "api_secret": SECRET, "label": "main"})
    assert r.status_code == 201, r.text
    seen.append(r.text)
    sid = r.json()["id"]
    assert r.json()["positions"] == 1 and r.json()["permissions"]["read_only" if False else "reading"] is True
    for path in ("/me/guardian", "/me/telegram", "/guardian/status", "/me/inbox"):
        seen.append(c.get(path).text)
    run(eng, Market(69_000), fakes)
    seen.append(c.get("/me/guardian").text)
    # malformed requests must not echo the key back either (FastAPI's 422 normally repeats the input)
    for bad in ([KEY, SECRET], KEY, {"api_key": KEY}, {"api_key": KEY, "api_secret": 5},
                {"api_key": [KEY], "api_secret": SECRET}, {"api_key": KEY + "!", "api_secret": SECRET}):
        rr = c.post("/me/guardian/sources/binance", json=bad)
        assert rr.status_code in (400, 422)
        seen.append(rr.text)
    assert c.delete(f"/me/guardian/sources/{sid}").status_code == 200
    text = "\n".join(seen) + "\n".join(rec.getMessage() for rec in caplog.records)
    for secret in (KEY, SECRET):
        assert secret not in text
    with eng.begin() as conn:   # deleting the source deleted the key at once
        assert conn.execute(sa.select(sa.func.count()).select_from(guardian_sources)).scalar_one() == 0


def test_stored_key_is_encrypted(fresh, eng):
    fakes, _ = fresh
    fakes.binance_keys[(KEY, SECRET)] = bn.FakeBinance()
    assert user().post("/me/guardian/sources/binance", json={"api_key": KEY, "api_secret": SECRET}).status_code == 201
    with eng.begin() as conn:
        row = conn.execute(sa.select(guardian_sources)).mappings().one()
        dump = json.dumps({k: str(v) for k, v in row.items()})
    assert KEY not in dump and SECRET not in dump
    assert crypto.decrypt(row["key_enc"]) == KEY and crypto.decrypt(row["secret_enc"]) == SECRET


def test_redaction_of_tokens_and_signatures(caplog):
    caplog.set_level(logging.INFO)
    logging.getLogger("httpx").info('HTTP Request: POST %s "HTTP/1.1 200 OK"',
                                    "https://api.telegram.org/bot123456:AAH-secret_token/sendMessage")
    logging.getLogger("httpx").info("HTTP Request: GET https://fapi.binance.com/fapi/v2/positionRisk?timestamp=1&signature=deadbeef")
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "AAH-secret_token" not in msgs and "deadbeef" not in msgs and "<redacted>" in msgs
    assert redact.clean("no secrets here") == "no secrets here"


def test_key_that_gains_trading_is_deleted(fresh, eng):
    fakes, _ = fresh
    fake = bn.FakeBinance()
    fakes.binance_keys[(KEY, SECRET)] = fake
    c = user()
    assert c.post("/me/guardian/sources/binance", json={"api_key": KEY, "api_secret": SECRET}).status_code == 201
    with eng.begin() as conn:
        conn.execute(sa.update(guardian_sources).values(permissions_checked_at=T0))
    fake._r = {"enableReading": True, "enableFutures": True}   # the user switched trading on at Binance
    run(eng, Market(), fakes, minutes=10)                      # not due yet: kept
    assert len(c.get("/me/guardian").json()["sources"]) == 1
    run(eng, Market(), fakes, minutes=7 * 60)                   # re-checked: refused, deleted, user told
    assert c.get("/me/guardian").json()["sources"] == []
    assert [e["kind"] for e in events(eng)] == ["guardian_key"]


# ------------------------------------------------------------------ sources
def hl_state(size=1.0, liq="64000", coin="BTC", value=None):
    return {"assetPositions": [{"position": {"coin": coin, "szi": str(size), "entryPx": "70000",
                                             "positionValue": str(value or abs(size) * 69_000), "liquidationPx": liq,
                                             "leverage": {"type": "cross", "value": 10}}}]}


def test_hyperliquid_address_positions_follow_the_exchange(fresh, eng):
    fakes, _ = fresh
    c = user()
    assert c.post("/me/guardian/sources/hyperliquid", json={"address": "not an address"}).status_code == 400
    fakes.hl.states[WALLET] = hl_state()
    r = c.post("/me/guardian/sources/hyperliquid", json={"address": WALLET.upper().replace("0X", "0x")})
    assert r.status_code == 201 and r.json()["positions"] == 1 and r.json()["address"] == WALLET
    assert c.post("/me/guardian/sources/hyperliquid", json={"address": WALLET}).status_code == 409
    p = c.get("/me/guardian").json()["positions"][0]
    assert (p["symbol"], p["side"], p["margin_mode"], p["manual"]) == ("BTCUSDT", "long", "cross", False)
    fakes.hl.states[WALLET] = {"assetPositions": [*hl_state(-2.0, "75000")["assetPositions"],
                                                  *hl_state(5.0, None, coin="HYPE", value=200)["assetPositions"]]}
    run(eng, Market(), fakes)
    ps = {(p["symbol"], p["side"]): p for p in c.get("/me/guardian").json()["positions"]}
    assert set(ps) == {("BTCUSDT", "short"), ("HYPE", "long")}   # the long was closed, a short opened
    assert ps[("HYPE", "long")]["covered"] is False and ps[("BTCUSDT", "short")]["covered"] is True
    assert c.delete(f"/me/guardian/positions/{ps[('HYPE', 'long')]['id']}").status_code == 409
    fakes.hl.states.pop(WALLET)
    run(eng, Market(), fakes, minutes=1)
    s = c.get("/me/guardian").json()["sources"][0]
    assert s["status"] == "error" and s["positions"] == 2   # an outage keeps the last known positions


def test_users_cannot_touch_each_others_guardian(fresh):
    a, b = user(), user("bob@example.test")
    pid = a.post("/me/guardian/positions", json={"symbol": "BTCUSDT", "side": "long", "entry": 70000, "leverage": 5,
                                                 "margin": 1000}).json()["id"]
    assert b.get("/me/guardian").json()["positions"] == []
    assert b.patch(f"/me/guardian/positions/{pid}", json={"muted": True}).status_code == 404
    assert b.delete(f"/me/guardian/positions/{pid}").status_code == 404
    assert a.delete(f"/me/guardian/positions/{pid}").status_code == 200


# ------------------------------------------------------------------ rules on a scripted price path
def test_rules_fire_once_and_rearm_on_a_scripted_path(fresh, eng):
    fakes, _ = fresh
    c = user()
    r = c.post("/me/guardian/positions", json={"symbol": "BTCUSDT", "side": "long", "entry": 70000, "leverage": 5,
                                               "margin": 1000})
    assert r.status_code == 201, r.text
    m, fired = Market(), []
    path = [70_000, 70_000, 60_000, 60_000, 57_000, 56_600, 56_600, 70_000, 60_000]
    for i, price in enumerate(path):
        m.price["BTCUSDT"] = price
        n0 = len(events(eng))
        run(eng, m, fakes, minutes=i)
        fired.append([e["kind"].removeprefix("guardian_") for e in events(eng)[n0:]])
    assert fired == [[], [], ["risk"], [], [], ["near"], [], [], ["risk"]]
    ev = events(eng)[0]
    assert "liquidation risk above your limit" in ev["title"] and "Safer version: add $" in ev["message"]
    s = ev["detail"]["safer"]
    assert s["status"] == "fixed" and s["p_liquidation"] <= 0.01 and s["leverage_safe"] < s["leverage_now"]
    assert s["add_margin_usdt"] > 0 and s["reduce_to_usdt"] < 5000


def test_muted_positions_send_nothing(fresh, eng):
    fakes, _ = fresh
    c = user()
    pid = c.post("/me/guardian/positions", json={"symbol": "BTCUSDT", "side": "long", "entry": 70000, "leverage": 50,
                                                 "margin": 100}).json()["id"]
    assert c.patch(f"/me/guardian/positions/{pid}", json={"muted": True}).json()["muted"] is True
    run(eng, Market(69_000), fakes)
    assert events(eng) == []
    p = c.get("/me/guardian").json()["positions"][0]
    assert p["p_liquidation"] > 0.01 and p["states"]["guardian_risk"] is True   # still shown with its risk
    c.patch(f"/me/guardian/positions/{pid}", json={"muted": False})
    run(eng, Market(69_000), fakes, minutes=1)
    assert {e["kind"] for e in events(eng)} >= {"guardian_risk"}


def test_stress_high_alerts_leveraged_longs_only(fresh, eng):
    fakes, _ = fresh
    c = user()
    for side in ("long", "short"):
        c.post("/me/guardian/positions", json={"symbol": "BTCUSDT", "side": side, "entry": 70000, "leverage": 3,
                                               "margin": 1000})
    m = Market()
    m.stress["BTCUSDT"] = {"level": "high", "p_stress_next_hour": 0.4, "base_rate": 0.04}
    run(eng, m, fakes)
    evs = events(eng)
    assert [e["kind"] for e in evs] == ["guardian_stress"] and "long" in evs[0]["title"]
    assert evs[0]["detail"]["safer"]["limit"] == pytest.approx(0.005)   # half the 1% limit on stress days


def test_own_limit_forces_an_alert(fresh, eng):
    """How the pass-mark check forces a real position over the limit: give it a tiny limit of its own."""
    fakes, _ = fresh
    c = user()
    pid = c.post("/me/guardian/positions", json={"symbol": "BTCUSDT", "side": "long", "entry": 70000, "leverage": 10,
                                                 "margin": 500}).json()["id"]
    run(eng, Market(), fakes)
    assert events(eng) == []
    assert c.patch(f"/me/guardian/positions/{pid}", json={"risk_limit": 0.0001}).json()["own_limit"] is True
    run(eng, Market(), fakes, minutes=1)
    assert [e["kind"] for e in events(eng)] == ["guardian_risk"]


def test_safer_version_matches_the_liquidation_price():
    lev = risk.leverage_from_now("BTCUSDT", "long", 70_000, 0.1, 63_300)
    from regimecast.risk import liquidation as liq
    lp = liq.liquidation_price(liq.Position(symbol="BTCUSDT", side="long", entry=70_000, qty=0.1, leverage=lev))
    assert lp == pytest.approx(63_300, rel=1e-6)
    lev_s = risk.leverage_from_now("BTCUSDT", "short", 70_000, 0.1, 77_000)
    lp_s = liq.liquidation_price(liq.Position(symbol="BTCUSDT", side="short", entry=70_000, qty=0.1, leverage=lev_s))
    assert lp_s == pytest.approx(77_000, rel=1e-6)


# ------------------------------------------------------------------ Telegram
def link_alice(c, eng, bot, chat_id=555):
    url = c.post("/me/telegram/link").json()["url"]
    assert url.startswith("https://t.me/regimecast_test_bot?start=")
    code = url.split("start=", 1)[1]
    bot.user_sends(chat_id, f"/start {code}")
    linking.poller.poll_once(bot, eng, timeout=0)
    return code


def test_linking_with_a_one_time_code(fresh, eng):
    _, bot = fresh
    c = user()
    assert c.get("/me/telegram").json() == {"configured": True, "linked": False, "username": None, "linked_at": None,
                                            "enabled": False, "kinds": None}
    code = link_alice(c, eng, bot)
    assert c.get("/me/telegram").json()["linked"] is True and "Connected" in bot.sent[-1][1]
    with eng.begin() as conn:   # the code is stored only as a hash, and is gone once used
        assert conn.execute(sa.select(sa.func.count()).select_from(telegram_codes)).scalar_one() == 0
    b = user("bob@example.test")
    bot.user_sends(777, f"/start {code}")                     # reused: refused
    linking.poller.poll_once(bot, eng, timeout=0)
    assert b.get("/me/telegram").json()["linked"] is False and "expired" in bot.sent[-1][1]
    bot.user_sends(777, f"/start {code}", chat_type="group")  # groups are ignored
    n = len(bot.sent)
    linking.poller.poll_once(bot, eng, timeout=0)
    assert len(bot.sent) == n
    bot.user_sends(555, "/unlink")
    linking.poller.poll_once(bot, eng, timeout=0)
    assert c.get("/me/telegram").json()["linked"] is False


def test_expired_code_is_refused(fresh, eng):
    _, bot = fresh
    c = user()
    with eng.begin() as conn:
        code, _ = linking.new_code(conn, uid_of(eng), T0 - timedelta(hours=1))
    bot.user_sends(9, f"/start {code}")
    linking.poller.poll_once(bot, eng, timeout=0)
    assert c.get("/me/telegram").json()["linked"] is False


def test_every_alert_kind_reaches_telegram_once(fresh, eng):
    fakes, bot = fresh
    c = user()
    with eng.begin() as conn:   # an alert from before linking stays in the inbox only
        conn.execute(sa.insert(alert_events).values(user_id=uid_of(eng), symbol="BTCUSDT", kind="stress_high",
                                                    title="old", message="old", fired_at=T0))
    link_alice(c, eng, bot)
    bot.sent.clear()
    # a Phase 11 price alert and a Guardian alert, then delivery
    c.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "price_below", "threshold": 69_000})
    c.post("/me/guardian/positions", json={"symbol": "BTCUSDT", "side": "long", "entry": 70000, "leverage": 50,
                                           "margin": 100})
    m = Market(70_000)
    with eng.begin() as conn:
        engine.run_once(conn, m.view(), T0)
    m.price["BTCUSDT"] = 68_900
    with eng.begin() as conn:
        engine.run_once(conn, m.view(), T0 + timedelta(minutes=1))
    run(eng, m, fakes, minutes=1)
    with eng.begin() as conn:
        assert dispatch.run_once(conn, bot, T0 + timedelta(minutes=1)) >= 2
        assert dispatch.run_once(conn, bot, T0 + timedelta(minutes=2)) == 0   # each once
    texts = [t for _, t in bot.sent]
    assert any("crossed below" in t for t in texts)
    g = next(t for t in texts if t.startswith("RegimeCast Guardian"))
    assert "Safer version" in g and "/guardian" in g and all(chat == "555" for chat, _ in bot.sent)
    assert not any(t.endswith("old") or "\nold\n" in t for t in texts)


def test_kinds_filter_rate_limit_and_blocked_bot(fresh, eng):
    _, bot = fresh
    c = user()
    link_alice(c, eng, bot)
    bot.sent.clear()
    uid = uid_of(eng)
    assert c.patch("/me/telegram", json={"kinds": ["guardian_risk"]}).json()["kinds"] == ["guardian_risk"]

    def add(kind, n=1):
        with eng.begin() as conn:
            for _ in range(n):
                conn.execute(sa.insert(alert_events).values(user_id=uid, symbol="BTCUSDT", kind=kind, title=kind,
                                                            message="m", fired_at=T0))
    add("stress_high")
    add("guardian_risk", 25)
    with eng.begin() as conn:
        assert dispatch.run_once(conn, bot, T0) == dispatch.MAX_PER_HOUR          # 20 an hour at most
        st = dict(conn.execute(sa.select(notify_log.c.status, sa.func.count()).group_by(notify_log.c.status)).all())
    assert st == {"sent": 20, "rate_limited": 5} and not any("stress_high" in t for _, t in bot.sent)
    add("guardian_risk")
    bot.fail = tg.TelegramError(429, "Too Many Requests")                        # transient: retried later
    with eng.begin() as conn:
        assert dispatch.run_once(conn, bot, T0 + timedelta(hours=2)) == 0
    bot.fail = None
    with eng.begin() as conn:
        assert dispatch.run_once(conn, bot, T0 + timedelta(hours=2)) == 1
    add("guardian_risk")
    bot.fail = tg.TelegramError(403, "Forbidden: bot was blocked by the user")  # gone: unlinked
    with eng.begin() as conn:
        dispatch.run_once(conn, bot, T0 + timedelta(hours=3))
        assert conn.execute(sa.select(sa.func.count()).select_from(telegram_links)).scalar_one() == 0


def test_telegram_not_configured(fresh):
    tg.set_client(None)
    c = user()
    assert c.get("/me/telegram").json()["configured"] is False
    r = c.post("/me/telegram/link")
    assert r.status_code == 503 and r.json()["status"] == "not_ready"


def test_test_message_and_unlink_from_the_app(fresh, eng):
    _, bot = fresh
    c = user()
    assert c.post("/me/telegram/test").status_code == 409
    link_alice(c, eng, bot)
    assert c.post("/me/telegram/test").status_code == 200 and "Test message" in bot.sent[-1][1]
    assert c.delete("/me/telegram").status_code == 200 and "Unlinked" in bot.sent[-1][1]
    assert c.get("/me/telegram").json()["linked"] is False


def test_alerts_table_untouched_by_guardian(fresh, eng):
    """Guardian alerts are inbox rows only; they never create Phase 11 alert rules."""
    fakes, _ = fresh
    c = user()
    c.post("/me/guardian/positions", json={"symbol": "BTCUSDT", "side": "long", "entry": 70000, "leverage": 50,
                                           "margin": 100})
    run(eng, Market(69_000), fakes)
    with eng.begin() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(alerts)).scalar_one() == 0
        assert conn.execute(sa.select(sa.func.count()).select_from(guardian_positions)).scalar_one() == 1
