"""Position Guardian and Telegram in the API (Phase 13C).

    GET    /me/telegram                  linked or not, which alert kinds go to Telegram
    POST   /me/telegram/link             a one-time t.me/<bot>?start=<code> link (10 minutes)
    PATCH  /me/telegram                  on/off and which kinds
    DELETE /me/telegram                  unlink
    POST   /me/telegram/test             send a test message
    GET    /me/guardian                  sources, watched positions with live risk, Guardian alert history
    POST   /me/guardian/sources/hyperliquid   watch a public wallet address
    POST   /me/guardian/sources/binance       watch with a READ-ONLY key (refused if it can trade or withdraw)
    DELETE /me/guardian/sources/{id}     stop watching; a Binance key is deleted at once
    POST   /me/guardian/positions        a position entered by hand (or from a saved risk check)
    PATCH  /me/guardian/positions/{id}   mute / unmute, own risk limit
    DELETE /me/guardian/positions/{id}   remove a hand-entered position
    GET    /guardian/status              is the loop running

Binance keys: encrypted before they are stored (guardian/crypto.py), never logged, never in any response. The
request body takes them as plain strings checked by hand, so even a malformed request cannot echo them back in
a validation error.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

from regimecast.alerts import rules
from regimecast.api.accounts import NOT_READY, Ok, Symbol, _iso, current_user, get_conn
from regimecast.api.inbox import InboxItem
from regimecast.db.schema import (alert_events, guardian_positions, guardian_sources, profiles, risk_checks,
                                  telegram_links)
from regimecast.guardian import binance as bn, crypto, hyperliquid as hl, loop
from regimecast.notify import dispatch, linking, telegram as tg

log = logging.getLogger("api.guardian")

MAX_SOURCES = 5
MAX_MANUAL = 20
_KEY = re.compile(r"^[A-Za-z0-9]{16,128}$")
GUARDIAN_KINDS = ("guardian_risk", "guardian_stress", "guardian_near", "guardian_key")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ schemas
class TelegramOut(BaseModel):
    configured: bool = Field(description="False when the server has no TELEGRAM_BOT_TOKEN")
    linked: bool
    username: str | None = None
    linked_at: str | None = None
    enabled: bool = False
    kinds: list[str] | None = Field(None, description="Alert kinds sent to Telegram; null = every kind")


class LinkOut(BaseModel):
    url: str = Field(description="Open on the phone and tap Start; works once, for 10 minutes")
    expires_at: str


class TelegramIn(BaseModel):
    enabled: bool | None = None
    kinds: list[str] | None = Field(None, max_length=50, description="Alert kinds to send; leave out to keep")
    all_kinds: bool = Field(False, description="True: send every kind (clears the list)")


class SourceOut(BaseModel):
    id: int
    kind: Literal["hyperliquid", "binance"]
    label: str | None
    address: str | None = Field(description="Hyperliquid: the public wallet address. Binance: always null")
    permissions: dict[str, Any] | None = Field(description="Binance: what the key may do, as last checked")
    status: str
    last_error: str | None
    last_polled_at: str | None
    created_at: str
    positions: int


class HyperliquidIn(BaseModel):
    address: str = Field(max_length=64, description="0x... wallet address (public; no key needed)")
    label: str | None = Field(None, max_length=60)


class BinanceIn(BaseModel):
    """A Binance API key with ONLY "Enable Reading" ticked. Checked on save: refused if it can trade or withdraw."""
    api_key: str = ""
    api_secret: str = ""
    label: str | None = None


class SaferOut(BaseModel):
    status: Literal["fixed", "safe", "none"]
    limit: float
    leverage_now: float | None = None
    leverage_safe: float | None = None
    add_margin_usdt: float | None = Field(None, description="Add this much margin, same size")
    reduce_to_usdt: float | None = Field(None, description="Or reduce the position to this size, keeping the margin")
    reduce_to_qty: float | None = None
    p_liquidation: float | None = None
    liquidation_price: float | None = None
    moves_1h: float | None = None
    grade: str | None = Field(None, description="Phase 12 grade of the fixed version, if its fix was used")
    stop_advice: str | None = None


class PositionOut(BaseModel):
    id: int
    source_id: int | None
    source: str = Field(description="Where it comes from, in words")
    manual: bool
    symbol: str
    side: Literal["long", "short"]
    entry: float
    qty: float
    leverage: float | None
    margin_mode: str
    stop: float | None
    muted: bool
    risk_limit: float = Field(description="The limit used: its own, else the profile's")
    own_limit: bool
    covered: bool | None = Field(None, description="False for coins RegimeCast does not model; null before the first check")
    note: str | None = None
    mark_price: float | None = None
    liquidation_price: float | None = None
    distance_pct: float | None = None
    moves_1h: float | None = Field(None, description="Distance to liquidation in typical one-hour moves")
    p_liquidation: float | None = Field(None, description="Chance of liquidation within 24 h")
    stress_level: str | None = None
    states: dict[str, bool | None] | None = Field(None, description="Each rule: true holds, false clear")
    safer: SaferOut | None = None
    checked_at: str | None = None


class ManualIn(BaseModel):
    """A position entered by hand, or from a saved risk check (check_id: the rest is taken from it)."""
    check_id: int | None = None
    symbol: Symbol | None = None
    side: Literal["long", "short"] | None = None
    entry: float | None = Field(None, gt=0)
    leverage: float | None = Field(None, gt=0, le=150)
    qty: float | None = Field(None, gt=0, description="Size in coins. Give qty OR margin.")
    margin: float | None = Field(None, gt=0)
    margin_mode: Literal["isolated", "cross"] = "isolated"
    wallet_balance: float | None = Field(None, gt=0)
    stop: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _complete(self):
        if self.check_id is None:
            if None in (self.symbol, self.side, self.entry, self.leverage):
                raise ValueError("give symbol, side, entry and leverage (or a saved check)")
            if (self.qty is None) == (self.margin is None):
                raise ValueError("give exactly one of qty or margin")
            if self.margin_mode == "cross" and self.wallet_balance is None:
                raise ValueError("cross margin needs wallet_balance")
        return self


class PositionPatch(BaseModel):
    muted: bool | None = None
    risk_limit: float | None = Field(None, ge=0.0001, le=0.5, description="Own limit for this position")
    clear_risk_limit: bool = Field(False, description="True: use the profile's limit again")


class GuardianStatus(BaseModel):
    running: bool
    last_run: str | None
    last_fired: int
    last_sent: int
    every_seconds: float
    telegram_configured: bool
    telegram_polling: bool
    binance_keys_enabled: bool


class GuardianOut(BaseModel):
    sources: list[SourceOut]
    positions: list[PositionOut]
    alerts: list[InboxItem] = Field(description="Guardian alerts, newest first")
    profile_risk_limit: float
    binance_keys_enabled: bool = Field(description="False when the server has no GUARDIAN_SECRET")
    telegram: TelegramOut
    status: GuardianStatus


# ------------------------------------------------------------------ helpers
def _telegram(conn: sa.Connection, uid: int) -> TelegramOut:
    ln = conn.execute(sa.select(telegram_links).where(telegram_links.c.user_id == uid)).mappings().first()
    if ln is None:
        return TelegramOut(configured=tg.get_client() is not None, linked=False)
    return TelegramOut(configured=tg.get_client() is not None, linked=True, username=ln["username"],
                       linked_at=_iso(ln["linked_at"]), enabled=ln["enabled"], kinds=ln["kinds"])


def _source_out(r, n: int) -> SourceOut:
    return SourceOut(id=r["id"], kind=r["kind"], label=r["label"], address=r["address"] if r["kind"] == "hyperliquid" else None,
                     permissions=r["permissions"], status=r["status"], last_error=r["last_error"],
                     last_polled_at=_iso(r["last_polled_at"]) if r["last_polled_at"] else None,
                     created_at=_iso(r["created_at"]), positions=n)


def _position_out(r) -> PositionOut:
    last = r["last"] or {}
    src = {"kind": r["source_kind"], "label": r["source_label"], "address": r["source_address"]}
    safer = last.get("safer")
    return PositionOut(
        id=r["id"], source_id=r["source_id"], source=loop.where(src if r["source_kind"] else None),
        manual=r["source_id"] is None, symbol=r["symbol"], side=r["side"], entry=r["entry"], qty=r["qty"],
        leverage=r["leverage"], margin_mode=r["margin_mode"], stop=r["stop"], muted=r["muted"],
        risk_limit=r["risk_limit"] or r["profile_limit"], own_limit=r["risk_limit"] is not None,
        covered=last.get("covered"), note=last.get("note"),
        mark_price=last.get("mark") or r["mark_price"], liquidation_price=last.get("liquidation_price", r["liquidation_price"]),
        distance_pct=last.get("distance_pct"), moves_1h=last.get("moves_1h"), p_liquidation=last.get("p_liquidation"),
        stress_level=last.get("stress_level"), states=last.get("states"),
        safer=SaferOut(**safer) if safer else None,
        checked_at=_iso(r["checked_at"]) if r["checked_at"] else None)


def _status() -> GuardianStatus:
    g = loop.guardian
    return GuardianStatus(running=g._thread is not None, last_run=g.last_run, last_fired=g.last_fired,
                          last_sent=g.last_sent, every_seconds=g.every, telegram_configured=tg.get_client() is not None,
                          telegram_polling=linking.poller._thread is not None, binance_keys_enabled=crypto.enabled())


def _clients() -> loop.Clients:
    if loop.guardian.clients is None:
        loop.guardian.clients = loop.Clients()
    return loop.guardian.clients


def _count_sources(conn, uid) -> int:
    return conn.execute(sa.select(sa.func.count()).select_from(guardian_sources)
                        .where(guardian_sources.c.user_id == uid)).scalar_one()


def _new_source(conn, uid, now, **vals):
    sid = conn.execute(sa.insert(guardian_sources).values(user_id=uid, status="new", created_at=now, **vals)
                       .returning(guardian_sources.c.id)).scalar_one()
    src = conn.execute(sa.select(guardian_sources).where(guardian_sources.c.id == sid)).mappings().one()
    loop.poll(conn, src, _clients(), now)   # read its positions now, so the page shows them at once
    r = conn.execute(sa.select(guardian_sources).where(guardian_sources.c.id == sid)).mappings().one()
    n = conn.execute(sa.select(sa.func.count()).select_from(guardian_positions)
                     .where(guardian_positions.c.source_id == sid)).scalar_one()
    return _source_out(r, n)


# ------------------------------------------------------------------ routes: Telegram
router = APIRouter(tags=["guardian"])


@router.get("/me/telegram", response_model=TelegramOut, responses={401: {}, **NOT_READY})
def telegram_get(uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> TelegramOut:
    return _telegram(conn, uid)


@router.post("/me/telegram/link", response_model=LinkOut, responses={401: {}, **NOT_READY})
def telegram_link(uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> LinkOut:
    """A one-time link that connects this account to the Telegram chat that opens it."""
    client = tg.get_client()
    if client is None:
        from regimecast.api.accounts import DatabaseNotReady
        raise DatabaseNotReady("Telegram is not set up on this server (no TELEGRAM_BOT_TOKEN).")
    try:
        bot = tg.bot_username(client)
    except tg.TelegramError:
        raise HTTPException(502, "Telegram is not answering right now. Try again in a minute.") from None
    code, expires = linking.new_code(conn, uid, _now())
    return LinkOut(url=f"https://t.me/{bot}?start={code}", expires_at=_iso(expires))


@router.patch("/me/telegram", response_model=TelegramOut, responses={401: {}, 404: {}, **NOT_READY})
def telegram_patch(body: TelegramIn, uid: int = Depends(current_user),
                   conn: sa.Connection = Depends(get_conn)) -> TelegramOut:
    changes: dict = {}
    if body.enabled is not None:
        changes["enabled"] = body.enabled
    if body.all_kinds:
        changes["kinds"] = None
    elif body.kinds is not None:
        changes["kinds"] = sorted({k[:20] for k in body.kinds})
    if changes:
        n = conn.execute(sa.update(telegram_links).where(telegram_links.c.user_id == uid).values(**changes)).rowcount
        if not n:
            raise HTTPException(404, "Telegram is not linked.")
    return _telegram(conn, uid)


@router.delete("/me/telegram", response_model=Ok, responses={401: {}, **NOT_READY})
def telegram_unlink(uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> Ok:
    ln = conn.execute(sa.select(telegram_links.c.chat_id).where(telegram_links.c.user_id == uid)).first()
    conn.execute(sa.delete(telegram_links).where(telegram_links.c.user_id == uid))
    client = tg.get_client()
    if ln and client is not None:
        try:
            client.send_message(ln.chat_id, "Unlinked from RegimeCast. No more messages will come to this chat.")
        except tg.TelegramError:
            pass
    return Ok()


@router.post("/me/telegram/test", response_model=Ok, responses={401: {}, 409: {}, 429: {}, 502: {}, **NOT_READY})
def telegram_test(uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> Ok:
    client = tg.get_client()
    if client is None or _telegram(conn, uid).linked is False:
        raise HTTPException(409, "Connect Telegram first.")
    try:
        ok = dispatch.send_direct(conn, client, uid, "Test message from RegimeCast: Telegram alerts work.")
    except tg.TelegramError:
        raise HTTPException(502, "Telegram did not accept the message. Try again, or connect Telegram again.") from None
    if not ok:
        raise HTTPException(429, f"At most {dispatch.MAX_PER_HOUR} Telegram messages an hour. Try later.")
    return Ok()


# ------------------------------------------------------------------ routes: Guardian
@router.get("/me/guardian", response_model=GuardianOut, responses={401: {}, **NOT_READY})
def guardian_get(uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> GuardianOut:
    """Everything the Guardian page shows."""
    counts = dict(conn.execute(sa.select(guardian_positions.c.source_id, sa.func.count())
                               .where(guardian_positions.c.user_id == uid).group_by(guardian_positions.c.source_id)).all())
    srcs = conn.execute(sa.select(guardian_sources).where(guardian_sources.c.user_id == uid)
                        .order_by(guardian_sources.c.id)).mappings().all()
    rows = conn.execute(loop.positions_query().where(guardian_positions.c.user_id == uid)
                        .order_by(guardian_positions.c.id)).mappings().all()
    evs = conn.execute(sa.select(alert_events).where(alert_events.c.user_id == uid,
                                                     alert_events.c.kind.in_(GUARDIAN_KINDS))
                       .order_by(alert_events.c.fired_at.desc(), alert_events.c.id.desc()).limit(30)).mappings().all()
    limit = conn.execute(sa.select(profiles.c.risk_limit).where(profiles.c.user_id == uid)).scalar_one()
    return GuardianOut(
        sources=[_source_out(r, counts.get(r["id"], 0)) for r in srcs], positions=[_position_out(r) for r in rows],
        alerts=[InboxItem(id=e["id"], alert_id=e["alert_id"], symbol=e["symbol"], kind=e["kind"], title=e["title"],
                          message=e["message"], value=e["value"], detail=e["detail"], fired_at=_iso(e["fired_at"]),
                          read=e["read_at"] is not None) for e in evs],
        profile_risk_limit=limit, binance_keys_enabled=crypto.enabled(), telegram=_telegram(conn, uid),
        status=_status())


@router.post("/me/guardian/sources/hyperliquid", response_model=SourceOut, status_code=201,
             responses={400: {}, 401: {}, 409: {}, **NOT_READY})
def add_hyperliquid(body: HyperliquidIn, uid: int = Depends(current_user),
                    conn: sa.Connection = Depends(get_conn)) -> SourceOut:
    addr = hl.normalise_address(body.address)
    if addr is None:
        raise HTTPException(400, "That is not a wallet address: it should be 0x followed by 40 letters and digits.")
    if _count_sources(conn, uid) >= MAX_SOURCES:
        raise HTTPException(409, f"You can connect at most {MAX_SOURCES} sources. Remove one first.")
    if conn.execute(sa.select(guardian_sources.c.id).where(guardian_sources.c.user_id == uid,
                                                           guardian_sources.c.address == addr)).first():
        raise HTTPException(409, "You are already watching this address.")
    return _new_source(conn, uid, _now(), kind="hyperliquid", address=addr, label=(body.label or "").strip() or None)


@router.post("/me/guardian/sources/binance", response_model=SourceOut, status_code=201,
             responses={400: {}, 401: {}, 409: {}, 502: {}, **NOT_READY})
def add_binance(body: BinanceIn, uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> SourceOut:
    """Checks the key with Binance first: it is stored (encrypted) only if it can read and cannot trade or withdraw."""
    if not crypto.enabled():
        from regimecast.api.accounts import DatabaseNotReady
        raise DatabaseNotReady("Binance keys are switched off on this server (no GUARDIAN_SECRET).")
    key, secret = body.api_key.strip(), body.api_secret.strip()
    label = (body.label or "").strip()[:60] or None
    if not _KEY.match(key) or not _KEY.match(secret):
        raise HTTPException(400, "That does not look like a Binance API key and secret (letters and digits only).")
    if _count_sources(conn, uid) >= MAX_SOURCES:
        raise HTTPException(409, f"You can connect at most {MAX_SOURCES} sources. Remove one first.")
    client = _clients().binance(key, secret)
    try:
        perms = bn.check_permissions(client)
    except bn.BinanceError as e:
        if e.code in bn.NO_PERMISSION or e.status in (400, 401):
            raise HTTPException(400, "Binance did not accept this key. Check that you copied the key and secret "
                                     "exactly, and that \"Enable Reading\" is ticked.") from None
        raise HTTPException(502, "Could not check the key's permissions with Binance, so it was not saved. "
                                 "Try again in a minute.") from None
    if not perms.read_only:
        raise HTTPException(400, perms.refusal())
    now = _now()
    return _new_source(conn, uid, now, kind="binance", label=label, key_enc=crypto.encrypt(key),
                       secret_enc=crypto.encrypt(secret), permissions=perms.as_dict(), permissions_checked_at=now)


@router.delete("/me/guardian/sources/{source_id}", response_model=Ok, responses={401: {}, 404: {}, **NOT_READY})
def delete_source(source_id: int, uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> Ok:
    """Stop watching. A Binance key is deleted from the database at once, with its positions."""
    n = conn.execute(sa.delete(guardian_sources).where(guardian_sources.c.id == source_id,
                                                       guardian_sources.c.user_id == uid)).rowcount
    if not n:
        raise HTTPException(404, "No such source.")
    return Ok()


@router.post("/me/guardian/positions", response_model=PositionOut, status_code=201,
             responses={401: {}, 404: {}, 409: {}, **NOT_READY})
def add_position(body: ManualIn, uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> PositionOut:
    n = conn.execute(sa.select(sa.func.count()).select_from(guardian_positions).where(
        guardian_positions.c.user_id == uid, guardian_positions.c.source_id.is_(None))).scalar_one()
    if n >= MAX_MANUAL:
        raise HTTPException(409, f"You can enter at most {MAX_MANUAL} positions by hand. Remove one first.")
    if body.check_id is not None:
        chk = conn.execute(sa.select(risk_checks.c.trade).where(risk_checks.c.id == body.check_id,
                                                                risk_checks.c.user_id == uid)).first()
        if not chk:
            raise HTTPException(404, "No such saved check.")
        p = rules.position_from_trade(chk.trade)
        vals = dict(symbol=p.symbol, side=p.side, entry=p.entry, qty=p.qty, leverage=p.leverage,
                    margin_mode=p.margin_mode, wallet_balance=p.wallet_balance, stop=chk.trade.get("stop"))
    else:
        qty = body.qty if body.qty is not None else body.margin * body.leverage / body.entry
        vals = dict(symbol=body.symbol, side=body.side, entry=body.entry, qty=qty, leverage=body.leverage,
                    margin_mode=body.margin_mode, wallet_balance=body.wallet_balance, stop=body.stop)
    now = _now()
    pid = conn.execute(sa.insert(guardian_positions).values(user_id=uid, source_id=None, check_id=body.check_id,
                                                            muted=False, created_at=now, updated_at=now, **vals)
                       .returning(guardian_positions.c.id)).scalar_one()
    return _position_out(conn.execute(loop.positions_query().where(guardian_positions.c.id == pid)).mappings().one())


@router.patch("/me/guardian/positions/{position_id}", response_model=PositionOut,
              responses={401: {}, 404: {}, **NOT_READY})
def patch_position(position_id: int, body: PositionPatch, uid: int = Depends(current_user),
                   conn: sa.Connection = Depends(get_conn)) -> PositionOut:
    """Mute or unmute (a muted position is still shown, but sends no alerts), or set its own risk limit."""
    changes: dict = {}
    if body.muted is not None:
        changes["muted"] = body.muted
    if body.clear_risk_limit:
        changes["risk_limit"] = None
    elif body.risk_limit is not None:
        changes["risk_limit"] = body.risk_limit
    where = (guardian_positions.c.id == position_id, guardian_positions.c.user_id == uid)
    if changes and not conn.execute(sa.update(guardian_positions).where(*where).values(**changes)).rowcount:
        raise HTTPException(404, "No such position.")
    row = conn.execute(loop.positions_query().where(*where)).mappings().first()
    if row is None:
        raise HTTPException(404, "No such position.")
    return _position_out(row)


@router.delete("/me/guardian/positions/{position_id}", response_model=Ok,
               responses={401: {}, 404: {}, 409: {}, **NOT_READY})
def delete_position(position_id: int, uid: int = Depends(current_user), conn: sa.Connection = Depends(get_conn)) -> Ok:
    row = conn.execute(sa.select(guardian_positions.c.source_id).where(
        guardian_positions.c.id == position_id, guardian_positions.c.user_id == uid)).first()
    if row is None:
        raise HTTPException(404, "No such position.")
    if row.source_id is not None:
        raise HTTPException(409, "This position comes from an exchange and follows it there. Mute it, or remove "
                                 "the source.")
    conn.execute(sa.delete(guardian_positions).where(guardian_positions.c.id == position_id))
    return Ok()


@router.get("/guardian/status", response_model=GuardianStatus)
def guardian_status() -> GuardianStatus:
    """Whether the Guardian loop and the Telegram bot run in this API process."""
    return _status()


def install(app: FastAPI) -> None:
    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def _no_echo(request, exc: RequestValidationError):
        """FastAPI's 422 repeats the input it rejected; for the Binance key route that could be a key, so the
        details are dropped there. Every other route answers exactly as before."""
        if request.url.path.rstrip("/").endswith("/me/guardian/sources/binance"):
            return JSONResponse({"detail": "Send the key as {\"api_key\": ..., \"api_secret\": ...}."}, status_code=422)
        return await request_validation_exception_handler(request, exc)
