"""Alert engine (M12): a background task in the API that checks every active alert once a minute.

* Market state comes from what the API already has: the mark price from Binance
  (api/market.py, cached), the stress gauge, regime and HAR-S volatility from the live
  model cache (vol/live.py). Nothing extra is fetched per user.
* Each alert follows alerts/rules.py: it fires once, then waits until its condition has
  cleared before it can fire again. A fired alert is one row in ``alert_events``, the
  user's inbox (GET /me/inbox, the bell in the app).
* ``whale_watch`` alerts copy new Whale Watch events (whales/) for their coin into the
  inbox; each event is delivered once (the alert remembers the last event id it delivered).

One API process runs the loop (Railway runs one replica). ALERT_ENGINE=0 switches it off.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable

import sqlalchemy as sa

from regimecast import db
from regimecast.alerts import rules
from regimecast.db.schema import alert_events, alerts, profiles, risk_checks, whale_events

log = logging.getLogger("alerts.engine")

EVERY_SECONDS = 60.0


class MarketView:
    """The current market as the API sees it, read once per symbol per run."""

    def __init__(self, price: Callable[[str], float | None], stress: Callable[[str], dict | None],
                 regime: Callable[[str], dict | None], sigma: Callable[[str, int], float | None], fhs: dict):
        self._price, self._stress, self._regime, self._sigma, self.fhs = price, stress, regime, sigma, fhs
        self._memo: dict = {}

    def _get(self, key, fn):
        if key not in self._memo:
            try:
                self._memo[key] = fn()
            except Exception as e:   # one source failing must not stop the other alerts
                log.warning("market view %s failed: %r", key, e)
                self._memo[key] = None
        return self._memo[key]

    def price(self, s):
        return self._get(("price", s), lambda: self._price(s))

    def stress(self, s):
        return self._get(("stress", s), lambda: self._stress(s))

    def regime(self, s):
        return self._get(("regime", s), lambda: self._regime(s))

    def sigma(self, s, h):
        return self._get(("sigma", s, h), lambda: self._sigma(s, h))


def live_view() -> MarketView:
    """The API's own live data (imported lazily so tests can build views without it)."""
    import math

    from regimecast.api import market
    from regimecast.risk import fhs_live
    from regimecast.vol import live as vol_live

    def price(s):
        m = market.cache.get(s)
        return m.mark_price if m and not m.stale else None

    def sigma(s, h):
        fc = vol_live.cache.get(s, h)
        return math.sqrt(fc["variance"]) if fc and fc.get("model", "").startswith("HAR-S") else None

    return MarketView(price, vol_live.cache.get_stress, vol_live.cache.get_regime, sigma, fhs_live.load())


def evaluate(a, view: MarketView, trade: dict | None, limit: float | None) -> rules.Check:
    kind, s = a["kind"], a["symbol"]
    if kind in rules.PRICE_KINDS:
        return rules.price_check(kind, a["threshold"], view.price(s), s)
    if kind in ("stress_elevated", "stress_high"):
        return rules.stress_check(kind, view.stress(s), s)
    if kind == "regime_stressed":
        return rules.regime_check(view.regime(s), s)
    if kind == "position_risk":
        if trade is None or limit is None:
            return rules.Check(None)
        return rules.risk_check(trade, limit, view.price(s), view.sigma(s, int(trade.get("horizon_hours", 24))),
                                view.fhs)
    return rules.Check(None)


def _deliver_whales(conn: sa.Connection, a, now: datetime) -> int:
    """New Whale Watch events for this alert's coin since the last one delivered."""
    last = int(a["last_value"] or 0)
    q = sa.select(whale_events).where(whale_events.c.symbol == a["symbol"], whale_events.c.id > last,
                                      whale_events.c.created_at >= a["created_at"]).order_by(whale_events.c.id)
    evs = conn.execute(q.limit(50)).mappings().all()
    for e in evs:
        conn.execute(sa.insert(alert_events).values(
            user_id=a["user_id"], alert_id=a["id"], symbol=a["symbol"], kind="whale_watch",
            title=e["title"], message=e["reason"], value=None,
            detail={"whale_event_id": e["id"], "whale_kind": e["kind"], "address": e["address"]}, fired_at=now))
    upd = {"last_checked_at": now}
    if evs:
        upd.update(last_value=float(evs[-1]["id"]), last_fired_at=now, fired_count=a["fired_count"] + len(evs))
    conn.execute(sa.update(alerts).where(alerts.c.id == a["id"]).values(**upd))
    return len(evs)


def run_once(conn: sa.Connection, view: MarketView, now: datetime | None = None) -> int:
    """Check every active alert once; returns how many inbox messages were written."""
    now = now or datetime.now(timezone.utc)
    rows = conn.execute(
        sa.select(alerts, risk_checks.c.trade, profiles.c.risk_limit)
        .select_from(alerts.outerjoin(risk_checks, alerts.c.check_id == risk_checks.c.id)
                     .join(profiles, profiles.c.user_id == alerts.c.user_id))
        .where(alerts.c.active.is_(True)).order_by(alerts.c.id)).mappings().all()
    fired = 0
    for a in rows:
        if a["kind"] == "whale_watch":
            fired += _deliver_whales(conn, a, now)
            continue
        limit = a["threshold"] if a["kind"] == "position_risk" and a["threshold"] else a["risk_limit"]
        chk = evaluate(a, view, a["trade"], limit)
        fire, armed = rules.step(a["armed"], chk.state, a["kind"])
        upd = {"armed": armed, "last_checked_at": now}
        if chk.value is not None:
            upd["last_value"] = float(chk.value)
        if fire:
            fired += 1
            upd.update(last_fired_at=now, fired_count=a["fired_count"] + 1)
            conn.execute(sa.insert(alert_events).values(
                user_id=a["user_id"], alert_id=a["id"], symbol=a["symbol"], kind=a["kind"], title=chk.title,
                message=chk.message, value=chk.value, detail={"note": a["note"]} if a["note"] else None,
                fired_at=now))
        conn.execute(sa.update(alerts).where(alerts.c.id == a["id"]).values(**upd))
    return fired


class Engine:
    def __init__(self, view_factory: Callable[[], MarketView] = live_view, every: float = EVERY_SECONDS):
        self.view_factory, self.every = view_factory, every
        self.last_run: str | None = None
        self.last_fired = 0
        self.last_error: str | None = None
        self._thread: threading.Thread | None = None

    def tick(self) -> None:
        eng = db.get_engine()
        if eng is None:
            return
        with eng.begin() as conn:
            self.last_fired = run_once(conn, self.view_factory())
        self.last_run = datetime.now(timezone.utc).isoformat()

    def start(self) -> None:
        if self._thread is not None or os.environ.get("ALERT_ENGINE", "1") == "0":
            return

        def loop():
            time.sleep(30)   # let the model cache do its first refresh
            while True:
                t0 = time.monotonic()
                try:
                    self.tick()
                    self.last_error = None
                except Exception as e:   # database hiccup: try again next minute
                    self.last_error = repr(e)
                    log.warning("alert engine run failed: %r", e)
                time.sleep(max(1.0, self.every - (time.monotonic() - t0)))

        self._thread = threading.Thread(target=loop, name="alert-engine", daemon=True)
        self._thread.start()


engine = Engine()
