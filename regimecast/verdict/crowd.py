"""Crowd warning: when everyone leans the same way, a sudden move against them is more likely.

Three signals, each as a percentile rank against its own last 14 days (so the rule adapts when a coin's
normal level drifts, and the app can compute it from Binance's public 1-hour history):

* funding rate (longs pay shorts when it is positive),
* top traders' long/short position ratio (``ls_top``),
* open-interest growth over 24 h (new money coming in).

The crowd is LONG when funding and the long/short ratio are both high (rank >= q) and open interest is
growing fast (rank >= q); SHORT when funding and the ratio are both low (rank <= 1 - q) with the same
open-interest growth. ``rule`` "all3" needs all three, "2of3" any two (the two price-side ones counted
on the same side), "no_oi" only funding and the ratio. q and the rule are chosen on the development
period (scripts/run_phase12.py) and frozen.

The event the warning is about: a sudden 5% move against the crowd within 24 h (the mark price falls 5%
below the hour's close at some minute for a long crowd, rises 5% for a short one). It is compared with
the same-direction event in hours without a warning.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW_HOURS = 14 * 24
MIN_HOURS = int(0.75 * WINDOW_HOURS)
SIGNALS = ["funding_rate", "ls_top", "oi_chg_24h"]
MOVE = 0.05
CANDIDATES = [(q, rule) for q in (0.80, 0.90, 0.95) for rule in ("all3", "2of3", "no_oi")]


def trailing_rank(x: pd.Series, window: int = WINDOW_HOURS, min_hours: int = MIN_HOURS) -> pd.Series:
    """Percentile rank of each value among the last ``window`` hourly values (itself included, nothing
    after it): share below + half the share equal. NaN with fewer than ``min_hours`` values."""
    x = x.astype(float)
    full = x.reindex(pd.date_range(x.index.min(), x.index.max(), freq="1h")) if len(x) else x
    v = full.to_numpy()
    pad = np.concatenate([np.full(window - 1, np.nan), v])
    win_all = np.lib.stride_tricks.sliding_window_view(pad, window)
    r = np.full(len(v), np.nan)
    for a in range(0, len(v), 5000):        # in chunks: the full window matrix is large
        win, cur = win_all[a:a + 5000], v[a:a + 5000, None]
        valid = ~np.isnan(win)
        n = valid.sum(axis=1)
        below = ((win < cur) & valid).sum(axis=1)
        equal = ((win == cur) & valid).sum(axis=1)
        rr = (below + 0.5 * equal) / np.maximum(n, 1)
        r[a:a + 5000] = np.where((n >= min_hours) & ~np.isnan(v[a:a + 5000]), rr, np.nan)
    return pd.Series(r, index=full.index).reindex(x.index)


def ranks(h: pd.DataFrame) -> pd.DataFrame:
    """Ranks of the three signals; h is hourly, indexed by hour end."""
    return pd.DataFrame({f"{c}_rank": trailing_rank(h[c]) for c in SIGNALS}, index=h.index)


def warning(rk: pd.DataFrame, q: float, rule: str) -> pd.Series:
    """+1 crowd long, -1 crowd short, 0 none (also 0 when a rank is missing)."""
    f, ls, oi = (rk[f"{c}_rank"].to_numpy() for c in SIGNALS)
    hi_f, hi_l, lo_f, lo_l, oi_up = f >= q, ls >= q, f <= 1 - q, ls <= 1 - q, oi >= q
    if rule == "all3":
        lo, sh = hi_f & hi_l & oi_up, lo_f & lo_l & oi_up
    elif rule == "2of3":
        lo = (hi_f.astype(int) + hi_l + oi_up) >= 2
        sh = (lo_f.astype(int) + lo_l + oi_up) >= 2
        lo &= hi_f | hi_l        # at least one price-side signal decides the side
        sh &= lo_f | lo_l
        both = lo & sh
        lo, sh = lo & ~both, sh & ~both
    elif rule == "no_oi":
        lo, sh = hi_f & hi_l, lo_f & lo_l
    else:
        raise ValueError(rule)
    ok = ~np.isnan(f) & ~np.isnan(ls) & (~np.isnan(oi) | (rule == "no_oi"))
    return pd.Series(np.where(ok & lo, 1, np.where(ok & sh, -1, 0)), index=rk.index, name="crowd")


def events(mae_long: pd.Series, mae_short: pd.Series, move: float = MOVE) -> tuple[pd.Series, pd.Series]:
    """(drop, rise): did the mark price fall / rise ``move`` from the hour's close within 24 h?"""
    drop = (mae_long >= -np.log(1 - move)).where(mae_long.notna())
    rise = (mae_short >= np.log(1 + move)).where(mae_short.notna())
    return drop, rise


def ratio(warn: np.ndarray, drop: np.ndarray, rise: np.ndarray) -> dict:
    """How much more often the move against the crowd happens after a warning than in other hours.

    expected = for each warning hour, the rate of the same-direction move in hours without a warning;
    ratio = hits after warnings / expected hits."""
    warn, drop, rise = np.asarray(warn), np.asarray(drop, float), np.asarray(rise, float)
    ok = ~np.isnan(drop) & ~np.isnan(rise)
    warn, drop, rise = warn[ok], drop[ok], rise[ok]
    other = warn == 0
    base_drop = drop[other].mean() if other.any() else np.nan
    base_rise = rise[other].mean() if other.any() else np.nan
    lo, sh = warn == 1, warn == -1
    hits = drop[lo].sum() + rise[sh].sum()
    expected = lo.sum() * base_drop + sh.sum() * base_rise
    n = int(lo.sum() + sh.sum())
    return {"n_hours": int(ok.sum()), "n_warnings": n, "n_long": int(lo.sum()), "n_short": int(sh.sum()),
            "share_warned": n / max(int(ok.sum()), 1), "hits": float(hits), "expected": float(expected),
            "rate_warned": float(hits / n) if n else np.nan, "rate_other": float(expected / n) if n else np.nan,
            "ratio": float(hits / expected) if n and expected > 0 else np.nan}


def block_bootstrap(warn, drop, rise, block_hours: int = 7 * 24, reps: int = 1000, level: float = 0.90,
                    seed: int = 12) -> tuple[float, float]:
    """Interval for the ratio by a circular block bootstrap of hours (blocks of a week keep the
    clustering of crashes and of warnings)."""
    warn, drop, rise = np.asarray(warn), np.asarray(drop, float), np.asarray(rise, float)
    n = len(warn)
    if n == 0:
        return np.nan, np.nan
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(n / block_hours))
    out = []
    for _ in range(reps):
        starts = rng.integers(0, n, nb)
        idx = (starts[:, None] + np.arange(block_hours)[None, :]).ravel()[:n] % n
        r = ratio(warn[idx], drop[idx], rise[idx])["ratio"]
        if np.isfinite(r):
            out.append(r)
    if not out:
        return np.nan, np.nan
    a = (1 - level) / 2
    return float(np.quantile(out, a)), float(np.quantile(out, 1 - a))


def odds(warn: pd.Series, drop: pd.Series, rise: pd.Series) -> dict:
    """For the app: per crowd side, the chance of the 5% move against it within 24 h after a warning,
    and normally (hours without a warning)."""
    w = warn.to_numpy()
    ok = drop.notna().to_numpy() & rise.notna().to_numpy()
    d, r = drop.to_numpy(float), rise.to_numpy(float)
    other = ok & (w == 0)
    out = {}
    for side, s, ev in (("long", 1, d), ("short", -1, r)):
        m = ok & (w == s)
        out[side] = {"after_warning": float(ev[m].mean()) if m.any() else None,
                     "normally": float(ev[other].mean()) if other.any() else None, "n_warnings": int(m.sum())}
    return out
