"""Phase 6 results: stress early warning vs simple rules, lead times before crashes.

    python scripts/run_phase6.py          # all coins -> data/reports/phase6_*.csv

Development period only: walk-forward from 2022-01 to 2025-09 (final test period closed).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import warnings

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.models import calibration as C
from regimecast.models import harness, stress as S

warnings.filterwarnings("ignore")
REP = config.DATA_DIR / "reports"
MODELS = ["persist", "vol_rule", "regime_rule", "gbm", "gbm_cal"]
OB = [f for f in S.FEATURES if f.startswith(("ob_", "log_ob_"))]


def load(symbol):
    h, t = store.features(symbol, "1h"), store.targets(symbol)
    f = REP / f"regimes_base_{symbol}.parquet"
    reg = pd.read_parquet(f) if f.exists() else None
    d = config.parquet_dir() / "dvol" / symbol / "all.parquet"
    return S.dataset(h, t, reg, pd.read_parquet(d) if d.exists() else None)


def scores(out: pd.DataFrame, symbol: str) -> pd.DataFrame:
    rows = []
    ok = out.dropna(subset=[*MODELS, "y"])
    for m in MODELS:
        rows.append({"symbol": symbol, "model": m, "n": len(ok), "base_rate": ok["y"].mean(),
                     "auc": S.auc(ok[m], ok["y"]), "avg_precision": S.average_precision(ok[m], ok["y"]),
                     "brier": C.brier(ok[m], ok["y"]), "ece": C.ece(ok[m], ok["y"])})
    return pd.DataFrame(rows)


def alarm_table(out, symbol):
    ok = out.dropna(subset=["gbm_cal", "y"])
    rows = [{"symbol": symbol, "model": "gbm_cal", **S.alarms(ok["gbm_cal"], ok["y"], th)} for th in (0.1, 0.2, 0.3, 0.5)]
    for m, th in (("persist", 0.2), ("vol_rule", 0.1)):
        rows.append({"symbol": symbol, "model": m, **S.alarms(ok[m], ok["y"], th)})
    return pd.DataFrame(rows)


def lead_times(out, symbol, threshold=0.2):
    f = REP / f"crash_leadtime_{symbol}.csv"
    if not f.exists():
        return pd.DataFrame()
    crashes = pd.read_csv(f, parse_dates=["onset"]).dropna(subset=["onset"])
    rows = []
    for _, c in crashes.iterrows():
        onset = pd.Timestamp(c["onset"])
        onset = onset.tz_localize("UTC") if onset.tzinfo is None else onset
        if onset < out.index.min():
            continue
        w = out.loc[onset - pd.Timedelta(hours=72): onset]
        p = w["gbm_cal"]
        run = 0
        for v in p[::-1]:
            if v >= threshold:
                run += 1
            else:
                break
        stress_hours = w.index[w["y"].shift(1).fillna(0) > 0]
        rows.append({"symbol": symbol, "event": c["event"], "onset": onset, "p_at_onset": float(p.iloc[-1]),
                     "max_p_24h_before": float(p.loc[onset - pd.Timedelta(hours=24): onset].max()),
                     "hours_alarm_before_onset": run,
                     "first_stress_event_before_onset_h": (onset - stress_hours[0]).total_seconds() / 3600
                     if len(stress_hours) else None})
    return pd.DataFrame(rows)


def main():
    all_scores, all_alarms, all_leads, shap, ob_rows = [], [], [], {}, []
    for sym in config.SYMBOLS:
        X, y = load(sym)
        out = S.compare(X, y)
        out = out[out.index >= pd.Timestamp("2022-01-01", tz="UTC")]
        out.to_parquet(REP / f"phase6_pred_{sym}.parquet")
        sc = scores(out, sym)
        all_scores.append(sc)
        all_alarms.append(alarm_table(out, sym))
        all_leads.append(lead_times(out, sym))
        print(f"\n=== {sym}\n{sc.round(4).to_string(index=False)}", flush=True)
        # order book: May-Sep 2025 only, with vs without the order-book inputs
        no_ob = harness.walk_forward(X, y, lambda: S.StressGBM([f for f in S.FEATURES if f not in OB]),
                                     "2025-05-01", 1, refit_every_hours=24 * 30)
        with_ob = harness.walk_forward(X, y, lambda: S.StressGBM(), "2025-05-01", 1, refit_every_hours=24 * 30)
        both = pd.DataFrame({"with_ob": with_ob, "without_ob": no_ob, "y": y}).dropna()
        ob_rows.append({"symbol": sym, "n": len(both), "auc_with_ob": S.auc(both["with_ob"], both["y"]),
                        "auc_without_ob": S.auc(both["without_ob"], both["y"]),
                        "ap_with_ob": S.average_precision(both["with_ob"], both["y"]),
                        "ap_without_ob": S.average_precision(both["without_ob"], both["y"])})
        dev = X.index <= harness.holdout_start() - pd.Timedelta(hours=1)
        m = S.StressGBM().fit(X[dev], y[dev])
        last = X[dev].loc[X[dev].index >= harness.holdout_start() - pd.Timedelta(days=365)]
        shap[sym] = m.contributions(last).drop(columns="base").abs().mean()
    pd.concat(all_scores).to_csv(REP / "phase6_scores.csv", index=False)
    pd.concat(all_alarms).to_csv(REP / "phase6_alarms.csv", index=False)
    pd.concat([l for l in all_leads if len(l)]).to_csv(REP / "phase6_leadtimes.csv", index=False)
    pd.DataFrame(ob_rows).to_csv(REP / "phase6_orderbook.csv", index=False)
    pd.DataFrame(shap).sort_values("BTCUSDT", ascending=False).to_csv(REP / "phase6_shap_importance.csv")
    print("\nalarms\n", pd.concat(all_alarms).round(3).to_string(index=False))
    print("\nlead times\n", pd.concat([l for l in all_leads if len(l)]).round(3).to_string(index=False))
    print("\norder book (May-Sep 2025)\n", pd.DataFrame(ob_rows).round(3).to_string(index=False))
    print("\nSHAP\n", pd.DataFrame(shap).sort_values("BTCUSDT", ascending=False).head(12).round(3).to_string())


if __name__ == "__main__":
    main()
