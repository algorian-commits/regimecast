"""Phase 10 part A: the final evaluation on the final test period, run ONCE.

    python scripts/final_eval.py rehearse --symbols SOLUSDT     # dress rehearsal on 2024-10..2025-09 (dev data only)
    python scripts/final_eval.py produce --symbols BTCUSDT      # walk-forward through the final test period, per coin
    python scripts/final_eval.py score                          # every pass bar -> data/reports/final_summary.json

``produce`` and ``score`` refuse to run unless the working tree is clean and HEAD is the commit tagged
``final-eval``; every call is appended to data/reports/final_log.json. The pass bars are in docs/PLAN.md
("Final evaluation: what counts as a pass"), fixed on 27 Oct 2026 before anything was opened.

What runs, exactly as in development (same code, settings and fold schedule; the walk-forwards simply continue
through the final test period, refitting every 90 days on past data only):
* regimes (M4): quarterly HMM refits                       -> final_regimes_<coin>.parquet
* volatility (M7): HAR-RV, HAR-S, LightGBM, combination     -> final_vol_<coin>_<h>h.parquet
* liquidation (M7): FHS and random walk on HAR-S and combo  -> final_liq_<coin>_<h>h.parquet (+ _hars)
* stress (M10): classifier and the three simple rules      -> final_stress_pred_<coin>.parquet
* paper agent (Phase 9) and trade verdict (Phase 12): their own frozen final entry points.
Origins scored: 2025-10-01 00:00 to 2026-09-30 23:00 UTC.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import datetime as dt
import json
import subprocess
import warnings

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.models import calibration as C
from regimecast.models import liquidation_risk as LR, regimes as RG, stress as S, volatility as V
from regimecast.vol import evaluate as ev

warnings.filterwarnings("ignore")
REP = config.DATA_DIR / "reports"
FINAL_WINDOW = ("2025-10-01", "2026-10-01")
REHEARSAL_WINDOW = ("2024-10-01", "2025-10-01")          # the last dev year, as a stand-in
DEV_WINDOW = ("2022-01-01", "2025-10-01")                # for the reproducibility check against Phase 5 / 6
CRASH = ("2025-10-10", "2025-10-12")
LOG = REP / "final_log.json"
STRESS_RULES = ["persist", "vol_rule", "regime_rule"]


# ------------------------------------------------------------------ guards and log
def _git(*a) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True,
                          cwd=pathlib.Path(__file__).resolve().parents[1]).stdout.strip()


def guard() -> str:
    head = _git("rev-parse", "HEAD")
    tag = _git("rev-list", "-n", "1", "final-eval")
    dirty = [l for l in _git("status", "--porcelain").splitlines() if not l[3:].startswith("data/")]
    if not tag or tag != head:
        raise SystemExit("HEAD is not the commit tagged final-eval: freeze first (git tag final-eval)")
    if dirty:
        raise SystemExit(f"working tree not clean: {dirty[:5]}")
    return head


def log(entry: dict) -> None:
    rows = json.loads(LOG.read_text()) if LOG.exists() else []
    rows.append({"time_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **entry})
    LOG.write_text(json.dumps(rows, indent=2))


def win(df, period, col=None):
    a, b = (pd.Timestamp(x, tz="UTC") for x in period)
    idx = df[col] if col else df.index
    return df[(idx >= a) & (idx < b)]


# ------------------------------------------------------------------ produce (walk-forward per coin)
def produce(symbol: str, final: bool, out_dir: pathlib.Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    h, t = store.features(symbol, "1h"), store.targets(symbol)
    f = config.parquet_dir() / "dvol" / symbol / "all.parquet"
    dvol = pd.read_parquet(f) if f.exists() else None
    print(f"[{symbol}] regimes", flush=True)
    reg = RG.fit_walk_forward(RG.regime_inputs(h), RG.FEATURE_SETS["base"], refit_every_hours=24 * 90, final=final)
    reg.to_parquet(out_dir / f"final_regimes_{symbol}.parquet")
    for H in (24, 1):
        print(f"[{symbol}] volatility {H}h", flush=True)
        X, y = V.dataset(h, t, H, dvol)
        preds = V.compare(X, y, H, test_start="2021-01-01", final=final)
        pd.DataFrame({"y": y, **preds}).dropna(how="all", subset=list(preds)).to_parquet(
            out_dir / f"final_vol_{symbol}_{H}h.parquet")
        print(f"[{symbol}] liquidation {H}h", flush=True)
        for name, src in (("", "combo"), ("_hars", "har_s")):
            p = LR.evaluate(symbol, H, X, preds[src], t, features=V.FEATURES, with_qgbm=False, final=final)
            p.to_parquet(out_dir / f"final_liq_{symbol}_{H}h{name}.parquet")
    print(f"[{symbol}] stress", flush=True)
    X, y = S.dataset(h, t, reg, dvol)
    out = S.compare(X, y, final=final)
    out.index.name = "ts"
    out.to_parquet(out_dir / f"final_stress_pred_{symbol}.parquet")
    print(f"[{symbol}] done", flush=True)


# ------------------------------------------------------------------ score
def score_vol(symbol, d, period):
    rows = []
    for H in (1, 24):
        v = pd.read_parquet(d / f"final_vol_{symbol}_{H}h.parquet")
        w = win(v, period)
        tab = ev.summary_table(w["y"], {m: w[m] for m in ("har", "har_s", "gbm", "combo")}, "har", H)
        c = tab.loc["combo"]
        dev = win(v, DEV_WINDOW)
        dev_tab = ev.summary_table(dev["y"], {m: dev[m] for m in ("har", "combo")}, "har", H)
        rows.append({"symbol": symbol, "horizon_h": H, "n": int(c["n"]),
                     "combo_vs_har_pct": float(c["qlike_vs_base_pct"]), "dm_p": float(c["dm_p"]),
                     "har_s_vs_har_pct": float(tab.loc["har_s", "qlike_vs_base_pct"]),
                     "gbm_vs_har_pct": float(tab.loc["gbm", "qlike_vs_base_pct"]),
                     "lower": bool(c["qlike_vs_base_pct"] < 0),
                     "significant": bool(c["qlike_vs_base_pct"] < 0 and c["dm_p"] < 0.05),
                     "dev_combo_vs_har_pct": float(dev_tab.loc["combo", "qlike_vs_base_pct"])})
    return rows


def score_liq(symbol, d, period):
    rows, crash = [], []
    for H in (1, 24):
        ph = pd.read_parquet(d / f"final_liq_{symbol}_{H}h_hars.parquet")
        pc = pd.read_parquet(d / f"final_liq_{symbol}_{H}h.parquet")
        r = {"symbol": symbol, "horizon_h": H}
        for tag, p, ms in (("hars", ph, ("fhs", "gauss")), ("combo", pc, ("fhs", "gauss"))):
            w = win(p, period, "ts").dropna(subset=[*ms, "hit"])
            r[f"n_{tag}"] = len(w)
            r[f"observed_{tag}"] = float(w["hit"].mean())
            for m in ms:
                r[f"{m}_{tag}_ece"] = C.ece(w[m], w["hit"])
                r[f"{m}_{tag}_brier"] = C.brier(w[m], w["hit"])
                r[f"{m}_{tag}_mean"] = float(w[m].mean())
            if tag == "hars":
                lo, hi = C.block_bootstrap(w.set_index("ts")[["fhs", "hit"]], lambda x: C.ece(x["fhs"], x["hit"]), n=100)
                r["fhs_hars_ece_lo"], r["fhs_hars_ece_hi"] = lo, hi
                cw = win(w, CRASH, "ts")
                cw = cw[cw["leverage"].isin([10, 20, 50])]
                if len(cw):
                    g = cw.groupby(["leverage", "side"]).agg(observed=("hit", "mean"), fhs=("fhs", "mean"),
                                                            gauss=("gauss", "mean"), n=("hit", "size")).reset_index()
                    crash.append(g.assign(symbol=symbol, horizon_h=H))
        limit = 0.005 if H == 1 else 0.015
        r["ece_lower"] = r["fhs_hars_ece"] < r["gauss_hars_ece"]
        r["brier_lower"] = r["fhs_hars_brier"] < r["gauss_hars_brier"]
        r["ece_within_limit"] = r["fhs_hars_ece"] <= limit
        rows.append(r)
    return rows, crash


def score_stress(symbol, d, period):
    out = win(pd.read_parquet(d / f"final_stress_pred_{symbol}.parquet"), period)
    ok = out.dropna(subset=["gbm", "gbm_cal", *STRESS_RULES, "y"])
    r = {"symbol": symbol, "n": len(ok), "base_rate": float(ok["y"].mean())}
    for m in ("gbm", "gbm_cal", *STRESS_RULES):
        r[f"auc_{m}"] = S.auc(ok[m], ok["y"])
        r[f"ap_{m}"] = S.average_precision(ok[m], ok["y"])
    r["best_rule_auc"] = max(r[f"auc_{m}"] for m in STRESS_RULES)
    r["best_rule_ap"] = max(r[f"ap_{m}"] for m in STRESS_RULES)
    r["auc_ok"] = r["auc_gbm"] >= 0.75 and r["auc_gbm"] > r["best_rule_auc"]
    r["ap_above"] = r["ap_gbm"] > r["best_rule_ap"]
    for th in (0.3, 0.5):
        r[f"alarms_{int(th * 100)}"] = S.alarms(ok["gbm_cal"], ok["y"], th)
    return r


def score_regimes(symbol, d, period):
    reg = win(pd.read_parquet(d / f"final_regimes_{symbol}.parquet"), period)
    s = RG.regime_summary(reg, store.features(symbol, "1h"))
    vol = s["next_24h_vol_annualised"].reindex(RG.NAMES)
    return {"symbol": symbol, **{f"share_{k}": float(s["share_of_hours"].get(k, np.nan)) for k in RG.NAMES},
            **{f"vol_{k}": float(vol[k]) for k in RG.NAMES},
            "ordered": bool(vol.notna().all() and vol.is_monotonic_increasing)}


def reproducibility(symbol, d):
    """The walk-forwards rerun the dev period too: it must match what Phases 4-6 saved."""
    r = {"symbol": symbol}
    a = pd.read_parquet(d / f"final_regimes_{symbol}.parquet")["p_stressed"]
    b = pd.read_parquet(REP / f"regimes_base_{symbol}.parquet")
    b = b.loc[~b["frozen"], "p_stressed"]
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    r["regimes_max_abs_diff"] = float((j.iloc[:, 0] - j.iloc[:, 1]).abs().max())
    a = pd.read_parquet(d / f"final_stress_pred_{symbol}.parquet")["gbm"]
    b = pd.read_parquet(REP / f"phase6_pred_{symbol}.parquet")["gbm"]
    j = pd.concat([a, b], axis=1, join="inner").dropna()
    r["stress_max_abs_diff"] = float((j.iloc[:, 0] - j.iloc[:, 1]).abs().max())
    return r


def score(symbols, d, period, final: bool) -> dict:
    vol, liq, crash, st, rg, rep = [], [], [], [], [], []
    for s in symbols:
        vol += score_vol(s, d, period)
        lr, cr = score_liq(s, d, period)
        liq += lr
        crash += cr
        st.append(score_stress(s, d, period))
        rg.append(score_regimes(s, d, period))
        rep.append(reproducibility(s, d))
    vol, liq, st, rg = pd.DataFrame(vol), pd.DataFrame(liq), pd.DataFrame(st), pd.DataFrame(rg)
    for name, df in (("vol", vol), ("liq", liq), ("stress", st), ("regimes", rg), ("reproducibility", pd.DataFrame(rep))):
        df.to_csv(d / f"final_{name}.csv", index=False)
    if crash:
        pd.concat(crash).to_csv(d / "final_liq_crash.csv", index=False)
    n = len(symbols) * 2
    bars = {
        "volatility": {"lower": int(vol["lower"].sum()), "significant": int(vol["significant"].sum()), "of": n,
                       "pass": bool(vol["lower"].sum() >= n - 1 and vol["significant"].sum() >= n // 2)},
        "liquidation": {"ece_lower": int(liq["ece_lower"].sum()), "brier_lower": int(liq["brier_lower"].sum()),
                        "ece_within_limit": int(liq["ece_within_limit"].sum()), "of": n,
                        "pass": bool(liq["ece_lower"].all() and liq["brier_lower"].sum() >= n - 1
                                     and liq["ece_within_limit"].sum() >= n - 1)},
        "stress": {"auc_ok": int(st["auc_ok"].sum()), "ap_above": int(st["ap_above"].sum()), "of": len(symbols),
                   "pass": bool(st["auc_ok"].all() and st["ap_above"].sum() >= min(2, len(symbols)))},
        "regimes": {"ordered": int(rg["ordered"].sum()), "of": len(symbols), "pass": bool(rg["ordered"].all())},
    }
    if final:
        from regimecast.agent.final import run_final as agent_final
        from regimecast.verdict.final import run_final as verdict_final
        bars["paper_agent"] = agent_final(final=True, reports=d, symbols=symbols)
        bars["trade_verdict"] = verdict_final(final=True, reports=d, symbols=symbols)
        bars["app"] = {"pass": None, "note": "one-time click-through by Saeed on the live URL, plus the latency check"}
        bars["trade_parser"] = {"pass": None, "note": "not run: waits for the Anthropic API key"}
    return bars


# ------------------------------------------------------------------ rehearsal (dev data only)
def rehearse(symbols, skip_produce: bool = False) -> None:
    import shutil
    d = REP / "rehearsal"
    for s in symbols:
        if not skip_produce:
            produce(s, final=False, out_dir=d)
        for f in (f"phase5_liq_{s}_24h.parquet", f"phase6_pred_{s}.parquet", f"regimes_base_{s}.parquet"):
            shutil.copy(REP / f, d / f)     # the readers take the dev files from the same folder, as in the real run
    bars = score(symbols, d, REHEARSAL_WINDOW, final=False)
    # the agent's and the verdict's readers must accept these files (loading only, nothing scored)
    from regimecast.agent import signals as AS
    from regimecast.verdict import evaluate as VE
    for s in symbols:
        a = AS.load(s, final=True, reports=d)
        p, r = VE._signals(s, d, final=True)
        print(f"[{s}] agent signals {a.shape} {list(a.columns)}; verdict signals {len(p)} / {len(r)}")
    (d / "rehearsal_summary.json").write_text(json.dumps(bars, indent=2, default=str))
    print(json.dumps(bars, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["rehearse", "produce", "score"])
    ap.add_argument("--symbols", nargs="+", default=config.SYMBOLS)
    ap.add_argument("--skip-produce", action="store_true", help="rehearse: reuse the files already produced")
    a = ap.parse_args()
    if a.step == "rehearse":
        return rehearse(a.symbols, a.skip_produce)
    head = guard()
    log({"step": a.step, "symbols": a.symbols, "commit": head, "status": "started"})
    if a.step == "produce":
        for s in a.symbols:
            produce(s, final=True, out_dir=REP)
        log({"step": "produce", "symbols": a.symbols, "commit": head, "status": "done"})
        return
    bars = score(a.symbols, REP, FINAL_WINDOW, final=True)
    summary = {"commit": head, "window": FINAL_WINDOW, "bars": bars}
    (REP / "final_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log({"step": "score", "symbols": a.symbols, "commit": head, "status": "done"})
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
