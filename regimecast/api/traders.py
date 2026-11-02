"""Popular trader tracker endpoints (PLAN 13D): regimecast/traders/.

    GET /traders/scorecards?label=&limit=&offset=   track record and skill label per account
    GET /traders/skilled                            what the skilled traders hold and did, net per coin
    GET /traders/liquidation-map/{symbol}           liquidation prices of tracked accounts, per price band
    GET /traders/copy-test                          the copy test's status or result, and what the page may say
    GET /traders/events?symbol=&kind=&limit=        skilled-trader and liquidation-cluster alerts
    GET /traders/status                             whether the live position feed is running

Scorecards and the copy test are results files shipped with the code; positions come live from
Hyperliquid's public data. Every number carries its sample size. A user who wants the events in the
inbox adds a ``trader_watch`` alert for the coin.
"""
from __future__ import annotations

import math
from typing import Any, Literal

import sqlalchemy as sa
from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from regimecast import db
from regimecast.api.accounts import _iso
from regimecast.db.schema import whale_events
from regimecast.traders import liqmap, published, rules
from regimecast.traders.live import watch

Symbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]
Label = Literal["skilled", "unproven", "losing"]
EventKind = Literal["skilled_trader", "liq_cluster"]


class CoinShare(BaseModel):
    coin: str
    share: float = Field(description="Share of the account's traded value in this coin (0..1)")


class Scorecard(BaseModel):
    address: str
    display_name: str = ""
    account_value: float | None = Field(description="Account value in USD when the pool was selected")
    label: Label
    tested: bool = Field(description="False: too little history for the skill test (needs 30+ days and 10+ trades)")
    p_value: float | None = Field(description="Skill test p-value before the correction across the pool")
    skill_score: float | None = Field(description="Mean daily PnL / its bootstrap standard error")
    n_fills: int
    n_trades: int = Field(description="Finished round trips that started inside the history")
    n_days: int = Field(description="Days in the daily PnL series")
    first_day: str | None
    last_fill: str | None
    net_pnl: float | None = Field(description="Realised PnL minus fees over the history, USD")
    win_rate: float | None = Field(description="Share of finished trades with a profit after fees (0..1)")
    profit_factor: float | None = Field(description="Total won / total lost; null if nothing was lost or won")
    return_on_account: float | None = Field(description="Net PnL / today's account value (0.5 = 50%)")
    sharpe: float | None = Field(description="Mean / standard deviation of daily PnL, annualised by sqrt(365)")
    max_drawdown: float | None = Field(description="Largest fall of cumulative PnL from a peak, USD")
    max_drawdown_of_account: float | None = Field(description="max_drawdown / today's account value")
    avg_leverage: float | None = Field(description="Estimate: average peak trade size / today's account value")
    median_hold_hours: float | None
    coins: list[CoinShare]
    history_cut_short: bool = Field(description="True: Hyperliquid no longer serves this account's older fills")


class ScorecardSummary(BaseModel):
    generated_at: str
    pool_size: int = Field(description="Accounts in the pool (Whale Watch's plus every account above $1M)")
    downloaded: int = Field(description="Accounts whose fills were downloaded")
    accounts_with_perp_fills: int = Field(description="The number of tests the correction runs over")
    counts: dict[str, int]
    fdr: float = Field(description="False discovery rate of the Benjamini-Hochberg correction")
    limits: list[str]


class ScorecardsOut(BaseModel):
    summary: ScorecardSummary
    total: int = Field(description="Cards matching the filter, before limit and offset")
    cards: list[Scorecard]
    headline: str = Field(description="What the page may say about following them")


class Position(BaseModel):
    coin: str
    side: Literal["long", "short"]
    size: float = Field(description="Size in coins (negative = short)")
    position_usd: float
    leverage: float | None
    leverage_type: str
    entry: float | None
    liquidation: float | None = Field(description="Hyperliquid's liquidation price; null when it has none")
    unrealized_pnl: float | None
    read_at: str | None


class SkilledTrader(BaseModel):
    card: Scorecard
    positions: list[Position] = Field(description="Open BTC, ETH and SOL positions")
    read_at: str | None = Field(description="When this account was last read; null if not yet")


class Trade(BaseModel):
    address: str
    ts: str
    coin: str
    side: Literal["buy", "sell"]
    action: str = Field(description="Hyperliquid's own words, e.g. 'Open Long'")
    qty: float
    price: float | None
    usd: float
    closed_pnl: float = Field(description="Realised PnL of this trade minus fees, USD")
    n_fills: int


class NetCoin(BaseModel):
    coin: str
    long_usd: float
    short_usd: float
    net_usd: float
    n_long: int
    n_short: int
    n_flat: int
    n_read: int = Field(description="Skilled accounts read so far: the sample size of this line")


class SkilledOut(BaseModel):
    traders: list[SkilledTrader]
    net: list[NetCoin]
    trades: list[Trade] = Field(description="Recent BTC, ETH and SOL trades, newest first")
    n_skilled: int
    bh_family: int = Field(description="Accounts the skill correction ran over")
    headline: str
    live: bool = Field(description="False: the live position feed is not running on this server")


class Band(BaseModel):
    from_pct: float = Field(description="Band start, % from the current price")
    to_pct: float
    price_low: float
    price_high: float
    long_usd: float = Field(description="Longs liquidated in this band, USD")
    short_usd: float = Field(description="Shorts liquidated in this band, USD")
    n_long: int
    n_short: int


class LiquidationMap(BaseModel):
    symbol: str
    coin: str
    mark: float
    band_pct: float
    span_pct: float
    bands: list[Band] = Field(description="Lowest price first; empty bands are left out")
    n_positions: int
    n_accounts: int
    outside_span_usd: float = Field(description="Positions liquidated further away than the span, USD")
    no_liquidation_price_usd: float
    n_no_liquidation_price: int
    accounts_read: int = Field(description="Tracked accounts read so far")
    accounts_followed: int
    oldest_read_at: str | None
    newest_read_at: str | None
    marks_at: str | None
    label: str


class CopyTestOut(BaseModel):
    status: Literal["not_run", "pending_forward", "passed", "failed"]
    worked: bool = Field(description="True only when the test ran and passed; the page may then say it has worked")
    headline: str
    plain: str = Field(description="The status in plain words")
    gate: dict[str, Any] | None = Field(None, description="The history check that chose the kind of test")
    result: dict[str, Any] | None = Field(None, description="Sharpe ratios, interval and the pass or fail of each part")
    forward: dict[str, Any] | None = Field(None, description="Forward test: start, end and where its log is")


class TraderEvent(BaseModel):
    id: int | None
    ts: str
    symbol: str
    kind: EventKind
    address: str = Field(description="Empty for a liquidation cluster, which is many accounts")
    title: str
    reason: str = Field(description="Why it fired, in plain words")
    detail: dict[str, Any] | None
    explorer_url: str | None


class TraderEventsOut(BaseModel):
    events: list[TraderEvent] = Field(description="Newest first")
    source: Literal["database", "memory"]
    thresholds: dict[str, float]


class TraderStatus(BaseModel):
    running: bool
    accounts_followed: int
    skilled: int
    accounts_read: int
    sweeps_done: int
    sweep_seconds: float | None
    marks_at: str | None
    recent_errors: list[str]


class NotReadyOut(BaseModel):
    status: str = "not_ready"
    detail: str


def _not_ready(detail: str) -> JSONResponse:
    return JSONResponse(NotReadyOut(detail=detail).model_dump(), status_code=503)


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and math.isfinite(x) else None


def _url(a: str) -> str | None:
    return f"https://app.hyperliquid.xyz/explorer/address/{a}" if a else None


def _card(c: dict) -> Scorecard:
    return Scorecard(
        address=c["address"], display_name=c.get("display_name") or "", account_value=_num(c.get("account_value")),
        label=c["label"], tested=bool(c.get("tested")), p_value=_num(c.get("p")), skill_score=_num(c.get("score")),
        n_fills=c.get("n_fills") or 0, n_trades=c.get("n_trades") or 0, n_days=c.get("n_days") or 0,
        first_day=c.get("first_day"), last_fill=c.get("last_fill"), net_pnl=_num(c.get("net_pnl")),
        win_rate=_num(c.get("win_rate")), profit_factor=_num(c.get("profit_factor")),
        return_on_account=_num(c.get("return_on_account")), sharpe=_num(c.get("sharpe")),
        max_drawdown=_num(c.get("max_drawdown")), max_drawdown_of_account=_num(c.get("max_drawdown_of_account")),
        avg_leverage=_num(c.get("avg_leverage")), median_hold_hours=_num(c.get("median_hold_hours")),
        coins=c.get("coins") or [], history_cut_short=bool(c.get("truncated")))


def _position(r: dict) -> Position:
    return Position(coin=r["coin"], side="long" if r["size"] > 0 else "short", size=r["size"],
                    position_usd=abs(_num(r.get("position_usd")) or 0.0), leverage=_num(r.get("leverage")),
                    leverage_type=r.get("leverage_type") or "", entry=_num(r.get("entry")),
                    liquidation=_num(r.get("liquidation")), unrealized_pnl=_num(r.get("unrealized_pnl")),
                    read_at=r.get("read_at"))


def net_positions(skilled: list[str], positions: list[dict], read: set[str], coins: list[str]) -> list[dict]:
    """Net dollars long minus short of the skilled accounts per coin, with how many were counted."""
    want, counted = set(skilled), [a for a in skilled if a in read]
    out = []
    for c in coins:
        rows = [p for p in positions if p["coin"] == c and p["address"] in want and p.get("size")]
        long_usd = sum(abs(p.get("position_usd") or 0.0) for p in rows if p["size"] > 0)
        short_usd = sum(abs(p.get("position_usd") or 0.0) for p in rows if p["size"] < 0)
        n_long, n_short = sum(p["size"] > 0 for p in rows), sum(p["size"] < 0 for p in rows)
        out.append({"coin": c, "long_usd": long_usd, "short_usd": short_usd, "net_usd": long_usd - short_usd,
                    "n_long": int(n_long), "n_short": int(n_short), "n_flat": len(counted) - int(n_long) - int(n_short),
                    "n_read": len(counted)})
    return out


router = APIRouter(tags=["traders"])
NOT_READY = {503: {"model": NotReadyOut}}


@router.get("/traders/scorecards", response_model=ScorecardsOut, responses=NOT_READY)
def scorecards(label: Label | None = None, limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    """Each account's track record and skill label, best skill score first. Descriptive: no pass mark."""
    data = published.scorecards()
    if not data:
        return _not_ready("scorecards not built yet: scripts/traders_run.py score")
    rows = [c for c in data["cards"] if c["label"] != "no_trades" and (label is None or c["label"] == label)]
    return ScorecardsOut(summary=ScorecardSummary(**data["summary"]), total=len(rows),
                         cards=[_card(c) for c in rows[offset:offset + limit]],
                         headline=published.headline(published.copy_test())["sentence"])


@router.get("/traders/skilled", response_model=SkilledOut, responses=NOT_READY)
def skilled():
    """What the skilled traders hold now, their recent trades, and their net long or short per coin."""
    data = published.scorecards()
    if not data:
        return _not_ready("scorecards not built yet: scripts/traders_run.py score")
    from regimecast.traders.live import COINS

    cards = [c for c in data["cards"] if c["label"] == "skilled"]
    addresses = [c["address"] for c in cards]
    want = set(addresses)
    positions = [p for p in watch.positions() if p["address"] in want]
    read = {a for a in addresses if a in watch.read_at}
    trades = sorted((t for a in addresses for t in watch.trades.get(a, [])), key=lambda t: t["ts"], reverse=True)[:50]
    traders = [SkilledTrader(card=_card(c), read_at=watch.read_at.get(c["address"]),
                             positions=[_position(p) for p in positions if p["address"] == c["address"]])
               for c in cards]
    return SkilledOut(traders=traders, net=[NetCoin(**n) for n in net_positions(addresses, positions, read, COINS)],
                      trades=[Trade(**t) for t in trades], n_skilled=len(cards),
                      bh_family=data["summary"]["accounts_with_perp_fills"],
                      headline=published.headline(published.copy_test())["sentence"],
                      live=watch.status()["running"])


@router.get("/traders/liquidation-map/{symbol}", response_model=LiquidationMap, responses=NOT_READY)
def liquidation_map(symbol: Symbol, band_pct: float = Query(liqmap.BAND_PCT, ge=0.25, le=5)):
    """Dollars of tracked longs and shorts that would be liquidated per price band near the current price."""
    coin = symbol.removesuffix("USDT")
    mark = watch.marks.get(coin)
    if not mark:
        return _not_ready("the live position feed has not read the market yet")
    m = liqmap.build(watch.positions(coin), coin, mark, band_pct)
    times = sorted(watch.read_at.values())
    return LiquidationMap(symbol=symbol, accounts_read=len(times), accounts_followed=len(watch.cards),
                          oldest_read_at=times[0] if times else None, newest_read_at=times[-1] if times else None,
                          marks_at=watch.marks_at, **m)


@router.get("/traders/copy-test", response_model=CopyTestOut)
def copy_test() -> CopyTestOut:
    """Would copying the skilled traders have made money? Pre-registered; see docs/PLAN.md, 13D."""
    r = published.copy_test()
    h = published.headline(r)
    if not r:
        return CopyTestOut(status="not_run", worked=False, headline=h["sentence"],
                           plain="The copy test has not run yet.")
    return CopyTestOut(status=r["status"], worked=h["worked"], headline=h["sentence"], plain=r.get("plain", ""),
                       gate=r.get("gate"), result=r.get("result"), forward=r.get("forward"))


@router.get("/traders/events", response_model=TraderEventsOut)
def events(symbol: Symbol | None = None, kind: EventKind | None = None,
           limit: int = Query(50, ge=1, le=200)) -> TraderEventsOut:
    """Latest alerts: a skilled trader opened or closed a large position; price near a liquidation cluster."""
    out, source = [], "memory"
    eng = None
    try:
        eng = db.get_engine()
    except sa.exc.OperationalError:
        pass
    if eng is not None:
        source = "database"
        q = sa.select(whale_events).where(whale_events.c.kind.in_(rules.EVENT_KINDS))
        if symbol:
            q = q.where(whale_events.c.symbol == symbol)
        if kind:
            q = q.where(whale_events.c.kind == kind)
        with eng.begin() as conn:
            for r in conn.execute(q.order_by(whale_events.c.ts.desc(), whale_events.c.id.desc()).limit(limit)).mappings():
                out.append(TraderEvent(id=r["id"], ts=_iso(r["ts"]), symbol=r["symbol"], kind=r["kind"],
                                       address=r["address"], title=r["title"], reason=r["reason"], detail=r["detail"],
                                       explorer_url=_url(r["address"])))
    else:
        for e in watch.recent:
            if (symbol and e["symbol"] != symbol) or (kind and e["kind"] != kind):
                continue
            out.append(TraderEvent(id=None, ts=e["ts"].isoformat(), symbol=e["symbol"], kind=e["kind"],
                                   address=e["address"], title=e["title"], reason=e["reason"], detail=e["detail"],
                                   explorer_url=_url(e["address"])))
            if len(out) >= limit:
                break
    return TraderEventsOut(events=out, source=source,
                           thresholds={"move_usd": rules.MOVE_USD, "cluster_usd": rules.CLUSTER_USD,
                                       "near_pct": rules.NEAR_PCT})


@router.get("/traders/status", response_model=TraderStatus)
def status() -> TraderStatus:
    return TraderStatus(**watch.status())


def install(app: FastAPI) -> None:
    app.include_router(router)
