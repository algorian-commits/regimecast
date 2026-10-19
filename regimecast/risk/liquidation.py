"""Liquidation price and liquidation probability for Binance USD-M perpetuals.

Key facts (from Binance docs):
* Liquidation is triggered by the MARK price, not the last traded price.
* Maintenance margin depends on position size ("leverage brackets"). Each tier
  has a maintenance margin rate (MMR) and a "maintenance amount" (cum).
* One-way mode, single position, liquidation price:

      LP = (WB - TMM + UPNL + cum - side * Q * EP) / (Q * MMR - side * Q)

  WB   = wallet balance used as margin (isolated: the position's margin;
         cross: the whole cross wallet)
  TMM  = maintenance margin of OTHER cross positions (0 for isolated)
  UPNL = unrealised PnL of OTHER cross positions (0 for isolated)
  side = +1 long, -1 short;  Q = size in coins;  EP = entry price.

Liquidation probability models
------------------------------
* ``first_passage_prob`` - simple random-walk formula 2*Phi(-b / (sigma*sqrt(T))).
  This is our baseline.
* ``FHSLiquidationModel`` - volatility-scaled filtered historical simulation on
  1-minute mark-price paths. This is our main model (see design decisions).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm


# ------------------------------------------------------------------ brackets
@dataclass(frozen=True)
class Tier:
    floor: float      # notional (USDT) where this tier starts
    cap: float        # notional where it ends
    mmr: float        # maintenance margin rate
    max_leverage: int
    cum: float        # maintenance amount


def build_tiers(rows: list[tuple[float, float, int]]) -> list[Tier]:
    """rows = [(notional_floor, mmr, max_leverage), ...] sorted by floor.
    The maintenance amount is computed so the margin is continuous between tiers:
    cum_n = cum_{n-1} + floor_n * (mmr_n - mmr_{n-1})."""
    tiers, cum = [], 0.0
    for i, (floor, mmr, lev) in enumerate(rows):
        if i:
            cum += floor * (mmr - rows[i - 1][1])
        cap = rows[i + 1][0] if i + 1 < len(rows) else float("inf")
        tiers.append(Tier(floor, cap, mmr, lev, cum))
    return tiers


# PLACEHOLDER brackets. Binance changes these often and the official endpoint
# (/fapi/v1/leverageBracket) needs an API key. Before any reported result, run
# scripts/fetch_brackets.py with a READ-ONLY key and use data/brackets.json.
DEFAULT_BRACKETS: dict[str, list[Tier]] = {
    "BTCUSDT": build_tiers([(0, 0.004, 125), (50_000, 0.005, 100), (600_000, 0.01, 75),
                            (3_000_000, 0.025, 50), (12_000_000, 0.05, 25), (70_000_000, 0.1, 20),
                            (100_000_000, 0.125, 10), (230_000_000, 0.15, 5), (480_000_000, 0.25, 4),
                            (600_000_000, 0.5, 1)]),
    "ETHUSDT": build_tiers([(0, 0.004, 125), (50_000, 0.005, 100), (600_000, 0.01, 75),
                            (3_000_000, 0.025, 50), (12_000_000, 0.05, 25), (50_000_000, 0.1, 20),
                            (65_000_000, 0.125, 10), (150_000_000, 0.15, 5), (320_000_000, 0.25, 4),
                            (400_000_000, 0.5, 1)]),
    "SOLUSDT": build_tiers([(0, 0.005, 75), (10_000, 0.01, 50), (50_000, 0.015, 40),
                            (250_000, 0.02, 25), (1_000_000, 0.05, 20), (5_000_000, 0.1, 10),
                            (10_000_000, 0.125, 5), (15_000_000, 0.15, 4), (25_000_000, 0.25, 2),
                            (50_000_000, 0.5, 1)]),
}


def load_brackets(path: str | Path | None = None) -> dict[str, list[Tier]]:
    """Load brackets saved from Binance's API (fetch_brackets.py); fall back to defaults."""
    p = Path(path) if path else None
    if not p or not p.exists():
        return DEFAULT_BRACKETS
    raw = json.loads(p.read_text())
    out = {}
    for item in raw:
        tiers = [Tier(b["notionalFloor"], b["notionalCap"], b["maintMarginRatio"],
                      b["initialLeverage"], b["cum"]) for b in item["brackets"]]
        out[item["symbol"]] = sorted(tiers, key=lambda t: t.floor)
    return out


def tier_for(symbol: str, notional: float, brackets: dict | None = None) -> Tier:
    tiers = (brackets or DEFAULT_BRACKETS)[symbol.upper()]
    for t in tiers:
        if t.floor <= notional < t.cap:
            return t
    return tiers[-1]


# ------------------------------------------------------------------ positions
@dataclass
class Position:
    symbol: str
    side: str                 # "long" or "short"
    entry: float              # entry price (USDT)
    qty: float                # size in coins (always positive)
    leverage: float
    margin_mode: str = "isolated"   # or "cross"
    wallet_balance: float | None = None   # cross only: whole cross wallet (USDT)
    other_mm: float = 0.0     # cross only: maintenance margin of other positions
    other_upnl: float = 0.0   # cross only: unrealised PnL of other positions
    stop: float | None = None
    taker_fee: float = 0.0005  # 0.05% per side (check your VIP level)
    brackets: dict | None = field(default=None, repr=False)

    @property
    def s(self) -> int:
        return 1 if self.side.lower() in ("long", "buy") else -1

    @property
    def notional(self) -> float:
        return self.qty * self.entry

    @property
    def initial_margin(self) -> float:
        return self.notional / self.leverage

    def margin_used(self) -> float:
        if self.margin_mode == "isolated":
            return self.initial_margin
        if self.wallet_balance is None:
            raise ValueError("cross margin needs wallet_balance")
        return self.wallet_balance


def liquidation_price(pos: Position, funding_paid: float = 0.0) -> float:
    """Binance one-way-mode liquidation price.

    ``funding_paid`` = funding (USDT) already paid out of this margin; positive
    numbers reduce margin and move the liquidation price closer.
    Returns 0 (long) or inf (short) if the position cannot be liquidated.
    """
    tier = tier_for(pos.symbol, pos.notional, pos.brackets)
    wb = pos.margin_used() - funding_paid
    tmm = pos.other_mm if pos.margin_mode == "cross" else 0.0
    upnl = pos.other_upnl if pos.margin_mode == "cross" else 0.0
    q, s = pos.qty, pos.s
    lp = (wb - tmm + upnl + tier.cum - s * q * pos.entry) / (q * tier.mmr - s * q)
    if s == 1:
        return max(lp, 0.0)
    return lp if lp > 0 else math.inf


def effective_leverage(pos: Position) -> float:
    """For cross margin: notional / margin actually backing the position."""
    return pos.notional / max(pos.margin_used() - pos.other_mm + pos.other_upnl, 1e-12)


def adverse_log_distance(pos: Position, price: float) -> float:
    """How far (log return) the mark price must move AGAINST the position to reach `price`."""
    if price <= 0 or math.isinf(price):
        return math.inf
    return math.log(pos.entry / price) if pos.s == 1 else math.log(price / pos.entry)


def fees_round_trip(pos: Position) -> float:
    return 2 * pos.taker_fee * pos.notional


# ------------------------------------------------------------------ probability: baseline
def first_passage_prob(distance: float, sigma_h: float) -> float:
    """P(driftless random walk touches a barrier `distance` log-units away within horizon).

    sigma_h = forecast volatility over the WHOLE horizon (std of log return),
    e.g. sqrt(forecast 24h realised variance).
    """
    if distance <= 0:
        return 1.0
    if not np.isfinite(distance) or sigma_h <= 0:
        return 0.0
    return float(min(1.0, 2.0 * norm.cdf(-distance / sigma_h)))


# ------------------------------------------------------------------ probability: main model
def max_adverse_excursion(mark_1m: pd.DataFrame, horizon_min: int) -> pd.DataFrame:
    """For each minute t: worst log move down (for longs) and up (for shorts)
    over the NEXT `horizon_min` minutes, using mark-price highs and lows.

    mark_1m needs columns ts, open, high, low, close (1-minute mark-price klines).
    The entry is taken at the close of minute t. Missing minutes stay missing
    (no forward fill), so any window with a gap gives NaN and is dropped instead
    of silently covering more than `horizon_min` minutes.
    """
    df = mark_1m.drop_duplicates("ts", keep="last").set_index("ts").sort_index()
    df = df.reindex(pd.date_range(df.index[0], df.index[-1], freq="1min"))
    # future window = minutes t+1 ... t+h  ->  reverse rolling trick
    fut_low = df["low"][::-1].rolling(horizon_min, min_periods=horizon_min).min()[::-1].shift(-1)
    fut_high = df["high"][::-1].rolling(horizon_min, min_periods=horizon_min).max()[::-1].shift(-1)
    out = pd.DataFrame(index=df.index)
    out["mae_long"] = np.log(df["close"] / fut_low).clip(lower=0)
    out["mae_short"] = np.log(fut_high / df["close"]).clip(lower=0)
    return out.dropna()


class FHSLiquidationModel:
    """Volatility-scaled filtered historical simulation.

    Idea: worst moves scale with volatility. So we store
        z = MAE / sigma_forecast
    from history. For a new trade with liquidation distance d and current
    forecast sigma, P(liq) = share of historical z that are >= d / sigma.
    Optional: keep a separate z-sample for each regime.
    """

    def __init__(self, horizon_hours: int):
        self.h = horizon_hours
        self.z: dict[tuple[str, object], np.ndarray] = {}

    def fit(self, mae: pd.DataFrame, sigma: pd.Series, regime: pd.Series | None = None,
            step_minutes: int = 60) -> "FHSLiquidationModel":
        """mae: output of max_adverse_excursion. sigma: forecast horizon vol made at
        each origin (index = ts, must only use data up to that ts)."""
        df = mae.join(sigma.rename("sigma"), how="inner")
        if regime is not None:
            df = df.join(regime.rename("regime"), how="inner")
        else:
            df["regime"] = "all"
        df = df[df.index.minute % step_minutes == 0] if step_minutes > 1 else df
        df = df[df["sigma"] > 0]
        for side in ("long", "short"):
            zs = df[f"mae_{side}"] / df["sigma"]
            self.z[(side, "all")] = np.sort(zs.to_numpy())
            for r, part in zs.groupby(df["regime"]):
                if len(part) >= 200:  # need enough history per regime
                    self.z[(side, r)] = np.sort(part.to_numpy())
        return self

    def prob(self, side: str, distance: float, sigma_h: float, regime=None) -> float:
        side = "long" if side.lower() in ("long", "buy") else "short"
        if distance <= 0:
            return 1.0
        if not np.isfinite(distance) or sigma_h <= 0:
            return 0.0
        z = self.z.get((side, regime)) if regime is not None else None
        if z is None:
            z = self.z[(side, "all")]
        thr = distance / sigma_h
        n = len(z)
        k = n - np.searchsorted(z, thr, side="left")
        return float((k + 0.5) / (n + 1))  # small smoothing: never exactly 0 or 1


# ------------------------------------------------------------------ leverage search
def max_safe_leverage(prob_fn, pos: Position, limit: float, lo: float = 1.0, hi: float | None = None,
                      tol: float = 0.05) -> float:
    """Highest leverage with P(liquidation) <= limit, found by bisection.

    prob_fn(position) -> probability. Margin (isolated) is kept as the user's
    margin; we vary leverage by changing the position size. A leverage that
    Binance would not allow for the resulting notional (bracket max leverage)
    counts as unsafe, so the answer is always a leverage the user can set.
    """
    tiers = (pos.brackets or DEFAULT_BRACKETS)[pos.symbol.upper()]
    hi = hi or float(tiers[0].max_leverage)
    margin = pos.initial_margin

    def at(lev: float) -> float:
        p = Position(**{**pos.__dict__, "leverage": lev, "qty": margin * lev / pos.entry})
        if lev > tier_for(p.symbol, p.notional, p.brackets).max_leverage:
            return 1.0
        return prob_fn(p)

    if at(lo) > limit:
        return 0.0
    if at(hi) <= limit:
        return hi
    while hi - lo > tol:
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if at(mid) <= limit else (lo, mid)
    return round(lo, 2)
