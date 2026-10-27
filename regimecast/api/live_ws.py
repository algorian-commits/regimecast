"""Live dashboard feed (M5): one Binance connection shared by every visitor, over a WebSocket.

    GET  /candles?symbol=BTCUSDT&hours=168   hourly candles, each with the regime known at its close
    WS   /ws/live                            one snapshot per second for BTC, ETH and SOL

The API keeps two upstream WebSockets to Binance USD-M (EU server, so not geo-blocked):
``/market`` for the mark price (1 s) and the forming hourly candle, ``/public`` for the top
20 order-book levels (500 ms). It folds every message into a small per-coin state and sends
each browser a snapshot once a second, so a hundred visitors cost one Binance connection.
If Binance drops, the API reconnects with backoff and the snapshot says
``upstream: "reconnecting"``; the browser reconnects to us on its own (web/src/lib/live.ts).

Spread and depth come from the top 20 levels only (for BTC that reaches a few basis points
from the mid price), so they describe the inside of the book, not its full depth.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, FastAPI, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from regimecast import config
from regimecast.vol import live as vol_live

log = logging.getLogger("api.live")

Symbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]
SEND_EVERY_S = 1.0
STALE_AFTER_S = 15.0   # a coin with no message for this long is reported as stale
MARKET_STREAMS = ("markPrice@1s", "kline_1h")
PUBLIC_STREAMS = ("depth20@500ms",)


def _iso_ms(ms: int | float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def stream_url(base: str, kind: str, symbols, streams) -> str:
    names = "/".join(f"{s.lower()}@{st}" for s in symbols for st in streams)
    return f"{base.rstrip('/')}/{kind}/stream?streams={names}"


def book_summary(bids: list, asks: list) -> dict | None:
    """Spread and depth from the top order-book levels ([[price, qty], ...], best first)."""
    b = [(float(p), float(q)) for p, q in bids if float(q) > 0]
    a = [(float(p), float(q)) for p, q in asks if float(q) > 0]
    if not b or not a:
        return None
    bid, ask = b[0][0], a[0][0]
    mid = (bid + ask) / 2

    def within(levels, bps):
        return sum(p * q for p, q in levels if abs(p - mid) / mid * 1e4 <= bps)

    bid_usd, ask_usd = sum(p * q for p, q in b), sum(p * q for p, q in a)
    return {
        "bid": bid, "ask": ask, "mid": mid,
        "spread_bps": (ask - bid) / mid * 1e4,
        "bid_depth_usdt": bid_usd, "ask_depth_usdt": ask_usd,
        "depth_2bps_usdt": within(b, 2) + within(a, 2),
        "imbalance": (bid_usd - ask_usd) / (bid_usd + ask_usd),   # +1 all bids, -1 all asks
        "levels": min(len(b), len(a)),
        "reach_bps": min(abs(b[-1][0] - mid), abs(a[-1][0] - mid)) / mid * 1e4,
    }


class LiveState:
    """Latest mark price, hourly candle and order book per coin, built from Binance messages."""

    def __init__(self, symbols=None, clock=time.time):
        self.symbols = list(symbols or config.SYMBOLS)
        self.clock = clock
        self.coins: dict[str, dict] = {s: {} for s in self.symbols}
        self.last_msg: dict[str, float] = {}
        self.upstream = {"market": False, "public": False}

    def apply(self, msg: dict) -> None:
        """Fold one combined-stream message ({"stream": ..., "data": {...}}) into the state."""
        d = msg.get("data") or {}
        sym = d.get("s")
        if sym not in self.coins:
            return
        c = self.coins[sym]
        e = d.get("e")
        if e == "markPriceUpdate":
            c.update(mark_price=float(d["p"]), index_price=float(d["i"]), funding_rate=float(d["r"]),
                     next_funding_time=_iso_ms(d["T"]), exchange_time=_iso_ms(d["E"]))
        elif e == "kline":
            k = d["k"]
            c["candle"] = {"t": _iso_ms(k["t"]), "open": float(k["o"]), "high": float(k["h"]),
                           "low": float(k["l"]), "close": float(k["c"]), "volume": float(k["v"]),
                           "closed": bool(k["x"])}
        elif e == "depthUpdate":
            bk = book_summary(d.get("b", []), d.get("a", []))
            if bk:
                c["book"] = {**bk, "exchange_time": _iso_ms(d["E"])}
        else:
            return
        self.last_msg[sym] = self.clock()

    def snapshot(self) -> dict:
        now = self.clock()
        up = all(self.upstream.values())
        return {
            "type": "tick",
            "at": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
            "upstream": "ok" if up else "reconnecting",
            "coins": {s: {**c, "stale": now - self.last_msg.get(s, -1e18) > STALE_AFTER_S}
                      for s, c in self.coins.items()},
        }


class Hub:
    """Owns the upstream Binance connections; started by the first browser that connects."""

    def __init__(self, state: LiveState | None = None, connect=None, base_url: str | None = None):
        self.state = state or LiveState()
        self.connect = connect
        self.base_url = base_url or config.BINANCE_FSTREAM_URL
        self.autostart = True
        self._tasks: list[asyncio.Task] = []

    async def _run(self, kind: str, streams) -> None:
        import websockets

        connect = self.connect or websockets.connect
        url = stream_url(self.base_url, kind, self.state.symbols, streams)
        delay = 1.0
        while True:
            try:
                async with connect(url, open_timeout=10, ping_interval=20) as ws:
                    self.state.upstream[kind] = True
                    delay = 1.0
                    async for raw in ws:
                        self.state.apply(json.loads(raw))
            except asyncio.CancelledError:
                raise
            except Exception as e:   # Binance closes every 24 h, or the network blips: reconnect
                log.warning("binance %s stream dropped: %r", kind, e)
            self.state.upstream[kind] = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    def ensure_started(self) -> None:
        if not self.autostart or any(not t.done() for t in self._tasks):
            return
        self._tasks = [asyncio.get_running_loop().create_task(self._run("market", MARKET_STREAMS)),
                       asyncio.get_running_loop().create_task(self._run("public", PUBLIC_STREAMS))]


hub = Hub()


# ------------------------------------------------------------------ routes
class Candle(BaseModel):
    t: str = Field(description="Start of the hour (UTC)")
    open: float
    high: float
    low: float
    close: float
    volume: float = Field(description="Traded volume in coins")
    regime: Literal["calm", "active", "stressed"] | None = Field(
        None, description="Regime known at the candle's close (forward-filtered HMM, no look-ahead)")
    p_stressed: float | None = None


class CandlesOut(BaseModel):
    symbol: str
    candles: list[Candle] = Field(description="Complete hours, oldest first")
    made_at: str


router = APIRouter(tags=["dashboard"])


@router.get("/candles", response_model=CandlesOut, responses={503: {"description": "Not ready yet"}})
def candles(symbol: Symbol = "BTCUSDT", hours: int = Query(168, ge=1, le=24 * 30)):
    """Hourly candles for the dashboard chart, each coloured by the regime at its close."""
    from regimecast.api.main import not_ready

    c = vol_live.cache.get_candles(symbol)
    if not c or not c["candles"]:
        return not_ready("no candles yet (the API refreshes every 10 minutes)")
    return {"symbol": symbol, "candles": c["candles"][-hours:], "made_at": c["made_at"]}


@router.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    """One JSON snapshot per second: mark price, funding, forming hourly candle, spread and depth."""
    await ws.accept()
    hub.ensure_started()
    try:
        while True:
            await ws.send_json(hub.state.snapshot())
            await asyncio.sleep(SEND_EVERY_S)
    except (WebSocketDisconnect, RuntimeError, OSError):   # the browser left (uvicorn raises one of these)
        pass


def install(app: FastAPI) -> None:
    app.include_router(router)
