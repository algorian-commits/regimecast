"""Fit the app's live volatility model (HAR-S) and save it in regimecast/vol/har_live.json.

    python scripts/fit_live_har.py --symbols BTCUSDT

Fits on the development period only (before config.HOLDOUT_START). Needs the 1-minute
candles locally (or run the same function on Modal, where all three coins are stored).
Commit the updated JSON so the API on Railway uses the same coefficients.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import json

from regimecast.vol import live

p = argparse.ArgumentParser()
p.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
a = p.parse_args()
models = live.load_models()
for s in a.symbols:
    models[s.upper()] = live.fit_models(s.upper())
    print(s.upper(), {k: (v["n_train"], v["fitted_through"]) for k, v in models[s.upper()].items()})
live.MODELS_FILE.write_text(json.dumps(models, indent=2) + "\n", encoding="utf-8")
print("saved", live.MODELS_FILE)
