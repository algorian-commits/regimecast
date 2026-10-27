"""Crowd warning: when traders pile into one side, a sudden move against them is more common.

At each hour end H (all inputs known at H, from the hourly feature table or the same feature code live):

* long-crowded warning: funding at or above its ``level`` percentile AND top traders' long/short position ratio at
  or above its ``level`` percentile AND open-interest growth over 24 h at or above its ``oi_level`` percentile;
* short-crowded warning: funding and the ratio at or below their (1 - ``level``) percentiles, open interest growing
  as above.

The percentiles are fixed per coin on 2022-01 to 2024-09 and frozen as numbers, so later hours never move them.
Outside warnings the crowd still leans one way: long when (funding - median) / IQR + (ratio - median) / IQR >= 0,
else short. A "sudden 5% move against the crowd" = within (H, H + 24 h] the mark price falls at least 5% below
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
COLS = ("funding_rate", "ls_top", "oi_chg_24h")


def fit(P: pd.DataFrame, level: float, oi_level: float) -> dict:
    """Thresholds from the hours in P (the choose period only)."""
    f, r, o = (P[c].dropna() for c in COLS)
    iqr = lambda s: float(s.quantile(0.75) - s.quantile(0.25)) or 1e-12     # noqa: E731
    return {"level": level, "oi_level": oi_level,
            "funding_hi": float(f.quantile(level)), "funding_lo": float(f.quantile(1 - level)),
            "ls_hi": float(r.quantile(level)), "ls_lo": float(r.quantile(1 - level)),
            "oi_hi": float(o.quantile(oi_level)),
            "funding_mid": float(f.median()), "funding_scale": iqr(f),
            "ls_mid": float(r.median()), "ls_scale": iqr(r)}


def warning(P: pd.DataFrame, th: dict) -> pd.Series:
    """+1 long-crowded, -1 short-crowded, 0 no warning (or an input missing)."""
    f, r, o = (P[c] for c in COLS)
    growing = o >= th["oi_hi"]
    long_ = (f >= th["funding_hi"]) & (r >= th["ls_hi"]) & growing
    short = (f <= th["funding_lo"]) & (r <= th["ls_lo"]) & growing
    return pd.Series(np.where(long_, 1, np.where(short, -1, 0)), index=P.index)


def lean(P: pd.DataFrame, th: dict) -> pd.Series:
    """+1 the crowd leans long, -1 short, 0 an input is missing."""
    z = (P["funding_rate"] - th["funding_mid"]) / th["funding_scale"] + (P["ls_top"] - th["ls_mid"]) / th["ls_scale"]
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
        what = ("high funding, top traders long, open interest growing" if self.side == "long"
                else "negative funding, top traders short, open interest growing")
        move = "fell" if self.side == "long" else "rose"
        tail = ""
        if self.p_after is not None and self.p_other is not None:
            tail = (f" After warnings like this (2022-2025), {coin} {move} 5% or more within a day "
                    f"{100 * self.p_after:.0f}% of the time, vs {100 * self.p_other:.0f}% in other hours.")
        return f"Traders are crowded on the {self.side} side ({what}).{tail}"


def assess(state: dict | None, th: dict | None) -> Crowd | None:
    """state: the latest hour's funding_rate, ls_top, oi_chg_24h. None without inputs or frozen thresholds."""
    if not state or not th or any(state.get(c) is None or not np.isfinite(state[c]) for c in COLS):
        return None
    w = int(warning(pd.DataFrame([{c: state[c] for c in COLS}]), th).iloc[0])
    side = {1: "long", -1: "short"}.get(w)
    c = Crowd(side, state["funding_rate"], state["ls_top"], state["oi_chg_24h"])
    od = th.get("odds", {})
    if side and od.get(side, {}).get("after_warning") is not None:
        c.p_after, c.p_other, c.n = od[side]["after_warning"], od.get("other"), od[side]["n"]
    return c
