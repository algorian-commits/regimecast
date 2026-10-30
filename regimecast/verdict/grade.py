"""Trade Grade (A to F) with three plain-English reasons, and the break-even win rate.

The grade says how well a trade is BUILT, never which way the price will go. It reads only what the risk check
already computes, in two parts:

1. The setup, each part scored 0 (fine) to 4 (as bad as it gets); the setup score is the worst of them:
   * knock-out: the chance that the stop or the liquidation price is reached within the horizon (FHS), banded
     by ``knockout_cuts`` (fixed on the dev period);
   * liquidation vs the user's own limit: chance / limit at most 1, 2, 5, 10, or above;
   * the stop: none (1, liquidation is the only exit) or past the liquidation price (4);
   * costs: fees and funding over the horizon as a share of a typical move, at most 10%, 25%, 50%, or above.
2. The market, each adding grade steps when its weight (fixed on the dev period) is 1: a stressed regime (1),
   stress elevated (1) or high (2), a crowd warning on the trade's side (1), bad timing (1). At most 2 steps.

Grade = A, B, C, D, F for setup score + market steps = 0, 1, 2, 3, 4 or more. The same ``scores`` and
``grade_index`` grade one live trade and the evaluation grid (evaluate.py), so the app and the pass bar
measure the same thing.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

GRADES = ("A", "B", "C", "D", "F")
LIQ_RATIO_CUTS = (1.0, 2.0, 5.0, 10.0)       # chance of liquidation / the user's limit
COST_CUTS = (0.10, 0.25, 0.50)               # fees + funding / a typical move
NO_STOP_SCORE = 1
STOP_PAST_LIQUIDATION_SCORE = 4
MAX_MARKET_STEPS = 2
STRESS_STEPS = {"low": 0, "elevated": 1, "high": 2}
SETUP = ("knockout", "liquidation", "stop", "costs")
MARKET = ("regime", "stress", "crowd", "timing")
PRIORITY = ("stop", "liquidation", "knockout", "costs", "stress", "regime", "crowd", "timing")
HEADLINES = {
    "A": "Well built. The setup keeps your risk small.",
    "B": "Fairly well built, with one thing to keep an eye on.",
    "C": "Shaky. Fix the points below before you trade.",
    "D": "Poorly built. An ordinary day could knock this trade out.",
    "F": "Badly built. See the safer version below.",
}
NOT_DIRECTION = "This grades how the trade is built, not which way the price will go: nobody can tell you that."


def scores(p_knockout, p_liquidation, risk_limit, has_stop, stop_past_liquidation, cost_share,
           stressed, stress_steps, crowd_on_side, bad_timing, cfg: dict) -> dict[str, np.ndarray]:
    """Each factor's score (arrays: one trade or a whole grid)."""
    f = lambda x: np.asarray(x, dtype=float)                       # noqa: E731
    b = lambda x: np.asarray(x, dtype=bool)                        # noqa: E731
    w = cfg["weights"]
    return {
        "knockout": np.searchsorted(cfg["knockout_cuts"], f(p_knockout), side="right"),
        "liquidation": np.searchsorted(LIQ_RATIO_CUTS, f(p_liquidation) / f(risk_limit), side="left"),
        "stop": np.where(~b(has_stop), NO_STOP_SCORE, np.where(b(stop_past_liquidation), STOP_PAST_LIQUIDATION_SCORE, 0)),
        "costs": np.searchsorted(COST_CUTS, f(cost_share), side="left"),
        "regime": w["regime"] * b(stressed).astype(int),
        "stress": w["stress"] * np.asarray(stress_steps, dtype=int),
        "crowd": w["crowd"] * b(crowd_on_side).astype(int),
        "timing": w["timing"] * b(bad_timing).astype(int),
    }


def grade_index(s: dict[str, np.ndarray]) -> np.ndarray:
    """0 = A ... 4 = F."""
    setup = np.max(np.broadcast_arrays(*[s[k] for k in SETUP]), axis=0)
    market = np.minimum(np.sum(np.broadcast_arrays(*[s[k] for k in MARKET]), axis=0), MAX_MARKET_STEPS)
    return np.minimum(setup + market, len(GRADES) - 1).astype(int)


# ------------------------------------------------------------------ one trade, in words
def pct(p: float) -> str:
    """0.354 -> '35%', 0.004 -> '0.4%', 0.00004 -> 'under 0.1%'."""
    if p >= 0.995:
        return "almost 100%"
    if p >= 0.095:
        return f"{100 * p:.0f}%"
    if p >= 0.001:
        return f"{100 * p:.1f}".removesuffix(".0") + "%"
    return "under 0.1%"


def moves(x: float) -> str:
    """1.25 -> '1.25', 1.0 -> '1', 0.8 -> '0.8' (a distance in typical moves)."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


@dataclass
class Facts:
    """What the risk check knows about one trade (api/verdict.py fills it)."""
    coin: str                       # "BTC"
    horizon_hours: int
    p_knockout: float               # stop or liquidation reached within the horizon
    p_liquidation: float
    risk_limit: float
    has_stop: bool
    stop_past_liquidation: bool
    stop_moves: float | None        # stop distance in typical moves (forecast volatility over the horizon)
    cost_share: float               # fees + funding over the horizon / a typical move
    regime: str | None = None
    stress_level: str | None = None
    p_stress: float | None = None
    stress_base: float | None = None
    crowd_on_side: bool = False
    crowd_text: str = ""
    timing: list[str] = field(default_factory=list)


@dataclass
class Factor:
    key: str
    score: int
    text: str


@dataclass
class Verdict:
    grade: str
    index: int
    headline: str
    reasons: list[str]
    factors: list[Factor]


def score_facts(x: Facts, cfg: dict) -> dict[str, int]:
    s = scores(x.p_knockout, x.p_liquidation, x.risk_limit, x.has_stop, x.stop_past_liquidation, x.cost_share,
               x.regime == "stressed", STRESS_STEPS.get(x.stress_level or "low", 0), x.crowd_on_side,
               bool(x.timing), cfg)
    return {k: int(v) for k, v in s.items()}


def assess(x: Facts, cfg: dict) -> Verdict:
    s = score_facts(x, cfg)
    idx = int(grade_index(s))
    h = f"{x.horizon_hours} h"
    exit_by_stop = x.has_stop and not x.stop_past_liquidation
    times = x.p_liquidation / x.risk_limit
    over = f"{times:.0f} times your {pct(x.risk_limit)} limit" if times >= 1.5 else f"above your {pct(x.risk_limit)} limit"
    texts = {
        "liquidation": (f"{pct(x.p_liquidation)} chance of liquidation within {h}: {over}." if s["liquidation"]
                        else f"Liquidation chance {pct(x.p_liquidation)} in {h} is within your {pct(x.risk_limit)} limit."),
        "costs": (f"Fees and funding take {pct(x.cost_share)} of a typical {h} move, so the trade starts behind."
                  if s["costs"] else f"Costs are small: fees and funding are {pct(max(x.cost_share, 0))} of a typical {h} move."),
    }
    if exit_by_stop:
        texts["knockout"] = (
            f"Normal price swings could hit your stop: about a {pct(x.p_knockout)} chance within {h} "
            f"(it is {moves(x.stop_moves)} typical moves away)." if s["knockout"] else
            f"Your stop sits outside normal noise: {moves(x.stop_moves)} typical moves away, "
            f"about a {pct(x.p_knockout)} chance it is hit within {h}.")
    else:  # without a working stop the knock-out IS liquidation: one reason, at the worse of the two scores
        s_liq = max(s["liquidation"], s["knockout"])
        texts["liquidation"] = (f"{pct(x.p_liquidation)} chance of liquidation within {h}"
                                + (f": {over}." if s["liquidation"] else ".") if s_liq else texts["liquidation"])
        s = {**s, "liquidation": s_liq, "knockout": 0}
    if not x.has_stop:
        texts["stop"] = "No stop-loss: if the price turns against you, liquidation is your only exit."
    elif x.stop_past_liquidation:
        texts["stop"] = "Your stop is past your liquidation price, so you would be liquidated before it triggers."
    if s["regime"]:
        texts["regime"] = "The market is in a stressed regime: bigger moves than usual, both ways."
    if s["stress"] and x.p_stress is not None:
        base = f" vs {pct(x.stress_base)} normally" if x.stress_base else ""
        texts["stress"] = f"Market stress risk for the next hour is {x.stress_level}: {pct(x.p_stress)}{base}."
    if s["crowd"]:
        texts["crowd"] = x.crowd_text
    if s["timing"]:
        texts["timing"] = x.timing[0]
    factors = [Factor(k, s[k], texts[k]) for k in PRIORITY if k in texts and (s[k] or k in SETUP)]
    if x.regime == "calm" and not s["stress"]:
        factors.append(Factor("calm", 0, "The market is calm right now."))
    bad = sorted([f for f in factors if f.score > 0], key=lambda f: (-f.score, PRIORITY.index(f.key)))
    good = [f for f in factors if f.score == 0]
    reasons = [f.text for f in (bad + good)[:3]]
    return Verdict(GRADES[idx], idx, HEADLINES[GRADES[idx]], reasons, factors)


# ------------------------------------------------------------------ break-even win rate
def break_even_win_rate(loss: float, gain: float, cost: float) -> float | None:
    """Share of trades you must win to make money when a win makes ``gain``, a loss costs ``loss`` (both before
    costs, in USDT) and every trade pays ``cost`` (fees and funding): p (gain - cost) = (1 - p) (loss + cost),
    so p = (loss + cost) / (gain + loss). Above 1: it loses money even when right every time."""
    if loss < 0 or gain <= 0:
        return None
    return (loss + cost) / (gain + loss)
