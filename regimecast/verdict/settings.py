"""Phase 12 settings: what ``scripts/run_phase12.py`` fixed on the dev period, else the defaults below.

The defaults were written down on 28 Oct 2026, before any dev-period run, so the app works before the freeze.
Until then the API says so (``settings_frozen_at`` is null), the crowd warning is off (its thresholds come
from the dev period only) and the what-if strip sizes its days from the volatility forecast.

``frozen.json`` holds, once written:
    grade         knock-out bands and which market factors count (chosen on 2022-01 to 2024-09)
    crowd         per coin: the extremes of funding, top traders' long/short ratio and open-interest growth,
                  and the historical odds shown with a warning
    similar       how many past hours to compare with, the shrinkage of the up-chance, the feature scaling
    whatif        per coin and regime: the 90th and 99th percentile 24 h moves (normal and bad day)
    thin_hours    per coin: the four hours of the day (UTC) with the least trading
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

FROZEN_FILE = Path(__file__).with_name("frozen.json")
SIMILAR_DIR = Path(__file__).with_name("similar")      # the past hours similar moments compares with

DEFAULTS: dict = {
    "frozen_at": None,
    "grade": {"knockout_cuts": [0.10, 0.25, 0.45, 0.65],
              "weights": {"regime": 1, "stress": 1, "crowd": 1, "timing": 1}},
    "crowd": {},
    "similar": {"k": 200, "shrink": 1.0, "scale": True, "scaling": {}},
    "whatif": {},
    "thin_hours": {},
}


def load(path: Path = FROZEN_FILE) -> dict:
    """The frozen settings, with any missing part filled from the defaults."""
    out = copy.deepcopy(DEFAULTS)
    if path.exists():
        out.update(json.loads(path.read_text(encoding="utf-8")))
    return out
