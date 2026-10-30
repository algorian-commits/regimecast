"""Hyperliquid's public /info endpoint, kept under its rate limit, and the trader pool.

Hyperliquid allows 1200 "weight" per minute per IP address. Most /info requests weigh 20,
``clearinghouseState`` weighs 2, and fill and funding histories weigh 1 more for every 20
items they return (a full page of 2000 fills weighs 120). ``Budget`` spends at most a set
weight per minute so a long download never gets the IP blocked and leaves room for the
API's own Whale Watch, which shares the address on Railway.

The pool (PLAN 13D task 1): the accounts Whale Watch tracks (largest accounts and best
month PnL from the public leaderboard, recorder/hyperliquid.py ``select_watchlist``) plus
every account above $1M account value in the same leaderboard data.
"""
from __future__ import annotations

import json
import math
import threading
import time

import httpx

from regimecast import config
from regimecast.recorder.hyperliquid import select_watchlist

POOL_MIN_ACCOUNT_USD = 1_000_000.0
FILLS_PAGE = 2000          # userFillsByTime returns at most this many fills per call
FILLS_AVAILABLE = 10_000   # and only an account's most recent 10,000 fills


def info_weight(body: dict, n_items: int = 0) -> int:
    """Hyperliquid's documented weight of one /info call."""
    t = body.get("type")
    if t in ("clearinghouseState", "allMids", "l2Book", "orderStatus", "spotClearinghouseState", "exchangeStatus"):
        return 2
    extra = math.ceil(n_items / 20) if t in ("userFills", "userFillsByTime", "fundingHistory", "userFunding") else 0
    return 20 + extra


class Budget:
    """Spends at most ``per_minute`` weight in any rolling minute (thread-safe)."""

    def __init__(self, per_minute: float = 900.0, clock=time.monotonic, sleep=time.sleep):
        self.per_minute, self.clock, self.sleep = per_minute, clock, sleep
        self.spent: list[tuple[float, float]] = []
        self._lock = threading.Lock()

    def _used(self, now: float) -> float:
        self.spent = [(t, w) for t, w in self.spent if now - t < 60.0]
        return sum(w for _, w in self.spent)

    def wait(self, weight: float) -> None:
        while True:
            with self._lock:
                now = self.clock()
                if self._used(now) + weight <= self.per_minute or not self.spent:
                    self.spent.append((now, weight))
                    return
                pause = 60.0 - (now - self.spent[0][0]) + 0.05
            self.sleep(max(0.05, pause))

    def charge(self, weight: float) -> None:
        """Book extra weight learnt from the reply (items returned)."""
        with self._lock:
            self.spent.append((self.clock(), weight))


class Info:
    """Blocking /info client with the budget and retries on 429 / network errors."""

    def __init__(self, budget: Budget | None = None, client: httpx.Client | None = None, url: str | None = None):
        self.budget = budget or Budget()
        self.client = client or httpx.Client(timeout=60)
        self.url = url or config.HL_INFO_URL

    def post(self, body: dict):
        w = info_weight(body)
        for attempt in range(6):
            self.budget.wait(w)
            try:
                r = self.client.post(self.url, json=body)
            except httpx.HTTPError:
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json()
            if isinstance(data, list):   # item-based weight is only known from the reply
                extra = info_weight(body, len(data)) - w
                if extra > 0:
                    self.budget.charge(extra)
            return data
        raise RuntimeError(f"Hyperliquid kept refusing {body.get('type')}")


def fetch_leaderboard(client: httpx.Client | None = None) -> list[dict]:
    """The public leaderboard (~40 MB of JSON, every account with its value and PnL windows)."""
    c = client or httpx.Client(timeout=300)
    r = c.get(config.HL_LEADERBOARD_URL)
    r.raise_for_status()
    return json.loads(r.content)["leaderboardRows"]


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return math.nan


def select_pool(rows: list[dict], min_account: float = POOL_MIN_ACCOUNT_USD) -> list[dict]:
    """Whale Watch's tracked accounts plus every account above ``min_account``; one row per address.

    ``whale_watch`` is True for the accounts Whale Watch tracks (its own selection, unchanged)."""
    tracked = {r["address"]: r for r in select_watchlist(rows)}
    out: dict[str, dict] = {}
    for r in rows:
        a = r["ethAddress"].lower()
        val = _f(r.get("accountValue"))
        if a not in tracked and not val >= min_account:
            continue
        w = {name: _f(x.get("pnl")) for name, x in r.get("windowPerformances", [])}
        out[a] = {"address": a, "display_name": r.get("displayName") or "", "account_value": val,
                  "whale_watch": a in tracked, "tracked_as": tracked.get(a, {}).get("reason", ""),
                  "pnl_month": w.get("month", math.nan), "pnl_all_time": w.get("allTime", math.nan)}
    return sorted(out.values(), key=lambda p: (not p["whale_watch"], -(p["account_value"] or 0)))
