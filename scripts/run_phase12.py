"""Phase 12 results: the trade verdict's settings chosen, checked and frozen on the development period.

    python scripts/run_phase12.py                        # about 5 minutes
    python -m http.server 8010 -d data/reports           # then open http://localhost:8010/phase12.html

Development period only (the final test period from 2025-10-01 stays closed, see history.py). Every
input is walk-forward and out of sample: HAR-S volatility and FHS refitted every 90 days on the past,
Phase 6's stress probability, Phase 4's forward-filtered regimes. As in Phases 5, 6 and 9:

1. every candidate is scored on 2022-01 to 2024-09 (the grid period) and logged;
2. the choice follows a rule written here before the check period is looked at:
   * grade cut points: among the candidates that meet the final bar (share knocked out rises from A to
     F, F at least 3x A) on all three coins with every letter used by at least 200 trades, the one with
     the largest smallest F/A ratio;
   * crowd warning (q, rule): among candidates with at least 100 warning hours on every coin, the one
     that meets the bar (ratio >= 1.5, 90% block-bootstrap interval above 1) on the most coins; ties:
     the highest second-best coin ratio (the bar needs 2 of 3 coins), then more warnings;
   * similar moments (k, scaled, prior): among candidates whose 80% range covers 75-85% and whose
     Brier score is no worse than the dev-period up share on every coin (if none: those that cover
     75-85%; if none: all), the coverage closest to 80%, then the lowest Brier;
   (the crowd and similar rules were revised once after the first run, before freezing; decision log);
   * thin hours: the 4 UTC hours with the lowest median volume (average over the coins);
3. the chosen settings are scored once on 2024-10 to 2025-09 (the check) and on the whole development
   period, and frozen in regimecast/verdict/frozen.json with the date. An existing frozen.json is never
   overwritten with different settings (``--refreeze`` only after a logged decision).

The what-if strip's crash-day line reads one day of the final test period, 10 Oct 2025, as a fixed
descriptive scenario (the size of that day's fall per coin); nothing is chosen or fitted on it (decision
log, 30 Oct 2026).

Outputs: data/reports/phase12_*.csv, phase12_summary.json, phase12.html; the similar-moments pools
shipped with the code (regimecast/verdict/similar_pool_<coin>.parquet).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import json
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.verdict import crowd, evaluate as E, history as H, rules, similar
from regimecast.verdict.final import FROZEN_FILE, POOL_FILE

REP = config.DATA_DIR / "reports"
GRID_PERIOD = ("2022-01-01", "2024-10-01")
CHECK_PERIOD = ("2024-10-01", "2025-10-01")
DEV_PERIOD = ("2022-01-01", "2025-10-01")
PERIODS = {"grid 2022-01..2024-09": GRID_PERIOD, "check 2024-10..2025-09": CHECK_PERIOD,
           "dev 2022-01..2025-09": DEV_PERIOD}
CRASH_DAY = ("2025-10-10", "2025-10-11")
MIN_CROWD_WARNINGS = 100


def window(df: pd.DataFrame, period, col: str | None = None) -> pd.DataFrame:
    a, b = (pd.Timestamp(x, tz="UTC") for x in period)
    ix = df.index if col is None else pd.DatetimeIndex(df[col])
    return df[(ix >= a) & (ix < b)]


def thin_hours(hs: dict) -> list[int]:
    meds = []
    for h in hs.values():
        v = window(h, GRID_PERIOD)["volume_usd"]
        m = v.groupby((v.index - pd.Timedelta(hours=1)).hour).median()
        meds.append(m / m.median())
    return sorted(int(x) for x in pd.concat(meds, axis=1).mean(axis=1).nsmallest(4).index)


def whatif_quantiles(h: pd.DataFrame) -> dict:
    """Worst move against a long (fall) and a short (rise) within 24 h, by regime, as price fractions."""
    out = {}
    d = h.dropna(subset=["mae_long", "mae_short", "regime"])
    for rg, part in list(d.groupby("regime")) + [("all", d)]:
        fall = 1 - np.exp(-part["mae_long"])
        rise = np.exp(part["mae_short"]) - 1
        out[rg] = {side: {f"p{q}": round(float(x.quantile(q / 100)), 5) for q in (50, 90, 99)} | {"n": int(len(x))}
                   for side, x in (("long", fall), ("short", rise))}
    return out


def crash_move(symbol: str) -> float:
    """10 Oct 2025: the largest fall of the mark price within 24 h from any hour of that day (a fixed
    descriptive scenario; see the module docstring)."""
    t = store.targets(symbol, start=CRASH_DAY[0], end=CRASH_DAY[1], columns=["ts", "mae_long_24h"])
    return round(-float(1 - np.exp(-t["mae_long_24h"].max())), 4)


def choose_cuts(grid: pd.DataFrame) -> tuple:
    g = grid[grid["period"] == "grid 2022-01..2024-09"]
    agg = g.groupby("cuts").agg(ok=("passes", "all"), letters=("all_letters_200", "all"), worst=("F_over_A", "min"))
    pool = agg[agg["ok"] & agg["letters"]]
    pool = pool if len(pool) else agg[agg["ok"]] if agg["ok"].any() else agg
    return tuple(json.loads(pool["worst"].idxmax()))


def choose_crowd(grid: pd.DataFrame) -> tuple:
    agg = grid.groupby(["q", "rule"]).agg(n_min=("n_warnings", "min"), n=("n_warnings", "sum"),
                                         coins=("passes", "sum"), second=("ratio", lambda r: sorted(r.fillna(0))[-2]))
    pool = agg[agg["n_min"] >= MIN_CROWD_WARNINGS]
    pool = pool if len(pool) else agg
    best = pool.sort_values(["coins", "second", "n"], ascending=False).index[0]
    return float(best[0]), str(best[1])


def choose_similar(sc: pd.DataFrame) -> dict:
    sc = sc.assign(skill=sc["brier"] - sc["brier_base"], miss=(sc["coverage"] - 0.8).abs())
    agg = sc.groupby(["k", "scaled", "prior"]).agg(cov_ok=("coverage_ok", "all"), brier_ok=("brier_ok", "all"),
                                                   skill=("skill", "mean"), miss=("miss", "mean"))
    both = agg[agg["cov_ok"] & agg["brier_ok"]]
    pool = both if len(both) else agg[agg["cov_ok"]] if agg["cov_ok"].any() else agg
    best = pool.sort_values(["miss", "skill"]).index[0]
    return {"k": int(best[0]), "scaled": bool(best[1]), "prior": float(best[2])}


def write_page(summary: dict, tables: dict[str, pd.DataFrame]) -> None:
    def t(df):
        return df.to_html(border=0, index=False, classes="t", na_rep="-", float_format=lambda v: f"{v:,.3f}")

    f = summary["frozen"]
    body = "".join(f"<h2>{name}</h2>{t(df)}" for name, df in tables.items())
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>RegimeCast Phase 12</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}}
.t{{border-collapse:collapse;font-size:13px}}.t td,.t th{{padding:4px 10px;border-bottom:1px solid #ddd;text-align:right}}
p.note{{color:#555}}</style></head><body>
<h1>Phase 12: trade verdict for beginners</h1>
<p class="note">Development period only (2022-01 to 2025-09); the final test period is closed. Every input is
walk-forward. Settings chosen on 2022-01 to 2024-09, checked on 2024-10 to 2025-09.</p>
<p><b>Frozen</b> ({f['frozen_at']}): grade cut points {f['grading']['cuts']}; crowd warning q = {f['crowd']['q']},
rule {f['crowd']['rule']}; similar moments k = {f['similar']['k']}, scaled = {f['similar']['scaled']},
prior = {f['similar']['prior']}; thin hours (UTC) {f['thin_hours']}.</p>
{body}</body></html>"""
    (REP / "phase12.html").write_text(page, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refreeze", action="store_true", help="overwrite frozen.json (log the reason first)")
    a = ap.parse_args()
    REP.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    hs = {s: H.hourly(s) for s in config.SYMBOLS}
    tables = {s: H.fhs_tables(h) for s, h in hs.items()}
    print(f"history built in {time.time() - t0:.0f}s", flush=True)
    thin = thin_hours(hs)

    # ---- crowd warning
    rows = []
    for s, h in hs.items():
        for q, rule in crowd.CANDIDATES:
            rows.append({"symbol": s, "q": q, "rule": rule, **E.crowd_scores(h, q, rule, *GRID_PERIOD, boot=True)})
    crowd_grid = pd.DataFrame(rows)
    crowd_grid.to_csv(REP / "phase12_crowd_grid.csv", index=False)
    cq, crule = choose_crowd(crowd_grid)
    rows, odds, warns = [], {}, {}
    for s, h in hs.items():
        warns[s] = crowd.warning(h[[f"{c}_rank" for c in crowd.SIGNALS]], cq, crule)
        for pname, period in PERIODS.items():
            rows.append({"symbol": s, "period": pname, **E.crowd_scores(h, cq, crule, *period, boot=True)})
        drop, rise = crowd.events(h["mae_long"], h["mae_short"])
        m = window(h, DEV_PERIOD).index
        odds[s] = crowd.odds(warns[s].reindex(m), drop.reindex(m), rise.reindex(m))
    crowd_res = pd.DataFrame(rows)
    crowd_res.to_csv(REP / "phase12_crowd.csv", index=False)
    print(f"crowd: q={cq} rule={crule} ({time.time() - t0:.0f}s)", flush=True)

    # ---- trade grade
    trades = {s: E.grid_trades(s, h, warns[s], thin, *DEV_PERIOD, tables=tables[s]) for s, h in hs.items()}
    rows = []
    for cuts in rules.CUT_CANDIDATES:
        for s, tr in trades.items():
            for pname, period in PERIODS.items():
                _, chk = E.grade_table(window(tr, period, "ts"), cuts)
                rows.append({"cuts": json.dumps(list(cuts)), "symbol": s, "period": pname, **chk})
    grade_grid = pd.DataFrame(rows)
    grade_grid.to_csv(REP / "phase12_grade_grid.csv", index=False)
    cuts = choose_cuts(grade_grid)
    rows, checks, by_points = [], [], []
    for s, tr in trades.items():
        for pname, period in PERIODS.items():
            tab, chk = E.grade_table(window(tr, period, "ts"), cuts)
            rows += [{"symbol": s, "period": pname, **r} for r in tab.to_dict("records")]
            checks.append({"symbol": s, "period": pname, **chk})
        w = window(tr, DEV_PERIOD, "ts")
        by_points.append(w.groupby("points")["hit"].agg(n="size", share_hit="mean").reset_index().assign(symbol=s))
    grade_res, grade_chk = pd.DataFrame(rows), pd.DataFrame(checks)
    grade_res.to_csv(REP / "phase12_grade.csv", index=False)
    grade_chk.to_csv(REP / "phase12_grade_checks.csv", index=False)
    pd.concat(by_points).to_csv(REP / "phase12_grade_points.csv", index=False)
    lev_mix = pd.concat({s: window(tr, DEV_PERIOD, "ts").assign(grade=lambda d: rules.letter(d["points"].to_numpy(), cuts))
                        .groupby(["leverage", "grade"]).size().unstack(fill_value=0) for s, tr in trades.items()},
                        names=["symbol"]).reset_index()
    lev_mix.to_csv(REP / "phase12_grade_by_leverage.csv", index=False)
    print(f"grade: cuts={cuts} ({time.time() - t0:.0f}s)", flush=True)

    # ---- similar moments
    scaling, up_share, runs = {}, {}, {}
    for s, h in hs.items():
        X = similar.describe(h)
        scaling[s] = similar.scaling(X[(X.index < pd.Timestamp(GRID_PERIOD[1], tz="UTC")) & X.notna().all(axis=1)])
        up_share[s] = float((window(h, DEV_PERIOD)["r24"].dropna() > 0).mean())
        runs[s] = E.similar_runs(h, scaling[s], *DEV_PERIOD, p0_const=up_share[s])
    rows = []
    for s, r in runs.items():
        for pname, period in PERIODS.items():
            rows.append(E.similar_scores(window(r, period, "ts"), up_share[s]).assign(symbol=s, period=pname))
    sim_all = pd.concat(rows, ignore_index=True)
    sim_all.to_csv(REP / "phase12_similar_grid.csv", index=False)
    sset = choose_similar(sim_all[sim_all["period"] == "grid 2022-01..2024-09"])
    sim_res = sim_all[(sim_all["k"] == sset["k"]) & (sim_all["scaled"] == sset["scaled"])
                      & (sim_all["prior"] == sset["prior"])]
    sim_res.to_csv(REP / "phase12_similar.csv", index=False)
    print(f"similar: {sset} ({time.time() - t0:.0f}s)", flush=True)

    # ---- what-if strip
    wq = {s: whatif_quantiles(h) for s, h in hs.items()}
    crash = {s: crash_move(s) for s in config.SYMBOLS}
    pd.DataFrame([{"symbol": s, "regime": rg, "side": side, **v} for s, d in wq.items() for rg, sides in d.items()
                  for side, v in sides.items()]).to_csv(REP / "phase12_whatif.csv", index=False)

    # ---- similar-moments pools shipped with the code (dev hours with a complete next day)
    for s, h in hs.items():
        X = similar.describe(h)
        pool = X.assign(sigma24=h["sigma24"], r24=h["r24"]).dropna().astype("float32")
        pool.reset_index().to_parquet(POOL_FILE.format(s=s), index=False, compression="zstd")

    # ---- freeze
    frozen = {
        "frozen_at": datetime.now(timezone.utc).date().isoformat(),
        "chosen_on": f"{GRID_PERIOD[0]} to 2024-09-30", "checked_on": f"{CHECK_PERIOD[0]} to 2025-09-30",
        "grading": {"cuts": list(cuts), "letters": rules.LETTERS, "candidates": [list(c) for c in rules.CUT_CANDIDATES],
                    "rule": "meets the bar on all 3 coins with every letter >= 200 trades; largest smallest F/A"},
        "thin_hours": thin,
        "crowd": {"q": cq, "rule": crule, "window_hours": crowd.WINDOW_HOURS, "move": crowd.MOVE,
                  "candidates": [list(c) for c in crowd.CANDIDATES], "odds": odds,
                  "choice": "at least 100 warnings per coin; most coins meeting the bar; then second-best ratio"},
        "similar": {**sset, "features": similar.FEATURES, "scaling": scaling, "up_share": up_share,
                    "choice": "covers 75-85% and Brier <= base on every coin; coverage closest to 80%"},
        "what_if": {"quantiles": wq, "crash_day": crash, "crash_day_source": "10 Oct 2025, worst 24 h mark-price fall"},
        "inputs": {"volatility": "HAR-S 24 h, walk-forward (refit every 90 days), as in the app",
                   "liquidation": "FHS on HAR-S, z-table rebuilt every 90 days from past outcomes",
                   "stress": "Phase 6 next-hour stress probability, walk-forward",
                   "regime": "Phase 4 forward-filtered HMM regimes, walk-forward"},
    }
    keys = ("grading", "crowd", "similar", "thin_hours")
    if FROZEN_FILE.exists() and not a.refreeze:
        old = json.loads(FROZEN_FILE.read_text())
        same = (old["grading"]["cuts"] == frozen["grading"]["cuts"] and old["thin_hours"] == thin
                and (old["crowd"]["q"], old["crowd"]["rule"]) == (cq, crule)
                and all(old["similar"][k] == sset[k] for k in sset))
        if not same:
            raise SystemExit(f"{FROZEN_FILE} holds different settings; not overwritten (use --refreeze after logging why)")
        frozen["frozen_at"] = old["frozen_at"]
    FROZEN_FILE.write_text(json.dumps(frozen, indent=2) + "\n")

    summary = {"frozen": {k: frozen[k] for k in ("frozen_at", *keys)},
               "grade_checks": grade_chk.to_dict("records"),
               "crowd": crowd_res[["symbol", "period", "n_warnings", "rate_warned", "rate_other", "ratio", "ci90_lo",
                                   "ci90_hi", "passes"]].to_dict("records"),
               "similar": sim_res.to_dict("records"), "crash_day": crash}
    (REP / "phase12_summary.json").write_text(json.dumps(summary, indent=2, default=float) + "\n")
    write_page(summary, {"Trade Grade: share knocked out within 24 h, by grade": grade_res,
                         "Trade Grade: the bar's checks": grade_chk,
                         "Crowd warning (chosen setting)": crowd_res,
                         "Crowd warning: every candidate on the grid period": crowd_grid,
                         "Similar moments (chosen setting)": sim_res,
                         "What-if quantiles (worst move within 24 h)": pd.read_csv(REP / "phase12_whatif.csv")})

    pd.set_option("display.width", 220)
    print(grade_res.round(3).to_string(index=False))
    print(grade_chk.round(3).to_string(index=False))
    print(crowd_res[["symbol", "period", "n_warnings", "rate_warned", "rate_other", "ratio", "ci90_lo", "ci90_hi",
                     "passes"]].round(3).to_string(index=False))
    print(sim_res.round(4).to_string(index=False))
    print("crash day:", crash, "| thin hours:", thin)
    print(f"frozen -> {FROZEN_FILE}; reports -> {REP} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
