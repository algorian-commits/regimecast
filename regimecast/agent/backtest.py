"""Hourly backtester for an equal-weight, long-only BTC/ETH/SOL perpetual account.

Timeline at each hour end H (the same labelling as every feature table):

1. the positions held over (H - 1 h, H] are marked to the mark price at H;
2. if H is a funding time (00:00, 08:00, 16:00 UTC), those positions pay or receive funding
   at the rate Binance charged at H: a long pays ``notional x rate`` when the rate is
   positive and receives it when it is negative;
3. the decision made at H (from signals stamped at or before H) sets each coin's position
   for (H, H + 1 h]: ``exposure x equity / 3`` of notional. A change smaller than the
   no-trade band is skipped; every change that is made pays the taker fee on its notional.

Exposure is the position's notional as a share of the coin's third of the account (0 to 1),
so the whole account is never more than 1x long and never short. A missing target (NaN)
keeps the position as it is. Unless ``final=True``, no price, funding or decision may be
stamped inside the final test period (from config.HOLDOUT_START).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from regimecast import config

TAKER_FEE = 0.0005                  # Binance USD-M taker fee, 0.05% of the notional traded
FUNDING_HOURS = (0, 8, 16)          # UTC hours when BTC/ETH/SOL perpetuals settle funding
HOURS_PER_YEAR = config.HOURS_PER_YEAR


def holdout_start() -> pd.Timestamp:
    return pd.Timestamp(config.HOLDOUT_START, tz="UTC")


def check_closed(index: pd.DatetimeIndex, final: bool) -> None:
    """Refuse anything stamped inside the final test period unless final=True."""
    if not final and len(index) and index.max() >= holdout_start():
        raise ValueError(f"data reaches into the final test period ({config.HOLDOUT_START}); pass final=True")


@dataclass
class Result:
    hourly: pd.DataFrame        # equity, ret, fee, funding, turnover, exposure_<coin> per hour end
    trades: int

    @property
    def equity(self) -> pd.Series:
        return self.hourly["equity"]


def simulate(mark: pd.DataFrame, funding: pd.DataFrame, target: pd.DataFrame, band: float = 0.0,
             fee: float = TAKER_FEE, final: bool = False) -> Result:
    """Run the account through ``mark.index`` (hour ends, one row per hour).

    mark:    mark price per coin (columns = coins)
    funding: funding rate charged at each funding time per coin (NaN at other hours)
    target:  exposure 0..1 decided at each hour (NaN = keep the position)
    """
    check_closed(mark.index, final)
    coins = list(mark.columns)
    k = len(coins)
    funding = funding.reindex(index=mark.index, columns=coins)
    target = target.reindex(index=mark.index, columns=coins)
    m = mark.to_numpy(float)
    fr = funding.to_numpy(float)
    w = target.to_numpy(float)
    n = len(mark)
    q = np.zeros(k)                         # position in coins
    last = np.full(k, np.nan)               # last valid mark price per coin
    eq = 1.0
    out = np.zeros((n, 5 + k))              # equity, ret, fee, funding, turnover, exposures
    trades = 0
    for i in range(n):
        prev = eq
        price = m[i]
        ok = np.isfinite(price)
        # 1. mark to market (a coin without a price this hour keeps its last price)
        move = np.where(ok & np.isfinite(last), price - last, 0.0)
        eq += float(q @ move)
        last = np.where(ok, price, last)
        # 2. funding on the positions held at the funding time
        paid = 0.0
        f = fr[i]
        has = np.isfinite(f) & np.isfinite(last)
        if has.any():
            paid = float(np.sum(np.where(has, q * last * f, 0.0)))
            eq -= paid
        # 3. decision and trades
        notional = q * np.where(np.isfinite(last), last, 0.0)
        sleeve = max(eq, 0.0) / k
        want = w[i]
        cost = 0.0
        traded = 0.0
        for j in range(k):
            if not (np.isfinite(want[j]) and np.isfinite(last[j])):
                continue
            x = notional[j] / sleeve if sleeve > 0 else 0.0
            tgt = min(max(want[j], 0.0), 1.0)
            if abs(tgt - x) < band:
                continue
            delta = tgt * sleeve - notional[j]
            if delta == 0.0:
                continue
            q[j] += delta / last[j]
            traded += abs(delta)
            cost += abs(delta) * fee
            trades += 1
        eq -= cost
        expo = q * np.where(np.isfinite(last), last, 0.0) / (max(eq, 1e-12) / k)
        out[i, 0] = eq
        out[i, 1] = eq / prev - 1.0
        out[i, 2] = cost / prev
        out[i, 3] = paid / prev
        out[i, 4] = traded / prev
        out[i, 5:] = expo
    cols = ["equity", "ret", "fee", "funding", "turnover", *[f"exposure_{c}" for c in coins]]
    return Result(pd.DataFrame(out, index=mark.index, columns=cols), trades)


# ------------------------------------------------------------------ metrics
def max_drawdown(equity: pd.Series) -> float:
    """Largest fall from a previous peak, as a negative fraction (-0.5 = lost half)."""
    e = equity.to_numpy(float)
    peak = np.maximum.accumulate(np.concatenate([[1.0], e]))[1:]
    return float(np.min(e / peak - 1.0)) if len(e) else np.nan


def sharpe(ret: pd.Series | np.ndarray) -> float:
    r = np.asarray(ret, float)
    sd = r.std(ddof=1)
    return float(r.mean() / sd * np.sqrt(HOURS_PER_YEAR)) if sd > 0 else np.nan


def metrics(res: Result, start_equity: float = 1.0) -> dict:
    h = res.hourly
    n = len(h)
    years = n / HOURS_PER_YEAR
    total = float(h["equity"].iloc[-1] / start_equity - 1.0)
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else np.nan
    mdd = max_drawdown(h["equity"] / start_equity)
    expo = h.filter(like="exposure_")
    return {
        "hours": n,
        "total_return": total,
        "annual_return": cagr,
        "annual_vol": float(h["ret"].std(ddof=1) * np.sqrt(HOURS_PER_YEAR)),
        "sharpe": sharpe(h["ret"]),
        "max_drawdown": mdd,
        "calmar": cagr / abs(mdd) if mdd < 0 else np.nan,
        "turnover_per_year": float(h["turnover"].sum() / years) if years > 0 else np.nan,
        "fees_paid": float(h["fee"].sum()),          # sum of hourly fees as a share of equity
        "funding_paid": float(h["funding"].sum()),   # negative = received
        "avg_exposure": float(expo.mean(axis=1).mean()),
        "time_in_market": float((expo.sum(axis=1) > 1e-9).mean()),
        "trades": res.trades,
    }


def sharpe_diff_bootstrap(a: pd.Series, b: pd.Series, block: int = 168, n: int = 2000,
                          seed: int = 0) -> dict:
    """Sharpe(a) - Sharpe(b) on the same hours, with a 95% circular block-bootstrap interval.

    Whole blocks of ``block`` hours (default one week) are resampled together for both series,
    so their correlation and the clustering of volatility are kept."""
    df = pd.concat([a, b], axis=1).dropna()
    x, y = df.iloc[:, 0].to_numpy(), df.iloc[:, 1].to_numpy()
    T = len(x)
    rng = np.random.default_rng(seed)
    nb = int(np.ceil(T / block))
    diffs = []
    for _ in range(0, n, 200):
        m = min(200, n - len(diffs))
        starts = rng.integers(0, T, size=(m, nb))
        idx = ((starts[:, :, None] + np.arange(block)[None, None, :]) % T).reshape(m, -1)[:, :T]
        xs, ys = x[idx], y[idx]
        sx = xs.mean(1) / xs.std(1, ddof=1)
        sy = ys.mean(1) / ys.std(1, ddof=1)
        diffs.extend((sx - sy) * np.sqrt(HOURS_PER_YEAR))
    diffs = np.asarray(diffs)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return {"sharpe_diff": sharpe(x) - sharpe(y), "ci_lo": float(lo), "ci_hi": float(hi),
            "p_le_0": float((diffs <= 0).mean()), "block_hours": block, "resamples": n}
