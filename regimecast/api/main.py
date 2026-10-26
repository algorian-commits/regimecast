"""RegimeCast backend API (FastAPI).

Run locally:  uvicorn regimecast.api.main:app --reload
Docs:         http://127.0.0.1:8000/docs   (Ryan: the typed client is generated
              from docs/openapi.json, which `python scripts/export_openapi.py` refreshes)

Endpoints that depend on models not built yet return HTTP 503 with
{"status": "not_ready"} so the front-end can show a placeholder.
"""
from __future__ import annotations

import json
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from regimecast import config
from regimecast.api import market
from regimecast.risk import fhs_live
from regimecast.risk import liquidation as liq
from regimecast.vol import live as vol_live


@asynccontextmanager
async def lifespan(app):
    vol_live.cache.start()   # HAR-S forecasts refresh in the background (vol/live.py)
    yield


app = FastAPI(title="RegimeCast API", version="0.1.0", lifespan=lifespan,
              description="Explainable crypto risk forecasting. Paper trading only. Not financial advice.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

Symbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]

# Used only if no forecast is available (e.g. the first minutes after a restart). Flagged in responses.
PLACEHOLDER_ANNUAL_VOL = {"BTCUSDT": 0.55, "ETHUSDT": 0.70, "SOLUSDT": 0.90}


# ------------------------------------------------------------------ schemas
class TradeIn(BaseModel):
    symbol: Symbol
    side: Literal["long", "short"]
    entry: float = Field(gt=0, description="Entry price in USDT")
    leverage: float = Field(gt=0, le=150, description="Binance allows up to 150x on BTC and ETH, 100x on SOL, less for big positions")
    qty: float | None = Field(None, gt=0, description="Size in coins. Give qty OR margin.")
    margin: float | None = Field(None, gt=0, description="Margin in USDT (isolated). Give qty OR margin.")
    margin_mode: Literal["isolated", "cross"] = "isolated"
    wallet_balance: float | None = Field(None, gt=0, description="Cross margin: whole cross wallet in USDT")
    stop: float | None = Field(None, gt=0)
    horizon_hours: Literal[1, 24] = 24
    risk_limit: float = Field(0.01, gt=0, lt=1, description="Max acceptable P(liquidation) for safe leverage")
    sigma_annual: float | None = Field(None, gt=0, description="Override volatility (annualised, e.g. 0.6)")

    @model_validator(mode="after")
    def _size(self):
        if (self.qty is None) == (self.margin is None):
            raise ValueError("give exactly one of qty or margin")
        if self.margin_mode == "cross" and self.wallet_balance is None:
            raise ValueError("cross margin needs wallet_balance")
        if self.stop is not None and (self.stop >= self.entry if self.side == "long" else self.stop <= self.entry):
            raise ValueError("stop must be below entry for a long and above entry for a short")
        return self


class StopInfo(BaseModel):
    stop: float
    stop_inside_liquidation: bool = Field(description="True if the stop would trigger AFTER liquidation")
    p_stop_hit: float
    loss_at_stop_usdt: float


class Driver(BaseModel):
    name: str = Field(description="e.g. 'volatility in the last day', 'day of week'")
    effect_pct: float = Field(description="How much it moves the volatility forecast vs a typical hour, in %")


class RiskOut(BaseModel):
    symbol: str
    side: str
    notional_usdt: float
    margin_usdt: float
    liquidation_price: float | None
    liquidation_distance_pct: float | None
    horizon_hours: int
    p_liquidation: float
    model: str
    sigma_horizon_pct: float
    volatility_source: str
    max_safe_leverage: float
    max_position_usdt: float
    fees_round_trip_usdt: float
    maintenance_margin_rate: float
    stop: StopInfo | None = None
    regime: str | None = None
    drivers: list[Driver] = Field([], description="What moves the volatility forecast (exact SHAP values of HAR-S)")
    explanation: list[str]
    warnings: list[str]
    generated_at: str


class NotReady(BaseModel):
    status: str = "not_ready"
    detail: str


def not_ready(detail: str) -> JSONResponse:
    """503 with a flat {"status": "not_ready", "detail": ...} body, as in the API contract."""
    return JSONResponse(NotReady(detail=detail).model_dump(), status_code=503)


# ------------------------------------------------------------------ helpers
def _latest_forecasts() -> dict:
    f = config.DATA_DIR / "forecasts" / "latest.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _forecast(symbol: str, horizon: int) -> dict | None:
    """A published model forecast (data/forecasts/latest.json, Phase 5) wins; else live HAR-S."""
    fc = _latest_forecasts().get(symbol, {}).get(f"{horizon}h")
    if fc and fc.get("variance"):
        return {"inputs_until": fc.get("made_at", "?"), **fc}
    return vol_live.cache.get(symbol, horizon)


FHS = fhs_live.load()


def _hit_model(symbol: str, side: str, horizon: int, source: str):
    """P(the mark price moves at least d against the position within the horizon).

    FHS (Phase 5's best calibrated model) when the volatility comes from live HAR-S, the
    pairing it was fitted on; otherwise the random-walk baseline."""
    entry = FHS.get(symbol, {}).get(f"{horizon}h")
    if entry and source.startswith("HAR-S"):
        return (lambda d, sigma: fhs_live.survival(entry[side], d, sigma)), "fhs_har_s"
    return liq.first_passage_prob, "first_passage_baseline"


def _drivers_line(drivers: list[dict]) -> str:
    top = [d for d in drivers if abs(d["effect_pct"]) >= 3][:3]
    if not top:
        return ""
    parts = [f"{d['name']} ({'+' if d['effect_pct'] > 0 else ''}{d['effect_pct']:.0f}%)" for d in top]
    return "What moves the volatility forecast vs a typical hour: " + ", ".join(parts) + "."


def _regime_line(rg: dict | None) -> str:
    if not rg:
        return ""
    typical = rg.get("typical_next_24h_vol_pct", {}).get(rg["regime"])
    tail = f"; after this regime the next 24 h have averaged {typical:.0f}% volatility a year" if typical else ""
    return f"Market regime now: {rg['regime']} (probability {100 * rg['p_' + rg['regime']]:.0f}%{tail})."


def _sigma(symbol: str, horizon: int, override: float | None) -> tuple[float, str]:
    """Volatility over the horizon (std of log return) and where it came from."""
    if override:
        return override * math.sqrt(horizon / config.HOURS_PER_YEAR), "user override"
    fc = _forecast(symbol, horizon)
    if fc:
        return math.sqrt(fc["variance"]), f"{fc['model']}, data to {fc['inputs_until'][:16].replace('T', ' ')} UTC"
    a = PLACEHOLDER_ANNUAL_VOL[symbol]
    return a * math.sqrt(horizon / config.HOURS_PER_YEAR), "PLACEHOLDER: no forecast available right now"


# ------------------------------------------------------------------ routes
@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat(), "version": app.version}


@app.get("/market/{symbol}", response_model=market.MarketOut, responses={503: {"model": NotReady}})
def market_now(symbol: Symbol):
    """Current mark price, funding and open interest from Binance (cached a few seconds)."""
    m = market.cache.get(symbol)
    return m if m else not_ready("live market data unavailable (Binance not reachable)")


@app.post("/risk-check", response_model=RiskOut)
def risk_check(t: TradeIn) -> RiskOut:
    qty = t.qty if t.qty is not None else t.margin * t.leverage / t.entry
    pos = liq.Position(symbol=t.symbol, side=t.side, entry=t.entry, qty=qty, leverage=t.leverage,
                       margin_mode=t.margin_mode, wallet_balance=t.wallet_balance, stop=t.stop)
    tier = liq.tier_for(t.symbol, pos.notional)
    warnings: list[str] = []
    if t.leverage > tier.max_leverage:
        warnings.append(f"Binance allows at most {tier.max_leverage}x for this position size.")

    sigma_h, source = _sigma(t.symbol, t.horizon_hours, t.sigma_annual)
    if source.startswith("PLACEHOLDER"):
        warnings.append("No volatility forecast is available right now, so a fixed placeholder is used.")

    lp = liq.liquidation_price(pos)
    dist = liq.adverse_log_distance(pos, lp)
    hit_prob, model_name = _hit_model(t.symbol, t.side, t.horizon_hours, source)
    p = hit_prob(dist, sigma_h)

    def prob_fn(p_: liq.Position) -> float:
        return hit_prob(liq.adverse_log_distance(p_, liq.liquidation_price(p_)), sigma_h)

    safe_lev = liq.max_safe_leverage(prob_fn, pos, t.risk_limit)
    margin = pos.margin_used()

    stop_info = None
    if t.stop:
        stop_dist = liq.adverse_log_distance(pos, t.stop)
        inside = stop_dist >= dist
        loss = abs(t.stop - t.entry) * qty + liq.fees_round_trip(pos)
        stop_info = StopInfo(stop=t.stop, stop_inside_liquidation=inside,
                             p_stop_hit=hit_prob(stop_dist, sigma_h),
                             loss_at_stop_usdt=round(min(loss, margin) if inside else loss, 2))
        if inside:
            warnings.append("Your stop is beyond the liquidation price: you would be liquidated first.")

    finite = math.isfinite(lp) and lp > 0
    move_pct = liq.adverse_move_pct(pos, lp)
    rg = vol_live.cache.get_regime(t.symbol)
    fc_drivers = (_forecast(t.symbol, t.horizon_hours) or {}).get("drivers", []) if source.startswith("HAR-S") else []
    expl = [
        f"Liquidation is triggered by the mark price at about {lp:,.2f} USDT." if finite
        else "This position cannot be liquidated at this margin.",
        f"That is a {move_pct:.2f}% adverse move from your entry." if finite else "",
        f"Expected volatility over {t.horizon_hours}h is about {100 * sigma_h:.2f}% ({source}).",
        f"Highest leverage keeping P(liquidation) under {100 * t.risk_limit:.1f}% is {safe_lev}x.",
        _regime_line(rg),
        _drivers_line(fc_drivers),
    ]
    return RiskOut(
        symbol=t.symbol, side=t.side, notional_usdt=round(pos.notional, 2), margin_usdt=round(margin, 2),
        liquidation_price=round(lp, 2) if finite else None,
        liquidation_distance_pct=round(move_pct, 3) if finite else None,
        horizon_hours=t.horizon_hours, p_liquidation=round(p, 5), model=model_name,
        sigma_horizon_pct=round(100 * sigma_h, 3), volatility_source=source,
        max_safe_leverage=safe_lev, max_position_usdt=round(safe_lev * pos.initial_margin, 2),
        fees_round_trip_usdt=round(liq.fees_round_trip(pos), 4), maintenance_margin_rate=tier.mmr,
        stop=stop_info, regime=rg["regime"] if rg else None, drivers=fc_drivers, explanation=[e for e in expl if e], warnings=warnings,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/forecast", responses={503: {"model": NotReady}})
def forecast(symbol: Symbol = "BTCUSDT", horizon_hours: Literal[1, 24] = 24):
    """Volatility forecast over the next 1 or 24 hours (HAR-S on live Binance data until Phase 5)."""
    fc = _forecast(symbol, horizon_hours)
    if not fc:
        return not_ready("no volatility forecast yet (the API refreshes every 10 minutes)")
    return {"symbol": symbol, "horizon_hours": horizon_hours, **fc}


@app.get("/regime", responses={503: {"model": NotReady}})
def regime(symbol: Symbol = "BTCUSDT"):
    """Market regime now (calm, active or stressed) from live Binance data (M4 HMM)."""
    rg = vol_live.cache.get_regime(symbol)
    if not rg:
        return not_ready("no regime yet (the API refreshes every 10 minutes)")
    return {"symbol": symbol, **rg}


class MoodHour(BaseModel):
    hour_end: str = Field(description="End of the hour (UTC). A headline at 10:20 counts for 11:00.")
    count: int = Field(description="Crypto headlines about this coin or the whole market")
    mean_score: float = Field(description="Average sentiment, -1 (negative) to +1 (positive); 0 when count is 0")
    share_negative: float = Field(description="Share of headlines labelled negative (0..1); 0 when count is 0")
    policy_shock_count: int = Field(description="Tariff, sanctions and regulation headlines")


class NewsMoodOut(BaseModel):
    symbol: str
    updated_at: str
    hours: list[MoodHour]


@app.get("/news-mood", response_model=NewsMoodOut, responses={503: {"model": NotReady}})
def news_mood(symbol: Symbol = "BTCUSDT", hours: int = Query(168, ge=1, le=24 * 90)):
    """Hourly news mood (M3), oldest hour first, ending at the last complete hour."""
    from regimecast.sentiment.pipeline import latest

    data = latest(symbol, hours)
    if not data:
        return not_ready("news mood not built yet: python -m regimecast.sentiment.pipeline collect")
    return data


@app.get("/track-record")
def track_record(symbol: Symbol = "BTCUSDT", limit: int = Query(500, ge=1, le=5000)):
    f = config.DATA_DIR / "track_record" / f"{symbol}.jsonl"
    if not f.exists():
        return {"symbol": symbol, "records": []}
    lines = f.read_text().strip().splitlines()[-limit:]
    return {"symbol": symbol, "records": [json.loads(x) for x in lines]}


@app.get("/data-health/{symbol}", responses={503: {"model": NotReady}})
def data_health(symbol: Symbol):
    f = config.DATA_DIR / "reports" / f"data_health_{symbol}.json"
    if not f.exists():
        return not_ready("run python -m regimecast.data.validate")
    return json.loads(f.read_text())
