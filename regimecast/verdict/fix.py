"""Fix my trade: the closest safer version of the same trade (same coin, side, entry and margin).

Tried: every leverage on LEVERAGE_STEPS at or below the user's (same margin, so a lower leverage is a smaller
position) with every stop at or beyond max(the user's stop, one typical move): the user's own distance and
STOP_MOVES typical moves. A version qualifies when
  * its chance of liquidation is within the user's limit,
  * the stop triggers well before liquidation (at most STOP_BUFFER of the way there), and
  * the most it can lose (at the stop, plus fees) is no more than the original's (its stop, or its margin).
Among those reaching grade B (or the best grade any of them reaches) it picks the smallest change: the sum of
|log(new / old)| over leverage and stop distance. The original counts as fine, with no fix, when it qualifies,
its stop is outside normal noise and its grade is B or better.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

LEVERAGE_STEPS = (125, 100, 75, 50, 40, 30, 25, 20, 15, 12, 10, 8, 7, 6, 5, 4, 3, 2, 1)
STOP_MOVES = (1.0, 1.25, 1.5, 2.0, 2.5, 3.0)     # stop distances tried, in typical moves
NOISE_MOVES = 1.0                                # a stop closer than one typical move sits inside normal noise
STOP_BUFFER = 0.8
TARGET = 1                                       # grade B


@dataclass
class Option:
    leverage: float
    stop_distance: float | None       # log distance from entry; None = no stop
    grade_index: int
    p_liquidation: float
    liquidation_distance: float       # log distance; inf if it cannot be liquidated
    max_loss: float                   # USDT: at the stop plus fees, else the liquidation loss

    @property
    def stop_ok(self) -> bool:
        return self.stop_distance is not None and self.stop_distance <= STOP_BUFFER * self.liquidation_distance


def _change(a: Option, b: Option, ref_stop: float) -> float:
    return abs(math.log(b.leverage / a.leverage)) + abs(math.log((b.stop_distance or ref_stop) / ref_stop))


def candidates(leverage: float, stop_distance: float | None, sigma: float) -> list[tuple[float, float]]:
    """(leverage, stop distance) pairs to try."""
    floor = max(stop_distance or 0.0, NOISE_MOVES * sigma)
    stops = sorted({floor, *(k * sigma for k in STOP_MOVES if k * sigma >= floor)})
    levs = [leverage, *(x for x in LEVERAGE_STEPS if x < leverage)]
    return [(lev, d) for lev in levs for d in stops]


def choose(original: Option, options: list[Option], risk_limit: float, sigma: float) -> Option | None:
    """The fix, or None when the original needs none (or nothing qualifies)."""
    def ok(o: Option) -> bool:
        return o.p_liquidation <= risk_limit and o.stop_ok and o.max_loss <= original.max_loss * (1 + 1e-9)

    noisy = original.stop_distance is None or original.stop_distance < NOISE_MOVES * sigma
    if ok(original) and not noisy and original.grade_index <= TARGET:
        return None
    good = [o for o in options if ok(o)]
    if not good:
        return None
    goal = max(TARGET, min(o.grade_index for o in good))
    ref = original.stop_distance or NOISE_MOVES * sigma
    best = min((o for o in good if o.grade_index <= goal), key=lambda o: (_change(original, o, ref), -o.leverage))
    return None if best.grade_index >= original.grade_index and ok(original) and not noisy else best


def fix(original: Option, evaluate: Callable[[float, float], Option], risk_limit: float, sigma: float) -> Option | None:
    """evaluate(leverage, stop_distance) -> Option, computed with the same models as the original's verdict."""
    opts = [evaluate(lev, d) for lev, d in candidates(original.leverage, original.stop_distance, sigma)]
    return choose(original, opts, risk_limit, sigma)
