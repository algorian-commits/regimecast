"""The verdict as the API returns it (part of POST /risk-check's answer)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ReasonOut(BaseModel):
    text: str
    good: bool = Field(description="True for a point in the trade's favour")
    points: int = Field(description="Penalty points this reason adds to the grade")


class BreakEvenOut(BaseModel):
    win_rate: float = Field(description="Share of trades that must reach the target to break even after costs")
    win_usdt: float
    loss_usdt: float
    reward_to_risk: float
    text: str


class WhatIfOut(BaseModel):
    key: str = Field(description="normal, bad, very_bad or crash")
    label: str
    move_pct: float = Field(description="Price change at the day's worst point, in % (negative = falls)")
    pnl_usdt: float = Field(description="Gain (+) or loss (-) in USDT, after fees")
    outcome: str = Field(description="gain, loss, stopped out or liquidated")


class FixOut(BaseModel):
    leverage: float
    qty: float
    notional_usdt: float
    margin_usdt: float
    stop: float | None
    liquidation_price: float | None
    p_liquidation_24h: float
    p_stop_24h: float | None
    loss_at_stop_usdt: float | None
    grade: str
    headline: str
    points: int
    changes: list[str] = Field(description="What changed, in plain words")


class CrowdOut(BaseModel):
    side: str | None = Field(None, description="long or short when the crowd warning is on; None otherwise")
    text: str
    after_warning: float | None = Field(None, description="Past chance of a 5% move against the crowd within 24 h "
                                                          "after a warning")
    normally: float | None = Field(None, description="The same chance in hours without a warning")
    funding_rank: float | None = None
    ls_top_rank: float | None = None
    oi_growth_rank: float | None = None


class SimilarExampleOut(BaseModel):
    time: str
    next_24h_move_pct: float


class SimilarOut(BaseModel):
    n: int
    range_lo_pct: float = Field(description="10th percentile of the next 24 h move, in %")
    range_hi_pct: float = Field(description="90th percentile of the next 24 h move, in %")
    typical_move_pct: float
    p_up: float
    coin_flip: bool
    text: list[str]
    examples: list[SimilarExampleOut]
    pool_until: str = Field(description="The most recent past hour the comparison can use")


class VerdictOut(BaseModel):
    grade: str = Field(description="A (well built) to F")
    headline: str
    points: int
    leverage: float = Field(description="The trade's leverage, as asked")
    reasons: list[ReasonOut] = Field(description="The three plain reasons shown first")
    all_reasons: list[ReasonOut]
    p_liquidation_24h: float
    p_stop_24h: float | None
    knockout_24h: float = Field(description="Chance the trade is ended within a day by the stop or liquidation")
    break_even: BreakEvenOut | None = None
    break_even_hint: str | None = None
    fix: FixOut | None = None
    fix_note: str
    what_if: list[WhatIfOut]
    crowd: CrowdOut | None = None
    similar: SimilarOut | None = None
    timing: list[str] = Field(description="Bad-timing warnings: macro events, weekend or thin hours, rising stress")
    notes: list[str]
    frozen_at: str | None = None
