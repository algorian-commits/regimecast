"""Final-evaluation entry point for the trade verdict (Phase 10 runs it ONCE; Phase 12 never does).

    from regimecast.verdict.final import run_final
    run_final(final=True)

Scores the frozen settings (frozen.json) on the final test period (2025-10-01 00:00 to 2026-09-30 23:00
UTC) with the same code as the development tables (evaluate.py), the walk-forward schedule continuing
as in development (HAR-S and the FHS table refitted every 90 days on the past only; the similar-moments
pool grows with every hour whose next day is complete).

Inputs it expects from Phase 10's walk-forward run, in data/reports (same columns as the dev files):
    final_stress_pred_<coin>.parquet   index ts, gbm_cal and gbm (or p_stress)
    final_regimes_<coin>.parquet       index ts, p_calm, p_active, p_stressed, regime
(the same files the paper agent's final run reads, regimecast/agent/final.py).

Pass bars (docs/PLAN.md, fixed 27 Oct 2026 before opening):
* Trade Grade: the share hit by liquidation or the stop within 24 h rises from A to F on all 3 coins and
  F's share is at least 3 times A's (grades with under 200 trades merged with the next);
* Crowd warning: the 5% move against the crowd happens at least 1.5 times as often after a warning as in
  other hours, with the 90% block-bootstrap interval of the ratio above 1, on at least 2 of 3 coins;
* Similar moments: the 80% range contains the actual move 75-85% of the time on every coin, and the up
  chance has a Brier score no worse than always saying the dev-period up share.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from regimecast import config

FROZEN_FILE = Path(__file__).with_name("frozen.json")   # written once by scripts/run_phase12.py
POOL_FILE = str(Path(__file__).with_name("similar_pool_{s}.parquet"))
FINAL_PERIOD = (config.HOLDOUT_START, "2026-10-01")
FINAL_CRASH = ("2025-10-10", "2025-10-12")


def load_frozen(path: Path = FROZEN_FILE) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def run_final(final: bool = False, reports: Path | None = None, symbols=None) -> dict:
    if not final:
        raise ValueError("run_final reads the final test period; only Phase 10 calls it, with final=True")
    from regimecast.verdict import crowd, evaluate as E, history as H, rules

    rep = reports or (config.DATA_DIR / "reports")
    symbols = symbols or config.SYMBOLS
    fz = load_frozen()
    cuts, thin = tuple(fz["grading"]["cuts"]), fz["thin_hours"]
    cq, crule, sim = fz["crowd"]["q"], fz["crowd"]["rule"], fz["similar"]
    grade_rows, grade_chk, crowd_rows, sim_rows, crash_rows = [], [], [], [], []
    for s in symbols:
        h = H.hourly(s, final=True, reports=rep)
        warn = crowd.warning(h[[f"{c}_rank" for c in crowd.SIGNALS]], cq, crule)
        trades = E.grid_trades(s, h, warn, thin, *FINAL_PERIOD)
        tab, chk = E.grade_table(trades, cuts)
        grade_rows += [{"symbol": s, **r} for r in tab.to_dict("records")]
        grade_chk.append({"symbol": s, **chk})
        crowd_rows.append({"symbol": s, **E.crowd_scores(h, cq, crule, *FINAL_PERIOD, boot=True)})
        runs = E.similar_runs(h, fz["similar"]["scaling"][s], *FINAL_PERIOD, p0_const=fz["similar"]["up_share"][s])
        runs = runs[(runs["k"] == sim["k"]) & (runs["scaled"] == sim["scaled"]) & (runs["prior"] == sim["prior"])]
        sim_rows.append(E.similar_scores(runs, fz["similar"]["up_share"][s]).assign(symbol=s))
        a, b = (pd.Timestamp(x, tz="UTC") for x in FINAL_CRASH)          # crash window: reported, no bar
        ct = trades[(trades["ts"] >= a) & (trades["ts"] < b)]
        crash_rows += [{"symbol": s, "grade": g, "n": len(p), "share_hit": float(p["hit"].mean())}
                       for g, p in ct.assign(grade=rules.letter(ct["points"].to_numpy(), cuts)).groupby("grade")]
    grade_chk, crowd_res = pd.DataFrame(grade_chk), pd.DataFrame(crowd_rows)
    sim_res = pd.concat(sim_rows, ignore_index=True)
    verdict = {
        "trade_grade": {"pass": bool(grade_chk["passes"].all()), "per_coin": grade_chk.to_dict("records")},
        "crowd_warning": {"pass": bool(crowd_res["passes"].sum() >= 2),
                          "per_coin": crowd_res[["symbol", "n_warnings", "ratio", "ci90_lo", "ci90_hi",
                                                 "passes"]].to_dict("records")},
        "similar_moments": {"pass": bool((sim_res["coverage_ok"] & sim_res["brier_ok"]).all()),
                            "per_coin": sim_res.to_dict("records")},
    }
    pd.DataFrame(grade_rows).to_csv(rep / "final_verdict_grade.csv", index=False)
    grade_chk.to_csv(rep / "final_verdict_grade_checks.csv", index=False)
    crowd_res.to_csv(rep / "final_verdict_crowd.csv", index=False)
    sim_res.to_csv(rep / "final_verdict_similar.csv", index=False)
    pd.DataFrame(crash_rows).to_csv(rep / "final_verdict_crash.csv", index=False)
    (rep / "final_verdict_verdict.json").write_text(json.dumps({"frozen_at": fz["frozen_at"], **verdict}, indent=2,
                                                               default=float))
    return verdict
