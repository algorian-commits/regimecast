"""Phase 12 results: the trade verdict's settings fixed on the dev period, and its dev-period tables.

    python scripts/run_phase12.py                 # choose, check, freeze -> data/reports/phase12_*
    python scripts/run_phase12.py --dry-run out   # the same steps on made-up data, into ./out (real files untouched)

Needs data/ (the hourly feature and target tables) and the walk-forward files of Phases 4-6 in data/reports
(phase6_pred_<coin>.parquet, regimes_base_<coin>.parquet). The final test period (from 2025-10-01) stays closed:
every query stops before it. The steps and the choice rules (fixed before the run) are in
regimecast/verdict/devrun.py.

Outputs: data/reports/phase12_*.csv, regimecast/verdict/frozen.json (never overwritten with different settings
unless --refreeze, after a logged decision) and regimecast/verdict/similar/<coin>.npz (the past hours the app's
similar moments compares with; commit them with frozen.json).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
from pathlib import Path

from regimecast import config
from regimecast.risk import fhs_live
from regimecast.verdict import devrun, evaluate as E, settings, timing as T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refreeze", action="store_true", help="overwrite frozen.json (log the reason first)")
    ap.add_argument("--dry-run", metavar="DIR", help="run on synthetic data into DIR, to check the pipeline")
    ap.add_argument("--every-h", type=int, default=None,
                    help="similar moments scored every N hours in the tables (default 1; dry run 6)")
    a = ap.parse_args()
    cal = T.load_calendar()
    if a.dry_run:
        out = Path(a.dry_run)
        panels = {s: E.synthetic_panel(seed=i) for i, s in enumerate(config.SYMBOLS)}
        fhs = {s: E.synthetic_fhs() for s in config.SYMBOLS}
        devrun.run(panels, fhs, cal, out, out / "frozen.json", out / "similar", refreeze=True, every_h=a.every_h or 6)
        return
    panels = {s: E.load(s) for s in config.SYMBOLS}
    devrun.run(panels, fhs_live.load(), cal, E.REPORTS, settings.FROZEN_FILE, settings.SIMILAR_DIR,
               refreeze=a.refreeze, every_h=a.every_h or 1)


if __name__ == "__main__":
    main()
