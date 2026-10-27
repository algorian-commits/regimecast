"""Whale Watch endpoints (M12): regimecast/whales/.

    GET /whales/events?symbol=&kind=&limit=   the latest events with their reasons (public data)
    GET /whales/status                        whether the live feed is running

Hyperliquid positions are public on-chain data, so the events are not per user; a user
who wants them in the inbox adds a ``whale_watch`` alert for the coin.
"""
from __future__ import annotations

from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, FastAPI, Query
from pydantic import BaseModel, Field

from regimecast import db
from regimecast.api.accounts import _iso
from regimecast.db.schema import whale_events
from regimecast.whales import rules
from regimecast.whales.live import watch

Symbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]
Kind = Literal["whale_move", "fresh_wallet", "suspicious_timing"]


class WhaleEvent(BaseModel):
    id: int | None
    ts: str
    symbol: str
    kind: Kind
    address: str
    title: str
    reason: str = Field(description="Why it fired, in plain words")
    detail: dict[str, Any] | None
    explorer_url: str = Field(description="The account on a public Hyperliquid explorer")


class WhaleEventsOut(BaseModel):
    events: list[WhaleEvent] = Field(description="Newest first")
    source: Literal["database", "memory"]
    thresholds: dict[str, float]
    note: str


class WhaleStatus(BaseModel):
    running: bool
    connected: bool
    tracked_accounts: int
    known_accounts: int
    rows: dict[str, int]
    last_row_at: str | None
    recent_errors: list[str]


def _url(a: str) -> str:
    return f"https://app.hyperliquid.xyz/explorer/address/{a}"


router = APIRouter(tags=["whales"])


@router.get("/whales/events", response_model=WhaleEventsOut)
def whale_events_list(symbol: Symbol | None = None, kind: Kind | None = None,
                      limit: int = Query(50, ge=1, le=200)) -> WhaleEventsOut:
    """Latest Whale Watch events: whale moves, fresh wallets and suspicious timing (a pattern, not an accusation)."""
    out, source = [], "memory"
    eng = None
    try:
        eng = db.get_engine()
    except sa.exc.OperationalError:
        pass
    if eng is not None:
        source = "database"
        q = sa.select(whale_events)
        if symbol:
            q = q.where(whale_events.c.symbol == symbol)
        if kind:
            q = q.where(whale_events.c.kind == kind)
        with eng.begin() as conn:
            rows = conn.execute(q.order_by(whale_events.c.ts.desc(), whale_events.c.id.desc()).limit(limit)).mappings()
            for r in rows:
                out.append(WhaleEvent(id=r["id"], ts=_iso(r["ts"]),
                                      symbol=r["symbol"], kind=r["kind"], address=r["address"], title=r["title"],
                                      reason=r["reason"], detail=r["detail"], explorer_url=_url(r["address"])))
    else:
        for e in watch.recent:
            if (symbol and e["symbol"] != symbol) or (kind and e["kind"] != kind):
                continue
            out.append(WhaleEvent(id=None, ts=e["ts"].isoformat(), symbol=e["symbol"], kind=e["kind"],
                                  address=e["address"], title=e["title"], reason=e["reason"], detail=e["detail"],
                                  explorer_url=_url(e["address"])))
            if len(out) >= limit:
                break
    return WhaleEventsOut(events=out, source=source,
                          thresholds={"whale_move_usd": rules.MOVE_USD, "fresh_wallet_usd": rules.FRESH_USD,
                                      "timing_usd": rules.TIMING_USD, "timing_hours": rules.TIMING_HOURS},
                          note=rules.PATTERN_NOTE)


@router.get("/whales/status", response_model=WhaleStatus)
def whale_status() -> WhaleStatus:
    return WhaleStatus(**watch.status())


def install(app: FastAPI) -> None:
    app.include_router(router)
