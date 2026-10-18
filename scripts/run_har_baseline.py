"""First real result: HAR-family baselines for BTC, walk-forward, out of sample.

    python scripts/run_har_baseline.py --symbol BTCUSDT --test-start 2024-01-01

Needs markPriceKlines or klines downloaded first (binance_archive). Prints a
QLIKE table with Diebold-Mariano tests vs plain HAR and the Model Confidence
Set, and saves it to data/reports/har_baseline_<symbol>.csv
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse

import pandas as pd

from regimecast import config
from regimecast.data.validate import load
from regimecast.vol import evaluate as ev
from regimecast.vol import har, realized

p = argparse.ArgumentParser()
p.add_argument("--symbol", default="BTCUSDT")
p.add_argument("--dataset", default="klines", help="klines (last price) or markPriceKlines")
p.add_argument("--test-start", default="2024-01-01")
a = p.parse_args()

df = load(a.dataset, a.symbol)
if df.empty:
    raise SystemExit("no data: run python -m regimecast.data.binance_archive first")
h = realized.build_targets(realized.minute_close(df))
tables = []
for horizon, target in ((1, "y_1h"), (24, "y_24h")):
    X = har.har_features(h, horizon)
    y = h[target]
    preds = {v: ev.walk_forward(X, y, lambda v=v: har.HARModel(v), a.test_start, horizon_hours=horizon)
             for v in ("har", "har_s", "har_j", "harq")}
    tab = ev.summary_table(y, preds, baseline="har", horizon=horizon)
    if horizon == 24:  # non-overlapping daily origins as a robustness check
        daily = {k: v[v.index.hour == 0] for k, v in preds.items()}
        tab["qlike_daily_origins"] = ev.summary_table(y, daily, "har", 24)["qlike"]
    losses = pd.DataFrame({k: ev.qlike(y.reindex(v.index), v) for k, v in preds.items()}).dropna()
    mcs = ev.model_confidence_set(losses, block=max(24, horizon))
    tab["in_MCS_90"] = [m in mcs["included"] for m in tab.index]
    tab.insert(0, "horizon_h", horizon)
    tables.append(tab)
    print(f"\n=== {a.symbol} {horizon}h ahead (test from {a.test_start}) ===")
    print(tab.round(4).to_string())
out = config.DATA_DIR / "reports" / f"har_baseline_{a.symbol}.csv"
out.parent.mkdir(parents=True, exist_ok=True)
pd.concat(tables).to_csv(out)
print("\nsaved", out)
