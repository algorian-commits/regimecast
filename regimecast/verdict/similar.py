"""Similar moments: the past hours that looked most like now, and how big the next day's move was.

Each hour end H is described by what was known at H: the 24 h volatility forecast (log, HAR-S, the app's model),
the last day's and last week's return (trend), funding, top traders' long/short ratio and open-interest growth
over 24 h, each scaled by its median and interquartile range on the choose period; and the regime.

For "now", the candidates are only the hours whose next 24 h had fully played out by now (H' + 24 h <= now), so
nothing after now, and no outcome still running, is used. Among them the k nearest (same regime when there are
at least k such hours, else all) give:
* the 80% range of the next 24 h move (10th to 90th percentile of their moves; with ``scale``, each past move is
  taken in units of its own volatility forecast and multiplied by today's);
* how often it went up, shrunk towards ``base``: (ups + m * base) / (k + m) with m = shrink x k. ``base`` is the
  dev-period up share (frozen), the same number the final bar compares with; without it, the share of up days
  among all candidates. When it is between 40% and 60% the app says plainly that the
  direction is close to a coin flip.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

FEATURES = ("log_sigma", "ret_24h", "ret_168h", "funding_rate", "ls_top", "oi_chg_24h")
REGIMES = ("calm", "active", "stressed")
OUTCOME = pd.Timedelta(hours=24)
RANGE = (0.10, 0.90)
KS = (50, 100, 200, 400)               # candidates for k, the shrinkage and the scaling (chosen on the dev period)
SHRINKS = (0.0, 1.0, 4.0, 10.0)
SCALES = (True, False)
COIN_FLIP = (0.40, 0.60)


def features(P: pd.DataFrame) -> pd.DataFrame:
    """The raw features at each hour end of a panel (evaluate.panel)."""
    return pd.DataFrame({"log_sigma": np.log(P["sigma"]), **{c: P[c] for c in FEATURES[1:]}}, index=P.index)


def fit_scaling(F: pd.DataFrame) -> dict:
    q = F.quantile([0.25, 0.5, 0.75])
    spread = (q.loc[0.75] - q.loc[0.25]).replace(0, 1e-12)
    return {"center": {c: float(q.loc[0.5, c]) for c in FEATURES}, "spread": {c: float(spread[c]) for c in FEATURES}}


def standardize(F: pd.DataFrame, scaling: dict) -> np.ndarray:
    c = np.array([scaling["center"][k] for k in FEATURES])
    s = np.array([scaling["spread"][k] for k in FEATURES])
    return ((F[list(FEATURES)].to_numpy(dtype=float) - c) / s).astype(np.float32)


@dataclass
class Library:
    """Past hours with their features and what followed (sorted by time)."""
    ts: np.ndarray          # hour end, int64 nanoseconds UTC
    regime: np.ndarray      # 0 calm, 1 active, 2 stressed, -1 unknown
    X: np.ndarray           # standardized features (float32)
    sigma: np.ndarray       # 24 h volatility forecast at that hour
    fwd: np.ndarray         # log move of the mark price over the next 24 h

    @classmethod
    def from_panel(cls, P: pd.DataFrame, scaling: dict) -> "Library":
        F = features(P)
        ok = F.notna().all(axis=1) & P["sigma"].gt(0) & P["fwd_24h"].notna()
        P, F = P[ok], F[ok]
        reg = P["regime"].fillna(-1).to_numpy(dtype=np.int8) if "regime" in P else np.full(len(P), -1, np.int8)
        X = standardize(F, scaling).astype(np.float16).astype(np.float32)   # as shipped (float16), so live = tested
        return cls(P.index.tz_convert("UTC").as_unit("ns").asi8.astype(np.int64), reg, X,
                   P["sigma"].to_numpy(np.float32), P["fwd_24h"].to_numpy(np.float32))

    def __len__(self) -> int:
        return len(self.ts)

    def known_by(self, now: pd.Timestamp) -> int:
        """How many rows (from the start) had their whole next 24 h before ``now``."""
        return int(np.searchsorted(self.ts, (pd.Timestamp(now) - OUTCOME).value, side="right"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, ts=self.ts, regime=self.regime, X=self.X.astype(np.float16), sigma=self.sigma,
                            fwd=self.fwd)

    @classmethod
    def load(cls, path: Path) -> "Library | None":
        if not path.exists():
            return None
        d = np.load(path)
        return cls(d["ts"], d["regime"], d["X"].astype(np.float32), d["sigma"], d["fwd"])


@dataclass
class Moments:
    n: int                  # past hours compared
    lo: float               # 80% range of the next 24 h move (log)
    hi: float
    typical: float          # median size of the move (log, absolute)
    p_up: float
    same_regime: bool

    @property
    def coin_flip(self) -> bool:
        return COIN_FLIP[0] <= self.p_up <= COIN_FLIP[1]

    def message(self, coin: str) -> str:
        pc = lambda m: 100 * (np.exp(m) - 1)                       # noqa: E731
        where = "in the same market regime " if self.same_regime else ""
        s = (f"In the {self.n} past hours {where}that looked most like now, {coin} typically moved "
             f"{pc(self.typical):.1f}% over the next 24 h; 8 times out of 10 the move was between "
             f"{pc(self.lo):+.1f}% and {pc(self.hi):+.1f}%. It went up {100 * self.p_up:.0f}% of the time")
        return s + (": close to a coin flip, so this says nothing about direction." if self.coin_flip else ".")


def _nearest(lib: Library, x: np.ndarray, n: int, regime: int, kmax: int) -> tuple[np.ndarray, np.ndarray]:
    """Indices of the nearest known rows, sorted by distance: within the same regime, and among all."""
    d = ((lib.X[:n] - x) ** 2).sum(axis=1)

    def top(idx):
        if len(idx) > kmax:
            idx = idx[np.argpartition(d[idx], kmax - 1)[:kmax]]
        return idx[np.argsort(d[idx], kind="stable")]

    same = top(np.flatnonzero(lib.regime[:n] == regime)) if regime >= 0 else np.array([], dtype=int)
    return same, top(np.arange(n))


def _summary(lib: Library, idx: np.ndarray, sigma_now: float, shrink: float, scale: bool, base: float,
             same: bool) -> Moments:
    k = len(idx)
    moves = lib.fwd[idx] / lib.sigma[idx] * sigma_now if scale else lib.fwd[idx]
    lo, hi = np.quantile(moves, RANGE)
    m = shrink * k
    p_up = (float((lib.fwd[idx] > 0).sum()) + m * base) / (k + m)
    return Moments(k, float(lo), float(hi), float(np.median(np.abs(moves))), float(p_up), same)


def pick(same: np.ndarray, every: np.ndarray, k: int) -> tuple[np.ndarray, bool]:
    return (same[:k], True) if len(same) >= k else (every[:k], False)


def query(lib: Library, x_raw: dict, regime: str | None, sigma_now: float, now: pd.Timestamp, cfg: dict,
          scaling: dict, base: float | None = None) -> Moments | None:
    """Similar moments for one hour. x_raw: the FEATURES at now (log_sigma may be given as sigma)."""
    n = lib.known_by(now)
    k = int(cfg["k"])
    if n < k or not sigma_now or sigma_now <= 0:
        return None
    raw = {"log_sigma": np.log(sigma_now), **{c: x_raw.get(c) for c in FEATURES[1:]}}
    if any(v is None or not np.isfinite(v) for v in raw.values()):
        return None
    x = standardize(pd.DataFrame([raw]), scaling)[0]
    r = REGIMES.index(regime) if regime in REGIMES else -1
    same, every = _nearest(lib, x, n, r, k)
    idx, is_same = pick(same, every, k)
    base = float((lib.fwd[:n] > 0).mean()) if base is None else base
    return _summary(lib, idx, sigma_now, cfg["shrink"], cfg["scale"], base, is_same)


def predict(lib: Library, Q: pd.DataFrame, X: np.ndarray, settings: list[dict],
            base: float | None = None) -> pd.DataFrame:
    """Out-of-sample predictions at every row of Q (query hours: columns sigma, regime), one per setting.

    X: the query rows' standardized features. Each query only sees library rows known by then (known_by)."""
    kmax = max(int(s["k"]) for s in settings)
    ups = np.concatenate([[0], np.cumsum(lib.fwd > 0)])
    rows = []
    for i, (t, q) in enumerate(Q.iterrows()):
        n = lib.known_by(t)
        if n < kmax or not np.isfinite(X[i]).all() or not q["sigma"] > 0:
            continue
        r = int(q["regime"]) if pd.notna(q.get("regime")) else -1
        same, every = _nearest(lib, X[i], n, r, kmax)
        b = ups[n] / n if base is None else base
        for j, s in enumerate(settings):
            idx, is_same = pick(same, every, int(s["k"]))
            m = _summary(lib, idx, float(q["sigma"]), s["shrink"], s["scale"], b, is_same)
            rows.append((t, j, m.lo, m.hi, m.p_up))
    return pd.DataFrame(rows, columns=["ts", "setting", "lo", "hi", "p_up"])


def score(pred: pd.DataFrame, fwd: pd.Series, up_share: float) -> dict:
    """Coverage of the 80% range and the up-chance's Brier score vs always saying ``up_share``."""
    y = fwd.reindex(pred["ts"]).to_numpy(dtype=float)
    ok = np.isfinite(y)
    p, y = pred[ok], y[ok]
    up = (y > 0).astype(float)
    return {"n": int(ok.sum()), "coverage": float(((p["lo"] <= y) & (y <= p["hi"])).mean()),
            "brier": float(((p["p_up"] - up) ** 2).mean()), "brier_base": float(((up_share - up) ** 2).mean()),
            "up_share_actual": float(up.mean())}
