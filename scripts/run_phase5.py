"""Phase 5 results: volatility forecaster vs HAR, and calibrated liquidation probability.

    python scripts/run_phase5.py                         # all coins, 1 h and 24 h (about 30 min)
    python -m http.server 8010 -d data/reports           # then open http://localhost:8010/phase5.html

Development period only: volatility forecasts are made walk-forward from 2021-01 (so the
liquidation models are fitted on out-of-sample volatility), and everything is scored from
2022-01 up to the final test period (from 2025-10-01), which stays closed.
Outputs: data/reports/phase5_*.csv and phase5.html.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import json
import warnings

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.models import calibration as C
from regimecast.models import harness, liquidation_risk as LR, volatility as V
from regimecast.vol import evaluate as ev

warnings.filterwarnings("ignore")
SCORE_FROM = pd.Timestamp("2022-01-01", tz="UTC")
CRASHES = {"FTX collapse": ("2022-11-06", "2022-11-12"), "Yen carry unwind": ("2024-08-03", "2024-08-08")}
REP = config.DATA_DIR / "reports"


def load(symbol: str, horizon: int):
    h, t = store.features(symbol, "1h"), store.targets(symbol)
    f = config.parquet_dir() / "dvol" / symbol / "all.parquet"
    dvol = pd.read_parquet(f) if f.exists() else None
    X, y = V.dataset(h, t, horizon, dvol)
    return X, y, t, dvol is not None


def vol_results(symbol: str, horizon: int, X, y, has_dvol: bool) -> tuple[pd.DataFrame, dict]:
    preds = V.compare(X, y, horizon, test_start="2021-01-01")
    if has_dvol:  # ablation: what DVOL adds
        feats = [f for f in V.FEATURES if f != "log_iv_h"]
        preds["gbm_no_dvol"] = harness.walk_forward(X, y, lambda: V.GBMVol(horizon, feats), "2021-01-01", horizon,
                                                    refit_every_hours=24 * 90).rename("gbm_no_dvol")
    scored = {k: v[v.index >= SCORE_FROM] for k, v in preds.items()}
    tab = ev.summary_table(y, scored, "har", horizon)
    vs_s = ev.summary_table(y, {k: scored[k] for k in ("har_s", "gbm", "combo")}, "har_s", horizon)
    tab["dm_p_vs_har_s"] = vs_s["dm_p"].reindex(tab.index)
    losses = pd.DataFrame({k: ev.qlike(y.reindex(v.index), v) for k, v in scored.items()}).dropna()
    mcs = ev.model_confidence_set(losses, block=max(24, horizon))
    tab["in_MCS_90"] = [m in mcs["included"] for m in tab.index]
    tab.insert(0, "horizon_h", horizon)
    tab.insert(0, "symbol", symbol)
    return tab, preds


def shap_importance(X, y, horizon: int) -> pd.Series:
    """Mean |SHAP| (log-variance scale) of a LightGBM fitted on the whole dev period, over its last year."""
    dev = X.index <= harness.holdout_start() - pd.Timedelta(hours=horizon)
    m = V.GBMVol(horizon).fit(X[dev], y[dev])
    last = X[dev].loc[X[dev].index >= harness.holdout_start() - pd.Timedelta(days=365)]
    c = m.contributions(last).drop(columns="base")
    return c.abs().mean().sort_values(ascending=False)


def liq_results(symbol: str, horizon: int, X, preds: dict, t) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    p = LR.evaluate(symbol, horizon, X, preds["combo"], t, features=V.FEATURES)
    ph = LR.evaluate(symbol, horizon, X, preds["har_s"], t, features=V.FEATURES, with_qgbm=False)
    p = p.merge(ph[["ts", "side", "leverage", "gauss", "fhs", "gauss_cal", "fhs_cal"]]
                .rename(columns=lambda c: c if c in ("ts", "side", "leverage") else f"{c}_hars"),
                on=["ts", "side", "leverage"], how="left")
    p = p[p["ts"] >= SCORE_FROM]
    models = ["gauss", "fhs", "qgbm", "gauss_cal", "fhs_cal", "qgbm_cal", "gauss_hars", "fhs_hars", "fhs_cal_hars"]
    ok = p.dropna(subset=models)
    rows = []
    for m in models:
        rows.append({"symbol": symbol, "horizon_h": horizon, "model": m, "n": len(ok),
                     "brier": C.brier(ok[m], ok["hit"]), "log_loss": C.log_loss(ok[m], ok["hit"]),
                     "ece": C.ece(ok[m], ok["hit"]), "mean_predicted": ok[m].mean(), "observed": ok["hit"].mean()})
    summary = pd.DataFrame(rows)
    for m in ("gauss", "fhs"):
        d = ok.set_index("ts")[[m, "hit"]]
        lo, hi = C.block_bootstrap(d, lambda x: C.ece(x[m], x["hit"]), n=100)
        summary.loc[summary["model"] == m, ["ece_lo", "ece_hi"]] = [lo, hi]
    by_lev = ok.groupby(["leverage", "side"]).agg(observed=("hit", "mean"), gauss=("gauss", "mean"),
                                                  fhs=("fhs", "mean"), fhs_hars=("fhs_hars", "mean"),
                                                  n=("hit", "size")).reset_index()
    by_lev.insert(0, "horizon_h", horizon)
    by_lev.insert(0, "symbol", symbol)
    crash = []
    for name, (a, b) in CRASHES.items():
        w = ok[(ok["ts"] >= pd.Timestamp(a, tz="UTC")) & (ok["ts"] < pd.Timestamp(b, tz="UTC")) & ok["leverage"].isin([10, 20, 50])]
        if len(w):
            g = w.groupby(["leverage", "side"]).agg(observed=("hit", "mean"), gauss=("gauss", "mean"), fhs=("fhs", "mean"),
                                                    n=("hit", "size")).reset_index()
            g.insert(0, "crash", name)
            crash.append(g)
    crash = pd.concat(crash) if crash else pd.DataFrame()
    if len(crash):
        crash.insert(0, "horizon_h", horizon)
        crash.insert(0, "symbol", symbol)
    p.to_parquet(REP / f"phase5_liq_{symbol}_{horizon}h.parquet")
    return summary, by_lev, crash


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=config.SYMBOLS)
    ap.add_argument("--horizons", nargs="+", type=int, default=[24, 1])
    a = ap.parse_args()
    REP.mkdir(parents=True, exist_ok=True)
    vols, liqs, levs, crashes, shap = [], [], [], [], {}
    for sym in a.symbols:
        for H in a.horizons:
            X, y, t, has_dvol = load(sym, H)
            tab, preds = vol_results(sym, H, X, y, has_dvol)
            vols.append(tab)
            print(f"\n=== {sym} {H}h volatility\n{tab.round(4).to_string()}", flush=True)
            s, l, c = liq_results(sym, H, X, preds, t)
            liqs.append(s); levs.append(l); crashes.append(c)
            print(s.round(5).to_string(index=False), flush=True)
            if H == 24:
                shap[sym] = shap_importance(X, y, H)
    pd.concat(vols).to_csv(REP / "phase5_volatility.csv")
    pd.concat(liqs).to_csv(REP / "phase5_liquidation.csv", index=False)
    pd.concat(levs).to_csv(REP / "phase5_by_leverage.csv", index=False)
    pd.concat([c for c in crashes if len(c)]).to_csv(REP / "phase5_crashes.csv", index=False)
    pd.DataFrame(shap).to_csv(REP / "phase5_shap_importance.csv")
    print("\nSHAP importance (24h):\n", pd.DataFrame(shap).head(12).round(3).to_string())
    print("saved", REP)


if __name__ == "__main__":
    main()
