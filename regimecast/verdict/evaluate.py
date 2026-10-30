"""The measures behind Phase 12's pass bars (docs/PLAN.md, "Final evaluation"), on the dev period or, with
``final=True``, the final test period. scripts/run_phase12.py and final.py call the same functions.

Panel: one row per hour end H with what was known at H (mark price, funding, top traders' long/short ratio,
open-interest growth, last day's and week's return, volume, the 24 h HAR-S volatility forecast made walk-forward,
the stress probability and the regime from the walk-forward files) and what happened after it (the 24 h worst
moves of the mark price, ``mae_long_24h`` and ``mae_short_24h``, and the 24 h move ``fwd_24h``). On the dev period
nothing is read from the final test period: the queries stop before it, and outcomes that would reach into it
are left empty.

Trade Grade bar: a fixed grid of example trades, every 6 hours, each coin, long and short, leverage 2x to 50x
(LEVERAGES), stops at 0.5 to 3 typical moves (STOP_MOVES); $1,000 isolated margin, risk limit 1%, 24 h horizon.
A trade is "hit" when the mark price reaches its stop or liquidation price within 24 h. Grades with under 200
trades are merged with the next grade (the last with the one before).
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.agent.backtest import check_closed, holdout_start
from regimecast.models import harness
from regimecast.risk import liquidation as liq
from regimecast.verdict import crowd as C, grade as G, similar as S, timing as T
from regimecast.vol import har

REPORTS = config.DATA_DIR / "reports"
DEV_FILES = {"stress": "phase6_pred_{s}.parquet", "regime": "regimes_base_{s}.parquet"}
FINAL_FILES = {"stress": "final_stress_pred_{s}.parquet", "regime": "final_regimes_{s}.parquet"}
SIGMA_FROM = "2021-01-01"
CHOOSE = ("2022-01-01", "2024-10-01")     # settings are chosen here
CHECK = ("2024-10-01", "2025-10-01")      # and checked here
DEV = ("2022-01-01", "2025-10-01")
FINAL = (config.HOLDOUT_START, "2026-10-01")
HORIZON = 24
OUTCOME_COLS = ["fwd_24h", "mae_long_24h", "mae_short_24h"]

LEVERAGES = (2, 3, 5, 10, 20, 50)
STOP_MOVES = (0.5, 1.0, 1.5, 2.0, 3.0)
GRID_MARGIN = 1000.0
GRID_RISK_LIMIT = 0.01
GRID_EVERY_H = 6
MIN_PER_GRADE = 200
TAKER_FEE = 0.0005
SETTLEMENTS_PER_DAY = 3


def window(df: pd.DataFrame, period) -> pd.DataFrame:
    a, b = (pd.Timestamp(x, tz="UTC") for x in period)
    return df[(df.index >= a) & (df.index < b)]


# ------------------------------------------------------------------ panel
def hars_sigma(h: pd.DataFrame, y24: pd.Series, final: bool = False) -> pd.Series:
    """24 h HAR-S volatility forecast (std of the log return), walk-forward, refitted every 90 days (as Phase 9)."""
    X = har.har_features(h, HORIZON)
    var = harness.walk_forward(X, y24.reindex(X.index), harness.BASELINES["har_s"], SIGMA_FROM, HORIZON,
                               refit_every_hours=24 * 90, final=final)
    return np.sqrt(var.clip(lower=0)).rename("sigma")


def panel_from(h: pd.DataFrame, t: pd.DataFrame, p_stress: pd.Series, regime: pd.Series,
               sigma: pd.Series | None = None, final: bool = False) -> pd.DataFrame:
    h = (h.set_index("ts") if "ts" in h.columns else h).sort_index()
    t = (t.set_index("ts") if "ts" in t.columns else t).sort_index()
    if sigma is None:
        sigma = hars_sigma(h, t["y_24h"], final)
    P = pd.DataFrame(index=h.index)
    for c in ("mark", "funding_rate", "ls_top", "oi_chg_24h", "ret_24h", "volume_usd"):
        P[c] = h[c]
    P["ret_168h"] = h["ret"].rolling(168, min_periods=120).sum()
    P["sigma"] = sigma.reindex(P.index)
    P["fwd_24h"] = np.log(P["mark"].reindex(P.index + pd.Timedelta(hours=HORIZON)).to_numpy() / P["mark"].to_numpy())
    for c in ("mae_long_24h", "mae_short_24h"):
        P[c] = t[c].reindex(P.index)
    P["p_stress"] = p_stress.reindex(P.index)
    P["regime"] = regime.reindex(P.index).astype(float)
    P = C.with_ranks(P)
    if not final:
        hold = holdout_start()
        P = P[P.index < hold].copy()
        P.loc[P.index > hold - pd.Timedelta(hours=HORIZON), OUTCOME_COLS] = np.nan
    P.index.name = "ts"
    check_closed(P.index, final)
    return P


def _read(path, final: bool) -> pd.DataFrame:
    """A walk-forward output file; on the dev period only its rows before the final test period are read."""
    import pyarrow.parquet as pq

    if final:
        return pd.read_parquet(path)
    col = "ts" if "ts" in pq.read_schema(path).names else "__index_level_0__"   # an unnamed time index
    df = pd.read_parquet(path, filters=[(col, "<", holdout_start())])
    check_closed(df.index if "ts" not in df.columns else pd.DatetimeIndex(df["ts"]), False)
    return df


def _signals(symbol: str, rep, final: bool) -> tuple[pd.Series, pd.Series]:
    parts_p, parts_r = [], []
    for files, fin in ((DEV_FILES, False), *(((FINAL_FILES, True),) if final else ())):
        st = _read(rep / files["stress"].format(s=symbol), fin)
        rg = _read(rep / files["regime"].format(s=symbol), fin)
        st, rg = (d.set_index("ts") if "ts" in d.columns else d for d in (st, rg))
        p = st["gbm_cal"].fillna(st["gbm"]) if "gbm_cal" in st else st["p_stress"]
        if fin:   # final files after the dev files; dev files give the hours before
            p, rg = p[p.index >= holdout_start()], rg[rg.index >= holdout_start()]
        parts_p.append(p)
        parts_r.append(rg["regime"])
    return pd.concat(parts_p).sort_index(), pd.concat(parts_r).sort_index()


def load(symbol: str, final: bool = False, reports=None) -> pd.DataFrame:
    """The panel for one coin: the dev period (default), or everything through the final test period."""
    from regimecast.data import store

    rep = reports or REPORTS
    hold = holdout_start()
    h = store.features(symbol, "1h", end=None if final else hold)
    t = store.targets(symbol, end=None if final else hold - pd.Timedelta(hours=HORIZON - 1),
                      columns=["ts", "y_24h", "mae_long_24h", "mae_short_24h"])
    p, rg = _signals(symbol, rep, final)
    return panel_from(h, t, p, rg, final=final)


# ------------------------------------------------------------------ liquidation chances (FHS, as the app)
def survival(entry: dict, d, sigma) -> np.ndarray:
    """risk.fhs_live.survival for arrays: P(worst move >= d) given the horizon volatility sigma."""
    d, sigma = np.broadcast_arrays(np.asarray(d, dtype=float), np.asarray(sigma, dtype=float))
    z, lv = np.asarray(entry["z"]), np.asarray(entry["levels"])
    floor = 0.5 / (entry["n"] + 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        k = d / sigma
        p = np.maximum(1 - np.interp(k, z, lv), floor)
    p = np.where(k >= z[-1], floor, p)
    p = np.where(~np.isfinite(d) | (sigma <= 0), 0.0, p)
    return np.where(d <= 0, 1.0, p)


def liquidation_distance(symbol: str, side: str, leverage: float, margin: float = GRID_MARGIN) -> float:
    """Log distance to the liquidation price of an isolated position with this margin. The same at any entry:
    with a fixed notional, liquidation price / entry depends only on the tier and the leverage."""
    entry = 100.0
    pos = liq.Position(symbol=symbol, side=side, entry=entry, qty=margin * leverage / entry, leverage=leverage)
    return liq.adverse_log_distance(pos, liq.liquidation_price(pos))


FHS_REFIT_H = 24 * 90


def walk_forward_fhs(P: pd.DataFrame, start=SIGMA_FROM, refit_hours: int = FHS_REFIT_H) -> list[tuple[pd.Timestamp, dict]]:
    """[(block start, {"long": entry, "short": entry})]: the FHS table rebuilt every 90 days from the hours whose
    24 h outcome was complete before the block (H + 24 h <= block start), in fhs_live's format. The dev tables use
    these, so no grade is scored with a table that saw its own outcome; the app and the final run use the table
    fitted on the whole dev period (risk/fhs_live.json), which is out of sample for the final test period."""
    from regimecast.risk import fhs_live

    ok = P["sigma"].notna() & (P["sigma"] > 0)
    out, b = [], pd.Timestamp(start, tz="UTC") + pd.Timedelta(hours=refit_hours)
    while b <= P.index.max():
        past = ok & (P.index <= b - pd.Timedelta(hours=HORIZON))
        if past.sum() >= 24 * 60:
            out.append((b, {side: fhs_live.fit(P.loc[past, f"mae_{side}_24h"] / P.loc[past, "sigma"])
                            for side in ("long", "short")}))
        b += pd.Timedelta(hours=refit_hours)
    return out


def _fhs_survival(fhs24, side: str, d, sigma, ts: pd.DatetimeIndex) -> np.ndarray:
    """survival() with one table ({"long", "short"}) or a walk-forward list of them (each hour uses the last
    table that started at or before it; hours before the first table get NaN)."""
    if isinstance(fhs24, dict):
        return survival(fhs24[side], d, sigma)
    d, sigma = np.broadcast_arrays(np.asarray(d, dtype=float), np.asarray(sigma, dtype=float))
    starts = np.array([b.value for b, _ in fhs24], dtype=np.int64)
    blk = np.searchsorted(starts, ts.as_unit("ns").asi8, side="right") - 1
    out = np.full(len(d), np.nan)
    for j in np.unique(blk[blk >= 0]):
        m = blk == j
        out[m] = survival(fhs24[j][1][side], d[m], sigma[m])
    return out


# ------------------------------------------------------------------ Trade Grade
def example_trades(P: pd.DataFrame, symbol: str, fhs24: dict, crowd_th: dict | None, calendar: pd.DataFrame,
                   thin_hours=(), period=None, final: bool = False) -> pd.DataFrame:
    """The fixed grid of example trades opened in ``period`` (default: all of P), with everything the grade
    needs and whether each was hit within 24 h. ``fhs24``: one FHS table per side, or walk_forward_fhs(P)."""
    need = ["sigma", "mark", "mae_long_24h", "mae_short_24h"]
    W = window(P, period) if period else P
    O = W[(W.index.hour % GRID_EVERY_H == 0) & W[need].notna().all(axis=1) & (W["sigma"] > 0)]
    check_closed(O.index, final)
    sig = O["sigma"].to_numpy()
    p = O["p_stress"].to_numpy(dtype=float)
    common = {"stressed": (O["regime"] == 2).to_numpy(),
              "stress_steps": np.where(p >= 0.30, 2, np.where(p >= 0.10, 1, 0)),
              "bad_timing": T.flags(O.index, HORIZON, calendar, thin_hours, P["p_stress"])["any"].to_numpy()}
    warn = C.warning(O, crowd_th).to_numpy() if crowd_th else np.zeros(len(O), dtype=int)
    funding = O["funding_rate"].fillna(0).to_numpy()
    out = []
    for side, s in (("long", 1), ("short", -1)):
        mae = O[f"mae_{side}_24h"].to_numpy()
        cost = (2 * TAKER_FEE + s * funding * SETTLEMENTS_PER_DAY) / sig
        for lev in LEVERAGES:
            d_liq = liquidation_distance(symbol, side, lev)
            p_liq = _fhs_survival(fhs24, side, d_liq, sig, O.index)
            for k in STOP_MOVES:
                d_stop = k * sig
                exit_d = np.minimum(d_stop, d_liq)
                out.append(pd.DataFrame({
                    "ts": O.index, "side": side, "leverage": lev, "stop_moves": k,
                    "p_knockout": _fhs_survival(fhs24, side, exit_d, sig, O.index), "p_liquidation": p_liq,
                    "stop_past_liquidation": d_stop >= d_liq, "cost_share": cost, **common,
                    "crowd_on_side": warn == s, "hit": mae >= exit_d}))
    T_ = pd.concat(out, ignore_index=True).dropna(subset=["p_knockout", "p_liquidation"])
    T_.insert(0, "symbol", symbol)
    return T_


def grade_trades(trades: pd.DataFrame, cfg: dict) -> np.ndarray:
    s = G.scores(trades["p_knockout"], trades["p_liquidation"], GRID_RISK_LIMIT, True,
                 trades["stop_past_liquidation"], trades["cost_share"], trades["stressed"], trades["stress_steps"],
                 trades["crowd_on_side"], trades["bad_timing"], cfg)
    return G.grade_index(s)


def merge_grades(n: list[int], hits: list[float], min_n: int = MIN_PER_GRADE) -> list[dict]:
    """Group grades A..F so each group has at least min_n trades: a small grade joins the next one; a small last
    group joins the one before."""
    groups, cur = [], []
    for g in range(len(G.GRADES)):
        if n[g] == 0:
            continue
        cur.append(g)
        if sum(n[i] for i in cur) >= min_n:
            groups.append(cur)
            cur = []
    if cur:
        if groups:
            groups[-1] = groups[-1] + cur
        else:
            groups.append(cur)
    out = []
    for gr in groups:
        tot = sum(n[i] for i in gr)
        out.append({"group": "+".join(G.GRADES[i] for i in gr), "n": tot,
                    "hit_share": sum(hits[i] for i in gr) / tot if tot else np.nan})
    return out


def grade_table(trades: pd.DataFrame, gi: np.ndarray, min_n: int = MIN_PER_GRADE) -> pd.DataFrame:
    rows = []
    d = trades[["symbol", "hit"]].assign(g=gi)
    for sym, part in d.groupby("symbol", sort=False):
        cnt = part.groupby("g")["hit"].agg(["size", "sum"]).reindex(range(len(G.GRADES)), fill_value=0)
        for r in merge_grades(cnt["size"].tolist(), cnt["sum"].astype(float).tolist(), min_n):
            rows.append({"symbol": sym, **r})
    return pd.DataFrame(rows)


def grade_bar(table: pd.DataFrame) -> pd.DataFrame:
    """Per coin: does the hit share rise from the first group to the last, and is the last at least 3x the first?"""
    rows = []
    for sym, t in table.groupby("symbol", sort=False):
        h = t["hit_share"].to_numpy()
        rises = bool(len(h) >= 2 and np.all(np.diff(h) > 0))
        ratio = float(h[-1] / h[0]) if len(h) >= 2 and h[0] > 0 else np.nan
        rows.append({"symbol": sym, "groups": " < ".join(t["group"]), "rises": rises, "ratio_last_first": ratio,
                     "passes": bool(rises and ratio >= 3)})
    return pd.DataFrame(rows)


GRADE_CANDIDATES = {"knockout_cuts": ([0.05, 0.15, 0.30, 0.50], [0.10, 0.25, 0.45, 0.65], [0.15, 0.30, 0.50, 0.70]),
                    "weights": [dict(zip(G.MARKET, w)) for w in itertools.product((0, 1), repeat=4)]}


def choose_grade(trades: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    """Rule fixed before the run: among settings whose hit share rises from A to F on all three coins, the
    highest smallest-coin F/A ratio; ties go to fewer market factors, then the middle knock-out bands. If none
    rises on every coin: the most coins rising, then the same order."""
    rows = []
    for i, (cuts, w) in enumerate(itertools.product(*GRADE_CANDIDATES.values())):
        cfg = {"knockout_cuts": cuts, "weights": w}
        bar = grade_bar(grade_table(trades, grade_trades(trades, cfg)))
        rows.append({"candidate": i, "knockout_cuts": str(cuts), **{f"w_{k}": v for k, v in w.items()},
                     "coins_rising": int(bar["rises"].sum()), "min_ratio": float(bar["ratio_last_first"].min()),
                     "market_factors": sum(w.values()), "middle": cuts == GRADE_CANDIDATES["knockout_cuts"][1],
                     **{f"ratio_{s}": r for s, r in zip(bar["symbol"], bar["ratio_last_first"])},
                     "cfg": cfg})
    g = pd.DataFrame(rows).sort_values(["coins_rising", "min_ratio", "market_factors", "middle"],
                                       ascending=[False, False, True, False])
    return g.iloc[0]["cfg"], g.drop(columns="cfg")


# ------------------------------------------------------------------ crowd warning
def ratio_bootstrap(o: pd.DataFrame, n: int = 1000, block: str = "7D", level: float = 0.90,
                    seed: int = 0) -> tuple[float, float]:
    """Block-bootstrap interval (``level``) of P(against | warning) / P(against | other hours): whole weeks are
    resampled, so runs of correlated hours stay together."""
    ok = o.dropna(subset=["against"])
    b = ok.assign(w=ok["warning"].astype(int), hw=ok["against"] * ok["warning"],
                  ho=ok["against"] * (~ok["warning"])).groupby(ok.index.floor(block))[["w", "hw", "ho"]].sum()
    b["o"] = ok.groupby(ok.index.floor(block)).size() - b["w"]
    a = b[["w", "hw", "o", "ho"]].to_numpy(dtype=float)
    rng = np.random.default_rng(seed)
    s = a[rng.integers(0, len(a), size=(n, len(a)))].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = (s[:, 1] / s[:, 0]) / (s[:, 3] / s[:, 2])
    r = r[np.isfinite(r)]
    if not len(r):
        return np.nan, np.nan
    lo, hi = np.percentile(r, [50 * (1 - level), 100 - 50 * (1 - level)])
    return float(lo), float(hi)


def crowd_row(o: pd.DataFrame) -> dict:
    a, b, r, n = C.ratio(o)
    lo, hi = ratio_bootstrap(o)
    return {"warning_hours": n, "hours": int(o["against"].notna().sum()), "p_after_warning": a, "p_other": b,
            "ratio": r, "ratio_lo90": lo, "ratio_hi90": hi, "passes": bool(r >= 1.5 and lo > 1)}


MIN_WARNINGS = 50


def choose_crowd(panels: dict[str, pd.DataFrame]) -> tuple[dict, pd.DataFrame]:
    """Rule fixed before the run (choose period): among candidates with at least 50 warning hours on every
    coin, the highest middle (second-best) coin's ratio; ties go to more warnings."""
    rows = []
    for level, oi in itertools.product(C.LEVELS, C.OI_LEVELS):
        r = {"level": level, "oi_level": oi}
        for sym, P in panels.items():
            a, b, rat, n = C.ratio(C.outcomes(P, C.fit(P, level, oi)))
            r |= {f"ratio_{sym}": rat, f"warnings_{sym}": n}
        ratios = [r[f"ratio_{s}"] for s in panels]
        r["median_ratio"] = float(pd.Series(ratios, dtype=float).median())
        r["min_warnings"] = min(r[f"warnings_{s}"] for s in panels)
        r["total_warnings"] = sum(r[f"warnings_{s}"] for s in panels)
        rows.append(r)
    g = pd.DataFrame(rows)
    g["enough"] = g["min_warnings"] >= MIN_WARNINGS
    g = g.sort_values(["enough", "median_ratio", "total_warnings"], ascending=[False, False, False])
    best = g.iloc[0]
    return {"level": float(best["level"]), "oi_level": float(best["oi_level"])}, g


# ------------------------------------------------------------------ similar moments
def similar_settings() -> list[dict]:
    return [{"k": k, "shrink": m, "scale": sc} for k, m, sc in itertools.product(S.KS, S.SHRINKS, S.SCALES)]


def similar_predict(lib: S.Library, P: pd.DataFrame, scaling: dict, settings: list[dict], every_h: int = 1,
                    base: float | None = None) -> pd.DataFrame:
    Q = P[P.index.hour % every_h == 0]
    return S.predict(lib, Q, S.standardize(S.features(Q), scaling), settings, base=base)


def similar_row(pred: pd.DataFrame, P: pd.DataFrame, up_share: float) -> dict:
    sc = S.score(pred, P["fwd_24h"], up_share)
    return {**sc, "passes": bool(0.75 <= sc["coverage"] <= 0.85 and sc["brier"] <= sc["brier_base"])}


def choose_similar(scores: pd.DataFrame) -> dict:
    """Rule fixed before the run (choose period, scores per setting and coin): among settings whose Brier score is
    no worse than the base rate's on every coin, the smallest worst-coin |coverage - 80%|; ties go to larger k.
    If none: the smallest worst-coin Brier excess."""
    g = scores.assign(gap=(scores["coverage"] - 0.80).abs(), excess=scores["brier"] - scores["brier_base"])
    agg = g.groupby("setting").agg(worst_gap=("gap", "max"), worst_excess=("excess", "max"), k=("k", "first"))
    ok = agg[agg["worst_excess"] <= 0]
    best = (ok.sort_values(["worst_gap", "k"], ascending=[True, False]) if len(ok)
            else agg.sort_values(["worst_excess", "worst_gap"])).index[0]
    s = scores[scores["setting"] == best].iloc[0]
    return {"k": int(s["k"]), "shrink": float(s["shrink"]), "scale": bool(s["scale"])}


# ------------------------------------------------------------------ synthetic data (tests and dry runs only)
def synthetic_panel(seed: int = 0, start="2021-01-01", end=config.HOLDOUT_START, crowd_effect: float = 0.5) -> pd.DataFrame:
    """A made-up panel with the same columns as ``panel_from``: regimes with their own volatility, a noisy
    volatility forecast, crowding inputs, and 24 h outcomes computed from the path itself. ``crowd_effect``
    makes moves against a crowded side more likely, so the crowd warning has something to find."""
    rng = np.random.default_rng(seed)
    ix = pd.date_range(start, end, freq="1h", tz="UTC", inclusive="left")
    n = len(ix)
    stay = np.array([0.995, 0.99, 0.97])
    reg = np.zeros(n, dtype=int)
    for i in range(1, n):
        reg[i] = reg[i - 1] if rng.random() < stay[reg[i - 1]] else rng.choice([r for r in range(3) if r != reg[i - 1]])
    vol = np.array([0.004, 0.007, 0.013])[reg] * np.exp(rng.normal(0, 0.15, n))
    ar = lambda mu, phi, sd: pd.Series(rng.normal(0, sd, n)).ewm(alpha=1 - phi).mean().to_numpy() + mu   # noqa: E731
    funding = ar(1e-4, 0.995, 2e-3)
    ls = ar(1.5, 0.995, 3.0)
    oi = ar(0.0, 0.98, 0.15)
    zf, zl = (funding - 1e-4) / funding.std(), (ls - 1.5) / ls.std()
    lean_ = np.tanh(zf + zl)
    ret = rng.normal(0, 1, n) * vol
    ret[1:] -= crowd_effect * vol[1:] * lean_[:-1] * (oi[:-1] > 0)          # crowded + growing OI -> moves against it
    logp = np.log(30000) + np.cumsum(ret)
    low = logp - np.abs(rng.normal(0, 0.6, n)) * vol
    high = logp + np.abs(rng.normal(0, 0.6, n)) * vol
    fut_low = pd.Series(low[::-1]).rolling(24, min_periods=24).min().to_numpy()[::-1]
    fut_high = pd.Series(high[::-1]).rolling(24, min_periods=24).max().to_numpy()[::-1]
    P = pd.DataFrame(index=ix)
    P["mark"] = np.exp(logp)
    P["funding_rate"], P["ls_top"], P["oi_chg_24h"] = funding, ls, oi
    r = pd.Series(ret, index=ix)
    P["ret_24h"] = r.rolling(24, min_periods=18).sum()
    P["ret_168h"] = r.rolling(168, min_periods=120).sum()
    P["volume_usd"] = 1e8 * (1.2 + np.sin(2 * np.pi * ix.hour / 24)) * np.exp(rng.normal(0, 0.2, n))
    P["sigma"] = vol * np.sqrt(24) * np.exp(rng.normal(0, 0.2, n))
    P["fwd_24h"] = np.r_[logp[24:] - logp[:-24], [np.nan] * 24]
    P["mae_long_24h"] = np.clip(logp - np.r_[fut_low[1:], np.nan], 0, None)
    P["mae_short_24h"] = np.clip(np.r_[fut_high[1:], np.nan] - logp, 0, None)
    P["p_stress"] = 1 / (1 + np.exp(-(reg * 1.6 - 3.5 + rng.normal(0, 0.7, n))))
    P["regime"] = reg.astype(float)
    P.index.name = "ts"
    return C.with_ranks(P)


def synthetic_fhs(n: int = 100_000) -> dict:
    """FHS entries for a random walk: P(worst move >= k sigma) = 2 Phi(-k), both sides, 1 h and 24 h."""
    from scipy.stats import norm
    from regimecast.risk.fhs_live import LEVELS

    z = norm.ppf(np.clip(1 - (1 - LEVELS) / 2, 0, 1 - 1e-12)).clip(min=0)
    e = {"levels": LEVELS.round(6).tolist(), "z": np.maximum.accumulate(z).round(6).tolist(), "n": n}
    return {h: {"long": e, "short": e} for h in ("1h", "24h")}
