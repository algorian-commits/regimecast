"""Phase 9 results: the paper-trading risk agent vs buy-and-hold and plain volatility targeting.

    python scripts/run_phase9.py                         # grid, check, freeze, reports (about 6 min)
    python -m http.server 8010 -d data/reports           # then open http://localhost:8010/phase9.html

Development period only (the final test period from 2025-10-01 stays closed):

1. every setting in GRID is run on 2022-01 to 2024-09 and logged (phase9_grid.csv);
2. the choice, fixed before looking at the check period: among the settings whose maximum
   drawdown is at least 25% smaller than buy-and-hold's (the final evaluation's bar), the
   highest Sharpe ratio; ties go to the lower turnover. The volatility-targeting baseline
   gets the same treatment on its own small grid, so both are tuned equally;
3. the chosen settings are run once on 2024-10 to 2025-09 (the check) and frozen in
   regimecast/agent/frozen.json with the date. An existing frozen.json is never overwritten
   with different settings (``--refreeze`` only after a logged decision).

Outputs: data/reports/phase9_*.csv, phase9_equity.parquet, phase9.html.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import itertools
import json
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.agent import backtest as B, rules as R, signals as S
from regimecast.agent.final import FROZEN_FILE

REP = config.DATA_DIR / "reports"
GRID_PERIOD = ("2022-01-01", "2024-10-01")      # [start, end): settings are chosen here
CHECK_PERIOD = ("2024-10-01", "2025-10-01")     # the frozen settings are checked here
DEV_PERIOD = ("2022-01-01", "2025-10-01")
CRASHES = {"FTX collapse": ("2022-11-06", "2022-11-12"), "Yen carry unwind": ("2024-08-03", "2024-08-08")}
GRID = {
    "target_vol": [0.3, 0.4, 0.5, 0.6, 0.8],
    "stress": [(R.NO_CUT, R.NO_CUT), (R.NO_CUT, 0.30), (R.NO_CUT, 0.50), (0.10, 0.30), (0.20, 0.50)],
    "regime_mult": [1.0, 0.5, 0.0],
    "band": [0.05, 0.10, 0.20],
}
VT_GRID = {"target_vol": GRID["target_vol"], "band": GRID["band"]}
DD_BAR = 0.75   # drawdown at most 75% of buy-and-hold's (at least 25% smaller)


def window(df: pd.DataFrame, period) -> pd.DataFrame:
    a, b = (pd.Timestamp(x, tz="UTC") for x in period)
    return df[(df.index >= a) & (df.index < b)]


class Runner:
    def __init__(self, symbols):
        self.symbols = symbols
        self.mark, self.funding = S.market(symbols)
        self.sig = S.load_all(symbols)

    def run(self, period, targets_fn, band: float) -> B.Result:
        m = window(self.mark, period)
        return B.simulate(m, self.funding.reindex(m.index), targets_fn(m.index), band=band)

    def agent(self, period, s: R.Settings, sig=None) -> B.Result:
        return self.run(period, lambda ix: R.agent_targets(sig or self.sig, s, ix), s.band)

    def buy_and_hold(self, period) -> B.Result:
        return self.run(period, lambda ix: R.buy_and_hold_targets(self.symbols, ix), 0.0)

    def vol_target(self, period, target_vol: float, band: float) -> B.Result:
        return self.run(period, lambda ix: R.vol_target_targets(self.sig, target_vol, ix), band)


def choose(rows: pd.DataFrame, bh_mdd: float) -> pd.Series:
    ok = rows[rows["max_drawdown"] >= DD_BAR * bh_mdd]
    pool = ok if len(ok) else rows
    return pool.sort_values(["sharpe", "turnover_per_year"], ascending=[False, True]).iloc[0]


def hars_signals(runner: Runner) -> dict:
    """Sensitivity: the agent with HAR-S volatility (what the live app has) instead of the combination."""
    from regimecast.data import store
    from regimecast.models import harness, volatility as V

    out = {}
    for s in runner.symbols:
        f = config.parquet_dir() / "dvol" / s / "all.parquet"
        X, y = V.dataset(store.features(s, "1h"), store.targets(s), 24, pd.read_parquet(f) if f.exists() else None)
        var = harness.walk_forward(X, y, harness.BASELINES["har_s"], "2021-01-01", 24, refit_every_hours=24 * 90)
        sig = runner.sig[s].copy()
        sig["vol_ann"] = np.sqrt(var * 365).reindex(sig.index).ffill(limit=24)
        out[s] = sig
    return out


def crash_rows(results: dict[str, B.Result]) -> pd.DataFrame:
    rows = []
    for name, (a, b) in CRASHES.items():
        a, b = pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC")
        for strat, res in results.items():
            h = res.hourly
            before = h.loc[h.index < a, "equity"].iloc[-1]
            w = h[(h.index >= a) & (h.index < b)]
            eq = w["equity"] / before
            rows.append({"crash": name, "from": a.date(), "to": b.date(), "strategy": strat,
                         "return": float(eq.iloc[-1] - 1), "max_drawdown": B.max_drawdown(eq),
                         "avg_exposure": float(w.filter(like="exposure_").mean(axis=1).mean()),
                         "fees_paid": float(w["fee"].sum()), "funding_paid": float(w["funding"].sum())})
    return pd.DataFrame(rows)


def write_page(results: pd.DataFrame, boot: pd.DataFrame, crashes: pd.DataFrame, equity: pd.DataFrame,
               frozen: dict, grid: pd.DataFrame) -> None:
    def t(df):
        return df.to_html(border=0, classes="t", na_rep="-", float_format=lambda v: f"{v:,.3f}")

    names = {"agent": "RegimeCast agent", "buy_and_hold": "Buy-and-hold", "vol_target": "Volatility targeting",
             "agent_hars_vol": "Agent with HAR-S volatility (live version)"}
    daily = equity.resample("1D").last()
    lines = [{"x": [str(i.date()) for i in daily.index], "y": daily[c].round(4).tolist(), "name": names[c],
              "type": "scatter", "mode": "lines"} for c in ["agent", "buy_and_hold", "vol_target"]]
    ftx = equity.loc["2022-11-01":"2022-11-20"]
    zoom = [{"x": [i.isoformat() for i in ftx.index], "y": (ftx[c] / ftx[c].iloc[0]).round(4).tolist(), "name": names[c],
             "type": "scatter", "mode": "lines"} for c in ["agent", "buy_and_hold", "vol_target"]]
    lay = {"yaxis": {"title": "account value (start = 1)", "type": "log"}, "height": 420, "margin": {"t": 20},
           "shapes": [{"type": "rect", "xref": "x", "yref": "paper", "x0": a, "x1": b, "y0": 0, "y1": 1,
                       "fillcolor": "#d33", "opacity": 0.12, "line": {"width": 0}} for a, b in CRASHES.values()]
           + [{"type": "line", "xref": "x", "yref": "paper", "x0": CHECK_PERIOD[0], "x1": CHECK_PERIOD[0], "y0": 0,
               "y1": 1, "line": {"dash": "dot", "color": "#888"}}]}
    lay_z = {"yaxis": {"title": "value (1 Nov 2022 = 1)"}, "height": 320, "margin": {"t": 20}}
    tab = results.copy()
    tab["strategy"] = tab["strategy"].map(names)
    keep = ["period", "strategy", "total_return", "annual_return", "sharpe", "max_drawdown", "calmar",
            "turnover_per_year", "fees_paid", "funding_paid", "avg_exposure", "time_in_market"]
    s = frozen["settings"]
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>RegimeCast Phase 9</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}}
.t{{border-collapse:collapse;font-size:13px}}.t td,.t th{{padding:4px 10px;border-bottom:1px solid #ddd;text-align:right}}
p.note{{color:#555}}</style></head><body>
<h1>Phase 9: paper-trading risk agent</h1>
<p class="note">Equal-weight BTC, ETH and SOL perpetuals, long only, 0x to 1x. Every hour the agent sets each coin's
position from what RegimeCast knew at that hour (walk-forward, out-of-sample forecasts). Taker fee 0.05% on every
change, funding at Binance's archived rates. Development period only; the final test period (from 2025-10-01) is
closed. Settings chosen on 2022-01 to 2024-09 ({len(grid)} settings tried), checked on 2024-10 to 2025-09 (dotted line).</p>
<p><b>Frozen settings</b> ({frozen['frozen_at']}): target volatility {s['target_vol']:.0%}, stress cut-offs
{s['stress_elevated']} / {s['stress_high']} (2.0 = off), regime multiplier {s['regime_mult']}, no-trade band {s['band']}.</p>
<h2>Account value, 2022 to Sep 2025 (crash windows shaded)</h2><div id="main"></div>
<h2>FTX collapse, hour by hour</h2><div id="ftx"></div>
<h2>Results</h2>{t(tab[keep])}
<h2>Sharpe difference, 95% block-bootstrap interval (one-week blocks)</h2>{t(boot)}
<h2>Crash windows</h2>{t(crashes)}
<h2>The 10 best settings on the grid period</h2>{t(grid.head(10))}
<script>
Plotly.newPlot("main", {json.dumps(lines)}, {json.dumps(lay)}, {{responsive: true}});
Plotly.newPlot("ftx", {json.dumps(zoom)}, {json.dumps(lay_z)}, {{responsive: true}});
</script></body></html>"""
    (REP / "phase9.html").write_text(page, encoding="utf-8")


def write_summary() -> None:
    """regimecast/agent/dev_summary.json for GET /agent (the deployed API has no data/ folder)."""
    frozen = json.loads(FROZEN_FILE.read_text())
    res = pd.read_csv(REP / "phase9_results.csv")
    res = res[res["strategy"].isin(["agent", "buy_and_hold", "vol_target"])]
    keep = ["period", "strategy", "total_return", "annual_return", "sharpe", "max_drawdown", "calmar",
            "turnover_per_year", "fees_paid", "funding_paid", "avg_exposure"]
    eq = pd.read_parquet(REP / "phase9_equity.parquet").resample("1D").last()
    out = {k: frozen[k] for k in ("frozen_at", "settings", "settings_tried", "chosen_on", "checked_on")}
    out["results"] = [{k: (None if pd.isna(v) else (round(v, 5) if isinstance(v, float) else v))
                       for k, v in r.items()} for r in res[keep].to_dict("records")]
    out["equity"] = [{"day": str(d.date()), "agent": round(float(r["agent"]), 5),
                      "buy_and_hold": round(float(r["buy_and_hold"]), 5), "vol_target": round(float(r["vol_target"]), 5)}
                     for d, r in eq.iterrows()]
    (FROZEN_FILE.parent / "dev_summary.json").write_text(json.dumps(out, separators=(",", ":")) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refreeze", action="store_true", help="overwrite frozen.json (log the reason first)")
    ap.add_argument("--no-hars", action="store_true", help="skip the HAR-S sensitivity run")
    ap.add_argument("--summary-only", action="store_true", help="only rewrite dev_summary.json from saved results")
    a = ap.parse_args()
    if a.summary_only:
        return write_summary()
    REP.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    run = Runner(config.SYMBOLS)

    # 1. grid on the grid period
    bh = B.metrics(run.buy_and_hold(GRID_PERIOD))
    rows = []
    for tv, (se, sh), rm, band in itertools.product(*GRID.values()):
        s = R.Settings(tv, se, sh, rm, band)
        rows.append({**s.to_dict(), **B.metrics(run.agent(GRID_PERIOD, s))})
    grid = pd.DataFrame(rows).sort_values("sharpe", ascending=False)
    grid["meets_dd_bar"] = grid["max_drawdown"] >= DD_BAR * bh["max_drawdown"]
    grid.to_csv(REP / "phase9_grid.csv", index=False)
    vt_rows = [{"target_vol": tv, "band": band, **B.metrics(run.vol_target(GRID_PERIOD, tv, band))}
               for tv, band in itertools.product(*VT_GRID.values())]
    vt_grid = pd.DataFrame(vt_rows).sort_values("sharpe", ascending=False)
    vt_grid.to_csv(REP / "phase9_vol_target_grid.csv", index=False)
    print(f"grid: {len(grid)} agent + {len(vt_grid)} vol-target settings in {time.time() - t0:.0f}s", flush=True)

    # 2. choose (rule fixed in the docstring) and freeze
    best = choose(grid, bh["max_drawdown"])
    chosen = R.Settings(*(float(best[k]) for k in R.Settings().to_dict()))
    vt_best = choose(vt_grid, bh["max_drawdown"])
    vt = {"target_vol": float(vt_best["target_vol"]), "band": float(vt_best["band"])}
    print("chosen:", chosen, "| vol targeting:", vt, flush=True)

    # 3. results on the grid, check and whole development periods
    res, boot, full = [], [], {}
    hsig = None if a.no_hars else hars_signals(run)
    for pname, period in (("grid 2022-01..2024-09", GRID_PERIOD), ("check 2024-10..2025-09", CHECK_PERIOD),
                          ("dev 2022-01..2025-09", DEV_PERIOD)):
        r = {"agent": run.agent(period, chosen), "buy_and_hold": run.buy_and_hold(period),
             "vol_target": run.vol_target(period, vt["target_vol"], vt["band"])}
        if hsig is not None:
            r["agent_hars_vol"] = run.agent(period, chosen, hsig)
        for k, v in r.items():
            res.append({"period": pname, "strategy": k, **B.metrics(v)})
        for other in ("buy_and_hold", "vol_target"):
            boot.append({"period": pname, "comparison": f"agent - {other}",
                         **B.sharpe_diff_bootstrap(r["agent"].hourly["ret"], r[other].hourly["ret"])})
        if period == DEV_PERIOD:
            full = r
    results = pd.DataFrame(res)
    boot = pd.DataFrame(boot)
    crashes = crash_rows({k: full[k] for k in ("agent", "buy_and_hold", "vol_target")})
    equity = pd.DataFrame({k: v.equity for k, v in full.items()})
    results.to_csv(REP / "phase9_results.csv", index=False)
    boot.to_csv(REP / "phase9_bootstrap.csv", index=False)
    crashes.to_csv(REP / "phase9_crashes.csv", index=False)
    equity.to_parquet(REP / "phase9_equity.parquet")
    pd.DataFrame({f"{k}_{c}": v.hourly[c] for k, v in full.items() for c in v.hourly.columns
                  if c.startswith("exposure_")}).to_parquet(REP / "phase9_exposure.parquet")

    chk = results[(results["period"].str.startswith("check"))].set_index("strategy")
    frozen = {
        "frozen_at": datetime.now(timezone.utc).date().isoformat(),
        "settings": chosen.to_dict(),
        "elevated_mult": R.ELEVATED_MULT,
        "chosen_on": f"{GRID_PERIOD[0]} to 2024-09-30",
        "checked_on": f"{CHECK_PERIOD[0]} to 2025-09-30",
        "rule": "max Sharpe among settings with max drawdown at least 25% smaller than buy-and-hold; ties: lower turnover",
        "settings_tried": int(len(grid)),
        "inputs": {"volatility": "24 h HAR-S + LightGBM combination (M7), walk-forward",
                   "stress": "next-hour stress probability (M10 part 1), calibrated, walk-forward",
                   "regime": "forward-filtered HMM regime (M4), walk-forward"},
        "vol_target_baseline": vt,
        "check": {k: {m: round(float(chk.loc[k, m]), 4) for m in ("sharpe", "max_drawdown", "total_return")}
                  for k in chk.index},
    }
    if FROZEN_FILE.exists() and not a.refreeze:
        old = json.loads(FROZEN_FILE.read_text())
        if old["settings"] != frozen["settings"] or old.get("vol_target_baseline") != vt:
            raise SystemExit(f"{FROZEN_FILE} holds different settings; not overwritten (use --refreeze after logging why)")
        frozen["frozen_at"] = old["frozen_at"]
    FROZEN_FILE.write_text(json.dumps(frozen, indent=2) + "\n")
    write_page(results, boot, crashes, equity, frozen, grid)
    write_summary()

    pd.set_option("display.width", 200)
    print(results.round(4).to_string(index=False))
    print(boot.round(4).to_string(index=False))
    print(crashes.round(4).to_string(index=False))
    print(f"frozen -> {FROZEN_FILE}; reports -> {REP} ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
