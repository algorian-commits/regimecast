"""Whale Watch rules (M12): three patterns in Hyperliquid positions, each with a plain reason.

Input rows have the recorder's ``hl_positions`` / ``hl_watchlist`` / ``hl_orders`` schema
(recorder/hyperliquid.py), so the same code runs on recorded samples (tests) and on the
API's own live feed (whales/live.py).

* **Whale move**: a tracked top account's position in a coin changes by at least
  MOVE_USD between two snapshots (size change x mark price, so a price move alone never
  counts). Opened, added, reduced, closed or flipped.
* **Fresh wallet**: an account Whale Watch has never seen before shows up holding at
  least FRESH_USD in a coin (found when it places a big order, or when it enters the
  top-accounts list). "New to Whale Watch", which for most is also new to the market.
* **Suspicious timing**: when a coin's stress gauge turns from low to elevated or high,
  every large position opened in that coin in the TIMING_HOURS before it. This is a
  **pattern**, not an accusation: big traders open positions all the time and most of
  these are coincidences. It is shown so a person can take a look.

A rule only looks at rows up to the moment it is called; nothing is revised later.
"""
from __future__ import annotations

import math
import os
from collections import deque

import pandas as pd

MOVE_USD = float(os.environ.get("WHALE_MOVE_USD", "5000000"))
FRESH_USD = float(os.environ.get("WHALE_FRESH_USD", "5000000"))
TIMING_USD = float(os.environ.get("WHALE_TIMING_USD", "5000000"))
TIMING_HOURS = float(os.environ.get("WHALE_TIMING_HOURS", "6"))
PATTERN_NOTE = ("This is a timing pattern, not evidence of wrongdoing: large traders open positions all the "
                "time, and most of these are coincidences.")


def short(address: str) -> str:
    return f"{address[:6]}…{address[-4:]}"


def usd(x: float) -> str:
    x = abs(x)
    return f"${x / 1e9:.2f}B" if x >= 1e9 else f"${x / 1e6:.1f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"


def side_of(size: float) -> str:
    return "long" if size > 0 else "short" if size < 0 else "flat"


def hhmm(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").strftime("%d %b %H:%M UTC")


class WhaleRules:
    def __init__(self, move_usd: float = MOVE_USD, fresh_usd: float = FRESH_USD, timing_usd: float = TIMING_USD,
                 timing_hours: float = TIMING_HOURS, known: set[str] | None = None):
        self.move_usd, self.fresh_usd, self.timing_usd = move_usd, fresh_usd, timing_usd
        self.timing = pd.Timedelta(hours=timing_hours)
        self.known: set[str] = set(known or ())
        self.baseline_done = bool(self.known)     # the very first watchlist is the starting point, not news
        self.newly_known: list[tuple[str, pd.Timestamp]] = []   # for the caller to persist
        self.info: dict[str, dict] = {}           # address -> why it is tracked
        self.prev: dict[tuple[str, str], dict] = {}
        self.opens: deque[dict] = deque(maxlen=5000)
        self.stress_level: dict[str, str] = {}
        self.orders: dict[str, dict] = {}         # last big order per taker, for the fresh-wallet reason
        self._snap: tuple | None = None
        self._snap_fresh = False

    # -------------------------------------------------------------- inputs
    def _know(self, address: str, ts) -> None:
        if address not in self.known:
            self.known.add(address)
            self.newly_known.append((address, pd.Timestamp(ts)))

    def watchlist(self, row: dict) -> None:
        a = row["address"].lower()
        rank = sum(1 for v in self.info.values() if v.get("event_ts") == row["event_ts"]) + 1
        self.info[a] = {"reason": row.get("reason", ""), "display_name": row.get("display_name") or "",
                        "account_value": row.get("account_value"), "event_ts": row["event_ts"], "rank": rank}
        if not self.baseline_done:
            self._know(a, row["event_ts"])

    def order(self, row: dict) -> None:
        self.orders[row["taker"].lower()] = row
        if len(self.orders) > 20_000:
            self.orders.pop(next(iter(self.orders)))

    def position(self, row: dict) -> list[dict]:
        """One hl_positions row (one account, one coin, one snapshot). Returns new events."""
        a, c, ts = row["address"].lower(), row["coin"], pd.Timestamp(row["event_ts"])
        if row.get("source") == "watchlist" and self.info:
            self.baseline_done = True   # the watchlist is complete once its first sweep starts
        key = (a, ts)
        if key != self._snap:   # first row of this account's snapshot
            self._snap = key
            self._snap_fresh = self.baseline_done and a not in self.known
            self._know(a, ts)
        size = float(row["size"] or 0.0)
        pos_usd = abs(float(row.get("position_usd") or 0.0))
        mark = pos_usd / abs(size) if size else math.nan
        events: list[dict] = []
        p = self.prev.get((a, c))
        if self._snap_fresh and pos_usd >= self.fresh_usd:
            events.append(self._fresh(row, a, c, ts, size, pos_usd))
            self.opens.append({"ts": ts, "coin": c, "address": a, "side": side_of(size), "usd": pos_usd,
                               "how": "appeared with"})
        elif p is not None and a in self.info:   # whale moves: tracked top accounts only
            mark = mark if size else p["mark"]
            d = (size - p["size"]) * mark if math.isfinite(mark) else math.nan
            if math.isfinite(d) and abs(d) >= self.move_usd:
                events.append(self._move(row, a, c, ts, p, size, mark, d))
                added = abs(size) * mark - (abs(p["size"]) * mark if p["size"] * size > 0 else 0.0)
                if added >= self.timing_usd:
                    self.opens.append({"ts": ts, "coin": c, "address": a, "side": side_of(size), "usd": added,
                                       "how": "opened" if p["size"] * size <= 0 else "added"})
        self.prev[(a, c)] = {"size": size, "mark": mark if math.isfinite(mark) else (p or {}).get("mark", math.nan),
                             "ts": ts}
        return events

    def stress(self, coin: str, level: str, ts, p: float | None = None) -> list[dict]:
        """The coin's stress gauge; returns suspicious-timing events when it turns from low to elevated/high."""
        ts = pd.Timestamp(ts)
        before = self.stress_level.get(coin)
        self.stress_level[coin] = level
        if before != "low" or level == "low":
            return []
        out = []
        for o in self.opens:
            if o["coin"] == coin and ts - self.timing <= o["ts"] <= ts and o["usd"] >= self.timing_usd:
                lead = (ts - o["ts"]).total_seconds() / 3600
                prob = f" ({100 * p:.0f}% chance of a stress event in the next hour)" if p is not None else ""
                out.append(self._event(
                    "suspicious_timing", ts, coin, o["address"],
                    f"Pattern: {usd(o['usd'])} {coin} {o['side']} {lead:.1f} h before a stress flag",
                    f"{short(o['address'])} {o['how']} a {usd(o['usd'])} {coin} {o['side']} around {hhmm(o['ts'])}; "
                    f"{lead:.1f} hours later the {coin} stress gauge turned {level}{prob}. {PATTERN_NOTE}",
                    {"opened_at": o["ts"].isoformat(), "usd": o["usd"], "side": o["side"], "stress_level": level,
                     "lead_hours": round(lead, 2), "p_stress": p},
                    key=f"timing:{o['address']}:{coin}:{o['ts'].value}:{ts.value}"))
        return out

    # -------------------------------------------------------------- events
    def _event(self, kind, ts, coin, address, title, reason, detail, key=None) -> dict:
        return {"key": key or f"{kind}:{address}:{coin}:{pd.Timestamp(ts).value}", "ts": pd.Timestamp(ts),
                "symbol": f"{coin}USDT", "kind": kind, "address": address, "title": title, "reason": reason,
                "detail": detail}

    def _who(self, a: str) -> str:
        i = self.info.get(a)
        if not i:
            return "A tracked account"
        why = {"largest_account": "one of the largest accounts", "top_month_pnl": "a best-month-PnL account"}
        parts = [why.get(r, r) for r in i["reason"].split("+") if r]
        name = f" ({i['display_name']})" if i["display_name"] else ""
        return f"A top Hyperliquid account{name}, {' and '.join(parts) or 'tracked'},"

    def _move(self, row, a, c, ts, p, size, mark, d) -> dict:
        was, now = side_of(p["size"]), side_of(size)
        if was == "flat":
            verb = f"opened a {usd(abs(size) * mark)} {c} {now}"
        elif now == "flat":
            verb = f"closed its {usd(abs(p['size']) * mark)} {c} {was}"
        elif was != now:
            verb = f"flipped its {c} position from {usd(abs(p['size']) * mark)} {was} to {usd(abs(size) * mark)} {now}"
        elif abs(size) > abs(p["size"]):
            verb = f"added {usd(d)} to its {c} {now} (now {usd(abs(size) * mark)})"
        else:
            verb = f"cut its {c} {now} by {usd(d)} (now {usd(abs(size) * mark)})"
        lev = row.get("leverage")
        extra = f" Leverage {lev:g}x." if isinstance(lev, (int, float)) and math.isfinite(lev) else ""
        reason = (f"{self._who(a)} {verb} between {hhmm(p['ts'])} and {hhmm(ts)}: size {p['size']:+,.4g} -> "
                  f"{size:+,.4g} {c} at about {mark:,.2f}.{extra}")
        return self._event("whale_move", ts, c, a, f"Whale move: {short(a)} {verb}", reason,
                           {"size_before": p["size"], "size_after": size, "change_usd": d, "mark": mark,
                            "tracked_as": self.info.get(a, {}).get("reason")})

    def _fresh(self, row, a, c, ts, size, pos_usd) -> dict:
        o = self.orders.get(a)
        trig = (f" It was spotted placing a {usd(o['notional_usd'])} {o['side']} order on {o['coin']} at "
                f"{hhmm(o['event_ts'])}." if o is not None else "")
        val = row.get("account_value")
        acct = f" Account value {usd(val)}." if isinstance(val, (int, float)) and math.isfinite(val) else ""
        reason = (f"{short(a)} is an account Whale Watch had not seen before (not among the top accounts it tracks), "
                  f"holding a {usd(pos_usd)} {c} {side_of(size)} at {hhmm(ts)}.{trig}{acct}")
        return self._event("fresh_wallet", ts, c, a, f"Fresh wallet: {short(a)} holds a {usd(pos_usd)} {c} "
                           f"{side_of(size)}", reason, {"position_usd": pos_usd, "size": size,
                                                         "entry": row.get("entry"), "account_value": val})
