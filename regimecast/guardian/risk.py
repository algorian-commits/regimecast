"""What the Guardian computes for one open position, the three alert rules, and the safer version.

Read-out (BTC, ETH and SOL; other coins are listed but not judged, since the models cover these three):
* the chance the mark price reaches the liquidation price within 24 h: FHS on the live HAR-S forecast, the
  Risk Check's model (alerts/rules.position_risk does the same for saved checks), from the CURRENT mark price;
* the distance to liquidation, in % and in typical one-hour moves (the 1 h HAR-S forecast);
* the stress level for the next hour.
The liquidation price is the exchange's own (Hyperliquid and Binance report it, cross or isolated); for a
position entered by hand it is computed as in the Risk Check.

Rules (each fires once and re-arms when it clears, alerts/rules.step):
* ``guardian_risk``   the 24 h chance is above the user's limit; clears at 80% of the limit.
* ``guardian_stress`` stress is high while the position is long with leverage >= 2; clears below high.
* ``guardian_near``   the mark price is within 2 typical one-hour moves of liquidation; clears at 2.5.

Safer version: the position as it stands now is the same risk as a trade opened now at the mark price with
an isolated margin that gives the same liquidation price, at "leverage from now" L0. Phase 12's "fix my trade"
is run on that trade; its leverage (or, if it finds no version, the highest Binance leverage step below L0)
is lowered further if needed until the chance is within the limit and liquidation is at least 2.5 typical
hourly moves away. It is shown as "add $Y margin (same size)" or "reduce the position to $X". On a stress
alert the limit is halved: FHS is known to understate long-side risk on crash days (Phase 5).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Callable

from regimecast.alerts import rules
from regimecast.risk import fhs_live
from regimecast.risk import liquidation as liq
from regimecast.verdict import fix as FX

COVERED = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
HORIZON = 24
NEAR_MOVES, NEAR_CLEAR = 2.0, 2.5
LEVERAGED = 2.0
STRESS_LIMIT_SHARE = 0.5
RULES = ("guardian_risk", "guardian_stress", "guardian_near")


@dataclass
class Reading:
    symbol: str
    side: str
    qty: float
    mark: float
    liquidation_price: float | None   # None: cannot be liquidated
    distance_pct: float | None
    moves_1h: float | None            # distance in typical one-hour moves
    p_liquidation: float
    limit: float
    leverage: float | None
    stress_level: str | None
    p_stress: float | None
    stress_base: float | None
    sigma_24h: float
    sigma_1h: float | None

    @property
    def coin(self) -> str:
        return self.symbol.removesuffix("USDT")

    @property
    def crossed(self) -> bool:
        """The mark price is already at or past the liquidation price."""
        return self.distance_pct is not None and self.distance_pct <= 0

    def as_dict(self) -> dict:
        return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in self.__dict__.items()}


def hit_fn(fhs: dict, symbol: str, side: str) -> Callable[[float, float], float]:
    """P(the mark price moves at least d against the position within 24 h), as in the Risk Check."""
    entry = fhs.get(symbol, {}).get(f"{HORIZON}h", {}).get(side)
    if entry:
        return lambda d, sigma: fhs_live.survival(entry, d, sigma)
    return liq.first_passage_prob


def log_distance(side: str, price: float, lp: float | None) -> float:
    if not lp or not math.isfinite(lp) or lp <= 0:
        return math.inf
    return math.log(price / lp) if side == "long" else math.log(lp / price)


def manual_liquidation_price(row) -> float | None:
    pos = liq.Position(symbol=row["symbol"], side=row["side"], entry=row["entry"], qty=row["qty"],
                       leverage=row["leverage"], margin_mode=row["margin_mode"], wallet_balance=row["wallet_balance"])
    lp = liq.liquidation_price(pos)
    return lp if math.isfinite(lp) and lp > 0 else None


def read(row, mark: float | None, sigma_24h: float | None, sigma_1h: float | None, stress: dict | None,
         fhs: dict, limit: float) -> Reading | None:
    """The read-out for one position row, or None when it cannot be judged (coin not covered, no data)."""
    if row["symbol"] not in COVERED or not mark or not sigma_24h:
        return None
    lp = row["liquidation_price"] if row["source_id"] is not None else manual_liquidation_price(row)
    d = log_distance(row["side"], mark, lp)
    p = hit_fn(fhs, row["symbol"], row["side"])(d, sigma_24h) if d > 0 else 1.0
    # Signed: negative when the mark is already past the liquidation price (a hand-entered position from an
    # entry far from today's price; the exchange would have closed it).
    pct = None if lp is None else 100 * ((1 - lp / mark) if row["side"] == "long" else (lp / mark - 1))
    moves = (d / sigma_1h if math.isfinite(d) else math.inf) if sigma_1h else None
    return Reading(symbol=row["symbol"], side=row["side"], qty=row["qty"], mark=mark, liquidation_price=lp,
                   distance_pct=pct, moves_1h=moves, p_liquidation=p, limit=limit, leverage=row["leverage"],
                   stress_level=(stress or {}).get("level"), p_stress=(stress or {}).get("p_stress_next_hour"),
                   stress_base=(stress or {}).get("base_rate"), sigma_24h=sigma_24h, sigma_1h=sigma_1h)


def states(r: Reading) -> dict[str, bool | None]:
    """Each rule's condition: True holds, False cleared, None unknown or inside the clearing band."""
    risk = True if r.p_liquidation > r.limit else False if r.p_liquidation <= rules.RISK_CLEAR_SHARE * r.limit else None
    if r.crossed:   # one message ("past its liquidation price"), not one per rule
        return {"guardian_risk": True, "guardian_stress": None, "guardian_near": None}
    exposed = r.side == "long" and (r.leverage or 0) >= LEVERAGED
    stress = None if r.stress_level is None else (exposed and r.stress_level == "high")
    if r.moves_1h is None:
        near = None
    else:
        near = True if r.moves_1h <= NEAR_MOVES else False if r.moves_1h >= NEAR_CLEAR else None
    return {"guardian_risk": risk, "guardian_stress": stress, "guardian_near": near}


# ------------------------------------------------------------------ safer version
def leverage_from_now(symbol: str, side: str, mark: float, qty: float, lp: float) -> float:
    """The isolated leverage that, opened now at the mark price, has this liquidation price."""
    def lp_at(lev: float) -> float:
        return liq.liquidation_price(liq.Position(symbol=symbol, side=side, entry=mark, qty=qty, leverage=lev))

    lo, hi = math.log(1.0), math.log(1000.0)
    up = side == "long"   # a long's liquidation price rises with leverage, a short's falls
    for _ in range(60):
        mid = (lo + hi) / 2
        v = lp_at(math.exp(mid))
        if (v < lp) == up:
            lo = mid
        else:
            hi = mid
    return math.exp((lo + hi) / 2)


def _version(symbol, side, mark, margin, lev, sigma_24h, sigma_1h, hit):
    pos = liq.Position(symbol=symbol, side=side, entry=mark, qty=margin * lev / mark, leverage=lev)
    lp = liq.liquidation_price(pos)
    d = log_distance(side, mark, lp)
    p = hit(d, sigma_24h) if d > 0 else 1.0
    moves = d / sigma_1h if sigma_1h and math.isfinite(d) else math.inf
    return p, (lp if math.isfinite(lp) and lp > 0 else None), moves


def phase12_fix(symbol, side, mark, qty, lev0, stop, limit, sigma_24h, hit, rg, st, state, now):
    """Phase 12's fix on the position as a trade opened now (api/verdict.py), or None."""
    from regimecast.api import verdict as V

    if stop is not None and not ((stop < mark) if side == "long" else (stop > mark)):
        stop = None   # a stop already passed by the price is no stop for a trade opened now
    t = SimpleNamespace(symbol=symbol, side=side, entry=mark, leverage=lev0, stop=stop, take_profit=None,
                        horizon_hours=HORIZON, risk_limit=limit)
    pos = liq.Position(symbol=symbol, side=side, entry=mark, qty=qty, leverage=lev0, stop=stop)
    try:
        ctx = V.Context(t, pos, sigma_24h, hit, rg, st, state, now)
        v, info, o = ctx.version(lev0, stop)
        return V._fix(ctx, v, info, o)
    except Exception:   # the Guardian's warning goes out even if the verdict cannot be built
        return None


def safer(r: Reading, stop: float | None, limit: float, fhs: dict, rg=None, st=None, state=None,
          now: datetime | None = None) -> dict | None:
    """The closest safer version of the open position: {"status": "fixed", ...}, {"status": "safe"} when it already
    keeps within the limit, 2.5+ typical hourly moves from liquidation, or {"status": "none"} when no lower leverage
    helps."""
    if r.liquidation_price is None:
        return {"status": "safe", "limit": limit}
    if r.crossed:   # nothing to rescale from: the margin is already gone at this price
        return {"status": "none", "limit": limit}
    hit = hit_fn(fhs, r.symbol, r.side)
    lev0 = leverage_from_now(r.symbol, r.side, r.mark, r.qty, r.liquidation_price)
    notional = r.qty * r.mark
    margin = notional / lev0

    def fine(lev: float) -> bool:
        p, _, moves = _version(r.symbol, r.side, r.mark, margin, lev, r.sigma_24h, r.sigma_1h, hit)
        return p <= limit and moves >= NEAR_CLEAR

    if fine(lev0):
        return {"status": "safe", "limit": limit}
    fx = phase12_fix(r.symbol, r.side, r.mark, r.qty, lev0, stop, limit, r.sigma_24h, hit, rg, st, state, now)
    start = fx.fixed.leverage if fx is not None and fx.fixed is not None else lev0
    ladder = [start, *(x for x in FX.LEVERAGE_STEPS if x < start)]
    lev = next((x for x in ladder if x < lev0 * 0.999 and fine(x)), None)
    if lev is None:
        return {"status": "none", "limit": limit}
    p, lp, moves = _version(r.symbol, r.side, r.mark, margin, lev, r.sigma_24h, r.sigma_1h, hit)
    stop_advice = None
    if fx is not None and fx.fixed is not None and fx.fixed.stop is not None:
        stop_advice = next((c for c in fx.changes if c.startswith(("Add a stop", "Stop from"))), None)
    add = notional / lev - margin
    return {"status": "fixed", "leverage_now": round(lev0, 2), "leverage_safe": lev, "add_margin_usdt": round(add, 2),
            "reduce_to_usdt": round(lev * margin, 2), "reduce_to_qty": lev * margin / r.mark,
            "p_liquidation": round(p, 5), "liquidation_price": round(lp, 2) if lp else None,
            "moves_1h": None if not math.isfinite(moves) else round(moves, 2),
            "grade": fx.fixed.grade if fx is not None and fx.fixed is not None else None,
            "stop_advice": stop_advice, "limit": limit}


# ------------------------------------------------------------------ messages
def fmt_price(p: float) -> str:
    return rules.fmt_price(p) if p >= 1 else f"{p:.4g}"


def pct(x: float) -> str:
    return f"{100 * x:.1f}%" if x >= 0.001 else f"{100 * x:.2f}%"


def name(r: Reading, where: str) -> str:
    lev = f"{r.leverage:g}x " if r.leverage else ""
    return f"{r.coin} {lev}{r.side}{f' ({where})' if where else ''}"


def safer_text(r: Reading, s: dict) -> str:
    if s["status"] == "safe":
        return (f"The position already keeps within {pct(s['limit'])} with liquidation more than {NEAR_CLEAR:g} typical "
                f"hourly moves away; no change needed for that.")
    if s["status"] == "none":
        return "No lower leverage at this margin brings it within the limit; closing the position is the safe option."
    qty = f"{s['reduce_to_qty']:.4g} {r.coin}"
    lp = f", liquidation at {fmt_price(s['liquidation_price'])}" if s["liquidation_price"] else ""
    lines = [f"Safer version: add ${s['add_margin_usdt']:,.0f} margin (same size), or reduce the position to "
             f"${s['reduce_to_usdt']:,.0f} ({qty}) and keep the margin in it. Either one takes it from about "
             f"{s['leverage_now']:g}x to {s['leverage_safe']:g}x from here: chance of liquidation within 24 h "
             f"{pct(s['p_liquidation'])}{lp}."]
    if s.get("stop_advice"):
        lines.append(s["stop_advice"])
    return "\n".join(lines)


def message(rule: str, r: Reading, where: str, s: dict) -> tuple[str, str]:
    """(title, message) for an inbox row and the Telegram text."""
    n = name(r, where)
    if r.crossed:
        place = (f"The mark price {fmt_price(r.mark)} is already past the liquidation price "
                 f"{fmt_price(r.liquidation_price)}: on the exchange this position would have been liquidated. "
                 f"If it is still open, check it there now.")
        title = f"{n}: past its liquidation price"
        tail = "Prices from Binance's live mark price. Not financial advice."
        return title, "\n".join((place, tail))
    if r.liquidation_price is not None:
        side_word = "below" if r.side == "long" else "above"
        moves = f", about {r.moves_1h:.1f} typical hourly moves" if r.moves_1h is not None else ""
        place = (f"Liquidation at {fmt_price(r.liquidation_price)}, {r.distance_pct:.1f}% {side_word} the mark "
                 f"price {fmt_price(r.mark)}{moves}.")
    else:
        place = ""
    chance = f"Chance of liquidation within 24 h: {pct(r.p_liquidation)} (your limit {pct(r.limit)})."
    if rule == "guardian_risk":
        title, lead = f"{n}: liquidation risk above your limit", chance
    elif rule == "guardian_stress":
        base = f" (normally {100 * r.stress_base:.0f}%)" if r.stress_base else ""
        title = f"{n}: market stress is high"
        lead = (f"{100 * (r.p_stress or 0):.0f}% chance of a market stress event on {r.coin} in the next hour{base}. "
                f"Stress means a burst of volatility, not a direction, and on crash days the odds understate the "
                f"risk for longs, so the safer version uses half your limit. {chance}")
    else:
        title, lead = f"{n}: price near liquidation", chance
    tail = "Odds from RegimeCast's liquidation model on the live volatility forecast. Not financial advice."
    return title, "\n".join(x for x in (lead, place, safer_text(r, s), tail) if x)
