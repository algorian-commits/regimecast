"""Crowd warning: when traders pile into one side, a sudden move against them is more common.

At each hour end H (all inputs known at H, from the hourly feature table or Binance's public 1-hour history live),
each of funding, top traders' long/short position ratio and open-interest growth over 24 h is ranked against its
own last 14 days (``trailing_rank``: H and the 335 hours before it, nothing after), so the rule keeps working when a
coin's usual level drifts (ETH's ratio ranged from 1.1 to 3.5 in 2022-25):

* long-crowded warning: funding rank and ratio rank at or above ``level`` AND open-interest growth rank at or above
  ``oi_level``;
* short-crowded warning: funding rank and ratio rank at or below 1 - ``level``, open interest growing as above.

``level`` and ``oi_level`` are chosen on 2022-01 to 2024-09 and frozen. Outside warnings the crowd still leans one
way: long when funding rank + ratio rank >= 1, else short. A "sudden 5% move against the crowd" = within (H, H + 24 h] the mark price falls at least 5% below
its value at H (crowd long) or rises at least 5% above it (crowd short), from the 24 h worst-move targets.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

MOVE = 0.05
AGAINST_LONG = -np.log(1 - MOVE)       # mae_long_24h at or above this: a 5% fall
AGAINST_SHORT = np.log(1 + MOVE)       # mae_short_24h at or above this: a 5% rise
LEVELS = (0.80, 0.85, 0.90, 0.95)      # candidates for funding and the ratio (chosen on the dev period)
OI_LEVELS = (0.75, 0.90)               # candidates for open-interest growth
RAW = ("funding_rate", "ls_top", "oi_chg_24h")
COLS = ("funding_rank", "ls_rank", "oi_rank")           # what the rule reads: RAW ranked over the last 14 days
WINDOW_HOURS = 14 * 24
MIN_HOURS = int(0.75 * WINDOW_HOURS)


def trailing_rank(x: pd.Series, window: int = WINDOW_HOURS, min_hours: int = MIN_HOURS) -> pd.Series:
    """Percentile rank of each hourly value among the last ``window`` hours (itself included, nothing after it):
    share below + half the share equal. NaN with fewer than ``min_hours`` values in the window."""
    x = x.astype(float).sort_index()
    if not len(x):
        return x
    full = x.reindex(pd.date_range(x.index.min(), x.index.max(), freq="1h"))
    v = full.to_numpy()
    win_all = np.lib.stride_tricks.sliding_window_view(np.concatenate([np.full(window - 1, np.nan), v]), window)
    r = np.full(len(v), np.nan)
    for a in range(0, len(v), 5000):        # in chunks: the full window matrix is large
        win, cur = win_all[a:a + 5000], v[a:a + 5000, None]
        valid = ~np.isnan(win)
        n = valid.sum(axis=1)
        rr = (((win < cur) & valid).sum(axis=1) + 0.5 * ((win == cur) & valid).sum(axis=1)) / np.maximum(n, 1)
        r[a:a + 5000] = np.where((n >= min_hours) & ~np.isnan(v[a:a + 5000]), rr, np.nan)
    return pd.Series(r, index=full.index).reindex(x.index)


def with_ranks(P: pd.DataFrame) -> pd.DataFrame:
    """P with the three rank columns added (P hourly, indexed by hour end)."""
    return P.assign(**{c: trailing_rank(P[raw]) for c, raw in zip(COLS, RAW)})


def fit(P: pd.DataFrame, level: float, oi_level: float) -> dict:
    """Thresholds on the rank scale (the same for every coin; ``P`` is kept for the call signature)."""
    return {"level": level, "oi_level": oi_level, "funding_hi": level, "funding_lo": 1 - level,
            "ls_hi": level, "ls_lo": 1 - level, "oi_hi": oi_level}


def warning(P: pd.DataFrame, th: dict) -> pd.Series:
    """+1 long-crowded, -1 short-crowded, 0 no warning (or an input missing)."""
    f, r, o = (P[c] for c in COLS)
    growing = o >= th["oi_hi"]
    long_ = (f >= th["funding_hi"]) & (r >= th["ls_hi"]) & growing
    short = (f <= th["funding_lo"]) & (r <= th["ls_lo"]) & growing
    return pd.Series(np.where(long_, 1, np.where(short, -1, 0)), index=P.index)


def lean(P: pd.DataFrame, th: dict) -> pd.Series:
    """+1 the crowd leans long, -1 short, 0 an input is missing."""
    z = P["funding_rank"] + P["ls_rank"] - 1
    return pd.Series(np.where(z.isna(), 0, np.where(z >= 0, 1, -1)), index=P.index)


def against(P: pd.DataFrame, direction: pd.Series) -> pd.Series:
    """1.0 if the 24 h after H held a 5% move against ``direction`` (+1 long, -1 short), NaN if unknown."""
    down = P["mae_long_24h"] >= AGAINST_LONG
    up = P["mae_short_24h"] >= AGAINST_SHORT
    hit = np.where(direction > 0, down, np.where(direction < 0, up, False)).astype(float)
    known = np.where(direction > 0, P["mae_long_24h"].notna(), P["mae_short_24h"].notna()) & (direction != 0)
    return pd.Series(np.where(known, hit, np.nan), index=P.index)


def outcomes(P: pd.DataFrame, th: dict) -> pd.DataFrame:
    """Per hour: the warning, the crowd's side (warning side, else its lean) and whether a 5% move came against it."""
    w = warning(P, th)
    side = w.where(w != 0, lean(P, th))
    return pd.DataFrame({"warning": w != 0, "side": side, "against": against(P, side)}, index=P.index)


def ratio(o: pd.DataFrame) -> tuple[float, float, float, int]:
    """P(5% against | warning), P(5% against | other hours), their ratio, and the number of warning hours."""
    ok = o.dropna(subset=["against"])
    w = ok[ok["warning"]]
    rest = ok[~ok["warning"]]
    a = float(w["against"].mean()) if len(w) else np.nan
    b = float(rest["against"].mean()) if len(rest) else np.nan
    return a, b, (a / b if b and np.isfinite(a) else np.nan), int(len(w))


def odds(o: pd.DataFrame) -> dict:
    """What the app shows with a warning: the historical odds per crowd side, and in other hours."""
    ok = o.dropna(subset=["against"])
    out = {"other": float(ok.loc[~ok["warning"], "against"].mean())}
    for name, s in (("long", 1), ("short", -1)):
        w = ok[ok["warning"] & (ok["side"] == s)]
        out[name] = {"after_warning": float(w["against"].mean()) if len(w) else None, "n": int(len(w))}
    return out


# ------------------------------------------------------------------ live
@dataclass
class Crowd:
    side: str | None                 # "long" or "short" when a warning is on
    funding_rate: float | None
    ls_top: float | None
    oi_chg_24h: float | None
    p_after: float | None = None     # dev period: share of warnings followed by a 5% move against the crowd in 24 h
    p_other: float | None = None
    n: int = 0

    def message(self, coin: str) -> str:
        if self.side is None:
            return ""
        what = ("funding and top traders' longs at two-week highs, open interest growing" if self.side == "long"
                else "funding and top traders' longs at two-week lows, open interest growing")
        move = "fell" if self.side == "long" else "rose"
        tail = ""
        if self.p_after is not None and self.p_other is not None:
            tail = (f" After warnings like this (2022-2025), {coin} {move} 5% or more within a day "
                    f"{100 * self.p_after:.0f}% of the time, vs {100 * self.p_other:.0f}% in other hours.")
        return f"Traders are crowded on the {self.side} side ({what}).{tail}"


def assess(state: dict | None, th: dict | None) -> Crowd | None:
    """state: the latest hour's funding_rank, ls_rank, oi_rank (and the raw values, for display).
    None without inputs or frozen thresholds."""
    if not state or not th or any(state.get(c) is None or not np.isfinite(state[c]) for c in COLS):
        return None
    w = int(warning(pd.DataFrame([{c: state[c] for c in COLS}]), th).iloc[0])
    side = {1: "long", -1: "short"}.get(w)
    c = Crowd(side, state.get("funding_rate"), state.get("ls_top"), state.get("oi_chg_24h"))
    od = th.get("odds", {})
    if side and od.get(side, {}).get("after_warning") is not None:
        c.p_after, c.p_other, c.n = od[side]["after_warning"], od.get("other"), od[side]["n"]
    return c


# ------------------------------------------------------------------ live ranks
def rank_frame(oi: pd.Series, ls: pd.Series, funding: pd.Series) -> pd.DataFrame:
    """Hourly frame (index = hour end) from Binance's 1-hour open interest and long/short history and the funding
    settlements, with the raw signals as in the feature table and their 14-day ranks."""
    end = max(oi.index.max(), ls.index.max()).floor("1h")
    grid = pd.date_range(end - pd.Timedelta(hours=max(len(oi), len(ls)) - 1), end, freq="1h")
    asof = lambda s: s.sort_index().reindex(grid, method="ffill")   # noqa: E731  (last value known at each hour)
    h = pd.DataFrame({"oi": asof(oi), "ls_top": asof(ls), "funding_rate": asof(funding)}, index=grid)
    h["oi_chg_24h"] = np.log(h["oi"] / h["oi"].shift(24))
    return with_ranks(h)


def fetch_live_ranks(symbol: str, client=None, limit: int = 500) -> dict:
    """The latest hour's crowd signals and their 14-day ranks from Binance's public API (about 20 days of 1-hour
    history; the feature table used the 5-minute value at each hour end, the 1-hour history gives the same moments)."""
    import httpx

    from regimecast import config

    own = client is None
    client = client or httpx.Client(base_url=config.BINANCE_FAPI_URL, timeout=10.0)
    try:
        def stats(path: str, col: str) -> pd.Series:
            r = client.get(path, params={"symbol": symbol, "period": "1h", "limit": limit})
            r.raise_for_status()
            df = pd.DataFrame(r.json())
            ts = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
            return pd.Series(df[col].astype(float).to_numpy(), index=ts)

        oi = stats("/futures/data/openInterestHist", "sumOpenInterest")
        ls = stats("/futures/data/topLongShortPositionRatio", "longShortRatio")
        fr = client.get("/fapi/v1/fundingRate", params={"symbol": symbol, "limit": 100})
        fr.raise_for_status()
        f = pd.DataFrame(fr.json())
    finally:
        if own:
            client.close()
    funding = pd.Series(f["fundingRate"].astype(float).to_numpy(),
                        index=pd.to_datetime(f["fundingTime"].astype("int64"), unit="ms", utc=True))
    last = rank_frame(oi, ls, funding).iloc[-1]
    return {c: (float(last[c]) if pd.notna(last[c]) else None) for c in COLS}
