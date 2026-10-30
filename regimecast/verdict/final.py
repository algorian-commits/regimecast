"""Final-evaluation entry point for the trade verdict (Phase 10 runs it ONCE; Phase 12 never does).

    from regimecast.verdict.final import run_final
    run_final(final=True)

Scores the frozen settings (frozen.json) on the final test period (2025-10-01 00:00 to 2026-09-30 23:00 UTC) and
writes ``data/reports/final_verdict_*``. The HAR-S volatility forecast continues its walk-forward schedule (refit
every 90 days on past data only); similar moments compares each hour with every earlier hour whose next 24 h had
played out, the final test period's own past hours included, exactly as live.

Inputs it expects from Phase 10's walk-forward run on the final test period, in data/reports (same columns as the
development files, see evaluate.py):
    final_stress_pred_<coin>.parquet   index ts, gbm_cal and gbm (or p_stress)
    final_regimes_<coin>.parquet       index ts, regime

Pass bars (docs/PLAN.md, fixed 27 Oct 2026 before opening):
* Trade Grade: on the fixed grid of example trades, the share hit by liquidation or the stop within 24 h rises from
  A to F on all 3 coins, and F's share is at least 3 times A's (grades under 200 trades merged with the next).
* Crowd warning: a sudden 5% move against the crowd within 24 h at least 1.5 times as often after a warning as in
  other hours, with the 90% block-bootstrap interval of that ratio above 1, on at least 2 of 3 coins.
* Similar moments: the 80% range contains the actual 24 h move 75-85% of the time on every coin, and the up-chance's
  Brier score is no worse than always saying the dev-period up share.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from regimecast import config
from regimecast.risk import fhs_live
from regimecast.verdict import crowd as C, evaluate as E, settings, similar as S, timing as T


def run_final(final: bool = False, reports: Path | None = None, symbols=None) -> dict:
    if not final:
        raise ValueError("run_final reads the final test period; only Phase 10 calls it, with final=True")
    cfg = settings.load()
    if cfg["frozen_at"] is None:
        raise RuntimeError("frozen.json is missing: run scripts/run_phase12.py on the dev period first")
    rep = reports or E.REPORTS
    symbols = symbols or config.SYMBOLS
    fhs = fhs_live.load()
    cal = T.load_calendar()
    trades, crowd_rows, sim_rows = [], [], []
    for sym in symbols:
        P = E.load(sym, final=True, reports=rep)
        W = E.window(P, E.FINAL)
        th = cfg["crowd"].get(sym)
        trades.append(E.example_trades(P, sym, fhs[sym]["24h"], th, cal, cfg["thin_hours"].get(sym, ()),
                                       period=E.FINAL, final=True))
        crowd_rows.append({"symbol": sym, **E.crowd_row(C.outcomes(W, th))})
        sc = cfg["similar"]["scaling"][sym]
        lib = S.Library.from_panel(P, sc)
        pred = E.similar_predict(lib, W, sc, [cfg["similar"]], base=cfg["similar"]["up_share"][sym])
        sim_rows.append({"symbol": sym, **E.similar_row(pred, W, cfg["similar"]["up_share"][sym])})
    tr = pd.concat(trades, ignore_index=True)
    table = E.grade_table(tr, E.grade_trades(tr, cfg["grade"]))
    bar = E.grade_bar(table)
    crowd_t, sim_t = pd.DataFrame(crowd_rows), pd.DataFrame(sim_rows)
    verdict = {"grade_pass": bool(bar["passes"].all()), "crowd_pass": bool(crowd_t["passes"].sum() >= 2),
               "similar_pass": bool(sim_t["passes"].all())}
    verdict["pass"] = all(verdict.values())
    table.to_csv(rep / "final_verdict_grade.csv", index=False)
    bar.to_csv(rep / "final_verdict_grade_bar.csv", index=False)
    crowd_t.to_csv(rep / "final_verdict_crowd.csv", index=False)
    sim_t.to_csv(rep / "final_verdict_similar.csv", index=False)
    (rep / "final_verdict.json").write_text(json.dumps({"frozen_at": cfg["frozen_at"], **verdict}, indent=2))
    return verdict
