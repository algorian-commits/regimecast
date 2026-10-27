"""Phase 12 on the dev period: choose the settings, check them, freeze them, write the tables.

Called by scripts/run_phase12.py with the real panels (evaluate.load), or by the tests and ``--dry-run`` with
synthetic ones. The final test period stays closed: every panel ends before 2025-10-01.

1. what-if: the 90th / 99th percentile 24 h moves per coin and regime, 2022-01 to 2025-09    phase12_whatif.csv
2. thin hours: per coin, the four hours of the day with the lowest median volume (choose)  phase12_thin_hours.csv
3. crowd warning: candidates on the choose period, choice by evaluate.choose_crowd, thresholds fixed on the
   choose period; ratio and 90% interval on choose / check / dev                    phase12_crowd_grid.csv, _crowd.csv
4. similar moments: candidates every 6 hours on the choose period, choice by evaluate.choose_similar; coverage and
   Brier every ``every_h`` hours on choose / check / dev; the library of past hours for the app
                                                 phase12_similar_grid.csv, _similar.csv, verdict/similar/<coin>.npz
5. Trade Grade: candidates on the example-trade grid of the choose period, choice by evaluate.choose_grade;
   hit share by grade on choose / check / dev                     phase12_grade_grid.csv, _grade.csv, _grade_bar.csv
6. freeze: frozen.json, never overwritten with different settings unless ``refreeze``.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from regimecast.verdict import crowd as C, evaluate as E, similar as S, whatif as WI

PERIODS = {"choose 2022-01..2024-09": E.CHOOSE, "check 2024-10..2025-09": E.CHECK, "dev 2022-01..2025-09": E.DEV}
RULES = {
    "grade": "among settings whose hit share rises A to F on all 3 coins (choose period), the highest smallest-coin "
             "F/A ratio; ties: fewer market factors, then the middle knock-out bands",
    "crowd": "among candidates with at least 50 warning hours on every coin (choose period), the highest "
             "second-best coin's ratio; ties: more warnings",
    "similar": "among settings with a Brier score no worse than the base rate's on every coin (choose period, every "
               "6 hours), the smallest worst-coin |coverage - 80%|; ties: larger k",
}


def thin_hours(P: pd.DataFrame, n: int = 4) -> list[int]:
    v = E.window(P, E.CHOOSE)["volume_usd"]
    return sorted(int(h) for h in v.groupby(v.index.hour).median().nsmallest(n).index)


def _log(msg: str, t0: float) -> None:
    print(f"[{time.time() - t0:5.0f}s] {msg}", flush=True)


def run(panels: dict[str, pd.DataFrame], fhs: dict, calendar: pd.DataFrame, out: Path, frozen_path: Path,
        similar_dir: Path, refreeze: bool = False, every_h: int = 1) -> dict:
    t0 = time.time()
    out.mkdir(parents=True, exist_ok=True)
    syms = list(panels)

    # 1-2. what-if table and thin hours
    whatif = {s: WI.move_table(E.window(P, E.DEV)) for s, P in panels.items()}
    pd.DataFrame([{"symbol": s, "regime": r, **v} for s, t in whatif.items() for r, v in t.items()]).to_csv(
        out / "phase12_whatif.csv", index=False)
    thin = {s: thin_hours(P) for s, P in panels.items()}
    pd.DataFrame([{"symbol": s, "thin_hours_utc": " ".join(map(str, h))} for s, h in thin.items()]).to_csv(
        out / "phase12_thin_hours.csv", index=False)
    _log("what-if and thin hours", t0)

    # 3. crowd warning
    choose = {s: E.window(P, E.CHOOSE) for s, P in panels.items()}
    pick, grid = E.choose_crowd(choose)
    grid.to_csv(out / "phase12_crowd_grid.csv", index=False)
    crowd_th, rows = {}, []
    for s, P in panels.items():
        th = C.fit(choose[s], pick["level"], pick["oi_level"])
        th["odds"] = C.odds(C.outcomes(E.window(P, E.DEV), th))
        crowd_th[s] = th
        for name, per in PERIODS.items():
            rows.append({"period": name, "symbol": s, **E.crowd_row(C.outcomes(E.window(P, per), th))})
    crowd_t = pd.DataFrame(rows)
    crowd_t.to_csv(out / "phase12_crowd.csv", index=False)
    _log(f"crowd warning: {pick}", t0)

    # 4. similar moments
    sets = E.similar_settings()
    scaling, libs, up_share, scores = {}, {}, {}, []
    for s, P in panels.items():
        scaling[s] = S.fit_scaling(S.features(choose[s]))
        libs[s] = S.Library.from_panel(P, scaling[s])
        up_share[s] = float((E.window(P, E.DEV)["fwd_24h"].dropna() > 0).mean())
        ch_up = float((choose[s]["fwd_24h"].dropna() > 0).mean())
        pred = E.similar_predict(libs[s], choose[s], scaling[s], sets, every_h=6)
        for j, st in enumerate(sets):
            scores.append({"symbol": s, "setting": j, **st, **S.score(pred[pred["setting"] == j], P["fwd_24h"], ch_up)})
    sim_grid = pd.DataFrame(scores)
    sim_grid.to_csv(out / "phase12_similar_grid.csv", index=False)
    sim = E.choose_similar(sim_grid)
    rows = []
    for s, P in panels.items():
        pred = E.similar_predict(libs[s], E.window(P, E.DEV), scaling[s], [sim], every_h)   # dev = choose + check
        for name, per in PERIODS.items():
            W = E.window(P, per)
            rows.append({"period": name, "symbol": s,
                         **E.similar_row(pred[pred["ts"].isin(W.index)], W, up_share[s])})
        libs[s].save(similar_dir / f"{s}.npz")
    sim_t = pd.DataFrame(rows)
    sim_t.to_csv(out / "phase12_similar.csv", index=False)
    _log(f"similar moments: {sim}", t0)

    # 5. Trade Grade
    trades = {name: pd.concat([E.example_trades(P, s, fhs[s]["24h"], crowd_th[s], calendar, thin[s], period=per)
                               for s, P in panels.items()], ignore_index=True) for name, per in PERIODS.items()}
    cfg, ggrid = E.choose_grade(trades[next(iter(PERIODS))])          # the choose period
    ggrid.to_csv(out / "phase12_grade_grid.csv", index=False)
    tabs, bars = [], []
    for name, tr in trades.items():
        tab = E.grade_table(tr, E.grade_trades(tr, cfg))
        tabs.append(tab.assign(period=name))
        bars.append(E.grade_bar(tab).assign(period=name))
    grade_t, bar_t = pd.concat(tabs), pd.concat(bars)
    grade_t.to_csv(out / "phase12_grade.csv", index=False)
    bar_t.to_csv(out / "phase12_grade_bar.csv", index=False)
    _log(f"Trade Grade: {cfg}", t0)

    # 6. freeze
    frozen = {
        "frozen_at": datetime.now(timezone.utc).date().isoformat(),
        "chosen_on": "2022-01-01 to 2024-09-30", "checked_on": "2024-10-01 to 2025-09-30", "rules": RULES,
        "grade": cfg,
        "grade_grid": {"leverages": list(E.LEVERAGES), "stop_moves": list(E.STOP_MOVES), "every_hours": E.GRID_EVERY_H,
                       "margin_usdt": E.GRID_MARGIN, "risk_limit": E.GRID_RISK_LIMIT, "horizon_hours": E.HORIZON,
                       "min_trades_per_grade": E.MIN_PER_GRADE},
        "crowd": crowd_th,
        "similar": {**sim, "scaling": scaling, "up_share": up_share},
        "whatif": whatif,
        "thin_hours": thin,
        "settings_tried": {"grade": int(len(ggrid)), "crowd": int(len(grid)), "similar": len(sets)},
        "check": {"grade": bar_t[bar_t["period"].str.startswith("check")].drop(columns="period").to_dict("records"),
                  "crowd": crowd_t[crowd_t["period"].str.startswith("check")].drop(columns="period").to_dict("records"),
                  "similar": sim_t[sim_t["period"].str.startswith("check")].drop(columns="period").to_dict("records")},
    }
    if frozen_path.exists() and not refreeze:
        old = json.loads(frozen_path.read_text(encoding="utf-8"))
        same = (old.get("grade") == cfg and old.get("crowd", {}).keys() == crowd_th.keys()
                and all(old["crowd"][s]["level"] == crowd_th[s]["level"] and old["crowd"][s]["oi_level"] == crowd_th[s]["oi_level"]
                        for s in syms)
                and {k: old.get("similar", {}).get(k) for k in sim} == sim)
        if not same:
            raise SystemExit(f"{frozen_path} holds different settings; not overwritten (use --refreeze after logging why)")
        frozen["frozen_at"] = old["frozen_at"]
    frozen_path.write_text(json.dumps(_clean(frozen), indent=2) + "\n", encoding="utf-8")
    _log(f"frozen -> {frozen_path}", t0)
    return frozen


def _clean(x):
    """JSON-safe: numpy numbers to Python, NaN to null."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.bool_, bool)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else round(float(x), 8)
    return x
