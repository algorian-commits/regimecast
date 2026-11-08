"""Trader tracker in the API: the pool's open positions, read from Hyperliquid's public endpoint.

``clearinghouseState`` weighs 2, so a sweep of every account that has traded perps costs a few
thousand weight. The watcher spends at most TRADER_WEIGHT_PER_MIN (default 500 of the 1,200 per
minute Hyperliquid allows an IP address; Whale Watch's sweep uses about 64 per minute on the same
address); requests go one at a time, so a full sweep of ~1,560 accounts takes about half an hour from a PC, and skilled accounts are read again every
SKILLED_EVERY_S on top. Each position keeps the time it was read. TRADER_WATCH=0 switches it off.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone

import pandas as pd

from regimecast import config, db
from regimecast.recorder.hyperliquid import coin_of, parse_ctx, parse_positions
from regimecast.traders import published
from regimecast.traders.hl import Budget, Info
from regimecast.traders.rules import ClusterRule, skilled_move

log = logging.getLogger("traders.live")

WEIGHT_PER_MIN = float(os.environ.get("TRADER_WEIGHT_PER_MIN", "500"))
SKILLED_EVERY_S = 120.0
MIN_SWEEP_S = 180.0
MARK_EVERY_S = 60.0
TRADES_EVERY_S = 900.0
TRADES_DAYS = 7
TRADES_KEEP = 20
COINS = [coin_of(s) for s in config.SYMBOLS]


def merge_trades(fills: list[dict], address: str, keep: int = TRADES_KEEP) -> list[dict]:
    """Fills in the same coin, side and minute are one trade; newest first, BTC/ETH/SOL only."""
    out: dict[tuple, dict] = {}
    for f in fills:
        if f.get("coin") not in COINS:
            continue
        ms = int(f["time"])
        k = (f["coin"], f["side"], f.get("dir", ""), ms // 60000)
        t = out.setdefault(k, {"address": address, "ts": pd.Timestamp(ms, unit="ms", tz="UTC").isoformat(),
                               "coin": f["coin"], "side": "buy" if f["side"] == "B" else "sell",
                               "action": f.get("dir", ""), "qty": 0.0, "usd": 0.0, "closed_pnl": 0.0, "n_fills": 0})
        sz, px = float(f["sz"]), float(f["px"])
        t["qty"] += sz
        t["usd"] += sz * px
        t["closed_pnl"] += float(f.get("closedPnl") or 0.0) - float(f.get("fee") or 0.0)
        t["n_fills"] += 1
    rows = sorted(out.values(), key=lambda t: t["ts"], reverse=True)[:keep]
    for t in rows:
        t["price"] = t["usd"] / t["qty"] if t["qty"] else None
    return rows


class TraderWatch:
    def __init__(self, cards: list[dict] | None = None, info: Info | None = None):
        self._cards = cards
        self.info = info
        self.pos: dict[str, dict[str, dict]] = {}      # address -> coin -> row (open positions only)
        self.read_at: dict[str, str] = {}
        self.marks: dict[str, float] = {}
        self.marks_at: str | None = None
        self.trades: dict[str, list[dict]] = {}
        self.pending: list[dict] = []
        self.recent: deque[dict] = deque(maxlen=200)   # also kept in memory (no database: still visible)
        self.rule = ClusterRule()
        self.sweep_seconds: float | None = None
        self.sweeps = 0
        self.errors: deque[str] = deque(maxlen=20)
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ the scorecards the watcher follows
    @property
    def cards(self) -> list[dict]:
        if self._cards is None:
            data = published.scorecards()
            self._cards = data["cards"] if data else []
        return self._cards

    @property
    def skilled(self) -> list[dict]:
        return [c for c in self.cards if c.get("label") == "skilled"]

    # ------------------------------------------------------------ reading
    def apply(self, card: dict | None, address: str, rows: list[dict], ts) -> None:
        """One account's snapshot; compares a skilled account with its previous one."""
        new = {r["coin"]: r for r in rows if r.get("size")}
        with self._lock:
            old = self.pos.get(address)
            self.pos[address] = new
            self.read_at[address] = pd.Timestamp(ts).isoformat()
            if card is not None and card.get("label") == "skilled" and old is not None:
                note = published.headline(published.copy_test())["sentence"]
                for c in COINS:
                    e = skilled_move(card, c, old.get(c), new.get(c), ts, note)
                    if e:
                        self.pending.append(e)

    def read(self, card: dict) -> None:
        a = card["address"]
        try:
            state = self.info.post({"type": "clearinghouseState", "user": a})
            ts = pd.Timestamp.now(tz="UTC")
            self.apply(card, a, parse_positions(state, a, COINS, ts, "traders"), ts)
        except Exception as e:
            self._err(f"positions {a}: {e!r}")

    def read_marks(self) -> None:
        try:
            now = pd.Timestamp.now(tz="UTC")
            rows = parse_ctx(self.info.post({"type": "metaAndAssetCtxs"}), COINS, now)
            with self._lock:
                self.marks = {r["coin"]: r["mark"] for r in rows}
                self.marks_at = now.isoformat()
        except Exception as e:
            self._err(f"marks: {e!r}")

    def read_trades(self, card: dict) -> None:
        a = card["address"]
        try:
            since = int((time.time() - TRADES_DAYS * 86400) * 1000)
            fills = self.info.post({"type": "userFillsByTime", "user": a, "startTime": since, "aggregateByTime": True})
            with self._lock:
                self.trades[a] = merge_trades(fills, a)
        except Exception as e:
            self._err(f"trades {a}: {e!r}")

    def _err(self, msg: str) -> None:
        log.warning("%s", msg)
        self.errors.append(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}")

    # ------------------------------------------------------------ what the API serves
    def positions(self, coin: str | None = None) -> list[dict]:
        with self._lock:
            return [{**r, "address": a, "read_at": self.read_at.get(a)} for a, by in self.pos.items()
                    for c, r in by.items() if coin is None or c == coin]

    def check_clusters(self) -> None:
        now = pd.Timestamp.now(tz="UTC")
        for c in COINS:
            m = self.marks.get(c)
            if m:
                ev = self.rule.check(self.positions(c), c, m, now)
                with self._lock:
                    self.pending += ev

    def flush(self) -> None:
        from regimecast.whales.live import store_events

        with self._lock:
            events, self.pending = self.pending, []
        for e in events:
            self.recent.appendleft(e)
        if not events:
            return
        try:
            eng = db.get_engine()
            if eng is None:
                return
            with eng.begin() as conn:
                store_events(conn, events, datetime.now(timezone.utc))
        except Exception as e:   # keep them for the next try
            self._err(f"storing events: {e!r}")
            with self._lock:
                self.pending = events + self.pending

    # ------------------------------------------------------------ the loop
    def _housekeeping(self, last: dict) -> None:
        now = time.monotonic()
        if now - last.get("marks", -1e9) >= MARK_EVERY_S:
            last["marks"] = now
            self.read_marks()
            self.check_clusters()
            self.flush()
        if now - last.get("skilled", -1e9) >= SKILLED_EVERY_S:
            last["skilled"] = now
            for c in self.skilled:
                self.read(c)
        if now - last.get("trades", -1e9) >= TRADES_EVERY_S:
            last["trades"] = now
            for c in self.skilled:
                self.read_trades(c)

    def _run(self) -> None:
        self.info = self.info or Info(Budget(WEIGHT_PER_MIN))
        last: dict[str, float] = {}
        skilled = {c["address"] for c in self.skilled}
        while True:
            t0 = time.monotonic()
            for card in self.cards:
                self._housekeeping(last)
                if card["address"] not in skilled:
                    self.read(card)
            self.sweep_seconds = time.monotonic() - t0
            self.sweeps += 1
            while time.monotonic() - t0 < MIN_SWEEP_S:   # a short pool must not read Hyperliquid in a tight loop
                self._housekeeping(last)
                time.sleep(5.0)

    def start(self) -> None:
        if self._thread is not None or os.environ.get("TRADER_WATCH", "1") == "0" or not self.cards:
            return
        self._thread = threading.Thread(target=self._run, name="trader-watch", daemon=True)
        self._thread.start()

    def status(self) -> dict:
        return {"running": self._thread is not None and self._thread.is_alive(), "accounts_followed": len(self.cards),
                "skilled": len(self.skilled), "accounts_read": len(self.read_at), "sweeps_done": self.sweeps,
                "sweep_seconds": self.sweep_seconds, "marks_at": self.marks_at, "recent_errors": list(self.errors)}


watch = TraderWatch()
