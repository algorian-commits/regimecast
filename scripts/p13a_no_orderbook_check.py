"""After the final test (13A): does the live combination, without the order book, still beat HAR?

    python scripts/p13a_no_orderbook_check.py        # writes data/reports/p13a_no_orderbook.csv

The final evaluation scored the combination with order-book inputs in its feature list. The app has no live order
book, so it runs without them. This reruns the final year's walk-forward (the same 90-day folds, past data only) for
both versions next to HAR. Not a pass bar and never a replacement for docs/FINAL_EVALUATION.md.

``ob_rows_fitted`` counts the order-book rows the trees could learn from in the fold's last refit: LightGBM keeps
the latest 20% of a training window for early stopping, so the order book (from May 2025) barely reaches its trees.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.models import harness, volatility as V
from regimecast.vol import combo_live, evaluate as E

OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "reports" / "p13a_no_orderbook.csv"
GRID_START, STEP = pd.Timestamp("2021-01-01", tz="UTC"), pd.Timedelta(days=90)
KW = dict(refit_every_hours=24 * 90, final=True, end="2026-09-30 23:00")
FIRST = GRID_START + ((harness.holdout_start() - GRID_START) // STEP) * STEP   # the fold holding 2025-10-01


def no_ob(H):
    m = V.Combo(H)
    m.gbm = V.GBMVol(H, combo_live.LIVE_FEATURES)
    return m


def ob_rows_fitted(X, y, H):
    """Order-book rows in the part of the last refit's window that the trees are fitted on."""
    fit_end = harness.folds(X.index, FIRST, H, **KW)[-1].fit_end
    idx = X.index[(X.index <= fit_end) & y.notna() & (y > 0)]
    cut = idx[int(len(idx) * 0.8)] - pd.Timedelta(hours=H)
    return int(X.loc[idx[idx < cut], "ob_spread_bps_mean"].notna().sum())


rows = []
for sym in config.SYMBOLS:
    h, t = store.features(sym, "1h"), store.targets(sym)
    f = config.parquet_dir() / "dvol" / sym / "all.parquet"
    dvol = pd.read_parquet(f) if f.exists() else None
    for H in (1, 24):
        X, y = V.dataset(h, t, H, dvol)
        p = pd.DataFrame({n: harness.walk_forward(X, y, mk, FIRST, H, **KW)
                          for n, mk in (("har", harness.BASELINES["har"]), ("combo", lambda: V.Combo(H)),
                                        ("combo_no_ob", lambda: no_ob(H)))})
        p = p[p.index >= harness.holdout_start()].assign(y=y).dropna()
        base = E.qlike(p["y"], p["har"])
        r = {"symbol": sym, "horizon_h": H, "n": len(p), "ob_rows_fitted": ob_rows_fitted(X, y, H),
             "hours_that_differ": int((p["combo"] != p["combo_no_ob"]).sum()),
             "largest_rel_diff": float((p["combo"] / p["combo_no_ob"] - 1).abs().max())}
        for n in ("combo", "combo_no_ob"):
            ql = E.qlike(p["y"], p[n])
            r[f"{n}_vs_har_pct"] = float(100 * (ql.mean() / base.mean() - 1))
            r[f"{n}_dm_p"] = E.dm_test(ql, base, H)["p_value"]
        rows.append(r)
        print(r["symbol"], H, "differ", r["hours_that_differ"], "ob rows", r["ob_rows_fitted"],
              round(r["combo_vs_har_pct"], 3), round(r["combo_no_ob_vs_har_pct"], 3), flush=True)
OUT.parent.mkdir(parents=True, exist_ok=True)
pd.DataFrame(rows).to_csv(OUT, index=False)
print("saved", OUT)
