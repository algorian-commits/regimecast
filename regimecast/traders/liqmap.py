"""Liquidation map (PLAN 13D task 4): where tracked accounts' open positions would be liquidated.

Every open position with a liquidation price counts once, at that price, with its current
dollar value. Bands are ``band_pct`` wide and counted from the current mark price, so band 0
starts at the mark. A position's liquidation price comes from Hyperliquid itself. It moves
with the account's margin, so the map is a snapshot, not a forecast, and it covers only the
tracked Hyperliquid accounts, not the whole market.
"""
from __future__ import annotations

import math
from typing import Iterable

BAND_PCT = 1.0
SPAN_PCT = 20.0
LABEL = "tracked Hyperliquid accounts only, not the whole market"


def _usable(p: dict) -> bool:
    x = p.get("liquidation")
    return x is not None and math.isfinite(float(x)) and float(x) > 0


def build(positions: Iterable[dict], coin: str, mark: float, band_pct: float = BAND_PCT,
          span_pct: float = SPAN_PCT) -> dict:
    """Dollar value of longs and shorts that would be liquidated per price band around ``mark``."""
    bands: dict[int, dict] = {}
    n = no_liq_n = 0
    outside_usd = no_liq_usd = 0.0
    accounts = set()
    for p in positions:
        if p.get("coin") != coin or not p.get("size"):
            continue
        usd = abs(float(p.get("position_usd") or 0.0))
        n += 1
        accounts.add(p.get("address"))
        if not _usable(p):
            no_liq_usd += usd
            no_liq_n += 1
            continue
        pct = 100 * (float(p["liquidation"]) / mark - 1)
        if abs(pct) > span_pct:
            outside_usd += usd
            continue
        i = math.floor(pct / band_pct)
        b = bands.setdefault(i, {"long_usd": 0.0, "short_usd": 0.0, "n_long": 0, "n_short": 0})
        side = "long" if p["size"] > 0 else "short"
        b[f"{side}_usd"] += usd
        b[f"n_{side}"] += 1
    rows = [{"from_pct": i * band_pct, "to_pct": (i + 1) * band_pct, "price_low": mark * (1 + i * band_pct / 100),
             "price_high": mark * (1 + (i + 1) * band_pct / 100), **b} for i, b in sorted(bands.items())]
    return {"coin": coin, "mark": mark, "band_pct": band_pct, "span_pct": span_pct, "bands": rows,
            "n_positions": n, "n_accounts": len(accounts), "outside_span_usd": outside_usd,
            "no_liquidation_price_usd": no_liq_usd, "n_no_liquidation_price": no_liq_n, "label": LABEL}


def near(positions: Iterable[dict], coin: str, mark: float, near_pct: float) -> dict:
    """Longs liquidated just below the mark and shorts just above it, within ``near_pct``."""
    out = {"long": {"usd": 0.0, "n": 0, "furthest": None}, "short": {"usd": 0.0, "n": 0, "furthest": None}}
    for p in positions:
        if p.get("coin") != coin or not p.get("size") or not _usable(p):
            continue
        liq = float(p["liquidation"])
        pct = 100 * (liq / mark - 1)
        side = "long" if p["size"] > 0 else "short"
        if (side == "long" and -near_pct <= pct <= 0) or (side == "short" and 0 <= pct <= near_pct):
            s = out[side]
            s["usd"] += abs(float(p.get("position_usd") or 0.0))
            s["n"] += 1
            f = s["furthest"]
            s["furthest"] = liq if f is None else (min(f, liq) if side == "long" else max(f, liq))
    return out
