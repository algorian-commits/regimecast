"""The Guardian loop: once a minute, read every source's open positions, judge every watched position, and write
an inbox row for each rule that fires; every 15 seconds, send new inbox rows to Telegram (notify/dispatch.py).

Market data is the alert engine's (alerts/engine.MarketView): Binance mark price, HAR-S volatility, stress and
regime from the API's live caches. For positions read from an exchange, that exchange's own mark price and
liquidation price are used.

Binance keys are checked again every PERMISSION_RECHECK: if a key has gained trading or withdrawal permission
since it was saved, it is deleted at once and the user is told.

One API process runs the loop (Railway runs one replica). GUARDIAN=0 switches it off.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import sqlalchemy as sa

from regimecast import db
from regimecast.alerts import engine as alert_engine
from regimecast.alerts import rules
from regimecast.db.schema import alert_events, guardian_positions, guardian_sources, profiles
from regimecast.guardian import binance as bn, crypto, hyperliquid as hl, risk
from regimecast.notify import dispatch, telegram as tg

log = logging.getLogger("guardian")

EVERY_SECONDS = 60.0
DISPATCH_SECONDS = 15.0
PERMISSION_RECHECK = timedelta(hours=6)
SOURCE_ERRORS = (hl.HyperliquidError, bn.BinanceError, crypto.NotConfigured)


def _utc(dt: datetime | None) -> datetime | None:
    return None if dt is None else dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


class Clients:
    """How the loop reaches the exchanges (tests swap in fakes)."""

    def __init__(self, hyperliquid=None, binance: Callable[[str, str], object] | None = None):
        self.hyperliquid = hyperliquid or hl.HyperliquidClient()
        self.binance = binance or (lambda key, secret: bn.BinanceClient(key, secret))

    def binance_for(self, src) -> object:
        return self.binance(crypto.decrypt(src["key_enc"]), crypto.decrypt(src["secret_enc"]))


def where(src) -> str:
    """How a position's source is named in messages."""
    if src is None or src.get("kind") is None:
        return "entered by hand"
    if src.get("label"):
        return src["label"]
    if src["kind"] == "hyperliquid":
        a = src["address"] or ""
        return f"Hyperliquid {a[:6]}...{a[-4:]}"
    return "Binance"


# ------------------------------------------------------------------ sources
def sync(conn: sa.Connection, src, fetched: list[dict], now: datetime) -> None:
    """Make the source's watched positions match what the exchange holds now (mute and alert state are kept)."""
    have = {(r["symbol"], r["side"]): r for r in conn.execute(
        sa.select(guardian_positions).where(guardian_positions.c.source_id == src["id"])).mappings()}
    seen = set()
    for p in fetched:
        k = (p["symbol"], p["side"])
        if k in seen:
            continue
        seen.add(k)
        vals = {f: p[f] for f in ("entry", "qty", "leverage", "margin_mode", "liquidation_price", "mark_price")}
        if k in have:
            conn.execute(sa.update(guardian_positions).where(guardian_positions.c.id == have[k]["id"])
                         .values(**vals, updated_at=now))
        else:
            conn.execute(sa.insert(guardian_positions).values(
                user_id=src["user_id"], source_id=src["id"], symbol=p["symbol"], side=p["side"], muted=False,
                created_at=now, updated_at=now, **vals))
    gone = [r["id"] for k, r in have.items() if k not in seen]
    if gone:
        conn.execute(sa.delete(guardian_positions).where(guardian_positions.c.id.in_(gone)))


def recheck(conn: sa.Connection, src, clients: Clients, now: datetime) -> bool:
    """Binance: check the key's permissions again when due. False if the key was removed."""
    checked = _utc(src["permissions_checked_at"])
    if src["kind"] != "binance" or (checked and now - checked < PERMISSION_RECHECK):
        return True
    perms = bn.check_permissions(clients.binance_for(src))
    if perms.read_only:
        conn.execute(sa.update(guardian_sources).where(guardian_sources.c.id == src["id"])
                     .values(permissions=perms.as_dict(), permissions_checked_at=now))
        return True
    conn.execute(sa.delete(guardian_sources).where(guardian_sources.c.id == src["id"]))   # key gone at once
    conn.execute(sa.insert(alert_events).values(
        user_id=src["user_id"], alert_id=None, symbol="-", kind="guardian_key",
        title="Binance key removed: it is no longer read-only",
        message=f"{perms.refusal()} The Guardian deleted the key and stopped reading this account. Add a "
                f"read-only key on the Guardian page to watch it again.",
        value=None, detail={"source_id": src["id"]}, fired_at=now))
    return False


def poll(conn: sa.Connection, src, clients: Clients, now: datetime) -> None:
    """Read one source's open positions now and store them (or the error)."""
    try:
        if not recheck(conn, src, clients, now):
            return
        if src["kind"] == "hyperliquid":
            fetched = clients.hyperliquid.positions(src["address"])
        else:
            fetched = clients.binance_for(src).positions()
        sync(conn, src, fetched, now)
        upd = {"status": "ok", "last_error": None, "last_polled_at": now}
    except SOURCE_ERRORS as e:
        upd = {"status": "error", "last_error": str(e)[:200], "last_polled_at": now}
    conn.execute(sa.update(guardian_sources).where(guardian_sources.c.id == src["id"]).values(**upd))


# ------------------------------------------------------------------ positions
def check(conn: sa.Connection, row, view: alert_engine.MarketView, now: datetime,
          state_fn: Callable[[str], dict | None] | None = None) -> int:
    """Judge one watched position; write an inbox row for each rule that fires. Returns how many fired."""
    s = row["symbol"]
    limit = row["risk_limit"] or row["profile_limit"]
    mark = row["mark_price"] if row["source_id"] is not None and row["mark_price"] else view.price(s)
    sigma_24h, sigma_1h = (view.sigma(s, 24), view.sigma(s, 1)) if s in risk.COVERED else (None, None)
    reading = risk.read(row, mark, sigma_24h, sigma_1h, view.stress(s) if s in risk.COVERED else None, view.fhs, limit)
    if reading is None:
        why = "not covered" if s not in risk.COVERED else "no market data yet"
        conn.execute(sa.update(guardian_positions).where(guardian_positions.c.id == row["id"])
                     .values(last={"covered": s in risk.COVERED, "note": why, "mark": mark}, checked_at=now))
        return 0
    st = risk.states(reading)
    armed = dict(row["armed"] or {})
    fired = []
    for rule in risk.RULES:
        if row["muted"]:
            continue
        fire, armed[rule] = rules.step(armed.get(rule), st[rule], rule)
        if fire:
            fired.append(rule)
    src = {"kind": row["source_kind"], "label": row["source_label"], "address": row["source_address"]}
    ctx = dict(fhs=view.fhs, rg=view.regime(s), st=view.stress(s), state=state_fn(s) if state_fn else None, now=now)
    worry = st["guardian_risk"] or st["guardian_near"] or bool(fired)
    safer = risk.safer(reading, row["stop"], limit, **ctx) if worry else None
    for rule in fired:
        s_rule = risk.safer(reading, row["stop"], limit * risk.STRESS_LIMIT_SHARE, **ctx) \
            if rule == "guardian_stress" else safer
        title, msg = risk.message(rule, reading, where(src if row["source_kind"] else None), s_rule)
        conn.execute(sa.insert(alert_events).values(
            user_id=row["user_id"], alert_id=None, symbol=s, kind=rule, title=title[:200], message=msg,
            value=reading.p_liquidation, detail={"position_id": row["id"], "safer": s_rule}, fired_at=now))
    conn.execute(sa.update(guardian_positions).where(guardian_positions.c.id == row["id"]).values(
        armed=armed, checked_at=now,
        last={"covered": True, **reading.as_dict(), "states": st, "safer": safer}))
    return len(fired)


def positions_query():
    return (sa.select(guardian_positions, profiles.c.risk_limit.label("profile_limit"),
                      guardian_sources.c.kind.label("source_kind"), guardian_sources.c.label.label("source_label"),
                      guardian_sources.c.address.label("source_address"))
            .select_from(guardian_positions.join(profiles, profiles.c.user_id == guardian_positions.c.user_id)
                         .outerjoin(guardian_sources, guardian_sources.c.id == guardian_positions.c.source_id)))


def run_once(conn: sa.Connection, view: alert_engine.MarketView, clients: Clients, now: datetime | None = None,
             state_fn: Callable[[str], dict | None] | None = None) -> int:
    """Poll every source, then judge every position. Returns how many alerts fired."""
    now = now or datetime.now(timezone.utc)
    for src in conn.execute(sa.select(guardian_sources).order_by(guardian_sources.c.id)).mappings().all():
        poll(conn, src, clients, now)
    rows = conn.execute(positions_query().order_by(guardian_positions.c.id)).mappings().all()
    return sum(check(conn, r, view, now, state_fn) for r in rows)


def live_state(symbol: str) -> dict | None:
    from regimecast.vol import live as vol_live
    return vol_live.cache.get_state(symbol)


class Guardian:
    def __init__(self, view_factory: Callable[[], alert_engine.MarketView] = alert_engine.live_view,
                 clients: Clients | None = None, every: float = EVERY_SECONDS, dispatch_every: float = DISPATCH_SECONDS):
        self.view_factory, self.every, self.dispatch_every = view_factory, every, dispatch_every
        self.clients = clients
        self.last_run: str | None = None
        self.last_fired = 0
        self.last_sent = 0
        self.last_error: str | None = None
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        eng = db.get_engine()
        if eng is None:
            return
        self.clients = self.clients or Clients()
        with eng.begin() as conn:
            self.last_fired = run_once(conn, self.view_factory(), self.clients, state_fn=live_state)
        self.last_run = datetime.now(timezone.utc).isoformat()

    def dispatch(self) -> None:
        eng = db.get_engine()
        if eng is None:
            return
        with eng.begin() as conn:
            self.last_sent = dispatch.run_once(conn, tg.get_client())

    def start(self) -> None:
        if self._thread is not None or os.environ.get("GUARDIAN", "1") == "0":
            return

        def loop():
            time.sleep(30)   # let the model cache do its first refresh
            next_tick = 0.0
            while True:
                t0 = time.monotonic()
                for job, due in ((self.tick, t0 >= next_tick), (self.dispatch, True)):
                    if not due:
                        continue
                    try:
                        job()
                        self.last_error = None
                    except Exception as e:   # database or network hiccup: try again next time
                        self.last_error = type(e).__name__
                        log.warning("guardian %s failed: %s", job.__name__, type(e).__name__)
                if t0 >= next_tick:
                    next_tick = t0 + self.every
                time.sleep(max(1.0, self.dispatch_every - (time.monotonic() - t0)))

        self._thread = threading.Thread(target=loop, name="guardian", daemon=True)
        self._thread.start()


guardian = Guardian()
