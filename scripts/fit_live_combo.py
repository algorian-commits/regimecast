"""Fit the app's volatility model (HAR-S + LightGBM, no order book) and save regimecast/vol/combo_live/.

    python scripts/fit_live_combo.py                 # BTC, ETH, SOL; 1 h and 24 h

One model per coin and horizon, on every hour whose target ended by 2026-09-30 (the end of the final test period),
with the final evaluation's fixed settings (models/volatility.py). Needs features_1h, targets_1h and dvol.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.models import volatility as V
from regimecast.vol import combo_live

for sym in config.SYMBOLS:
    h, t = store.features(sym, "1h"), store.targets(sym)
    f = config.parquet_dir() / "dvol" / sym / "all.parquet"
    dvol = pd.read_parquet(f) if f.exists() else None
    models, Xs, ys = {}, {}, {}
    for key, H in combo_live.HORIZONS.items():
        Xs[key], ys[key] = V.dataset(h, t, H, dvol)
        models[key] = combo_live.fit(Xs[key], ys[key], H)
    meta = combo_live.save(sym, models, Xs, ys)
    for key, m in meta.items():
        print(sym, key, m["train_from"][:10], "to", m["train_to"][:16], "rows", m["n_train"],
              "trees", m["gbm"]["best_iteration"])
print("saved", combo_live.MODEL_DIR)
