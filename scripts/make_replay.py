"""Build a crash replay file for the app (M12): regimecast/replay/events/<event>.json.gz.

    python scripts/make_replay.py ftx_2022     # FTX collapse, Nov 2022 (development period)
    python scripts/make_replay.py oct_2025     # 10 Oct 2025: refused until docs/FINAL_EVALUATION.md exists

Needs the stored archive (data/parquet, on the PC); ETH and SOL mark prices are read from
Binance's public API when their minute archive is not on this machine. Uses the app's frozen
models only (regimecast/replay/build.py). Commit the file it writes: the deployed API has no data/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from regimecast.replay import build  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("event", choices=sorted(build.EVENTS))
    ap.add_argument("--symbols", nargs="*", help="default: BTCUSDT ETHUSDT SOLUSDT")
    a = ap.parse_args()
    try:
        replay = build.build(a.event, a.symbols)
    except build.FinalTestPeriodClosed as e:
        sys.exit(f"Not built: {e}")
    f = build.save(replay)
    for s, c in replay["coins"].items():
        hrs = [h for h in c["hours"] if h["p_stress"] is not None]
        print(f"{s}: {c['n_minutes']} minutes, {len(c['hours'])} hours, "
              f"peak stress {max(h['p_stress'] for h in hrs):.0%}, "
              f"stressed regime {sum(h['regime'] == 'stressed' for h in c['hours'])} h")
    print(f"wrote {f} ({f.stat().st_size / 1e3:.0f} kB)")


if __name__ == "__main__":
    main()
