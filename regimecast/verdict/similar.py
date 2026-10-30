"""Similar moments: the past hours that looked most like now, and what the next 24 h did after them.

Each hour is described by eight numbers, all known at that hour:

    p_stressed, p_calm     regime probabilities (M4, forward-filtered)
    log_sigma              log of the 24 h volatility forecast (HAR-S)
    trend_24h, trend_7d    the last day's and week's return, in units of the volatility forecast
    funding_rank, ls_rank, oi_rank   the crowd signals' 14-day ranks (crowd.py)

Each is standardised with a mean and spread fixed on the development period. The k nearest past hours
(Euclidean distance) are the "similar moments". Only hours whose next 24 h were complete strictly before
now can be used: an hour t qualifies at time T when t + 24 h <= T.

What we show from them:
* the 80% range of the next 24 h move: the 10th and 90th percentiles of the neighbours' moves. With
  ``scaled`` each move is first divided by the volatility forecast of its own hour and then multiplied
  by today's forecast (same idea as FHS), so a calm neighbour does not shrink today's range;
* how often the price went up, pulled toward the development period's up share (``prior`` pseudo-hours
  per neighbour) because direction is close to a coin flip (Phase 6b). The text shows the neighbours'
  own up share; the API's ``p_up`` is the pulled one, which the final bar scores.

k, scaled and prior are chosen on the development period and frozen.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = ["p_stressed", "p_calm", "log_sigma", "trend_24h", "trend_7d", "funding_rank", "ls_rank", "oi_rank"]
K_CANDIDATES = [50, 100, 200, 400]
PRIOR_CANDIDATES = [1.0, 3.0, 10.0, 30.0]  # pseudo-hours as a multiple of k
COIN_FLIP_BAND = 0.10                     # "close to a coin flip" when the up share is within 40-60%
MIN_GAP_HOURS = 24                        # examples shown to the user are at least a day apart


def describe(h: pd.DataFrame) -> pd.DataFrame:
    """The eight numbers per hour from the hourly history (history.py columns)."""
    sig = h["sigma24"]
    return pd.DataFrame({"p_stressed": h["p_stressed"], "p_calm": h["p_calm"], "log_sigma": np.log(sig),
                         "trend_24h": h["ret_24h"] / sig, "trend_7d": h["ret_168h"] / (sig * np.sqrt(7)),
                         "funding_rank": h["funding_rate_rank"], "ls_rank": h["ls_top_rank"],
                         "oi_rank": h["oi_chg_24h_rank"]}, index=h.index)


def scaling(X: pd.DataFrame) -> dict:
    return {"mean": X[FEATURES].mean().round(6).to_dict(), "std": X[FEATURES].std().round(6).to_dict()}


def standardise(X: pd.DataFrame, sc: dict) -> np.ndarray:
    m = np.array([sc["mean"][c] for c in FEATURES])
    s = np.array([sc["std"][c] for c in FEATURES])
    return ((X[FEATURES].to_numpy(float) - m) / np.where(s > 0, s, 1)).astype(np.float32)


class Pool:
    """Past hours with their description and what followed (r24 = next 24 h log return of the mark price)."""

    def __init__(self, ts, Z, sigma, r24):
        t = pd.DatetimeIndex(ts)
        t = (t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")).as_unit("ns").asi8   # ns, UTC
        order = np.argsort(t)
        self.ts = t[order]
        self.Z, self.sigma, self.r24 = Z[order], np.asarray(sigma, float)[order], np.asarray(r24, float)[order]
        ok = np.isfinite(self.Z).all(axis=1) & np.isfinite(self.r24) & (self.sigma > 0)
        self.ts, self.Z, self.sigma, self.r24 = self.ts[ok], self.Z[ok], self.sigma[ok], self.r24[ok]
        self.done = self.ts + 24 * 3600 * 10**9        # when each hour's outcome was complete

    def known_at(self, now) -> int:
        """Number of pool hours (a prefix, in time order) whose outcome was complete at ``now``."""
        now = pd.Timestamp(now)
        now = now.tz_localize("UTC") if now.tzinfo is None else now
        return int(np.searchsorted(self.done, now.value, side="right"))

    def time(self, i: int) -> pd.Timestamp:
        return pd.Timestamp(int(self.ts[i]), tz="UTC")

    def neighbours(self, z: np.ndarray, now, k: int) -> np.ndarray:
        """Indices of the k nearest pool hours known at ``now``, nearest first."""
        n = self.known_at(now)
        if n == 0 or not np.isfinite(z).all():
            return np.array([], dtype=int)
        d = ((self.Z[:n] - z[None, :]) ** 2).sum(axis=1)
        k = min(k, n)
        idx = np.argpartition(d, k - 1)[:k]
        return idx[np.argsort(d[idx])]


def summarise(pool: Pool, idx: np.ndarray, sigma_now: float, k: int, scaled: bool, prior: float,
              p0: float) -> dict:
    """80% range, typical size and up chance from the first k neighbours."""
    idx = idx[:k]
    if len(idx) == 0:
        return {"n": 0, "lo": np.nan, "hi": np.nan, "typical_abs": np.nan, "p_up": p0}
    r = pool.r24[idx]
    moves = r / pool.sigma[idx] * sigma_now if scaled else r
    lo, hi = np.quantile(moves, [0.10, 0.90])
    a = prior * k
    p_up = ((r > 0).sum() + a * p0) / (len(idx) + a)
    return {"n": int(len(idx)), "lo": float(lo), "hi": float(hi), "typical_abs": float(np.median(np.abs(moves))),
            "p_up": float(p_up), "share_up": float((r > 0).mean())}


def examples(pool: Pool, idx: np.ndarray, n: int = 5) -> list[dict]:
    """A few of the nearest moments, at least a day apart, with what the next 24 h did."""
    out, taken = [], []
    for i in idx:
        t = pool.time(i)
        if any(abs((t - u).total_seconds()) < MIN_GAP_HOURS * 3600 for u in taken):
            continue
        taken.append(t)
        out.append({"time": t.isoformat(),
                    "next_24h_move_pct": round(100 * float(np.expm1(pool.r24[i])), 2)})
        if len(out) == n:
            break
    return out


def plain_text(s: dict) -> list[str]:
    """What the user reads."""
    if not s.get("n"):
        return []
    lo, hi = 100 * np.expm1(s["lo"]), 100 * np.expm1(s["hi"])
    up = 100 * s.get("share_up", s["p_up"])
    lines = [f"In the {s['n']} past hours most like now, the price usually moved about "
             f"{100 * np.expm1(s['typical_abs']):.1f}% over the next day; 8 times in 10 the move was between "
             f"{lo:+.1f}% and {hi:+.1f}%."]
    if abs(s.get("share_up", s["p_up"]) - 0.5) <= COIN_FLIP_BAND:
        lines.append(f"It went up {up:.0f}% of the time: close to a coin flip, so this says nothing useful about "
                     f"direction.")
    else:
        lines.append(f"It went up {up:.0f}% of the time. Direction is still hard to call: treat this as a weak hint "
                     f"at best.")
    return lines
