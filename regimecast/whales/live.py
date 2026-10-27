"""Whale Watch in the API (M12): live Hyperliquid positions through the Whale Watch rules.

The recorder's files sit on its own Railway volume, which the API cannot read, so the API
reads Hyperliquid's public endpoints itself, with the recorder's own code
(recorder/hyperliquid.py ``HyperliquidWatcher``, used as is and not changed): the
leaderboard once a day for the top accounts, their positions every 5 minutes, the public
trade stream for big orders, and a one-off snapshot of any unknown wallet that places one.
Instead of writing rows to Parquet, every row goes through whales/rules.py; events are
stored in ``whale_events`` (and delivered to inboxes by the alert engine).

The stress gauge (vol/live.py) is read once a minute for the suspicious-timing rule.
WHALE_WATCH=0 switches it off.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone

import pandas as pd
import sqlalchemy as sa

from regimecast import config, db
from regimecast.db.schema import whale_events, whale_seen
from regimecast.whales.rules import WhaleRules

log = logging.getLogger("whales.live")


def store_events(conn: sa.Connection, events: list[dict], now: datetime) -> int:
    """Insert new events (the same key is stored once); returns how many were new."""
    if not events:
        return 0
    keys = [e["key"] for e in events]
    have = set(conn.execute(sa.select(whale_events.c.key).where(whale_events.c.key.in_(keys))).scalars())
    new = [e for e in events if e["key"] not in have]
    for e in new:
        conn.execute(sa.insert(whale_events).values(
            key=e["key"], ts=e["ts"].to_pydatetime(), symbol=e["symbol"], kind=e["kind"], address=e["address"],
            title=e["title"][:200], reason=e["reason"], detail=_jsonable(e["detail"]), created_at=now))
    return len(new)


def _jsonable(d: dict) -> dict:
    import math

    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in d.items()}


def store_seen(conn: sa.Connection, seen: list[tuple[str, pd.Timestamp]]) -> None:
    if not seen:
        return
    have = set(conn.execute(sa.select(whale_seen.c.address)
                            .where(whale_seen.c.address.in_([a for a, _ in seen]))).scalars())
    for a, ts in dict(seen).items():
        if a not in have:
            conn.execute(sa.insert(whale_seen).values(address=a, first_seen=ts.to_pydatetime()))


class _Store:
    """Stands in for the recorder's Store: rows go to the rules instead of to disk."""

    def __init__(self, watch: "WhaleWatch"):
        self.w = watch

    def add(self, stream: str, row: dict) -> None:
        self.w.on_row(stream, row)


class _Host:
    """The few recorder attributes HyperliquidWatcher uses."""

    def __init__(self, watch: "WhaleWatch"):
        self.store = _Store(watch)
        self.symbols = config.SYMBOLS
        self.connected: dict[str, bool] = {}
        self.errors: deque[str] = deque(maxlen=20)

    def _err(self, msg: str) -> None:
        log.warning("%s", msg)
        self.errors.append(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}")


class WhaleWatch:
    def __init__(self, rules: WhaleRules | None = None):
        self.rules = rules
        self.recent: deque[dict] = deque(maxlen=200)   # also kept in memory (no database: still visible)
        self.pending: list[dict] = []
        self.rows = {"hl_positions": 0, "hl_orders": 0, "hl_watchlist": 0}
        self.last_row_at: str | None = None
        self.host: _Host | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # rows from HyperliquidWatcher (called on the watcher's event loop)
    def on_row(self, stream: str, row: dict) -> None:
        if stream not in self.rows:
            return
        self.rows[stream] += 1
        self.last_row_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            if stream == "hl_watchlist":
                self.rules.watchlist(row)
            elif stream == "hl_orders":
                self.rules.order(row)
            else:
                self.pending += self.rules.position(row)

    def on_stress(self) -> None:
        from regimecast.vol import live as vol_live

        for s in config.SYMBOLS:
            g = vol_live.cache.get_stress(s)
            if g:
                with self._lock:
                    self.pending += self.rules.stress(s.removesuffix("USDT"), g["level"], g["inputs_until"],
                                                      g["p_stress_next_hour"])

    def flush(self) -> None:
        with self._lock:
            events, self.pending = self.pending, []
            seen, self.rules.newly_known = self.rules.newly_known, []
        for e in events:
            self.recent.appendleft(e)
        eng = db.get_engine()
        if eng is None:
            return
        try:
            with eng.begin() as conn:
                store_seen(conn, seen)
                n = store_events(conn, events, datetime.now(timezone.utc))
            if n:
                log.info("whale watch: %d new events", n)
        except Exception as e:   # keep them for the next try
            log.warning("whale watch: storing events failed: %r", e)
            with self._lock:
                self.pending = events + self.pending
                self.rules.newly_known = seen + self.rules.newly_known

    def _load_known(self) -> set[str]:
        eng = db.get_engine()
        if eng is None:
            return set()
        with eng.begin() as conn:
            return set(conn.execute(sa.select(whale_seen.c.address)).scalars())

    async def _main(self) -> None:
        from regimecast.recorder.hyperliquid import HyperliquidWatcher

        try:
            known = self._load_known()
        except Exception as e:
            log.warning("whale watch: could not read known accounts: %r", e)
            known = set()
        self.rules = self.rules or WhaleRules(known=known)
        self.host = _Host(self)
        hl = HyperliquidWatcher(self.host)

        async def housekeeping():
            while True:
                await asyncio.sleep(60)
                try:
                    self.on_stress()
                except Exception as e:
                    log.warning("whale watch: stress read failed: %r", e)
                await asyncio.to_thread(self.flush)

        await asyncio.gather(hl.ws_loop(), hl.watchlist_loop(), hl.positions_loop(), housekeeping())

    def start(self) -> None:
        if self._thread is not None or os.environ.get("WHALE_WATCH", "1") == "0":
            return
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), name="whale-watch", daemon=True)
        self._thread.start()

    def status(self) -> dict:
        r = self.rules
        return {"running": self._thread is not None and self._thread.is_alive(),
                "tracked_accounts": len(r.info) if r else 0, "known_accounts": len(r.known) if r else 0,
                "rows": dict(self.rows), "last_row_at": self.last_row_at,
                "connected": bool(self.host and self.host.connected.get("hyperliquid")),
                "recent_errors": list(self.host.errors) if self.host else []}


watch = WhaleWatch()
