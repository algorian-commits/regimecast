"""Live market data for the app: mark price, funding and open interest from Binance.

Fetched when a request comes in and cached for a few seconds, so many visitors share
one upstream call and the app never reads stored recordings (PLAN.md, Data strategy).
If Binance fails, a recent cached value is served with `stale: true`; with nothing
recent the API answers 503 not_ready and the app shows a placeholder.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

import httpx
from pydantic import BaseModel, Field

from regimecast import config

CACHE_SECONDS = 5.0  # fresh enough for a pre-filled entry price
STALE_SECONDS = 300.0  # serve an old value (flagged) for this long if Binance is down


class MarketOut(BaseModel):
    symbol: str
    mark_price: float
    index_price: float
    funding_rate: float = Field(description="Last funding rate per 8h interval (0.0001 = 0.01%)")
    next_funding_time: str
    open_interest: float = Field(description="Open interest in coins")
    open_interest_usdt: float
    exchange_time: str = Field(description="Binance timestamp of the mark price")
    fetched_at: str
    stale: bool = Field(False, description="True if Binance could not be reached and this is an older value")
    source: str = "binance_usdm"


def _iso_ms(ms: int | float) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def parse(premium: dict, oi: dict, fetched_at: datetime) -> MarketOut:
    """Combine Binance /premiumIndex and /openInterest responses."""
    mark = float(premium["markPrice"])
    oi_coins = float(oi["openInterest"])
    return MarketOut(
        symbol=premium["symbol"], mark_price=mark, index_price=float(premium["indexPrice"]),
        funding_rate=float(premium["lastFundingRate"]),
        next_funding_time=_iso_ms(premium["nextFundingTime"]),
        open_interest=oi_coins, open_interest_usdt=round(oi_coins * mark, 2),
        exchange_time=_iso_ms(premium["time"]), fetched_at=fetched_at.isoformat(),
    )


def fetch_binance(symbol: str) -> MarketOut:
    with httpx.Client(base_url=config.BINANCE_FAPI_URL, timeout=5.0) as c:
        p = c.get("/fapi/v1/premiumIndex", params={"symbol": symbol})
        o = c.get("/fapi/v1/openInterest", params={"symbol": symbol})
        p.raise_for_status()
        o.raise_for_status()
        return parse(p.json(), o.json(), datetime.now(timezone.utc))


class MarketCache:
    """Per-symbol cache: fresh for CACHE_SECONDS, served flagged as stale up to STALE_SECONDS."""

    def __init__(self, fetch: Callable[[str], MarketOut] = fetch_binance,
                 clock: Callable[[], float] = time.monotonic):
        self.fetch, self.clock = fetch, clock
        self._store: dict[str, tuple[float, MarketOut]] = {}

    def get(self, symbol: str) -> MarketOut | None:
        now = self.clock()
        hit = self._store.get(symbol)
        if hit and now - hit[0] < CACHE_SECONDS:
            return hit[1]
        try:
            m = self.fetch(symbol)
        except Exception:  # network error, Binance down or blocked: fall back to cache
            if hit and now - hit[0] < STALE_SECONDS:
                return hit[1].model_copy(update={"stale": True})
            return None
        self._store[symbol] = (now, m)
        return m


cache = MarketCache()
