"""The measures behind the final-evaluation bars (docs/PLAN.md, fixed 27 Oct 2026), shared by the dev
tables (scripts/run_phase12.py) and the final run (final.py), so both are scored by the same code.

Trade Grade: a fixed grid of example trades, every 6 hours, each coin, long and short, leverage 2x to
50x, stops at 0.5 to 3 times the forecast move; the share hit by liquidation or the stop within 24 h
must rise from A to F, and F's share be at least 3 times A's (grades with under 200 trades merged with
the next).
Crowd warning: the 5% move against the crowd happens at least 1.5 times as often after a warning as in
other hours, with the 90% block-bootstrap interval above 1.
Similar moments: the 80% range contains the actual move 75-85% of the time, and the up chance has a
Brier score no worse than always saying the dev-period up share.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from regimecast.risk import liquidation as liq
from regimecast.verdict import calendar, crowd, rules, similar
from regimecast.verdict import history as H

LEVERAGES = [2, 3, 5, 10, 20, 25, 50]
STOP_MULTS = [0.5, 1.0, 1.5, 2.0, 3.0]
EVERY_HOURS = 6
MIN_GROUP = 200
RISK_LIMIT = 0.01        # the app's default risk limit
GRID_MARGIN = 1000.0     # USDT; every grid position stays in Binance's first margin tier


def liq_distance(symbol: str, side: str, leverage: float) -> float:
    """Log distance to liquidation for an isolated position in the first tier (it does not depend on price)."""
    entry = 100.0
    pos = liq.Position(symbol=symbol, side=side, entry=entry, qty=GRID_MARGIN * leverage / entry, leverage=leverage)
    return liq.adverse_log_distance(pos, liq.liquidation_price(pos))


def grid_trades(symbol: str, h: pd.DataFrame, crowd_warn: pd.Series, thin_hours: list[int],
                start, end, tables=None) -> pd.DataFrame:
    """Every grid trade in [start, end) with its points and whether it was knocked out within 24 h."""
    a, b = (pd.Timestamp(x, tz="UTC") for x in (start, end))
    tables = tables if tables is not None else H.fhs_tables(h)
    sel = h[(h.index >= a) & (h.index < b) & (h.index.hour % EVERY_HOURS == 0)]
    sel = sel.dropna(subset=["sigma24", "mae_long", "mae_short", "p_stress", "regime"])
    blk = H.block_of(sel.index, tables)
    sel, blk = sel[blk >= 0], blk[blk >= 0]
    ev = calendar.event_flags(sel.index, rules.HORIZON_HOURS)
    thin = (sel.index.dayofweek >= 5) | np.isin(sel.index.hour, thin_hours)
    cw = crowd_warn.reindex(sel.index).fillna(0).astype(int).to_numpy()
    rising = rules.stress_rising(sel["p_stress"].to_numpy(), sel["p_stress_3h"].to_numpy())
    lvl = sel["stress_level"].map(rules.STRESS_POINTS).to_numpy()
    rows = []
    for side, sgn in (("long", 1), ("short", -1)):
        cost = (2 * 0.0005 + sgn * sel["funding_rate"].fillna(0).to_numpy() * rules.HORIZON_HOURS / 8) / (
            sel["sigma24"].to_numpy() * rules.TYPICAL_MOVE)
        mae = sel[f"mae_{side}"].to_numpy()
        sig = sel["sigma24"].to_numpy()
        for lev in LEVERAGES:
            d_liq = liq_distance(symbol, side, lev)
            p_liq = np.empty(len(sel))
            for j in np.unique(blk):
                m = blk == j
                p_liq[m] = H.survival_vec(tables[j][1][side], np.full(m.sum(), d_liq), sig[m])
            for k in STOP_MULTS:
                d_stop = k * sig
                p_stop = np.empty(len(sel))
                for j in np.unique(blk):
                    m = blk == j
                    p_stop[m] = H.survival_vec(tables[j][1][side], d_stop[m], sig[m])
                past = d_stop >= d_liq
                pts = rules.points_array(sgn, p_liq, RISK_LIMIT, p_stop, past, cost, sel["regime"].eq("stressed"),
                                         lvl, rising, cw, ev, thin)
                rows.append(pd.DataFrame({"ts": sel.index, "side": side, "leverage": lev, "stop_mult": k,
                                          "points": pts, "p_liq": p_liq, "p_stop": p_stop,
                                          "hit": mae >= np.minimum(d_stop, d_liq)}))
    out = pd.concat(rows, ignore_index=True)
    out["symbol"] = symbol
    return out


def merge_small(counts: pd.DataFrame, min_n: int = MIN_GROUP) -> list[list[str]]:
    """Groups of letters, A to F, each with at least min_n trades: a small grade joins the next one
    (and a small last group joins the one before)."""
    groups, cur, n = [], [], 0
    for g in rules.LETTERS:
        c = int(counts.get(g, 0))
        if c == 0 and not cur:
            continue
        cur.append(g)
        n += c
        if n >= min_n:
            groups.append(cur)
            cur, n = [], 0
    if cur:
        if groups:
            groups[-1] += cur
        else:
            groups.append(cur)
    return groups


def grade_table(trades: pd.DataFrame, cuts) -> tuple[pd.DataFrame, dict]:
    """Share knocked out per grade (after merging) and the bar's two checks, for one coin's trades."""
    t = trades.assign(grade=rules.letter(trades["points"].to_numpy(), cuts))
    counts = t["grade"].value_counts()
    groups = merge_small(counts)
    rows = []
    for grp in groups:
        m = t["grade"].isin(grp)
        rows.append({"grade": "+".join(grp), "n": int(m.sum()), "share_hit": float(t.loc[m, "hit"].mean())})
    tab = pd.DataFrame(rows)
    shares = tab["share_hit"].to_numpy()
    rises = bool(len(shares) >= 2 and np.all(np.diff(shares) > 0))
    f_over_a = float(shares[-1] / shares[0]) if len(shares) >= 2 and shares[0] > 0 else np.nan
    raw = t.groupby("grade")["hit"].agg(["size", "mean"]).reindex(rules.LETTERS)
    return tab, {"rises_A_to_F": rises, "F_over_A": f_over_a, "groups": len(groups),
                 "all_letters_200": bool((raw["size"].fillna(0) >= MIN_GROUP).all()),
                 "passes": bool(rises and f_over_a >= 3)}


def crowd_scores(h: pd.DataFrame, q: float, rule: str, start, end, boot: bool = False) -> dict:
    a, b = (pd.Timestamp(x, tz="UTC") for x in (start, end))
    w = crowd.warning(h[[f"{c}_rank" for c in crowd.SIGNALS]], q, rule)
    drop, rise = crowd.events(h["mae_long"], h["mae_short"])
    m = (h.index >= a) & (h.index < b)
    out = crowd.ratio(w[m].to_numpy(), drop[m].to_numpy(), rise[m].to_numpy())
    if boot:
        ok = drop[m].notna().to_numpy() & rise[m].notna().to_numpy()
        lo, hi = crowd.block_bootstrap(w[m].to_numpy()[ok], drop[m].to_numpy()[ok], rise[m].to_numpy()[ok])
        out.update({"ci90_lo": lo, "ci90_hi": hi,
                    "passes": bool(out["ratio"] >= 1.5 and lo > 1) if np.isfinite(out["ratio"]) else False})
    return out


def similar_runs(h: pd.DataFrame, sc: dict, start, end, p0_const: float | None = None,
                 k_max: int = max(similar.K_CANDIDATES)) -> pd.DataFrame:
    """For every 6th hour in [start, end): the neighbours' summaries for every (k, scaled, prior) candidate
    and what actually happened. The pool is every earlier hour with a complete outcome. The up chance is
    pulled toward ``p0_const`` (the dev-period up share, as in the app), or the pool's past up share."""
    X = similar.describe(h)
    Z = similar.standardise(X, sc)
    pool = similar.Pool(h.index, Z, h["sigma24"].to_numpy(), h["r24"].to_numpy())
    a, b = (pd.Timestamp(x, tz="UTC") for x in (start, end))
    q = (h.index >= a) & (h.index < b) & (h.index.hour % EVERY_HOURS == 0) & np.isfinite(Z).all(axis=1) \
        & h["r24"].notna().to_numpy() & h["sigma24"].notna().to_numpy()
    rows = []
    up_ns = pool.r24 > 0
    for i in np.flatnonzero(q):
        t = h.index[i]
        n_known = pool.known_at(t)
        if n_known < k_max:
            continue
        idx = pool.neighbours(Z[i], t, k_max)
        p0 = p0_const if p0_const is not None else float(up_ns[:n_known].mean())
        sig = float(h["sigma24"].iloc[i])
        for k in similar.K_CANDIDATES:
            for scaled in (True, False):
                for prior in similar.PRIOR_CANDIDATES:
                    s = similar.summarise(pool, idx, sig, k, scaled, prior, p0)
                    rows.append({"ts": t, "k": k, "scaled": scaled, "prior": prior, "lo": s["lo"], "hi": s["hi"],
                                 "p_up": s["p_up"], "r24": float(h["r24"].iloc[i])})
    return pd.DataFrame(rows)


def similar_scores(runs: pd.DataFrame, up_share: float) -> pd.DataFrame:
    """Coverage of the 80% range and Brier of the up chance vs always saying ``up_share``."""
    r = runs.assign(inside=(runs["r24"] >= runs["lo"]) & (runs["r24"] <= runs["hi"]), up=(runs["r24"] > 0) * 1.0)
    r["brier"] = (r["p_up"] - r["up"]) ** 2
    r["brier_base"] = (up_share - r["up"]) ** 2
    g = r.groupby(["k", "scaled", "prior"]).agg(n=("inside", "size"), coverage=("inside", "mean"),
                                                brier=("brier", "mean"), brier_base=("brier_base", "mean"))
    g["coverage_ok"] = g["coverage"].between(0.75, 0.85)
    g["brier_ok"] = g["brier"] <= g["brier_base"]
    return g.reset_index()
