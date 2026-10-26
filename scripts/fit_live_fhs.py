"""Fit the app's liquidation model (FHS on HAR-S volatility) and save regimecast/risk/fhs_live.json.

    python scripts/fit_live_fhs.py                 # BTC, ETH, SOL; 1 h and 24 h

Development period only: HAR-S is forecast walk-forward from 2021 (out of sample), each
hour's worst mark-price move is scaled by that forecast, and the quantiles of the scaled
moves up to 2025-09-30 are kept. Needs features_1h and targets_1h (from Modal).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import json

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.models import harness, volatility as V
from regimecast.risk import fhs_live
from regimecast.vol import har

out = {}
for sym in config.SYMBOLS:
    h, t = store.features(sym, "1h"), store.targets(sym)
    out[sym] = {}
    for H, key in ((1, "1h"), (24, "24h")):
        X, y = V.dataset(h, t, H)
        sigma = np.sqrt(harness.walk_forward(X, y, lambda: har.HARModel("har_s"), "2021-01-01", H,
                                             refit_every_hours=24 * 90))
        tt = t.set_index("ts").reindex(sigma.index)
        end = harness.holdout_start() - pd.Timedelta(hours=H)
        entry = {"sigma_source": "HAR-S", "fitted_through": "2025-09-30"}
        for side in ("long", "short"):
            z = (tt[f"mae_{side}_{key}"] / sigma)[sigma.index <= end]
            entry[side] = fhs_live.fit(z)
        out[sym][key] = entry
        print(sym, key, {s: entry[s]["n"] for s in ("long", "short")}, "median z",
              round(entry["long"]["z"][50], 3), round(entry["short"]["z"][50], 3))
fhs_live.FILE.write_text(json.dumps(out) + "\n", encoding="utf-8")
print("saved", fhs_live.FILE)
