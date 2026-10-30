"""Phase 13B: the new-coin exam, run ONCE on five coins RegimeCast has never touched.

    python scripts/new_coin_exam.py rehearse                      # the whole exam path on BTC/ETH/SOL (dev coins)
    python scripts/new_coin_exam.py produce --symbols XRPUSDT     # walk-forward on one new coin's own past
    python scripts/new_coin_exam.py score                         # every pass bar -> data/reports/new_coin_summary.json

``produce`` and ``score`` refuse to run unless the working tree is clean and HEAD is the commit tagged
``new-coin-exam``; every call is appended to data/reports/new_coin_log.json (as scripts/final_eval.py). The
pass bars are in docs/PLAN.md ("13B: Crash mode ...", fixed 31 Oct 2026 before any new-coin data was
downloaded); the decision log names exactly how each is scored.

What runs is exactly the final evaluation's code (``final_eval.produce`` and its scorers, imported, not
copied), plus the frozen crash mode (regimecast/models/crash_mode.json). Two exam rules on top:
* warm-up: each coin's first 6 months of data are dropped before anything sees them (listing-period mess);
* scored window per coin: from 2022-01-01, or the first month starting 12 months after the warm-up if
  later, to 2026-09-30 23:00 UTC.
Per-hour outputs go to data/reports/new_coin_exam/ under the final evaluation's file names, so its scorers
read them unchanged.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))                         # scripts/ (final_eval)

import argparse
import contextlib
import datetime as dt
import json
import math
import subprocess
import warnings

import numpy as np
import pandas as pd

import final_eval as fe
from regimecast import config
from regimecast.data import store
from regimecast.models import calibration as C
from regimecast.models import crash_mode as CM

warnings.filterwarnings("ignore")
REP = config.DATA_DIR / "reports"
EXAM_DIR = REP / "new_coin_exam"
REHEARSAL_DIR = REP / "new_coin_rehearsal"
LOG = REP / "new_coin_log.json"
NEW_COINS = ["XRPUSDT", "DOGEUSDT", "BNBUSDT", "ADAUSDT", "AVAXUSDT"]
DEV_COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TAG = "new-coin-exam"
WARMUP_MONTHS = 6
SCORE_FROM = pd.Timestamp("2022-01-01", tz="UTC")
SCORE_TO = pd.Timestamp("2026-10-01", tz="UTC")          # exclusive: last origin 2026-09-30 23:00
CRASH_ECE_SLACK = 0.0025                                  # 0.25 percentage points


# ------------------------------------------------------------------ guards and log (as final_eval)
def _git(*a) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True,
                          cwd=pathlib.Path(__file__).resolve().parents[1]).stdout.strip()


def guard() -> str:
    head = _git("rev-parse", "HEAD")
    tag = _git("rev-list", "-n", "1", TAG)
    dirty = [l for l in _git("status", "--porcelain").splitlines() if not l[3:].startswith("data/")]
    if not tag or tag != head:
        raise SystemExit(f"HEAD is not the commit tagged {TAG}: freeze first (git tag {TAG})")
    if dirty:
        raise SystemExit(f"working tree not clean: {dirty[:5]}")
    if not CM.FROZEN_FILE.exists():
        raise SystemExit("crash mode is not frozen (regimecast/models/crash_mode.json)")
    return head


def log(entry: dict) -> None:
    rows = json.loads(LOG.read_text()) if LOG.exists() else []
    rows.append({"time_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **entry})
    LOG.write_text(json.dumps(rows, indent=2))


# ------------------------------------------------------------------ exam rules: warm-up and window
_orig_features, _orig_targets = store.features, store.targets


def first_hour(symbol: str) -> pd.Timestamp:
    return _orig_features(symbol, "1h", columns=["ts"])["ts"].min()


def warmup_end(symbol: str, months: int = WARMUP_MONTHS) -> pd.Timestamp:
    return first_hour(symbol) + pd.DateOffset(months=months)


def window(symbol: str, months: int = WARMUP_MONTHS) -> tuple[str, str]:
    """(first origin, end exclusive) as dates, the form final_eval.win takes."""
    t = warmup_end(symbol, months) + pd.DateOffset(months=12)
    month = t.normalize().replace(day=1)
    start = month if month == t else month + pd.DateOffset(months=1)
    return max(SCORE_FROM, start).strftime("%Y-%m-%d"), SCORE_TO.strftime("%Y-%m-%d")


@contextlib.contextmanager
def warmed_up(months: int = WARMUP_MONTHS):
    """Every store.features / store.targets read (the final evaluation's code included) starts after the
    coin's warm-up."""
    def features(symbol, freq="1h", start=None, end=None, columns=None):
        df = _orig_features(symbol, freq, start, end, columns)
        return df[df["ts"] >= warmup_end(symbol, months)].reset_index(drop=True) if months else df

    def targets(symbol, start=None, end=None, columns=None):
        df = _orig_targets(symbol, start, end, columns)
        return df[df["ts"] >= warmup_end(symbol, months)].reset_index(drop=True) if months else df

    store.features, store.targets = features, targets
    try:
        yield
    finally:
        store.features, store.targets = _orig_features, _orig_targets


# ------------------------------------------------------------------ produce
def produce(symbol: str, out_dir: pathlib.Path, months: int = WARMUP_MONTHS) -> None:
    frozen = CM.load_frozen()
    s = frozen["settings"]
    with warmed_up(months):
        fe.produce(symbol, final=True, out_dir=out_dir)      # regimes, volatility, liquidation, stress
        print(f"[{symbol}] crash mode", flush=True)
        t = store.targets(symbol)
        ps = pd.read_parquet(out_dir / f"final_stress_pred_{symbol}.parquet")["gbm"]
        cm = {"train_cut": s["train_cut"], "combine": s["combine"], "floor": s["floor"]}
        for H in (24, 1):
            sigma = pd.read_parquet(out_dir / f"final_vol_{symbol}_{H}h.parquet")["har_s"]
            p = CM.evaluate(symbol, H, sigma, t, ps, [cm], final=True).rename(columns={CM.name(cm): "crash"})
            p.to_parquet(out_dir / f"crash_{symbol}_{H}h.parquet")
    print(f"[{symbol}] exam produce done", flush=True)


# ------------------------------------------------------------------ score
def score_crash(symbol, d, period):
    rows, day = [], []
    for H in (24, 1):
        p = fe.win(pd.read_parquet(d / f"crash_{symbol}_{H}h.parquet"), period, "ts")
        r = CM.score(p, ["crash"], switch_on=CM.SWITCH_ON)
        ref = fe.win(pd.read_parquet(d / f"final_liq_{symbol}_{H}h_hars.parquet",
                                     columns=["ts", "side", "leverage", "fhs"]), period, "ts")
        j = p[["ts", "side", "leverage", "fhs"]].merge(ref, on=["ts", "side", "leverage"], suffixes=("", "_ref"))
        row = {"symbol": symbol, "horizon_h": H, "window_start": str(period[0]), **r,
               "fhs_matches_liquidation_run": float((j["fhs"] - j["fhs_ref"]).abs().max()) if len(j) else None,
               "share_hours_crash_on": float((p.loc[p["side"] == "long", "p_stress"] >= CM.SWITCH_ON).mean())}
        row["gap_smaller"] = bool(r["crash_hot_long_gap"] < r["fhs_hot_long_gap"])
        row["ece_excess"] = r["crash_ece"] - r["fhs_ece"]
        row["ece_ok"] = bool(row["ece_excess"] <= CRASH_ECE_SLACK)
        rows.append(row)
        cw = fe.win(p, fe.CRASH, "ts")
        cw = cw[cw["leverage"].isin([10, 20, 50])]
        if len(cw):
            g = cw.groupby(["leverage", "side"]).agg(observed=("hit", "mean"), fhs=("fhs", "mean"),
                                                    crash=("crash", "mean"), n=("hit", "size")).reset_index()
            day.append(g.assign(symbol=symbol, horizon_h=H))
    return rows, day


def at_least(frac: float, n: int) -> int:
    return math.ceil(frac * n - 1e-9)


def score(symbols, d, months: int = WARMUP_MONTHS) -> dict:
    vol, liq, crash_day, st, rg, cr, cday, wins = [], [], [], [], [], [], [], {}
    with warmed_up(months):
        for s in symbols:
            period = window(s, months)
            wins[s] = {"first_hour": str(first_hour(s)), "warmup_end": str(warmup_end(s, months)),
                       "scored_from": str(period[0]), "scored_to_exclusive": str(period[1])}
            vol += [r | {"window_start": str(period[0])} for r in fe.score_vol(s, d, period)]
            lr, cd = fe.score_liq(s, d, period)
            liq += lr
            crash_day += cd
            st.append(fe.score_stress(s, d, period))
            rg.append(fe.score_regimes(s, d, period))
            a, b = score_crash(s, d, period)
            cr += a
            cday += b
    vol, liq, st, rg, cr = (pd.DataFrame(x) for x in (vol, liq, st, rg, cr))
    for name, df in (("vol", vol), ("liq", liq), ("stress", st), ("regimes", rg), ("crash", cr)):
        df.to_csv(d / f"new_coin_{name}.csv", index=False)
    if crash_day:
        pd.concat(crash_day).to_csv(d / "new_coin_liq_crash_day.csv", index=False)
    if cday:
        pd.concat(cday).to_csv(d / "new_coin_crash_mode_crash_day.csv", index=False)
    n, k = len(symbols) * 2, len(symbols)
    c24 = cr[cr["horizon_h"] == 24]
    need = {"vol_lower": at_least(0.8, n), "vol_sig": at_least(0.5, n), "liq_ece_lower": at_least(0.9, n),
            "liq_brier_lower": at_least(0.8, n), "liq_within": at_least(0.8, n), "coins": at_least(0.8, k)}
    bars = {
        "volatility": {"lower": int(vol["lower"].sum()), "significant": int(vol["significant"].sum()), "of": n,
                       "need_lower": need["vol_lower"], "need_significant": need["vol_sig"],
                       "pass": bool(vol["lower"].sum() >= need["vol_lower"] and vol["significant"].sum() >= need["vol_sig"])},
        "liquidation": {"ece_lower": int(liq["ece_lower"].sum()), "brier_lower": int(liq["brier_lower"].sum()),
                        "ece_within_limit": int(liq["ece_within_limit"].sum()), "of": n,
                        "need_ece_lower": need["liq_ece_lower"], "need_brier_lower": need["liq_brier_lower"],
                        "need_within": need["liq_within"],
                        "pass": bool(liq["ece_lower"].sum() >= need["liq_ece_lower"]
                                     and liq["brier_lower"].sum() >= need["liq_brier_lower"]
                                     and liq["ece_within_limit"].sum() >= need["liq_within"])},
        "stress": {"auc_ok": int(st["auc_ok"].sum()), "ap_above": int(st["ap_above"].sum()), "of": k,
                   "need": need["coins"],
                   "pass": bool(st["auc_ok"].sum() >= need["coins"] and st["ap_above"].sum() >= need["coins"])},
        "regimes": {"ordered": int(rg["ordered"].sum()), "of": k, "need": need["coins"],
                    "pass": bool(rg["ordered"].sum() >= need["coins"])},
        "crash_mode": {"gap_smaller": int(c24["gap_smaller"].sum()), "ece_ok": int(c24["ece_ok"].sum()), "of": k,
                       "need_gap_smaller": need["coins"], "need_ece_ok": k,
                       "pass": bool(c24["gap_smaller"].sum() >= need["coins"] and c24["ece_ok"].all())},
    }
    return {"windows": wins, "crash_mode": CM.load_frozen()["name"], "bars": bars}


# ------------------------------------------------------------------ rehearsal (dev coins only)
def rehearse(symbols, skip_produce: bool = False) -> None:
    d = REHEARSAL_DIR
    d.mkdir(parents=True, exist_ok=True)
    if not skip_produce:
        for s in symbols:
            produce(s, d)
    out = score(symbols, d)
    (d / "rehearsal_summary.json").write_text(json.dumps(out, indent=2, default=str))
    print(json.dumps(out, indent=2, default=str))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["rehearse", "produce", "score"])
    ap.add_argument("--symbols", nargs="+", default=None)
    ap.add_argument("--skip-produce", action="store_true", help="rehearse: reuse the files already produced")
    a = ap.parse_args()
    if a.step == "rehearse":
        syms = a.symbols or DEV_COINS
        if set(syms) & set(NEW_COINS):
            raise SystemExit("the rehearsal runs on the development coins only")
        return rehearse(syms, a.skip_produce)
    syms = a.symbols or NEW_COINS
    if not set(syms) <= set(NEW_COINS):
        raise SystemExit(f"the exam runs on the new coins only: {NEW_COINS}")
    head = guard()
    EXAM_DIR.mkdir(parents=True, exist_ok=True)
    log({"step": a.step, "symbols": syms, "commit": head, "status": "started"})
    if a.step == "produce":
        for s in syms:
            produce(s, EXAM_DIR)
        log({"step": "produce", "symbols": syms, "commit": head, "status": "done"})
        return
    if syms != NEW_COINS:
        raise SystemExit("score runs on all five new coins at once")
    out = score(syms, EXAM_DIR)
    summary = {"commit": head, **out}
    for f in EXAM_DIR.glob("new_coin_*.csv"):          # small tables next to the summary, for the repo
        (REP / f.name).write_bytes(f.read_bytes())
    (REP / "new_coin_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    log({"step": "score", "symbols": syms, "commit": head, "status": "done"})
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
