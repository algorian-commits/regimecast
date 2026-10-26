"""What the Phase 5 models predict: outcomes AFTER each hourly origin.

These columns look into the future on purpose, so they live in their own table
(``targets_1h``), never in the feature tables. A row labelled H (an hour end, the
moment the forecast is made) holds what happened in (H, H + horizon]:

* ``y_1h``, ``y_24h``: realised variance of the traded price (vol/realized.py);
* ``mae_long_<h>``, ``mae_short_<h>``: the largest adverse move of the MARK price
  (what triggers liquidation), as a log return from the mark price at H. A long is
  liquidated if the mark price falls to its liquidation price at any minute, so the
  minimum low matters, not where the hour closes.

Windows that cross a gap in the 1-minute mark data are left empty (NaN), not
stretched (risk/liquidation.max_adverse_excursion).
"""
from __future__ import annotations

import pandas as pd

from regimecast.risk.liquidation import max_adverse_excursion
from regimecast.vol import realized

HORIZONS = {"1h": 60, "24h": 24 * 60}


def hourly_targets(last: pd.DataFrame, mark: pd.DataFrame) -> pd.DataFrame:
    """last, mark: 1-minute bars (``ts`` = open time). Returns one row per hour end ``ts``."""
    t = realized.build_targets(realized.minute_close(last))[["y_1h", "y_24h"]]
    out = t.copy()
    for name, minutes in HORIZONS.items():
        mae = max_adverse_excursion(mark[["ts", "open", "high", "low", "close"]], minutes)
        mae.index = mae.index + pd.Timedelta(minutes=1)          # entry at the minute's close
        mae = mae[mae.index.minute == 0]                           # hourly origins (hour ends)
        out = out.join(mae.rename(columns={"mae_long": f"mae_long_{name}", "mae_short": f"mae_short_{name}"}),
                       how="left")
    out.index.name = "ts"
    return out.reset_index()
