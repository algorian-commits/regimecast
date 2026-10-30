"""The copy test (PLAN 13D task 5), exactly as pre-registered on 31 Oct 2026.

Question: would copying the skilled traders have made money?

1. **History check first** (``history_gate``): an account "has 6+ months of fills" when its
   first and last available fills are at least ``MIN_SPAN_DAYS`` apart. At least
   ``MIN_ACCOUNTS`` such accounts are needed for the period-A / period-B test; otherwise the
   test becomes the 8-week forward test (forward.py).
2. **Periods**: the common history of those accounts runs from the latest of their first
   fills to the earliest download time. A is its first two thirds, B the last third.
3. **Selection on A only**: the scorecard's skill score (scorecard.py, perps, realised
   daily PnL) computed on fills before B starts. The copy universe is the eligible accounts
   with at least one BTC/ETH/SOL fill in A; the top ``TOP_SHARE`` of them by score are copied.
4. **Copying in B**: the copier holds each selected trader's BTC, ETH and SOL position,
   ``DELAY`` after each of their fills, at the minute close then (Binance USD-M 1-minute
   prices), pays ``TAKER_FEE`` on every change and Hyperliquid's hourly funding.
   Sizing: equal risk per trader, each scaled by 1 / (standard deviation of its copied
   daily PnL in A).
5. **Comparisons on the same days**: ``N_RANDOM`` random draws of the same number of
   accounts from the copy universe (copied the same way; the median Sharpe ratio is the
   bar), and buy-and-hold of BTC, ETH and SOL (a third each, 1x, paying funding).
6. **Pass** if the copy portfolio's Sharpe ratio beats both, and the 90% block-bootstrap
   interval of (copy Sharpe - median random Sharpe) lies above 0.

Nothing in B is read before selection is fixed: ``select`` only receives fills before
``b_start`` (tested).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from regimecast.traders import scorecard as sc

MIN_SPAN_DAYS = 182
MIN_ACCOUNTS = 60
TOP_SHARE = 0.10
DELAY = pd.Timedelta(minutes=5)
TAKER_FEE = 0.00045
COPY_COINS = ("BTC", "ETH", "SOL")
N_RANDOM = 200
N_BOOT = 2000
SEED = 13


# ------------------------------------------------------------------ 1-2. history check and periods
def history_gate(depth: pd.DataFrame, min_span_days: float = MIN_SPAN_DAYS, min_accounts: int = MIN_ACCOUNTS) -> dict:
    d = depth[depth["n_fills"] > 0].copy()
    first, last = pd.to_datetime(d["first_fill"], utc=True), pd.to_datetime(d["last_fill"], utc=True)
    d["span_days"] = (last - first).dt.total_seconds() / 86400
    ok = d[d["span_days"] >= min_span_days]
    return {"accounts_with_fills": int(len(d)), "accounts_6m": int(len(ok)), "needed": min_accounts,
            "min_span_days": min_span_days, "historical_test": bool(len(ok) >= min_accounts),
            "eligible": sorted(ok["address"]),
            "span_days_quantiles": {str(q): float(d["span_days"].quantile(q)) for q in (0.5, 0.75, 0.9, 0.95)}
            if len(d) else {}}


def periods(depth: pd.DataFrame, eligible: list[str]) -> dict:
    d = depth.set_index("address").loc[eligible]
    start = pd.to_datetime(d["first_fill"], utc=True).max().ceil("D")
    end = pd.to_datetime(d["downloaded_at"], utc=True).min().floor("D")
    b_start = start + (end - start) * 2 / 3
    b_start = b_start.floor("D")
    return {"a_start": start, "b_start": b_start, "end": end}


# ------------------------------------------------------------------ 3. selection (period A only)
def copy_fills(fills: pd.DataFrame) -> pd.DataFrame:
    return fills[fills["coin"].isin(COPY_COINS)]


def select(fills_a: dict[str, pd.DataFrame], b_start: pd.Timestamp, top_share: float = TOP_SHARE,
           n_boot: int = sc.N_BOOT) -> pd.DataFrame:
    """Skill scores on period A and the chosen accounts. Every fill passed in must be before ``b_start``."""
    rows = []
    for a, f in fills_a.items():
        if len(f) and f["time"].max() >= b_start:
            raise ValueError("selection was given a fill from period B")
        p = sc.perp_fills(f)
        tr = sc.track_record(p, b_start - pd.Timedelta(days=1), n_boot=n_boot)
        rows.append({"address": a, "score": tr.get("score", math.nan), "p": tr.get("p", math.nan),
                     "n_days": tr.get("n_days", 0), "n_trades": tr.get("n_trades", 0),
                     "copyable": bool(len(copy_fills(p)))})
    s = pd.DataFrame(rows)
    uni = s[s["copyable"]].sort_values(["score", "address"], ascending=[False, True])
    k = max(1, math.ceil(top_share * len(uni))) if len(uni) else 0
    s["selected"] = s["address"].isin(uni["address"].head(k))
    s["in_universe"] = s["copyable"]
    return s


# ------------------------------------------------------------------ 4. copying
def positions_after(f: pd.DataFrame) -> np.ndarray:
    return f["start_position"].to_numpy(float) + sc.signed(f)


def simulate_coin(f: pd.DataFrame, close: pd.Series, funding: pd.Series, t0: pd.Timestamp, t1: pd.Timestamp,
                  delay: pd.Timedelta = DELAY, fee: float = TAKER_FEE) -> pd.Series:
    """Daily PnL (dollars, 1 coin unit = 1 of the trader's) of holding one trader's position in one coin,
    ``delay`` after each fill, from ``t0`` to ``t1``.

    ``close``: 1-minute closes indexed by the minute's open time; ``funding``: hourly rates indexed by time.
    The copier trades at the close of the minute that contains fill time + delay."""
    px = close[(close.index >= t0) & (close.index < t1)]
    days = pd.date_range(t0.floor("D"), (t1 - pd.Timedelta(minutes=1)).floor("D"), freq="D")
    if px.empty:
        return pd.Series(0.0, index=days)
    f = f.sort_values(["time", "tid"])
    after = positions_after(f)
    execm = (f["time"] + delay).dt.floor("min").to_numpy()
    grid = px.index.to_numpy()
    # position at the close of each minute: the last fill executed at or before that minute
    k = np.searchsorted(execm, grid, side="right") - 1
    first_start = f["start_position"].to_numpy(float)[0] if len(f) else 0.0
    pos = np.where(k >= 0, after[np.clip(k, 0, None)] if len(after) else 0.0, first_start)
    p = px.to_numpy(float)
    pnl = np.zeros(len(p))
    pnl[1:] = pos[:-1] * np.diff(p)
    trade = np.abs(np.diff(np.concatenate([[pos[0]], pos])))
    pnl -= trade * p * fee
    out = pd.Series(pnl, index=px.index)
    if len(funding):
        fr = funding[(funding.index >= t0) & (funding.index < t1)]
        if len(fr):
            at = np.searchsorted(grid, fr.index.to_numpy(), side="right") - 1
            ok = at >= 0
            pay = -pos[at[ok]] * p[at[ok]] * fr.to_numpy(float)[ok]
            out = out.add(pd.Series(pay, index=fr.index[ok]).groupby(level=0).sum(), fill_value=0.0)
    return out.groupby(out.index.floor("D")).sum().reindex(days, fill_value=0.0)


def simulate_trader(f: pd.DataFrame, prices: dict[str, pd.Series], funding: dict[str, pd.Series],
                    t0: pd.Timestamp, t1: pd.Timestamp) -> pd.Series:
    days = pd.date_range(t0.floor("D"), (t1 - pd.Timedelta(minutes=1)).floor("D"), freq="D")
    total = pd.Series(0.0, index=days)
    f = copy_fills(f)
    for c in COPY_COINS:
        g = f[f["coin"] == c]
        g = g[g["time"] < t1]
        if len(g):
            total = total.add(simulate_coin(g, prices[c], funding.get(c, pd.Series(dtype=float)), t0, t1),
                              fill_value=0.0)
    return total


def buy_and_hold(prices: dict[str, pd.Series], funding: dict[str, pd.Series], t0, t1) -> pd.Series:
    """Daily return of a third of the account in each coin at t0 (1x long, quantities fixed, funding paid)."""
    pnl = None
    for c in COPY_COINS:
        px = prices[c]
        q = (1 / 3) / float(px[px.index >= t0].iloc[0])
        f = pd.DataFrame({"time": [t0 - pd.Timedelta(minutes=10)], "start_position": [0.0], "side": ["B"],
                          "sz": [q], "tid": [0], "coin": [c]})
        s = simulate_coin(f, px, funding.get(c, pd.Series(dtype=float)), t0, t1, delay=pd.Timedelta(0), fee=0.0)
        pnl = s if pnl is None else pnl + s
    equity = 1.0 + pnl.cumsum()
    return pnl / equity.shift(1, fill_value=1.0)


# ------------------------------------------------------------------ 5-6. comparison and bar
def sharpe(x: np.ndarray, axis: int = -1) -> np.ndarray:
    x = np.asarray(x, float)
    sd = x.std(axis=axis, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(sd > 0, x.mean(axis=axis) / sd * math.sqrt(365), 0.0)


def portfolio(daily: dict[str, pd.Series], scale: dict[str, float], members: list[str]) -> np.ndarray:
    return np.sum([daily[a].to_numpy() * scale[a] for a in members], axis=0)


def compare(daily_b: dict[str, pd.Series], scale: dict[str, float], selected: list[str], universe: list[str],
            bh: pd.Series, n_random: int = N_RANDOM, n_boot: int = N_BOOT, seed: int = SEED) -> dict:
    rng = np.random.default_rng(seed)
    k = len(selected)
    copy = portfolio(daily_b, scale, selected)
    draws = [sorted(rng.choice(universe, size=k, replace=False)) for _ in range(n_random)]
    rand = np.array([portfolio(daily_b, scale, d) for d in draws])   # (n_random, days)
    s_copy, s_rand = float(sharpe(copy)), sharpe(rand, axis=1)
    s_bh = float(sharpe(bh.to_numpy()))
    n = len(copy)
    idx = sc._blocks(n, n_boot, sc.block_length(n), rng)
    diff = sharpe(copy[idx], axis=1) - np.median(sharpe(rand[:, idx], axis=2), axis=0)
    lo, hi = np.quantile(diff, [0.05, 0.95])
    med = float(np.median(s_rand))
    return {"days": n, "k": k, "sharpe_copy": s_copy, "sharpe_random_median": med,
            "sharpe_random_q05_q95": [float(np.quantile(s_rand, 0.05)), float(np.quantile(s_rand, 0.95))],
            "share_random_beaten": float(np.mean(s_rand < s_copy)), "sharpe_buy_and_hold": s_bh,
            "diff_vs_random_ci90": [float(lo), float(hi)],
            "beats_random": s_copy > med, "beats_buy_and_hold": s_copy > s_bh, "ci_above_zero": bool(lo > 0),
            "passed": bool(s_copy > med and s_copy > s_bh and lo > 0),
            "copy_total_pnl_units": float(copy.sum())}
