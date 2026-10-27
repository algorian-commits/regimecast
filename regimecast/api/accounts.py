"""User accounts (M6): sign up, log in, profile and settings, saved risk checks, alerts.

* Passwords are hashed with argon2id (argon2-cffi) and never stored or logged as text.
* A session is a random 256-bit token in an httpOnly, Secure, SameSite=Lax cookie. The
  database keeps only HMAC-SHA256(SESSION_SECRET, token), so a copy of the database alone
  cannot be used to log in. Sessions last 30 days; logging out deletes them.
* Every /me/* query is filtered by the logged-in user's id: one user can never read,
  change or delete another user's checks, alerts or profile (tests/test_accounts.py).
* Without DATABASE_URL the endpoints answer 503 {"status": "not_ready"} like the other
  unfinished parts of the API.

The web app reaches these through its /api/* forwarder, which passes the cookie along,
so the cookie belongs to the website's own domain.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import sqlalchemy as sa
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from regimecast import db
from regimecast.db.schema import alerts, profiles, risk_checks, sessions, users

log = logging.getLogger("api.accounts")

Symbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]
COOKIE = "rc_session"
SESSION_DAYS = 30
COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") != "0"   # 0 only for plain-http local dev
MAX_ALERTS = 50
MAX_JSON_CHARS = 20_000
DEFAULT_PROFILE = {"paper_balance": 10_000.0, "risk_limit": 0.01, "default_margin_mode": "isolated",
                   "favourite_coins": ["BTCUSDT"]}

_hasher = PasswordHasher()   # argon2id, library defaults (64 MiB, 3 passes)
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))   # verified against for unknown e-mails: same timing
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class DatabaseNotReady(Exception):
    pass


# ------------------------------------------------------------------ secrets and tokens
_secret: bytes | None = None


def session_secret() -> bytes:
    global _secret
    if _secret is None:
        s = os.environ.get("SESSION_SECRET", "")
        if len(s) < 32:
            log.warning("SESSION_SECRET is not set (or shorter than 32 characters): using a random key, so "
                        "everyone is logged out when the API restarts")
            s = secrets.token_hex(32)
        _secret = s.encode()
    return _secret


def token_hash(token: str) -> str:
    return hmac.new(session_secret(), token.encode(), hashlib.sha256).hexdigest()


def _utc(dt: datetime) -> datetime:
    """SQLite hands back naive datetimes; everything here is UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ failed-login throttle
class LoginThrottle:
    """At most `limit` failed logins per e-mail in `window` seconds (in memory, per API process)."""

    def __init__(self, limit: int = 10, window: float = 900.0, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, email: str) -> bool:
        now = self.clock()
        with self._lock:
            recent = [t for t in self._fails.get(email, []) if now - t < self.window]
            self._fails[email] = recent
            return len(recent) >= self.limit

    def fail(self, email: str) -> None:
        with self._lock:
            self._fails.setdefault(email, []).append(self.clock())

    def clear(self, email: str) -> None:
        with self._lock:
            self._fails.pop(email, None)


throttle = LoginThrottle()


# ------------------------------------------------------------------ schemas
class AuthIn(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128, description="8 to 128 characters")

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip().lower()
        if not _EMAIL.match(v):
            raise ValueError("enter a valid e-mail address")
        return v


class ProfileOut(BaseModel):
    paper_balance: float = Field(description="Paper account balance in USDT")
    risk_limit: float = Field(description="Highest chance of liquidation you accept per trade (0.01 = 1%); "
                                          "the risk check's safe leverage and position size use it")
    default_margin_mode: Literal["isolated", "cross"]
    favourite_coins: list[Symbol]
    updated_at: str


class ProfileIn(BaseModel):
    """Change any of the fields; the ones left out keep their value."""
    paper_balance: float | None = Field(None, gt=0, le=1e9)
    risk_limit: float | None = Field(None, ge=0.0005, le=0.5)
    default_margin_mode: Literal["isolated", "cross"] | None = None
    favourite_coins: list[Symbol] | None = Field(None, max_length=3)

    @field_validator("favourite_coins")
    @classmethod
    def _unique(cls, v):
        return list(dict.fromkeys(v)) if v is not None else v


class MeOut(BaseModel):
    email: str
    created_at: str
    profile: ProfileOut


class CheckIn(BaseModel):
    symbol: Symbol
    side: Literal["long", "short"]
    text: str | None = Field(None, max_length=500, description="The trade as the user typed it, if any")
    trade: dict[str, Any] = Field(description="The request sent to /risk-check")
    result: dict[str, Any] = Field(description="The /risk-check answer")

    @model_validator(mode="after")
    def _size(self):
        import json
        if len(json.dumps(self.trade)) + len(json.dumps(self.result)) > MAX_JSON_CHARS:
            raise ValueError("trade and result are too large to save")
        return self


class CheckOut(BaseModel):
    id: int
    symbol: str
    side: str
    text: str | None
    trade: dict[str, Any]
    result: dict[str, Any]
    created_at: str


class ChecksOut(BaseModel):
    checks: list[CheckOut] = Field(description="Newest first")
    total: int = Field(description="How many checks match the filters (before limit/offset)")


AlertKind = Literal["price_above", "price_below", "stress_elevated", "stress_high", "regime_stressed",
                    "position_risk", "whale_watch"]


class AlertIn(BaseModel):
    """price_above / price_below: the mark price crosses ``threshold``. stress_elevated / stress_high: the
    stress gauge reaches that level. regime_stressed: the regime turns stressed. position_risk: the saved
    check ``check_id``'s chance of liquidation goes above ``threshold`` (default: the profile's risk limit).
    whale_watch: every new Whale Watch event for the coin. Each alert fires once, then waits until its
    condition has cleared (Phase 11 alert engine, checked once a minute)."""
    symbol: Symbol
    kind: AlertKind
    threshold: float | None = Field(None, gt=0, description="Price in USDT (price alerts) or probability "
                                                            "(position_risk, e.g. 0.02 = 2%)")
    check_id: int | None = Field(None, description="position_risk only: the saved risk check with the position")
    note: str | None = Field(None, max_length=200)

    @model_validator(mode="after")
    def _threshold(self):
        if self.kind.startswith("price_") and self.threshold is None:
            raise ValueError("price alerts need a threshold price")
        if self.kind == "position_risk":
            if self.check_id is None:
                raise ValueError("position alerts need the saved check (check_id) that holds the position")
            if self.threshold is not None and not 0 < self.threshold < 1:
                raise ValueError("the limit for a position alert is a probability between 0 and 1")
        else:
            self.check_id = None
        if not self.kind.startswith("price_") and self.kind != "position_risk":
            self.threshold = None
        return self


class AlertOut(BaseModel):
    id: int
    symbol: str
    kind: AlertKind
    threshold: float | None
    note: str | None
    active: bool
    created_at: str
    check_id: int | None = None
    armed: bool | None = Field(None, description="True: waiting for its condition; False: fired, waiting for "
                                                 "the condition to clear; null: not checked yet")
    last_value: float | None = Field(None, description="Last value compared (price or probability)")
    last_checked_at: str | None = None
    last_fired_at: str | None = None
    fired_count: int = 0


class Ok(BaseModel):
    ok: bool = True


# ------------------------------------------------------------------ dependencies
def get_conn():
    try:
        eng = db.get_engine()
    except sa.exc.OperationalError as e:   # database down or unreachable
        log.warning("database unavailable: %s", type(e).__name__)
        raise DatabaseNotReady("the accounts database is not reachable right now") from None
    if eng is None:
        raise DatabaseNotReady("accounts are not set up on this server (no DATABASE_URL)")
    with eng.begin() as conn:
        yield conn


def current_user(request: Request, conn: sa.Connection = Depends(get_conn)) -> int:
    token = request.cookies.get(COOKIE)
    if token:
        row = conn.execute(sa.select(sessions.c.user_id, sessions.c.expires_at)
                           .where(sessions.c.token_hash == token_hash(token))).first()
        if row and _utc(row.expires_at) > _now():
            return row.user_id
    raise HTTPException(401, "Log in first.")


# ------------------------------------------------------------------ helpers
def _iso(dt: datetime) -> str:
    return _utc(dt).isoformat()


def _profile(conn: sa.Connection, user_id: int) -> ProfileOut:
    p = conn.execute(sa.select(profiles).where(profiles.c.user_id == user_id)).mappings().one()
    return ProfileOut(paper_balance=p["paper_balance"], risk_limit=p["risk_limit"],
                      default_margin_mode=p["default_margin_mode"], favourite_coins=p["favourite_coins"],
                      updated_at=_iso(p["updated_at"]))


def _me(conn: sa.Connection, user_id: int) -> MeOut:
    u = conn.execute(sa.select(users.c.email, users.c.created_at).where(users.c.id == user_id)).one()
    return MeOut(email=u.email, created_at=_iso(u.created_at), profile=_profile(conn, user_id))


def _start_session(conn: sa.Connection, response: Response, user_id: int) -> None:
    now = _now()
    conn.execute(sa.delete(sessions).where(sessions.c.user_id == user_id, sessions.c.expires_at < now))
    token = secrets.token_urlsafe(32)
    conn.execute(sa.insert(sessions).values(user_id=user_id, token_hash=token_hash(token), created_at=now,
                                            expires_at=now + timedelta(days=SESSION_DAYS)))
    response.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, secure=COOKIE_SECURE,
                        samesite="lax", path="/")


def _check_out(r) -> CheckOut:
    return CheckOut(id=r["id"], symbol=r["symbol"], side=r["side"], text=r["text"], trade=r["trade"],
                    result=r["result"], created_at=_iso(r["created_at"]))


def _alert_out(r) -> AlertOut:
    return AlertOut(id=r["id"], symbol=r["symbol"], kind=r["kind"], threshold=r["threshold"], note=r["note"],
                    active=r["active"], created_at=_iso(r["created_at"]), check_id=r["check_id"], armed=r["armed"],
                    last_value=r["last_value"] if r["kind"] != "whale_watch" else None,
                    last_checked_at=_iso(r["last_checked_at"]) if r["last_checked_at"] else None,
                    last_fired_at=_iso(r["last_fired_at"]) if r["last_fired_at"] else None,
                    fired_count=r["fired_count"] or 0)


# ------------------------------------------------------------------ routes
router = APIRouter(tags=["accounts"])
NOT_READY = {503: {"description": "Accounts database not configured or not reachable"}}


@router.post("/auth/signup", response_model=MeOut, status_code=201, responses={409: {}, **NOT_READY})
def signup(body: AuthIn, response: Response, conn: sa.Connection = Depends(get_conn)) -> MeOut:
    """Create an account (paper trading only) and log in."""
    if conn.execute(sa.select(users.c.id).where(users.c.email == body.email)).first():
        raise HTTPException(409, "An account with this e-mail already exists. Log in instead.")
    now = _now()
    try:
        uid = conn.execute(sa.insert(users).values(email=body.email, password_hash=_hasher.hash(body.password),
                                                   created_at=now).returning(users.c.id)).scalar_one()
    except sa.exc.IntegrityError:   # two sign-ups with the same e-mail at the same moment
        raise HTTPException(409, "An account with this e-mail already exists. Log in instead.") from None
    conn.execute(sa.insert(profiles).values(user_id=uid, updated_at=now, **DEFAULT_PROFILE))
    _start_session(conn, response, uid)
    return _me(conn, uid)


@router.post("/auth/login", response_model=MeOut, responses={401: {}, 429: {}, **NOT_READY})
def login(body: AuthIn, response: Response, conn: sa.Connection = Depends(get_conn)) -> MeOut:
    if throttle.blocked(body.email):
        raise HTTPException(429, "Too many failed attempts. Wait 15 minutes and try again.")
    u = conn.execute(sa.select(users.c.id, users.c.password_hash).where(users.c.email == body.email)).first()
    try:
        _hasher.verify(u.password_hash if u else _DUMMY_HASH, body.password)
        ok = u is not None
    except (VerificationError, InvalidHashError):
        ok = False
    if not ok:
        throttle.fail(body.email)
        raise HTTPException(401, "Wrong e-mail or password.")
    throttle.clear(body.email)
    if _hasher.check_needs_rehash(u.password_hash):
        conn.execute(sa.update(users).where(users.c.id == u.id).values(password_hash=_hasher.hash(body.password)))
    _start_session(conn, response, u.id)
    return _me(conn, u.id)


@router.post("/auth/logout", response_model=Ok, responses=NOT_READY)
def logout(request: Request, response: Response, conn: sa.Connection = Depends(get_conn)) -> Ok:
    token = request.cookies.get(COOKIE)
    if token:
        conn.execute(sa.delete(sessions).where(sessions.c.token_hash == token_hash(token)))
    response.delete_cookie(COOKIE, path="/", secure=COOKIE_SECURE, httponly=True, samesite="lax")
    return Ok()


@router.get("/me", response_model=MeOut, responses={401: {}, **NOT_READY})
def me(uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> MeOut:
    """The logged-in user, with profile and settings."""
    return _me(conn, uid)


@router.patch("/me/profile", response_model=ProfileOut, responses={401: {}, **NOT_READY})
def update_profile(body: ProfileIn, uid: int = Depends(current_user),
                   conn: sa.Connection = Depends(get_conn)) -> ProfileOut:
    """Paper balance and risk limit (profile), default margin mode and favourite coins (settings)."""
    changes = body.model_dump(exclude_none=True)
    if changes:
        conn.execute(sa.update(profiles).where(profiles.c.user_id == uid).values(**changes, updated_at=_now()))
    return _profile(conn, uid)


@router.post("/me/checks", response_model=CheckOut, status_code=201, responses={401: {}, **NOT_READY})
def save_check(body: CheckIn, uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> CheckOut:
    """Save a risk check (the trade and the answer) to the user's history."""
    r = conn.execute(sa.insert(risk_checks).values(user_id=uid, created_at=_now(), **body.model_dump())
                     .returning(*risk_checks.c)).mappings().one()
    return _check_out(r)


@router.get("/me/checks", response_model=ChecksOut, responses={401: {}, **NOT_READY})
def list_checks(symbol: Symbol | None = None,
                since: datetime | None = Query(None, description="Only checks at or after this time (ISO 8601)"),
                until: datetime | None = Query(None, description="Only checks before this time (ISO 8601)"),
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> ChecksOut:
    """The user's saved risk checks, newest first, optionally for one coin and a time range."""
    where = [risk_checks.c.user_id == uid]
    if symbol:
        where.append(risk_checks.c.symbol == symbol)
    if since:
        where.append(risk_checks.c.created_at >= _utc(since))
    if until:
        where.append(risk_checks.c.created_at < _utc(until))
    total = conn.execute(sa.select(sa.func.count()).select_from(risk_checks).where(*where)).scalar_one()
    rows = conn.execute(sa.select(risk_checks).where(*where)
                        .order_by(risk_checks.c.created_at.desc(), risk_checks.c.id.desc())
                        .limit(limit).offset(offset)).mappings().all()
    return ChecksOut(checks=[_check_out(r) for r in rows], total=total)


@router.delete("/me/checks/{check_id}", response_model=Ok, responses={401: {}, 404: {}, **NOT_READY})
def delete_check(check_id: int, uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> Ok:
    n = conn.execute(sa.delete(risk_checks).where(risk_checks.c.id == check_id, risk_checks.c.user_id == uid)).rowcount
    if not n:
        raise HTTPException(404, "No such saved check.")
    return Ok()


@router.get("/me/alerts", response_model=list[AlertOut], responses={401: {}, **NOT_READY})
def list_alerts(uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> list[AlertOut]:
    """The user's alerts, newest first, with their state in the alert engine."""
    rows = conn.execute(sa.select(alerts).where(alerts.c.user_id == uid)
                        .order_by(alerts.c.created_at.desc(), alerts.c.id.desc())).mappings().all()
    return [_alert_out(r) for r in rows]


@router.post("/me/alerts", response_model=AlertOut, status_code=201,
             responses={401: {}, 404: {}, 409: {}, **NOT_READY})
def create_alert(body: AlertIn, uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> AlertOut:
    n = conn.execute(sa.select(sa.func.count()).select_from(alerts).where(alerts.c.user_id == uid)).scalar_one()
    if n >= MAX_ALERTS:
        raise HTTPException(409, f"You have {MAX_ALERTS} alerts already. Delete one first.")
    if body.kind == "position_risk":
        chk = conn.execute(sa.select(risk_checks.c.symbol).where(risk_checks.c.id == body.check_id,
                                                                 risk_checks.c.user_id == uid)).first()
        if not chk:
            raise HTTPException(404, "No such saved check.")
        body.symbol = chk.symbol   # the position's coin, whatever was sent
    r = conn.execute(sa.insert(alerts).values(user_id=uid, active=True, created_at=_now(), **body.model_dump())
                     .returning(*alerts.c)).mappings().one()
    return _alert_out(r)


@router.delete("/me/alerts/{alert_id}", response_model=Ok, responses={401: {}, 404: {}, **NOT_READY})
def delete_alert(alert_id: int, uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> Ok:
    n = conn.execute(sa.delete(alerts).where(alerts.c.id == alert_id, alerts.c.user_id == uid)).rowcount
    if not n:
        raise HTTPException(404, "No such alert.")
    return Ok()


def install(app: FastAPI) -> None:
    """Add the account routes to the API (one line in main.py)."""
    app.include_router(router)

    @app.exception_handler(DatabaseNotReady)
    async def _not_ready(_request, exc: DatabaseNotReady):
        return JSONResponse({"status": "not_ready", "detail": str(exc)}, status_code=503)
