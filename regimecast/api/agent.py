"""GET /agent (M10 part 2): the paper-trading risk agent.

* ``live``: the hourly paper log the frozen agent writes on GitHub Actions (regimecast/agent/live.py),
  read from the data branch ``agent-data`` (``api/latest.json``), cached for 5 minutes; a copy in
  ``data/agent/api/latest.json`` (local runs) wins. If a fetch fails, the last good copy is served.
* ``backtest``: the development-period results the settings were frozen on
  (``regimecast/agent/dev_summary.json``, written by scripts/run_phase9.py and shipped with the code).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from regimecast import config

router = APIRouter()

AGENT_DATA_URL = os.environ.get(
    "AGENT_DATA_URL", "https://raw.githubusercontent.com/algorian-commits/regimecast/agent-data/api/latest.json")
DEV_SUMMARY = Path(__file__).resolve().parents[1] / "agent" / "dev_summary.json"
REMOTE_TTL_S = 300
_cache: dict[str, tuple[float, dict]] = {}


class AgentSettings(BaseModel):
    target_vol: float = Field(description="Annualised volatility each position is sized to")
    stress_elevated: float = Field(description="P(stress next hour) at which exposure is halved (2 = never)")
    stress_high: float = Field(description="P(stress next hour) at which exposure goes to zero (2 = never)")
    regime_mult: float = Field(description="Share of the exposure kept when the regime is stressed")
    band: float = Field(description="No-trade band: smaller changes of exposure are skipped")


class CurvePoint(BaseModel):
    hour: str
    agent: float = Field(description="Paper account value (start = 1)")
    buy_and_hold: float
    exposure: float = Field(description="Average exposure over the three coins (0..1)")


class CoinDecision(BaseModel):
    mark: float | None
    vol_ann: float | None = Field(None, description="24 h volatility forecast, annualised (0.5 = 50%)")
    p_stress: float | None = None
    regime: int | None = Field(None, description="0 calm, 1 active, 2 stressed")
    exposure_before: float
    target: float | None
    exposure: float = Field(description="Position after the decision, share of the coin's third (0..1)")
    traded: float = Field(description="Notional bought (+) or sold (-), in account units")
    reasons: list[str]


class Decision(BaseModel):
    hour: str = Field(description="Hour end the decision is for (positions held until the next hour end)")
    decided_at: str = Field(description="When it was written, before the hour played out")
    coins: dict[str, CoinDecision]
    fee: float
    equity: float
    bh_equity: float
    total_exposure: float


class AgentLive(BaseModel):
    updated_at: str
    started_at: str | None
    hours_logged: int
    settings: AgentSettings
    frozen_at: str
    equity: float
    bh_equity: float
    curve: list[CurvePoint]
    latest: Decision | None


class BacktestRow(BaseModel):
    period: str
    strategy: str
    total_return: float
    annual_return: float
    sharpe: float
    max_drawdown: float
    calmar: float | None
    turnover_per_year: float
    fees_paid: float
    funding_paid: float
    avg_exposure: float


class EquityDay(BaseModel):
    day: str
    agent: float
    buy_and_hold: float
    vol_target: float


class AgentBacktest(BaseModel):
    frozen_at: str
    settings: AgentSettings
    settings_tried: int
    chosen_on: str
    checked_on: str
    results: list[BacktestRow]
    equity: list[EquityDay]


class AgentOut(BaseModel):
    live: AgentLive | None = Field(description="The live paper log; null until its first hour is published")
    backtest: AgentBacktest | None


def _live() -> dict | None:
    local = config.DATA_DIR / "agent" / "api" / "latest.json"
    if local.exists():
        return json.loads(local.read_text())
    hit = _cache.get("live")
    if hit and time.time() - hit[0] < REMOTE_TTL_S:
        return hit[1]
    try:
        r = httpx.get(AGENT_DATA_URL, timeout=10, follow_redirects=True)
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, dict) or "curve" not in data:
            raise ValueError("unexpected agent file")
    except (httpx.HTTPError, ValueError):
        return hit[1] if hit else None
    _cache["live"] = (time.time(), data)
    return data


@router.get("/agent", response_model=AgentOut, responses={503: {"description": "Nothing published yet"}})
def agent() -> AgentOut | JSONResponse:
    """The paper-trading risk agent: live hourly decisions with their reasons, the paper equity vs buy-and-hold,
    and the development-period backtest its settings were frozen on. Paper trading only; not advice."""
    live = _live()
    back = json.loads(DEV_SUMMARY.read_text()) if DEV_SUMMARY.exists() else None
    if live is None and back is None:
        return JSONResponse({"status": "not_ready", "detail": "the paper agent has not published anything yet"},
                            status_code=503)
    return AgentOut(live=live, backtest=back)
