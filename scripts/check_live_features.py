"""Do live features match the archive-built ones? Compares hourly features built from live
Binance data (features/live.py) with the training tables, on hours both cover.

    python scripts/check_live_features.py --symbol BTCUSDT
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse

import numpy as np
import pandas as pd

from regimecast.data import store
from regimecast.features import build, live

COLS = ["close", "rv", "rv_24h", "volume_usd", "taker_buy_share", "premium_mean", "mark_index_gap", "range_max_1m",
        "funding_rate", "oi", "oi_usd", "ls_top", "ls_accounts", "taker_ls", "oi_chg_24h", "stress_minutes"]

ap = argparse.ArgumentParser()
ap.add_argument("--symbol", default="BTCUSDT")
a = ap.parse_args()
inp = live.fetch_inputs(a.symbol, minutes=31 * 24 * 60, detail_minutes=6 * 24 * 60)
h_live = build.hourly_features(inp).set_index("ts")
h_arch = store.features(a.symbol, "1h").set_index("ts")
common = h_live.index.intersection(h_arch.index)
common = common[common >= h_live.index[-1] - pd.Timedelta(days=4)]     # where the live detail data is complete
print(f"{len(common)} overlapping hours, {common.min()} to {common.max()}")
rows = []
for c in COLS:
    x, y = h_live.loc[common, c], h_arch.loc[common, c]
    ok = x.notna() & y.notna()
    rel = ((x[ok] - y[ok]).abs() / y[ok].abs().clip(lower=1e-12)).median() if ok.any() else np.nan
    rows.append({"feature": c, "n": int(ok.sum()), "median_rel_diff": rel,
                 "corr": np.corrcoef(x[ok], y[ok])[0, 1] if ok.sum() > 2 and y[ok].std() > 0 else np.nan})
print(pd.DataFrame(rows).round(4).to_string(index=False))
