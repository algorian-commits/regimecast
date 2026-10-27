"""Accounts (Phase 8, M6): sign-up, log-in, sessions, profile, and that users' data stays private.

Runs on SQLite by default; CI also runs it on PostgreSQL (TEST_DATABASE_URL), the same
database as on Railway.
"""
import logging
import os
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from regimecast import db
from regimecast.api import accounts, main
from regimecast.db.schema import metadata, risk_checks, sessions, users

PW = "correct horse battery"   # test-only password


@pytest.fixture(scope="module")
def engine():
    eng = db.make_engine(os.environ.get("TEST_DATABASE_URL") or "sqlite://")
    with eng.begin() as c:   # a clean schema built by the migrations, as in production
        metadata.drop_all(c)
        c.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    db.upgrade(eng)
    db.upgrade(eng)   # running it again is a no-op
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def fresh(engine, monkeypatch):
    with engine.begin() as c:
        for t in reversed(metadata.sorted_tables):
            c.execute(t.delete())
    monkeypatch.setattr(db, "_engine", engine)
    accounts.throttle._fails.clear()
    yield


def client():
    return TestClient(main.app, base_url="https://testserver")   # the session cookie is Secure


def signup(c, email="alice@example.test", password=PW):
    r = c.post("/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r


CHECK = {"symbol": "BTCUSDT", "side": "long", "text": "long btc 10x",
         "trade": {"symbol": "BTCUSDT", "side": "long", "entry": 80000, "leverage": 10, "margin": 100},
         "result": {"p_liquidation": 0.004, "liquidation_price": 72300.5}}


def test_signup_logs_in_and_never_stores_the_password(engine, caplog):
    caplog.set_level(logging.DEBUG)
    c = client()
    r = signup(c, email="  Alice@Example.TEST ")
    me = r.json()
    assert me["email"] == "alice@example.test"
    assert me["profile"] == {**me["profile"], "paper_balance": 10000, "risk_limit": 0.01,
                             "default_margin_mode": "isolated", "favourite_coins": ["BTCUSDT"]}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
    assert c.get("/me").json()["email"] == "alice@example.test"
    with engine.connect() as conn:
        row = conn.execute(sa.select(users.c.password_hash)).one()
        stored = conn.execute(sa.select(sessions.c.token_hash)).scalar_one()
    assert row.password_hash.startswith("$argon2id$") and PW not in row.password_hash
    assert stored != c.cookies.get(accounts.COOKIE) and len(stored) == 64   # only an HMAC of the token
    assert PW not in caplog.text


def test_duplicate_email_and_bad_input():
    c = client()
    signup(c)
    assert c.post("/auth/signup", json={"email": "ALICE@example.test", "password": PW}).status_code == 409
    assert c.post("/auth/signup", json={"email": "not-an-email", "password": PW}).status_code == 422
    assert c.post("/auth/signup", json={"email": "bob@example.test", "password": "short"}).status_code == 422


def test_login_logout_and_wrong_password():
    signup(client())
    c = client()
    assert c.get("/me").status_code == 401
    bad = c.post("/auth/login", json={"email": "alice@example.test", "password": "wrong password!"})
    unknown = c.post("/auth/login", json={"email": "nobody@example.test", "password": PW})
    assert bad.status_code == unknown.status_code == 401
    assert bad.json() == unknown.json()   # does not reveal which e-mails have accounts
    assert c.post("/auth/login", json={"email": "Alice@example.test", "password": PW}).status_code == 200
    assert c.get("/me").status_code == 200
    assert c.post("/auth/logout").status_code == 200
    assert c.get("/me").status_code == 401


def test_logout_ends_only_that_session(engine):
    phone, laptop = client(), client()
    signup(phone)
    laptop.post("/auth/login", json={"email": "alice@example.test", "password": PW})
    phone.post("/auth/logout")
    assert phone.get("/me").status_code == 401 and laptop.get("/me").status_code == 200


def test_stolen_or_expired_cookie_is_refused(engine):
    c = client()
    signup(c)
    other = client()
    other.cookies.set(accounts.COOKIE, "made-up-token", domain="testserver")
    assert other.get("/me").status_code == 401
    with engine.begin() as conn:
        conn.execute(sa.update(sessions).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    assert c.get("/me").status_code == 401


def test_failed_logins_are_throttled():
    signup(client())
    c = client()
    for _ in range(accounts.throttle.limit):
        assert c.post("/auth/login", json={"email": "alice@example.test", "password": "wrong password!"}).status_code == 401
    assert c.post("/auth/login", json={"email": "alice@example.test", "password": PW}).status_code == 429


def test_profile_and_settings():
    c = client()
    signup(c)
    p = c.patch("/me/profile", json={"paper_balance": 2500, "risk_limit": 0.02}).json()
    assert p["paper_balance"] == 2500 and p["risk_limit"] == 0.02 and p["default_margin_mode"] == "isolated"
    p = c.patch("/me/profile", json={"default_margin_mode": "cross", "favourite_coins": ["SOLUSDT", "ETHUSDT", "SOLUSDT"]}).json()
    assert p["paper_balance"] == 2500 and p["default_margin_mode"] == "cross" and p["favourite_coins"] == ["SOLUSDT", "ETHUSDT"]
    assert c.patch("/me/profile", json={"risk_limit": 0.9}).status_code == 422
    assert c.patch("/me/profile", json={"paper_balance": -5}).status_code == 422
    assert c.patch("/me/profile", json={"favourite_coins": ["DOGEUSDT"]}).status_code == 422
    assert client().patch("/me/profile", json={"paper_balance": 1}).status_code == 401


def test_saved_checks_filters():
    c = client()
    signup(c)
    for sym in ("BTCUSDT", "ETHUSDT", "BTCUSDT"):
        assert c.post("/me/checks", json={**CHECK, "symbol": sym}).status_code == 201
    all_ = c.get("/me/checks").json()
    assert all_["total"] == 3 and [x["symbol"] for x in all_["checks"]] == ["BTCUSDT", "ETHUSDT", "BTCUSDT"]
    assert all_["checks"][0]["trade"] == CHECK["trade"] and all_["checks"][0]["text"] == "long btc 10x"
    assert c.get("/me/checks", params={"symbol": "ETHUSDT"}).json()["total"] == 1
    later = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
    assert c.get("/me/checks", params={"since": later}).json()["total"] == 0
    assert c.get("/me/checks", params={"until": later}).json()["total"] == 3
    page = c.get("/me/checks", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 3 and len(page["checks"]) == 1
    big = {**CHECK, "result": {"x": "y" * accounts.MAX_JSON_CHARS}}
    assert c.post("/me/checks", json=big).status_code == 422


def test_users_cannot_see_or_touch_each_others_data(engine):
    alice, bob = client(), client()
    signup(alice, "alice@example.test")
    signup(bob, "bob@example.test")
    chk = alice.post("/me/checks", json=CHECK).json()
    alert = alice.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "price_below", "threshold": 70000}).json()
    alice.patch("/me/profile", json={"paper_balance": 777})

    assert bob.get("/me/checks").json() == {"checks": [], "total": 0}
    assert bob.get("/me/alerts").json() == []
    assert bob.get("/me").json()["profile"]["paper_balance"] == 10000
    assert bob.delete(f"/me/checks/{chk['id']}").status_code == 404
    assert bob.delete(f"/me/alerts/{alert['id']}").status_code == 404
    # Bob's own data is his alone too
    bob.post("/me/checks", json={**CHECK, "symbol": "SOLUSDT"})
    assert [x["symbol"] for x in alice.get("/me/checks").json()["checks"]] == ["BTCUSDT"]
    assert alice.get("/me/alerts").json()[0]["id"] == alert["id"]
    assert alice.get("/me").json()["profile"]["paper_balance"] == 777
    # ... and only the owner can delete
    assert alice.delete(f"/me/checks/{chk['id']}").status_code == 200
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(risk_checks)).scalar_one() == 1   # Bob's


def test_alerts_rules():
    c = client()
    signup(c)
    assert c.post("/me/alerts", json={"symbol": "BTCUSDT", "kind": "price_above"}).status_code == 422
    a = c.post("/me/alerts", json={"symbol": "ETHUSDT", "kind": "stress_elevated", "threshold": 5}).json()
    assert a["threshold"] is None and a["active"] is True
    assert c.delete(f"/me/alerts/{a['id']}").status_code == 200 and c.get("/me/alerts").json() == []


def test_without_database_accounts_are_not_ready(monkeypatch):
    monkeypatch.setattr(db, "_engine", None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    r = client().post("/auth/signup", json={"email": "alice@example.test", "password": PW})
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
    assert client().get("/health").status_code == 200   # the rest of the API is unaffected


def test_postgres_url_uses_psycopg():
    assert db.normalise_url("postgresql://u:p@h:5432/d") == "postgresql+psycopg://u:p@h:5432/d"
    assert db.normalise_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert db.normalise_url("sqlite://") == "sqlite://"
