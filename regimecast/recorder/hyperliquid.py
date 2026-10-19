"""Whale Watch recording: Hyperliquid data that shows WHO is positioning.

Hyperliquid is an on-chain perpetuals exchange, so every trade carries the
buyer's and seller's wallet address and any wallet's positions can be read.
Nothing here can be fetched for the past later (positions are only "now"),
so it is recorded live next to the Binance streams. Streams:

* ``hl_orders``     - aggressive orders >= MIN_ORDER_USD on BTC/ETH/SOL, rebuilt
                      from fills (fills of one order share a tx hash; TWAP slices
                      have a zero hash and are grouped by taker, coin and side).
* ``hl_positions``  - every POSITIONS_SECONDS: positions of the watchlist accounts
                      (one row per account and coin, size 0 = flat), plus one-off
                      "probe" snapshots of unknown wallets that place a big order
                      (the "fresh wallet, big bet" pattern of 10 Oct 2025).
* ``hl_watchlist``  - once a day: which accounts we watch and why (from the public
                      leaderboard: largest accounts + best month PnL).
* ``hl_ctx``        - every CTX_SECONDS: mark price, funding, open interest, volume.

All Hyperliquid endpoints used are public and need no key.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from collections import OrderedDict

import pandas as pd

from regimecast import config

log = logging.getLogger("hyperliquid")

MIN_ORDER_USD = float(os.environ.get("HL_MIN_ORDER_USD", "50000"))
PROBE_ORDER_USD = float(os.environ.get("HL_PROBE_ORDER_USD", "250000"))
POSITIONS_SECONDS = float(os.environ.get("HL_POSITIONS_SECONDS", "300"))
CTX_SECONDS = float(os.environ.get("HL_CTX_SECONDS", "60"))
WATCHLIST_PER_RANKING = int(os.environ.get("HL_WATCHLIST_PER_RANKING", "100"))
WATCHLIST_MIN_ACCOUNT_USD = float(os.environ.get("HL_WATCHLIST_MIN_ACCOUNT_USD", "1000000"))
WATCHLIST_REFRESH_SECONDS = 24 * 3600
ORDER_IDLE_SECONDS = 2.0      # an order is complete once no fill arrived for this long
TWAP_IDLE_SECONDS = 65.0      # TWAPs fire a slice every 30 s: merge slices into one order
PROBE_COOLDOWN_SECONDS = 3600  # probe one wallet at most once an hour
REQUEST_GAP_SECONDS = 0.5     # spacing of /info calls; well under the 1200 weight/min limit
SILENT_RECONNECT_SECONDS = 120.0  # trades can be quiet for a while at night

ZERO_HASH = "0x" + "0" * 64


def _ts(ms) -> pd.Timestamp:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC")


def _f(x) -> float:
    return math.nan if x is None else float(x)


def coin_of(symbol: str) -> str:
    """BTCUSDT -> BTC (Hyperliquid names perps by coin)."""
    s = symbol.upper()
    return s[:-4] if s.endswith("USDT") else s


def taker_of(fill: dict) -> str:
    """users = [buyer, seller]; side is the aggressor's side (B = buy, A = sell)."""
    return fill["users"][0] if fill["side"] == "B" else fill["users"][1]


# ------------------------------------------------------------------ orders from fills
class OrderAggregator:
    """Rebuilds aggressive orders from the public fill stream."""

    def __init__(self, min_usd: float = MIN_ORDER_USD):
        self.min_usd = min_usd
        self.open: dict[tuple, dict] = {}
        self._seen: OrderedDict[int, None] = OrderedDict()  # recent fill ids (resubscribe sends a snapshot)

    def add(self, fill: dict, now: float) -> None:
        tid = fill.get("tid")
        if tid is not None:
            if tid in self._seen:
                return
            self._seen[tid] = None
            if len(self._seen) > 50_000:
                self._seen.popitem(last=False)
        taker = taker_of(fill)
        twap = fill["hash"] == ZERO_HASH
        key = ("twap", taker, fill["coin"], fill["side"]) if twap else ("hash", fill["hash"])
        px, sz = float(fill["px"]), float(fill["sz"])
        o = self.open.get(key)
        if o is None:
            o = self.open[key] = {"first": fill["time"], "last": fill["time"], "coin": fill["coin"],
                                  "side": fill["side"], "taker": taker, "hash": fill["hash"], "twap": twap,
                                  "usd": 0.0, "qty": 0.0, "fills": 0, "makers": {}}
        maker = fill["users"][1] if fill["side"] == "B" else fill["users"][0]
        o["usd"] += px * sz
        o["qty"] += sz
        o["fills"] += 1
        o["last"] = max(o["last"], fill["time"])
        o["makers"][maker] = o["makers"].get(maker, 0.0) + px * sz
        o["seen_at"] = now

    def pop_done(self, now: float, idle: float = ORDER_IDLE_SECONDS, twap_idle: float = TWAP_IDLE_SECONDS) -> list[dict]:
        """Finished orders at or above the size threshold, as table rows."""
        rows = []
        done = [k for k, o in self.open.items() if now - o["seen_at"] >= (twap_idle if o["twap"] else idle)]
        for key in done:
            o = self.open.pop(key)
            if o["usd"] < self.min_usd:
                continue
            top_maker, top_maker_usd = max(o["makers"].items(), key=lambda kv: kv[1])
            rows.append({
                "event_ts": _ts(o["first"]), "last_fill_ts": _ts(o["last"]), "coin": o["coin"],
                "side": "buy" if o["side"] == "B" else "sell", "taker": o["taker"],
                "notional_usd": o["usd"], "qty": o["qty"], "vwap": o["usd"] / o["qty"],
                "n_fills": o["fills"], "n_makers": len(o["makers"]),
                "top_maker": top_maker, "top_maker_usd": top_maker_usd, "hash": o["hash"], "twap": o["twap"],
            })
        return rows


# ------------------------------------------------------------------ parsing REST replies
def parse_ctx(meta_and_ctxs: list, coins: list[str], now: pd.Timestamp) -> list[dict]:
    meta, ctxs = meta_and_ctxs
    names = [u["name"] for u in meta["universe"]]
    rows = []
    for c in coins:
        if c not in names:
            continue
        x = ctxs[names.index(c)]
        mark, oi = _f(x.get("markPx")), _f(x.get("openInterest"))
        rows.append({"event_ts": now, "coin": c, "mark": mark, "oracle": _f(x.get("oraclePx")),
                     "mid": _f(x.get("midPx")), "funding": _f(x.get("funding")),
                     "premium": _f(x.get("premium")), "open_interest": oi, "open_interest_usd": oi * mark,
                     "day_volume_usd": _f(x.get("dayNtlVlm"))})
    return rows


def parse_positions(state: dict, address: str, coins: list[str], now: pd.Timestamp, source: str) -> list[dict]:
    """One row per coin we study; size 0 means flat, so closes are visible."""
    account_value = _f(state.get("marginSummary", {}).get("accountValue"))
    held = {p["position"]["coin"]: p["position"] for p in state.get("assetPositions", [])}
    rows = []
    for c in coins:
        p = held.get(c)
        lev = (p or {}).get("leverage") or {}
        rows.append({
            "event_ts": now, "address": address, "coin": c, "source": source,
            "size": _f(p["szi"]) if p else 0.0,  # coins; negative = short
            "position_usd": _f(p.get("positionValue")) if p else 0.0,
            "entry": _f(p.get("entryPx")) if p else math.nan,
            "liquidation": _f(p.get("liquidationPx")) if p else math.nan,
            "leverage": _f(lev.get("value")), "leverage_type": lev.get("type") or "",
            "unrealized_pnl": _f(p.get("unrealizedPnl")) if p else 0.0,
            "margin_used": _f(p.get("marginUsed")) if p else 0.0,
            "account_value": account_value,
        })
    return rows


def select_watchlist(rows: list[dict], n: int = WATCHLIST_PER_RANKING,
                     min_account: float = WATCHLIST_MIN_ACCOUNT_USD) -> list[dict]:
    """Largest accounts plus best month PnL from the public leaderboard."""
    def windows(r: dict) -> dict:
        return {name: _f(w.get("pnl")) for name, w in r.get("windowPerformances", [])}

    big = [r for r in rows if _f(r.get("accountValue")) >= min_account]
    by_value = sorted(big, key=lambda r: -_f(r["accountValue"]))[:n]
    by_month = sorted(big, key=lambda r: -windows(r).get("month", -math.inf))[:n]
    out: dict[str, dict] = {}
    for reason, group in (("largest_account", by_value), ("top_month_pnl", by_month)):
        for r in group:
            a = r["ethAddress"].lower()
            if a in out:
                out[a]["reason"] += "+" + reason
                continue
            w = windows(r)
            out[a] = {"address": a, "display_name": r.get("displayName") or "", "reason": reason,
                      "account_value": _f(r["accountValue"]), "pnl_day": w.get("day", math.nan),
                      "pnl_week": w.get("week", math.nan), "pnl_month": w.get("month", math.nan),
                      "pnl_all_time": w.get("allTime", math.nan)}
    return list(out.values())


# ------------------------------------------------------------------ live loops
class HyperliquidWatcher:
    """Runs next to the Binance recorder and writes into the same Store."""

    def __init__(self, recorder):
        self.rec = recorder
        self.store = recorder.store
        self.coins = [coin_of(s) for s in recorder.symbols]
        self.orders = OrderAggregator()
        self.watchlist: list[str] = []
        self.watch_set: set[str] = set()
        self.probes: asyncio.Queue[str] = asyncio.Queue()
        self._probed: dict[str, float] = {}

    async def _info(self, client, body: dict):
        for attempt in range(4):
            r = await client.post(config.HL_INFO_URL, json=body)
            if r.status_code == 429:  # rate limited: back off
                await asyncio.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"rate limited on {body.get('type')}")

    def _emit_orders(self, now: float) -> None:
        for row in self.orders.pop_done(now):
            self.store.add("hl_orders", row)
            a = row["taker"].lower()
            if (row["notional_usd"] >= PROBE_ORDER_USD and a not in self.watch_set
                    and now - self._probed.get(a, -math.inf) > PROBE_COOLDOWN_SECONDS):
                self._probed[a] = now
                self.probes.put_nowait(a)

    async def ws_loop(self) -> None:
        import websockets

        backoff = 1
        while True:
            try:
                async with websockets.connect(config.HL_WS_URL, ping_interval=20, max_size=2**22) as ws:
                    for c in self.coins:
                        await ws.send(json.dumps({"method": "subscribe", "subscription": {"type": "trades", "coin": c}}))
                    self.rec.connected["hyperliquid"], backoff = True, 1
                    log.info("connected: %s trades %s", config.HL_WS_URL, self.coins)
                    last_msg = time.time()
                    while True:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            raw = None  # wake up anyway to emit finished orders
                        now = time.time()
                        if raw is not None:
                            last_msg = now
                            msg = json.loads(raw)
                            if msg.get("channel") == "trades":
                                for fill in msg["data"]:
                                    self.orders.add(fill, now)
                        elif now - last_msg > SILENT_RECONNECT_SECONDS:
                            raise TimeoutError(f"no Hyperliquid message for {SILENT_RECONNECT_SECONDS:.0f} s")
                        self._emit_orders(now)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.rec._err(f"websocket hyperliquid: {e!r}")
            self.rec.connected["hyperliquid"] = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def ctx_loop(self) -> None:
        import httpx

        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                try:
                    data = await self._info(client, {"type": "metaAndAssetCtxs"})
                    for row in parse_ctx(data, self.coins, pd.Timestamp.now(tz="UTC")):
                        self.store.add("hl_ctx", row)
                except Exception as e:
                    self.rec._err(f"hyperliquid ctx: {e!r}")
                await asyncio.sleep(CTX_SECONDS)

    async def watchlist_loop(self) -> None:
        import httpx

        while True:
            try:
                # the leaderboard is ~40 MB of JSON; parse it off the event loop
                async with httpx.AsyncClient(timeout=300) as client:
                    r = await client.get(config.HL_LEADERBOARD_URL)
                    r.raise_for_status()
                    body = r.content
                picked = await asyncio.to_thread(lambda: select_watchlist(json.loads(body)["leaderboardRows"]))
                del body
                now = pd.Timestamp.now(tz="UTC")
                for row in picked:
                    self.store.add("hl_watchlist", {"event_ts": now, **row})
                self.watchlist = [row["address"] for row in picked]
                self.watch_set = set(self.watchlist)
                log.info("watchlist: %d accounts", len(self.watchlist))
                await asyncio.sleep(WATCHLIST_REFRESH_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.rec._err(f"hyperliquid leaderboard: {e!r}")
                await asyncio.sleep(600)

    async def _snapshot(self, client, address: str, source: str) -> None:
        state = await self._info(client, {"type": "clearinghouseState", "user": address})
        for row in parse_positions(state, address, self.coins, pd.Timestamp.now(tz="UTC"), source):
            self.store.add("hl_positions", row)

    async def _serve_probes(self, client) -> None:
        while not self.probes.empty():
            a = self.probes.get_nowait()
            try:
                await self._snapshot(client, a, "probe")
            except Exception as e:
                self.rec._err(f"hyperliquid probe {a}: {e!r}")
            await asyncio.sleep(REQUEST_GAP_SECONDS)

    async def positions_loop(self) -> None:
        """Sweep the watchlist every POSITIONS_SECONDS. Probes are served within about
        a second, also in the middle of a sweep, so a big bet is captured close to when it happened."""
        import httpx

        async with httpx.AsyncClient(timeout=20) as client:
            next_sweep = 0.0
            while True:
                await self._serve_probes(client)
                if self.watchlist and time.monotonic() >= next_sweep:
                    next_sweep = time.monotonic() + POSITIONS_SECONDS
                    for address in list(self.watchlist):
                        await self._serve_probes(client)
                        try:
                            await self._snapshot(client, address, "watchlist")
                        except Exception as e:
                            self.rec._err(f"hyperliquid positions {address}: {e!r}")
                        await asyncio.sleep(REQUEST_GAP_SECONDS)
                await asyncio.sleep(1.0)

    def tasks(self) -> list:
        return [self.ws_loop(), self.ctx_loop(), self.watchlist_loop(), self.positions_loop()]
