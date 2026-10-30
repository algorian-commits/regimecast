"""Phase 13B step 1: choose and freeze crash mode on BTC, ETH and SOL.

    python scripts/crash_mode_dev.py run       # the grid (8 settings) -> data/reports/p13b/ and p13b_crash_grid.csv
    python scripts/crash_mode_dev.py freeze    # apply the rule below -> regimecast/models/crash_mode.json

Inputs are the walk-forward, out-of-sample outputs the final evaluation already produced for 2021-2026
(data/reports/final_vol_* for the HAR-S forecast, final_stress_pred_* for the stress probability), so no
crash-mode number at an hour uses anything fitted on that hour. The old final test period may be used for
development now (Phase 13 plan).

THE SELECTION RULE (written and committed before the grid ran):
1. Choosing period: origins 2022-01-01 to 2024-09-30, 24 h horizon (the bar's horizon), BTC, ETH, SOL.
2. A setting is admissible if its ECE over all hours (pooled over long and short and the 2x-100x leverage
   grid) is at most 0.25 percentage points above plain FHS's on each of the three coins (the bar's second
   condition).
3. Among admissible settings, choose the smallest average over the three coins of the long side's
   calibration gap |mean predicted - observed| in hours with stress >= 10% (the bar's first condition).
4. Settings within 0.05 points of the best on step 3 count as tied; the tie goes to the earlier one in the
   fixed order of crash_mode.candidates() (cut 0.10 before 0.30, replace before max, no floor before floor).
5. If no setting is admissible, the one with the smallest worst-coin ECE excess is frozen and reported as
   failing this check (the new-coin exam then judges it anyway).
6. Check period 2024-10-01 to 2026-09-30 (includes the 10 Oct 2025 crash) and the 1 h horizon are reported
   for every setting and never used for choosing.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import datetime as dt
import json
import warnings

import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.models import crash_mode as CM

warnings.filterwarnings("ignore")
REP = config.DATA_DIR / "reports"
OUT = REP / "p13b"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CHOOSE = ("2022-01-01", "2024-10-01")
CHECK = ("2024-10-01", "2026-10-01")
CRASH = ("2025-10-10", "2025-10-12")
ECE_SLACK = 0.0025
TIE = 0.0005


def win(df, period):
    a, b = (pd.Timestamp(x, tz="UTC") for x in period)
    return df[(df["ts"] >= a) & (df["ts"] < b)]


def run() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cands = CM.candidates()
    models = [CM.name(c) for c in cands]
    rows, repro = [], []
    for s in SYMBOLS:
        t = store.targets(s)
        ps = pd.read_parquet(REP / f"final_stress_pred_{s}.parquet")["gbm"]
        for H in (24, 1):
            print(f"[{s}] {H}h", flush=True)
            sigma = pd.read_parquet(REP / f"final_vol_{s}_{H}h.parquet")["har_s"]
            p = CM.evaluate(s, H, sigma, t, ps, cands, final=True)
            p.to_parquet(OUT / f"crash_dev_{s}_{H}h.parquet")
            ref = pd.read_parquet(REP / f"final_liq_{s}_{H}h_hars.parquet", columns=["ts", "side", "leverage", "fhs"])
            j = p[["ts", "side", "leverage", "fhs"]].merge(ref, on=["ts", "side", "leverage"], suffixes=("", "_ref"))
            repro.append({"symbol": s, "horizon_h": H, "rows": len(p), "matched": len(j),
                          "fhs_max_abs_diff": float((j["fhs"] - j["fhs_ref"]).abs().max())})
            for label, period in (("choose", CHOOSE), ("check", CHECK), ("crash_10_11_oct_2025", CRASH)):
                r = CM.score(win(p, period), models)
                for m in models:
                    rows.append({"symbol": s, "horizon_h": H, "period": label, "setting": m,
                                 "n_hot_hours": r["n_hot_hours"], "observed_hot_long": r["observed_hot_long"],
                                 "fhs_hot_long_mean": r["fhs_hot_long_mean"], "fhs_hot_long_gap": r["fhs_hot_long_gap"],
                                 "fhs_ece": r["fhs_ece"], "fhs_brier": r["fhs_brier"],
                                 "hot_long_mean": r[f"{m}_hot_long_mean"], "hot_long_gap": r[f"{m}_hot_long_gap"],
                                 "ece": r[f"{m}_ece"], "brier": r[f"{m}_brier"]})
    pd.DataFrame(rows).to_csv(REP / "p13b_crash_grid.csv", index=False)
    pd.DataFrame(repro).to_csv(REP / "p13b_crash_reproducibility.csv", index=False)
    print(pd.DataFrame(repro).to_string())


def freeze() -> None:
    g = pd.read_csv(REP / "p13b_crash_grid.csv")
    c = g[(g["period"] == "choose") & (g["horizon_h"] == 24)].copy()
    c["ece_excess"] = c["ece"] - c["fhs_ece"]
    by = c.groupby("setting", sort=False).agg(worst_ece_excess=("ece_excess", "max"), mean_gap=("hot_long_gap", "mean"),
                                              fhs_mean_gap=("fhs_hot_long_gap", "mean"))
    order = [CM.name(x) for x in CM.candidates()]
    by = by.reindex(order)
    by["admissible"] = by["worst_ece_excess"] <= ECE_SLACK
    ok = by[by["admissible"]]
    if len(ok):
        best = ok["mean_gap"].min()
        chosen = next(n for n in order if n in ok.index and ok.loc[n, "mean_gap"] <= best + TIE)
        passed = True
    else:
        chosen = by["worst_ece_excess"].idxmin()
        passed = False
    setting = next(x for x in CM.candidates() if CM.name(x) == chosen)
    chk = g[(g["period"] == "check") & (g["horizon_h"] == 24) & (g["setting"] == chosen)]
    frozen = {
        "frozen_at": dt.date.today().isoformat(),
        "settings": setting, "name": chosen, "switch_on": CM.SWITCH_ON, "min_past": CM.MIN_PAST,
        "applies_to": "long side only, 1 h and 24 h, on FHS with the HAR-S forecast (the app's liquidation model)",
        "stress_input": "P(stress in the next hour), the stress classifier's probability (models/stress.py, gbm)",
        "chosen_on": "BTC, ETH, SOL, origins 2022-01-01 to 2024-09-30, 24 h (scripts/crash_mode_dev.py rule)",
        "passed_dev_ece_check": passed,
        "choose_period": {n: {k: round(float(v), 6) for k, v in r.items() if k != "admissible"} | {"admissible": bool(r["admissible"])}
                          for n, r in by.iterrows()},
        "check_period_chosen": {r["symbol"]: {"hot_long_gap": round(r["hot_long_gap"], 6),
                                              "fhs_hot_long_gap": round(r["fhs_hot_long_gap"], 6),
                                              "ece": round(r["ece"], 6), "fhs_ece": round(r["fhs_ece"], 6)}
                                for _, r in chk.iterrows()},
    }
    CM.FROZEN_FILE.write_text(json.dumps(frozen, indent=2) + "\n", encoding="utf-8")
    print(by.to_string())
    print(json.dumps(frozen, indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["run", "freeze"])
    a = ap.parse_args()
    run() if a.step == "run" else freeze()


if __name__ == "__main__":
    main()
