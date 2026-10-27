"""Crash replay endpoints (M12): regimecast/replay/.

    GET /replay/events                 the crashes that can be replayed
    GET /replay/{event_id}?symbol=...  minute-by-minute frames for a chosen position

The replay files ship with the code (regimecast/replay/events/), like the model files,
because the deployed API has no copy of the stored archive.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from regimecast.replay import engine

Symbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]


class ReplayEvent(BaseModel):
    id: str
    title: str
    summary: str
    start: str
    end: str
    available: bool = Field(description="False until its replay file has been generated (scripts/make_replay.py)")


class ReplayHour(BaseModel):
    t: str = Field(description="Hour END (UTC): these values were known from this time on")
    regime: str | None
    p_stressed: float | None
    p_stress: float | None = Field(description="Stress gauge: P(stress event in the next hour), app's frozen model")
    stress_level: str | None
    stress_drivers: list[list[str]] = Field(description="Top drivers of the stress gauge: [name, up|down]")
    p_stress_walk_forward: float | None = Field(
        description="The same hour from Phase 6's walk-forward run (models fitted on earlier data only)")
    sigma_pct: float | None = Field(description="Volatility forecast over the horizon (std of log return, %)")
    safe_leverage: float | None = Field(description="Risk Check's highest safe leverage for a new trade at this hour")


class ReplayMinutes(BaseModel):
    price: list[float | None] = Field(description="Mark price at each minute's close")
    low: list[float | None]
    high: list[float | None]
    p_liquidation: list[float | None] = Field(description="Chance the position is liquidated within the horizon")
    distance_pct: list[float | None] = Field(description="Move still needed to reach liquidation, % of price")
    hour: list[int] = Field(description="Index into hours: the last complete hour at this minute (-1: none yet)")


class ReplayOut(BaseModel):
    event: dict
    symbol: str
    side: str
    leverage: float
    entry: float
    margin: float
    horizon_hours: int
    risk_limit: float
    liquidation_price: float | None
    liquidated_at: str | None = Field(description="First minute close at which the mark price reached liquidation")
    first_close: str = Field(description="Close time of the first minute; minute i closes i minutes later")
    n_minutes: int
    minutes: ReplayMinutes
    hours: list[ReplayHour]
    stress_base_rate: float | None


router = APIRouter(tags=["replay"])


@router.get("/replay/events", response_model=list[ReplayEvent])
def replay_events() -> list[ReplayEvent]:
    """Crashes that can be replayed. The October 2025 crash is listed but not available until
    the final evaluation has run (it is inside the final test period)."""
    return [ReplayEvent(**e) for e in engine.available()]


@router.get("/replay/{event_id}", response_model=ReplayOut, responses={404: {}})
def replay(event_id: str, symbol: Symbol = "BTCUSDT", side: Literal["long", "short"] = "long",
           leverage: float = Query(10, gt=0, le=100), entry: float | None = Query(None, gt=0,
                                                                                 description="Default: first price"),
           horizon_hours: Literal["1", "24"] = "24", risk_limit: float = Query(0.01, ge=0.0005, le=0.5),
           margin: float = Query(engine.DEFAULT_MARGIN, gt=0, le=1e7)) -> ReplayOut:
    """Every minute of the crash as the dashboard and the Risk Check would have shown it, for a
    position opened at the start (isolated margin). Frozen models only: nothing is fitted."""
    r = engine.load(event_id)
    if r is None:
        raise HTTPException(404, "No replay for this event yet.")
    if symbol not in r["coins"]:
        raise HTTPException(404, f"This replay has no {symbol}.")
    return ReplayOut(**engine.frames(r, symbol, side, leverage, entry, int(horizon_hours), risk_limit, margin))


def install(app: FastAPI) -> None:
    app.include_router(router)
