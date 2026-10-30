"""The written rules of the trade verdict: grade, break-even win rate, fix my trade, what-if strip.

Trade Grade
-----------
A trade collects penalty points; the grade is read from the total. Every rule is a step table below,
used by both the single-trade code (the app) and the vectorised code (the dev and final tables), so
the two can never disagree (tested). The verdict always looks one day (24 h) ahead.

=========================  =========================================================  ======
What                       Step                                                        Points
=========================  =========================================================  ======
Knock-out chance           chance the trade is ended within 24 h, by the stop or by   0 2 4 6
                           liquidation, whichever comes first: < 15%, < 30%, < 50%,
                           50% or more
Liquidation vs your limit  P(liquidation in 24 h) vs the user's limit L:              0 1 2 3 4
                           <= L/2, <= L, <= 3L, <= 10L, more
No stop                    nothing but liquidation limits the loss                   2
Stop past liquidation      you would be liquidated before the stop triggers           2
Costs                      fees + funding as a share of a typical day's move:         0 1 2
                           < 10%, < 25%, 25% or more
Regime                     stressed                                                    1
Stress gauge               elevated / high                                             1 / 2
Stress rising              chance of a stress event at least 10% and doubled in 3 h   1
Crowd                      crowd warning on the same side as the trade                2
Macro event                Fed decision, CPI or jobs report within the 24 h           1
Thin market                weekend, or a thin hour of the day                        1
=========================  =========================================================  ======

The knock-out chance counts once, whichever way the trade ends; liquidation gets extra points on top
because it loses the whole margin, while a stop is a loss the user chose.

Letters from the total: below CUTS[0] is A, below CUTS[1] B, below CUTS[2] C, below CUTS[3] D, else F.
The cut points were chosen on the development period from a short list written beforehand
(scripts/run_phase12.py) and are frozen in frozen.json.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

LETTERS = ["A", "B", "C", "D", "F"]
DEFAULT_CUTS = (2, 4, 6, 8)
CUT_CANDIDATES = [(2, 4, 6, 8), (1, 3, 5, 8), (2, 4, 7, 10), (3, 5, 7, 9), (2, 5, 8, 11)]
HEADLINES = {"A": "Well built", "B": "Reasonably built", "C": "Risky in places", "D": "Poorly built",
             "F": "Very likely to go wrong"}
HORIZON_HOURS = 24
TYPICAL_MOVE = math.sqrt(2 / math.pi)   # E|move| = sigma * sqrt(2/pi) for a normal move

# step tables: (upper edges, points); value < edge[i] -> points[i], else the last points
KNOCK_EDGES, KNOCK_POINTS = (0.15, 0.30, 0.50), (0, 2, 4, 6)          # P(stop or liquidation in 24 h)
LIQ_EDGES, LIQ_POINTS = (0.5, 1.0, 3.0, 10.0), (0, 1, 2, 3, 4)        # P(liquidation) / limit (edges inclusive)
COST_EDGES, COST_POINTS = (0.10, 0.25), (0, 1, 2)                     # costs / typical move
STOP_PAST_LIQ, NO_STOP = 2, 2
REGIME_STRESSED = 1
STRESS_POINTS = {"low": 0, "elevated": 1, "high": 2}
STRESS_RISING, CROWD_SAME_SIDE, MACRO_EVENT, THIN_MARKET = 1, 2, 1, 1
STRESS_RISING_MIN, STRESS_RISING_FACTOR = 0.10, 2.0
GOOD_STOP = KNOCK_EDGES[0]     # a stop "outside normal noise": under a 15% chance of being hit in a day


def step(x, edges, points, inclusive: bool = False):
    """Points for x from a step table; works on numbers and numpy arrays alike."""
    idx = np.searchsorted(np.asarray(edges, float), np.asarray(x, float), side="left" if inclusive else "right")
    out = np.asarray(points)[idx]
    return int(out) if np.ndim(out) == 0 else out


def letter(points, cuts=DEFAULT_CUTS):
    idx = np.searchsorted(np.asarray(cuts), np.asarray(points), side="right")
    out = np.asarray(LETTERS)[idx]
    return str(out) if np.ndim(out) == 0 else out


def stress_rising(p_now, p_3h_ago):
    """The stress chance is at least 10% and has at least doubled in the last 3 hours."""
    p_now, p_3h_ago = np.asarray(p_now, float), np.asarray(p_3h_ago, float)
    out = (p_now >= STRESS_RISING_MIN) & (p_now >= STRESS_RISING_FACTOR * np.nan_to_num(p_3h_ago, nan=np.inf))
    return bool(out) if out.ndim == 0 else out


def pct(p: float) -> str:
    """0.004 -> '0.4%', 0.25 -> '25%'."""
    v = 100 * p
    return f"{v:.1f}%" if v < 10 else f"{v:.0f}%"


def a_pct(p: float) -> str:
    """'a 9.6%' or 'an 8.0%' / 'an 11%' / 'an 18%' (said 'eight', 'eleven', 'eighteen')."""
    s = pct(p)
    an = s.startswith("8") or re.match(r"1[18](?!\d)", s) is not None
    return f"{'an' if an else 'a'} {s}"


# ------------------------------------------------------------------ the facts and the grade
@dataclass
class Facts:
    side: str
    p_liq: float                      # P(liquidation within 24 h)
    risk_limit: float
    p_stop: float | None = None       # P(stop hit within 24 h); None = no stop
    stop_past_liq: bool = False
    cost_share: float | None = None   # fees + funding over 24 h / typical 24 h move
    regime: str | None = None
    stress_level: str | None = None
    stress_rising: bool = False
    crowd: str | None = None          # "long" or "short": the side the crowd is on, when the warning is on
    crowd_odds: tuple[float, float] | None = None   # (after a warning, normally): chance of a 5% move against
    events: list[dict] = field(default_factory=list)
    weekend: bool = False
    thin_hour: bool = False


@dataclass
class Reason:
    points: int
    text: str
    good: bool = False
    key: str = ""


def knockout(f: Facts) -> float:
    """Chance the trade is ended within 24 h: by the stop if it sits before the liquidation price,
    otherwise by liquidation."""
    if f.p_stop is None or f.stop_past_liq:
        return f.p_liq
    return max(f.p_stop, f.p_liq)


def _liq_text(p_liq: float, limit: float, lp: int) -> str:
    ratio = p_liq / limit
    if lp <= 1:
        return f"{a_pct(p_liq).capitalize()} chance of being liquidated in the next day, within your {pct(limit)} limit."
    if lp == 2:
        return f"{a_pct(p_liq).capitalize()} chance of being liquidated in the next day is above your {pct(limit)} limit."
    return (f"Leverage is too high: {a_pct(p_liq)} chance of being liquidated in the next day, {ratio:.0f} times "
            f"your {pct(limit)} limit.")


def reasons_for(f: Facts) -> list[Reason]:
    out: list[Reason] = []
    lp = step(f.p_liq / f.risk_limit, LIQ_EDGES, LIQ_POINTS, inclusive=True)
    kp = step(knockout(f), KNOCK_EDGES, KNOCK_POINTS)
    liq_good = (f"Liquidation is very unlikely: about {pct(f.p_liq)} in the next day, well under your "
                f"{pct(f.risk_limit)} limit.")
    if f.p_stop is None or f.stop_past_liq:      # liquidation is what ends the trade: one reason for both
        out.append(Reason(kp + lp, liq_good if kp + lp == 0 else _liq_text(f.p_liq, f.risk_limit, lp), kp + lp == 0,
                          "liq"))
        if f.p_stop is None:
            out.append(Reason(NO_STOP, "There is no stop-loss, so nothing limits your loss before liquidation.",
                              key="stop"))
        else:
            out.append(Reason(STOP_PAST_LIQ, "Your stop is past the liquidation price: you would be liquidated "
                                             "before it triggers.", key="stop"))
    else:
        out.append(Reason(lp, liq_good if lp == 0 else _liq_text(f.p_liq, f.risk_limit, lp), lp == 0, "liq"))
        k, ak = pct(knockout(f)), a_pct(knockout(f))
        if kp == 0:
            out.append(Reason(0, f"Your stop is outside normal price noise ({ak} chance it is hit in a day).", True,
                              "stop"))
        elif kp == 2:
            out.append(Reason(kp, f"An ordinary day's move could hit your stop ({k} chance in the next day).",
                              key="stop"))
        elif kp == 4:
            out.append(Reason(kp, f"Your stop is tight: normal movement has {ak} chance of hitting it in the next "
                                  f"day.", key="stop"))
        else:
            out.append(Reason(kp, f"Your stop is inside normal price wiggle: {ak} chance it is hit in the next day "
                                  f"even if you are right about the direction.", key="stop"))
    if f.cost_share is not None:
        cp = step(f.cost_share, COST_EDGES, COST_POINTS)
        share = f"{100 * f.cost_share:.0f}%"
        out.append(Reason(cp, f"Costs are small: fees and funding are about {share} of a typical day's move."
                          if cp == 0 else f"Fees and funding eat about {share} of a typical day's move.",
                          cp == 0, "cost"))
    if f.regime == "stressed":
        out.append(Reason(REGIME_STRESSED, "The market is in a wild (stressed) phase right now: big swings are "
                                           "more common.", key="regime"))
    elif f.regime == "calm":
        out.append(Reason(0, "The market is in a calm phase right now.", True, "regime"))
    if f.stress_level in ("elevated", "high"):
        out.append(Reason(STRESS_POINTS[f.stress_level], f"The chance of a sudden market shock in the next hour is "
                                                         f"{f.stress_level}.", key="stress"))
    if f.stress_rising:
        out.append(Reason(STRESS_RISING, "Market stress is rising: the shock chance has at least doubled in the "
                                         "last 3 hours.", key="stress_rising"))
    if f.crowd and f.crowd == f.side:
        odds = (f" After moments like this, a sudden 5% move against the crowd within a day happened "
                f"{pct(f.crowd_odds[0])} of the time (normally {pct(f.crowd_odds[1])}).") if f.crowd_odds else ""
        out.append(Reason(CROWD_SAME_SIDE, f"Crowded trade: funding, big traders' positions and new money all lean "
                                           f"{f.crowd}, the same way as you.{odds}", key="crowd"))
    if f.events:
        e = f.events[0]
        when = e["time_utc"][:16].replace("T", " ")
        out.append(Reason(MACRO_EVENT, f"{e['name']} at {when} UTC falls inside your trade's day: prices often "
                                       f"jump around it.", key="event"))
    if f.weekend or f.thin_hour:
        what = "It is the weekend" if f.weekend else "This is one of the quietest hours of the day"
        out.append(Reason(THIN_MARKET, f"{what}: fewer traders, so the price can jump more easily.", key="thin"))
    return out


def grade(f: Facts, cuts=DEFAULT_CUTS) -> dict:
    """Grade, total points and the three plain reasons (worst first; good points fill the gaps)."""
    rs = reasons_for(f)
    total = sum(r.points for r in rs)
    order = {k: i for i, k in enumerate(["liq", "stop", "crowd", "stress", "stress_rising", "regime", "event",
                                         "thin", "cost"])}
    bad = sorted([r for r in rs if r.points > 0], key=lambda r: (-r.points, order.get(r.key, 99)))
    good = sorted([r for r in rs if r.points == 0], key=lambda r: order.get(r.key, 99))
    top = (bad + good)[:3]
    g = letter(total, cuts)
    return {"grade": g, "headline": HEADLINES[g], "points": total,
            "reasons": [{"text": r.text, "good": r.good, "points": r.points} for r in top],
            "all_reasons": [{"text": r.text, "good": r.good, "points": r.points, "key": r.key} for r in rs]}


def points_array(side_sign, p_liq, risk_limit, p_stop, stop_past_liq, cost_share, regime_stressed,
                 stress_level_points, rising, crowd_sign, event, thin) -> np.ndarray:
    """The same points as grade() for many trades at once (the dev and final tables).

    side_sign and crowd_sign are +1 long / -1 short (crowd 0 = no warning); p_stop NaN = no stop."""
    p_stop, p_liq = np.asarray(p_stop, float), np.asarray(p_liq, float)
    has_stop = ~np.isnan(p_stop)
    past = np.asarray(stop_past_liq, bool) & has_stop
    by_liq = ~has_stop | past
    knock = np.where(by_liq, p_liq, np.maximum(np.nan_to_num(p_stop), p_liq))
    pts = (step(knock, KNOCK_EDGES, KNOCK_POINTS) + step(p_liq / risk_limit, LIQ_EDGES, LIQ_POINTS, inclusive=True)
           + np.where(~has_stop, NO_STOP, np.where(past, STOP_PAST_LIQ, 0))
           + step(np.asarray(cost_share), COST_EDGES, COST_POINTS)
           + REGIME_STRESSED * np.asarray(regime_stressed, bool) + np.asarray(stress_level_points)
           + STRESS_RISING * np.asarray(rising, bool)
           + CROWD_SAME_SIDE * (np.asarray(crowd_sign) == np.asarray(side_sign))
           + MACRO_EVENT * np.asarray(event, bool) + THIN_MARKET * np.asarray(thin, bool))
    return pts.astype(int)


def cost_share(side: str, fee_rate: float, funding_rate: float | None, sigma_24h: float,
               hours: int = HORIZON_HOURS) -> float:
    """Fees (in and out) plus funding over the day, as a share of a typical day's move (sigma * sqrt(2/pi)).

    Funding is paid every 8 hours; a long pays a positive rate, a short receives it (and the other way round)."""
    sign = 1 if side == "long" else -1
    funding = sign * (funding_rate or 0.0) * hours / 8
    return (2 * fee_rate + funding) / max(sigma_24h * TYPICAL_MOVE, 1e-12)


# ------------------------------------------------------------------ break-even win rate
def break_even(side: str, entry: float, qty: float, stop: float | None, target: float | None,
               fee_rate: float = 0.0005, funding_usdt: float = 0.0) -> dict | None:
    """'You need to be right X% of the time to make money after fees and funding.'

    A win reaches the target, a loss the stop. Win = (|target - entry| x qty) - fees - funding,
    loss = (|entry - stop| x qty) + fees + funding, fees = fee_rate on the entry and on the exit
    notional. Break-even when p x win = (1 - p) x loss, so p = loss / (win + loss). ``funding_usdt``
    is what the position pays over its life (negative if it receives)."""
    if stop is None or target is None:
        return None
    s = 1 if side == "long" else -1
    if s * (target - entry) <= 0 or s * (entry - stop) <= 0:
        raise ValueError("the target must be on the profit side and the stop on the loss side")
    win = abs(target - entry) * qty - fee_rate * qty * (entry + target) - funding_usdt
    loss = abs(entry - stop) * qty + fee_rate * qty * (entry + stop) + funding_usdt
    if win <= 0:
        return {"win_rate": 1.0, "win_usdt": round(win, 2), "loss_usdt": round(loss, 2), "reward_to_risk": 0.0,
                "text": "Your target does not even cover fees and funding: this trade cannot make money."}
    p = loss / (win + loss)
    return {"win_rate": round(p, 4), "win_usdt": round(win, 2), "loss_usdt": round(loss, 2),
            "reward_to_risk": round(win / loss, 2),
            "text": f"With this stop and target you need to be right {100 * p:.0f}% of the time to make money "
                    f"after fees and funding (you win ${win:,.2f} or lose ${loss:,.2f})."}


# ------------------------------------------------------------------ what-if strip
def what_if(side: str, entry: float, qty: float, margin_lost: float, liq_price: float | None,
            stop: float | None, scenarios: list[dict], fee_rate: float = 0.0005) -> list[dict]:
    """Gain or loss in dollars for each scenario.

    Each scenario: {"key", "label", "move"}: the price change as a fraction (-0.08 = falls 8%), at its worst
    point in the day. Reaching the stop first closes the trade at the stop; reaching the liquidation price
    loses the margin (``margin_lost``: the position's margin, or the whole cross wallet)."""
    s = 1 if side == "long" else -1
    out = []
    for sc in scenarios:
        price = entry * (1 + sc["move"])
        against = s * (price - entry) < 0
        stop_hit = stop is not None and against and s * (price - stop) <= 0
        liq_hit = liq_price is not None and against and s * (price - liq_price) <= 0
        stop_first = stop_hit and (liq_price is None or s * (stop - liq_price) > 0)
        if stop_first:
            pnl, outcome = -abs(entry - stop) * qty - fee_rate * qty * (entry + stop), "stopped out"
        elif liq_hit:
            pnl, outcome = -margin_lost, "liquidated"
        else:
            pnl = s * (price - entry) * qty - fee_rate * qty * (entry + price)
            outcome = "gain" if pnl > 0 else "loss"
        out.append({**sc, "move_pct": round(100 * sc["move"], 2), "pnl_usdt": round(pnl, 2), "outcome": outcome})
    return out


def scenarios(side: str, quantiles: dict, crash_move: float | None) -> list[dict]:
    """The strip's rows for one coin and regime: the typical (median), 1-in-10 and 1-in-100 worst move
    against the position within a day (dev period, this regime), and a crash day like 10 Oct 2025."""
    s = 1 if side == "long" else -1
    rows = [{"key": "normal", "label": "Normal day", "move": -s * quantiles["p50"]},
            {"key": "bad", "label": "Bad day (1 in 10)", "move": -s * quantiles["p90"]},
            {"key": "very_bad", "label": "Very bad day (1 in 100)", "move": -s * quantiles["p99"]}]
    if crash_move is not None:
        rows.append({"key": "crash", "label": "Crash day like 10 Oct 2025", "move": crash_move})
    return rows


# ------------------------------------------------------------------ fix my trade
LEVERAGE_LADDER = [125, 100, 75, 50, 40, 30, 25, 20, 15, 12, 10, 8, 7, 6, 5, 4, 3, 2, 1]
STOP_MULTS = [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]


@dataclass
class Candidate:
    leverage: float
    qty: float
    stop: float | None
    p_liq: float
    p_stop: float | None
    liq_price: float | None


def fix_trade(side: str, entry: float, qty: float, leverage: float, stop: float | None, risk_limit: float,
              sigma_24h: float, evaluate, round_price=None) -> Candidate | None:
    """The closest safer version of the same trade: same coin, side, entry and margin.

    1. Stop: keep the user's stop if normal movement has under a 15% chance of hitting it in a day;
       otherwise the nearest stop (1.5, 2, 2.5, 3, 4 or 5 forecast moves away) that does.
    2. Leverage: the highest step on the ladder, no higher than now, that keeps the chance of liquidation
       within the user's limit AND the liquidation price beyond the stop. The margin stays the same, so a
       lower leverage is a smaller position.

    ``evaluate(leverage, qty, stop) -> Candidate`` prices a version with the app's own models.
    Returns None when the trade already meets both conditions."""
    s = 1 if side == "long" else -1
    rp = round_price or (lambda x: x)
    current = evaluate(leverage, qty, stop)

    def ok(c: Candidate) -> bool:
        stop_inside = c.stop is not None and (c.liq_price is None or s * (c.stop - c.liq_price) > 0)
        return c.p_liq <= risk_limit and stop_inside and c.p_stop is not None and c.p_stop < GOOD_STOP

    if ok(current):
        return None
    stops = [stop] if stop is not None and current.p_stop is not None and current.p_stop < GOOD_STOP else []
    stops += [rp(entry * math.exp(-s * k * sigma_24h)) for k in STOP_MULTS]
    levels = [leverage] + [x for x in LEVERAGE_LADDER if x < leverage]
    for st in stops:
        probe = evaluate(1.0, qty / leverage, st)
        if probe.p_stop is None or probe.p_stop >= GOOD_STOP:
            continue
        for lev in levels:   # the nearest acceptable stop decides; only the leverage moves after that
            c = evaluate(lev, qty * lev / leverage, st)
            if ok(c):
                return c
        return evaluate(levels[-1], qty * levels[-1] / leverage, st)
    return evaluate(levels[-1], qty * levels[-1] / leverage, stops[-1])
