"""The two trader alerts (PLAN 13D task 6), in the same event shape as Whale Watch (whales/rules.py).

* ``skilled_trader``: an account labelled skilled opens, closes or flips a position of at
  least MOVE_USD in BTC, ETH or SOL between two snapshots. The first snapshot of an account
  is only the starting point.
* ``liq_cluster``: tracked longs (or shorts) worth at least CLUSTER_USD would be liquidated
  within NEAR_PCT of the current price. It fires once, and fires again only after the
  amount has dropped under half of CLUSTER_USD (Phase 11's fire-once rule).

Neither says what to do or which way the price goes.
"""
from __future__ import annotations

import os

import pandas as pd

from regimecast.traders import liqmap
from regimecast.traders.published import NOT_SHOWN
from regimecast.whales.rules import hhmm, short, side_of, usd

MOVE_USD = float(os.environ.get("TRADER_MOVE_USD", "1000000"))
CLUSTER_USD = float(os.environ.get("TRADER_CLUSTER_USD", "10000000"))
NEAR_PCT = float(os.environ.get("TRADER_NEAR_PCT", "1.5"))
EVENT_KINDS = ("skilled_trader", "liq_cluster")


def skilled_move(card: dict, coin: str, before: dict | None, after: dict | None, ts,
                 note: str = NOT_SHOWN) -> dict | None:
    """Event if one skilled account's position in a coin opened, closed or flipped at MOVE_USD or more."""
    size0, size1 = float((before or {}).get("size") or 0.0), float((after or {}).get("size") or 0.0)
    usd0 = abs(float((before or {}).get("position_usd") or 0.0))
    usd1 = abs(float((after or {}).get("position_usd") or 0.0))
    was, now = side_of(size0), side_of(size1)
    if was == now:
        return None
    if was == "flat":
        verb, big = f"opened a {usd(usd1)} {coin} {now}", usd1
    elif now == "flat":
        verb, big = f"closed its {usd(usd0)} {coin} {was}", usd0
    else:
        verb, big = f"flipped its {coin} position from {usd(usd0)} {was} to {usd(usd1)} {now}", max(usd0, usd1)
    if big < MOVE_USD:
        return None
    a, ts = card["address"], pd.Timestamp(ts)
    lev = (after or {}).get("leverage")
    lev_txt = f" Leverage {lev:g}x." if isinstance(lev, (int, float)) and lev == lev else ""
    reason = (f"{short(a)} is labelled skilled on its scorecard ({card.get('n_days', 0)} days, "
              f"{card.get('n_trades', 0)} finished trades). It {verb} by {hhmm(ts)}.{lev_txt} {note}")
    return {"key": f"skilled_trader:{a}:{coin}:{ts.value}", "ts": ts, "symbol": f"{coin}USDT",
            "kind": "skilled_trader", "address": a, "title": f"Skilled trader: {short(a)} {verb}", "reason": reason,
            "detail": {"size_before": size0, "size_after": size1, "usd": big, "n_days": card.get("n_days"),
                       "n_trades": card.get("n_trades")}}


class ClusterRule:
    """Fires once per coin and side, then waits until the amount near the price has cleared."""

    def __init__(self, cluster_usd: float = CLUSTER_USD, near_pct: float = NEAR_PCT):
        self.cluster_usd, self.near_pct = cluster_usd, near_pct
        self.armed: dict[tuple[str, str], bool] = {}

    def check(self, positions: list[dict], coin: str, mark: float, ts) -> list[dict]:
        ts, out = pd.Timestamp(ts), []
        for side, s in liqmap.near(positions, coin, mark, self.near_pct).items():
            key = (coin, side)
            if self.armed.get(key, True) and s["usd"] >= self.cluster_usd:
                self.armed[key] = False
                where = "below" if side == "long" else "above"
                out.append({
                    "key": f"liq_cluster:{coin}:{side}:{ts.value}", "ts": ts, "symbol": f"{coin}USDT",
                    "kind": "liq_cluster", "address": "",
                    "title": f"Price near a liquidation cluster: {usd(s['usd'])} of {coin} {side}s",
                    "reason": (f"{s['n']} {side} positions worth {usd(s['usd'])} would be liquidated within "
                               f"{self.near_pct:g}% {where} the {coin} price of {mark:,.2f}, as far as "
                               f"{s['furthest']:,.2f}. Counts {liqmap.LABEL}. It shows where forced selling or "
                               f"buying could start; it does not say the price will get there."),
                    "detail": {"side": side, "usd": s["usd"], "n": s["n"], "mark": mark, "furthest": s["furthest"],
                               "near_pct": self.near_pct}})
            elif s["usd"] < 0.5 * self.cluster_usd:
                self.armed[key] = True
        return out
