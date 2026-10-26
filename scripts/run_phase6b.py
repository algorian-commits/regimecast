"""Phase 6b: direction-aware liquidation model vs FHS (the app's model), overall and in crashes.

    python scripts/run_phase6b.py         # data/reports/phase6b_*.csv

Same scoring as Phase 5 (2022-01 to 2025-09, out of sample, final test period closed) and the same
volatility the app uses (HAR-S, walk-forward from 2021). Needs phase5_liq_*.parquet (FHS predictions).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import warnings

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.models import calibration as C
from regimecast.models import harness, liquidation_risk as LR, stress as S, volatility as V
from regimecast.vol import har

warnings.filterwarnings("ignore")
REP = config.DATA_DIR / "reports"
CRASHES = {"FTX collapse": ("2022-11-06", "2022-11-12"), "Yen carry unwind": ("2024-08-03", "2024-08-08")}


def state(sym):
    """Directional state per hour, all out of sample / known at t; stress probability walk-forward from 2021."""
    h, t = store.features(sym, "1h"), store.targets(sym)
    reg = pd.read_parquet(REP / f"regimes_base_{sym}.parquet")
    dvf = config.parquet_dir() / "dvol" / sym / "all.parquet"
    dv = pd.read_parquet(dvf) if dvf.exists() else None
    Xs, ys = S.dataset(h, t, reg, dv)
    p_stress = harness.walk_forward(Xs, ys, lambda: S.StressGBM(S.LIVE_FEATURES), "2021-01-01", 1,
                                    refit_every_hours=24 * 90)
    F = Xs.copy()
    F["p_stress"] = p_stress
    return h, t, dv, F


def main():
    summ, crash_rows = [], []
    for sym in config.SYMBOLS:
        h, t, dv, F = state(sym)
        for H in (24, 1):
            X, y = V.dataset(h, t, H, dv)
            sig = harness.walk_forward(X, y, lambda: har.HARModel("har_s"), "2021-01-01", H, refit_every_hours=24 * 90)
            F["iv_minus_sigma"] = X["log_iv_h"] - np.log(sig.reindex(X.index))
            d = LR.evaluate_direct(sym, H, F, sig, t)
            fhs = pd.read_parquet(REP / f"phase5_liq_{sym}_{H}h.parquet")[["ts", "side", "leverage", "hit", "fhs_hars"]]
            for df in (d, fhs):
                df["ts"] = pd.to_datetime(df["ts"], utc=True).astype("datetime64[us, UTC]")
                df["leverage"] = df["leverage"].astype(int)
            m = d.merge(fhs, on=["ts", "side", "leverage"], how="inner", suffixes=("", "_p5"))
            m = m[m["ts"] >= pd.Timestamp("2022-01-01", tz="UTC")].dropna(subset=["direct", "direct_cal", "fhs_hars"])
            assert (m["hit"] == m["hit_p5"]).all()
            m.to_parquet(REP / f"phase6b_{sym}_{H}h.parquet")
            for model in ("fhs_hars", "direct", "direct_cal"):
                for side in ("all", "long", "short"):
                    g = m if side == "all" else m[m["side"] == side]
                    summ.append({"symbol": sym, "horizon_h": H, "model": model, "side": side, "n": len(g),
                                 "brier": C.brier(g[model], g["hit"]), "ece": C.ece(g[model], g["hit"]),
                                 "mean_p": g[model].mean(), "observed": g["hit"].mean()})
            for name, (a, b) in CRASHES.items():
                w = m[(m["ts"] >= pd.Timestamp(a, tz="UTC")) & (m["ts"] < pd.Timestamp(b, tz="UTC")) & m["leverage"].isin([10, 20, 50])]
                g = w.groupby(["leverage", "side"]).agg(observed=("hit", "mean"), fhs=("fhs_hars", "mean"),
                                                        direct=("direct_cal", "mean"), n=("hit", "size")).reset_index()
                g.insert(0, "crash", name); g.insert(0, "horizon_h", H); g.insert(0, "symbol", sym)
                crash_rows.append(g)
            s = pd.DataFrame(summ)
            print(s[(s.symbol == sym) & (s.horizon_h == H)].round(5).to_string(index=False), flush=True)
    pd.DataFrame(summ).to_csv(REP / "phase6b_summary.csv", index=False)
    cr = pd.concat(crash_rows)
    cr.to_csv(REP / "phase6b_crashes.csv", index=False)
    print(cr[cr.horizon_h == 24].round(3).to_string(index=False))


if __name__ == "__main__":
    main()
