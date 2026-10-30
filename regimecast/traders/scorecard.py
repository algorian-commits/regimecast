"""Trader scorecards (PLAN 13D task 2): track record, a skill test, and a label.

Everything is computed from the account's own fills (fills.py), perpetuals only (spot
fills have coin names like ``@107`` or ``PURR/USDC`` and are left out).

* **Daily PnL** = realised PnL of the day's closing fills minus the day's fees, every UTC
  day from the first available fill to the download (days without fills count as 0).
  Funding payments and the open positions' unrealised PnL are not in the fills, so they
  are not counted: a known limit, written on the page.
* **Trades** are round trips per coin: from flat (or the first fill seen) to flat again; a
  flip from long to short closes one trade and opens the next. Win rate, profit factor and
  holding time are over finished round trips that started inside the history.
* **Skill test**: is the average daily PnL above zero? A one-sided circular block
  bootstrap (blocks of about n^(1/3) days keep the day-to-day dependence): the series is
  shifted to mean 0 (the "no skill" world), resampled ``N_BOOT`` times, and the p-value is
  the share of resampled means at least as high as the real one. Accounts with fewer than
  ``MIN_DAYS`` days or ``MIN_TRADES`` finished trades get p = 1 (too little to tell).
* **Benjamini-Hochberg** over every account in the pool that has perp fills (FDR
  ``FDR_Q``), so "skilled" is not just the luckiest of thousands.
* **Labels**: ``skilled`` (BH-significant, average daily PnL above zero), ``losing``
  (net realised PnL below zero), ``unproven`` (everything else), ``no_trades``.

The skill score used for ranking is mean / bootstrap standard error (a t-like number that
does not depend on account size).
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

N_BOOT = 2000
MIN_DAYS = 30
MIN_TRADES = 10
FDR_Q = 0.05
LABELS = ("skilled", "unproven", "losing", "no_trades")


def is_perp(coin: str) -> bool:
    return not coin.startswith("@") and "/" not in coin


def perp_fills(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["coin"].map(is_perp)].reset_index(drop=True) if len(df) else df


def signed(df: pd.DataFrame) -> np.ndarray:
    return np.where(df["side"].to_numpy() == "B", 1.0, -1.0) * df["sz"].to_numpy(float)


def daily_pnl(df: pd.DataFrame, start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> pd.Series:
    """Realised PnL minus fees per UTC day, zeros on days without fills, from ``start`` to ``end`` (days)."""
    if len(df):
        day = df["time"].dt.floor("D")
        s = (df["closed_pnl"] - df["fee"]).groupby(day).sum()
    else:
        s = pd.Series(dtype=float)
    start = pd.Timestamp(start).floor("D") if start is not None else (s.index.min() if len(s) else None)
    end = pd.Timestamp(end).floor("D") if end is not None else (s.index.max() if len(s) else None)
    if start is None or end is None or end < start:
        return pd.Series(dtype=float)
    idx = pd.date_range(start, end, freq="D")
    return s.reindex(idx, fill_value=0.0).astype(float)


def round_trips(df: pd.DataFrame) -> pd.DataFrame:
    """Finished round trips per coin: open time, close time, PnL after fees, entry notional."""
    rows = []
    for coin, g in df.groupby("coin", sort=False):
        g = g.sort_values(["time", "tid"])
        start = g["start_position"].to_numpy(float)
        delta = signed(g)
        t = g["time"].to_numpy()
        pnl = (g["closed_pnl"] - g["fee"]).to_numpy(float)
        px = g["px"].to_numpy(float)
        cur = None   # the trip in progress
        for i in range(len(g)):
            before, after = start[i], start[i] + delta[i]
            tol = 1e-9 * max(abs(before), abs(delta[i]), 1.0)
            if cur is None:
                cur = {"coin": coin, "opened": t[i], "pnl": 0.0, "notional": 0.0,
                       "inside": abs(before) <= tol}   # started inside the history?
            cur["pnl"] += pnl[i]
            if abs(after) > abs(before) or before * after < 0:
                cur["notional"] = max(cur["notional"], abs(after) * px[i])
            flat = abs(after) <= tol
            flipped = before * after < 0 and not flat
            if flat or flipped:
                cur["closed"] = t[i]
                rows.append(cur)
                cur = None
                if flipped:
                    cur = {"coin": coin, "opened": t[i], "pnl": 0.0, "notional": abs(after) * px[i], "inside": True}
    out = pd.DataFrame(rows, columns=["coin", "opened", "closed", "pnl", "notional", "inside"])
    if len(out):
        out["hours"] = (pd.to_datetime(out["closed"]) - pd.to_datetime(out["opened"])).dt.total_seconds() / 3600
    else:
        out["hours"] = pd.Series(dtype=float)
    return out


def _blocks(n: int, n_boot: int, block: int, rng: np.random.Generator) -> np.ndarray:
    """Circular block bootstrap indices, shape (n_boot, n)."""
    k = math.ceil(n / block)
    starts = rng.integers(0, n, size=(n_boot, k))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(n_boot, k * block)[:, :n]
    return idx % n


def block_length(n: int) -> int:
    return max(1, round(n ** (1 / 3)))


def skill_test(x: np.ndarray, n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """One-sided block-bootstrap test of mean(x) > 0. Returns p, score (mean / SE), SE, block length."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 2 or not np.isfinite(x).all() or np.allclose(x, x[0]):
        return {"p": 1.0, "score": 0.0, "se": math.nan, "block": 1}
    rng = np.random.default_rng(seed)
    b = block_length(n)
    means = x[_blocks(n, n_boot, b, rng)].mean(axis=1)
    m = x.mean()
    se = means.std(ddof=1)
    null = means - m   # the same resampling in a world where the true mean is 0
    p = (1 + np.sum(null >= m)) / (n_boot + 1)
    return {"p": float(p), "score": float(m / se) if se > 0 else 0.0, "se": float(se), "block": b}


def benjamini_hochberg(p: np.ndarray, q: float = FDR_Q) -> np.ndarray:
    """True where the hypothesis is rejected at false discovery rate q."""
    p = np.asarray(p, float)
    m = len(p)
    if m == 0:
        return np.zeros(0, bool)
    order = np.argsort(p)
    ranked = p[order]
    ok = ranked <= q * np.arange(1, m + 1) / m
    k = np.nonzero(ok)[0].max() + 1 if ok.any() else 0
    rej = np.zeros(m, bool)
    rej[order[:k]] = True
    return rej


def max_drawdown(cum: np.ndarray) -> float:
    """Largest fall of a cumulative PnL curve from its running peak (dollars, >= 0)."""
    if len(cum) == 0:
        return 0.0
    curve = np.concatenate([[0.0], cum])
    return float(np.max(np.maximum.accumulate(curve) - curve))


def track_record(df: pd.DataFrame, end: pd.Timestamp, account_value: float | None = None,
                 n_boot: int = N_BOOT, seed: int = 0) -> dict:
    """Scorecard numbers for one account (fills already filtered to perps); no label yet."""
    if not len(df):
        return {"n_fills": 0, "n_trades": 0, "n_days": 0, "p": math.nan, "score": math.nan}
    d = daily_pnl(df, df["time"].min(), end)
    rt = round_trips(df)
    done = rt[rt["inside"]]
    wins, losses = done.loc[done["pnl"] > 0, "pnl"].sum(), -done.loc[done["pnl"] < 0, "pnl"].sum()
    by_coin = (df["px"] * df["sz"]).groupby(df["coin"]).sum().sort_values(ascending=False)
    total = float(d.sum())
    x = d.to_numpy()
    sd = x.std(ddof=1) if len(x) > 1 else math.nan
    enough = len(x) >= MIN_DAYS and len(done) >= MIN_TRADES
    test = skill_test(x, n_boot, seed) if enough else {"p": 1.0, "score": 0.0, "se": math.nan, "block": 0}
    av = account_value if account_value and account_value > 0 else math.nan
    return {
        "n_fills": int(len(df)), "n_trades": int(len(done)), "n_days": int(len(x)),
        "first_day": d.index.min().date().isoformat(), "last_fill": df["time"].max().isoformat(),
        "net_pnl": total, "mean_daily_pnl": float(x.mean()),
        "win_rate": float((done["pnl"] > 0).mean()) if len(done) else math.nan,
        "profit_factor": float(wins / losses) if losses > 0 else (math.inf if wins > 0 else math.nan),
        "return_on_account": total / av if math.isfinite(av) else math.nan,
        "sharpe": float(x.mean() / sd * math.sqrt(365)) if sd and sd > 0 else math.nan,
        "max_drawdown": max_drawdown(np.cumsum(x)),
        "max_drawdown_of_account": max_drawdown(np.cumsum(x)) / av if math.isfinite(av) else math.nan,
        "median_hold_hours": float(done["hours"].median()) if len(done) else math.nan,
        "fees": float(df["fee"].sum()),
        "coins": [{"coin": c, "share": float(v / by_coin.sum())} for c, v in by_coin.head(5).items()],
        "tested": enough, "p": test["p"], "score": test["score"], "block_days": test["block"],
    }


def label(cards: pd.DataFrame, q: float = FDR_Q) -> pd.DataFrame:
    """Adds BH significance and the label across the whole pool (rows = accounts)."""
    cards = cards.copy()
    has = cards["n_fills"].fillna(0) > 0
    p = cards.loc[has, "p"].fillna(1.0).to_numpy()
    rej = np.zeros(len(cards), bool)
    rej[np.nonzero(has.to_numpy())[0]] = benjamini_hochberg(p, q)
    cards["bh_significant"] = rej & (cards["mean_daily_pnl"].fillna(0) > 0)
    cards["label"] = np.select(
        [~has, cards["bh_significant"], cards["net_pnl"].fillna(0) < 0],
        ["no_trades", "skilled", "losing"], default="unproven")
    cards["bh_family"] = int(has.sum())
    return cards
