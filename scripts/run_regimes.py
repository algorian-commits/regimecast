"""Phase 4 results: market regimes on real data, the crash lead-time table and the demo page.

    python scripts/run_regimes.py                 # BTC (default), writes data/reports/...
    python -m http.server 8010 -d data/reports    # then open http://localhost:8010/phase4.html

Needs the hourly feature tables (data/parquet/features_1h/<SYMBOL>/all.parquet), built on
Modal (regimecast/jobs/modal_app.py::features_all) and copied with
``modal volume get regimecast-data parquet/features_1h data/parquet/features_1h``.
Charts use Plotly from a CDN, so the page needs internet to draw.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import html
import json

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.models import harness, regimes

COLORS = {0: "#2e9d5b", 1: "#e0a020", 2: "#d0393b"}  # calm, active, stressed


def run(symbol: str) -> dict:
    h = store.features(symbol, "1h")
    if h.empty:
        raise SystemExit(f"no hourly features for {symbol}: build them on Modal and copy them first")
    x = regimes.regime_inputs(h)
    reg = regimes.fit_walk_forward(x, regimes.FEATURE_SETS["base"], refit_every_hours=24 * 90)  # quarterly refits
    out = {"base": reg}
    # order-book version: Bybit data from May 2025, so it is fitted on May-Sep 2025 only
    if x["log_spread"].notna().sum() > 24 * 60:
        ob_x = x[x.index >= x["log_spread"].first_valid_index()]
        out["orderbook"] = regimes.fit_walk_forward(ob_x, regimes.FEATURE_SETS["orderbook"],
                                                    min_train_hours=24 * 30, refit_every_hours=24 * 14)
    close = regimes.hourly_close(h)
    crash = regimes.crash_study(close, reg)
    summ = {k: regimes.regime_summary(r, h) for k, r in out.items()}
    rep = config.DATA_DIR / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    for k, r in out.items():
        r.to_parquet(rep / f"regimes_{k}_{symbol}.parquet")
        summ[k].to_csv(rep / f"regime_summary_{k}_{symbol}.csv")
    crash.to_csv(rep / f"crash_leadtime_{symbol}.csv", index=False)
    return {"h": h, "close": close, "reg": out, "crash": crash, "summary": summ}


def table(df: pd.DataFrame) -> str:
    return df.to_html(border=0, classes="t", float_format=lambda v: f"{v:,.3f}", na_rep="-")


def page(symbol: str, r: dict) -> str:
    reg, close = r["reg"]["base"], r["close"]
    d = pd.DataFrame({"close": close}).join(reg[["regime", "p_stressed", "frozen"]], how="inner").dropna(subset=["close"])
    traces = [{"type": "scattergl", "mode": "markers", "name": regimes.NAMES[k], "x": [t.isoformat() for t in g.index],
               "y": g["close"].round(2).tolist(), "marker": {"size": 2, "color": COLORS[k]}}
              for k, g in d.groupby("regime")]
    shapes = [{"type": "line", "x0": ev, "x1": ev, "yref": "paper", "y0": 0, "y1": 1,
               "line": {"color": "#444", "dash": "dot", "width": 1}} for ev in regimes.EVENTS.values()]
    shapes.append({"type": "rect", "x0": str(harness.holdout_start()), "x1": d.index[-1].isoformat(), "yref": "paper",
                   "y0": 0, "y1": 1, "fillcolor": "#888", "opacity": 0.08, "line": {"width": 0}})
    ann = [{"x": ev, "y": 1, "yref": "paper", "text": name, "showarrow": False, "font": {"size": 10},
            "textangle": -90, "xanchor": "right", "yanchor": "top"} for name, ev in regimes.EVENTS.items()]
    ann.append({"x": str(harness.holdout_start()), "y": 0.02, "yref": "paper", "text": "final test period (model frozen)",
                "showarrow": False, "xanchor": "left", "font": {"size": 10}})
    main = {"data": traces, "layout": {"title": f"{symbol}: price coloured by filtered regime (walk-forward, no look-ahead)",
                                       "yaxis": {"type": "log", "title": "price (USDT)"}, "shapes": shapes,
                                       "annotations": ann, "height": 520, "legend": {"orientation": "h"}}}
    zooms = []
    for name, day in list(regimes.EVENTS.items()) + [("10 Oct 2025 crash (frozen model, descriptive)", "2025-10-10")]:
        t = pd.Timestamp(day, tz="UTC")
        z = d[t - pd.Timedelta(days=5): t + pd.Timedelta(days=3)]
        zooms.append({"data": [{"type": "scatter", "x": [i.isoformat() for i in z.index], "y": z["close"].tolist(),
                                "name": "price", "line": {"color": "#333", "width": 1}},
                               {"type": "scatter", "x": [i.isoformat() for i in z.index], "y": z["p_stressed"].tolist(),
                                "name": "P(stressed)", "yaxis": "y2", "fill": "tozeroy",
                                "line": {"color": COLORS[2], "width": 1}}],
                      "layout": {"title": name, "height": 300, "margin": {"t": 40},
                                 "yaxis": {"title": "price"},
                                 "yaxis2": {"overlaying": "y", "side": "right", "range": [0, 1], "title": "P(stressed)"},
                                 "legend": {"orientation": "h"}}})
    crash = r["crash"].copy()
    summ = "".join(f"<h3>{html.escape(k)} features</h3>" + table(v) for k, v in r["summary"].items())
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>RegimeCast Phase 4</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}}
.t{{border-collapse:collapse;font-size:13px}}.t td,.t th{{padding:4px 10px;border-bottom:1px solid #ddd;text-align:right}}
p.note{{color:#555}}</style></head><body>
<h1>Phase 4: market regimes ({symbol})</h1>
<p class="note">A 3-state Gaussian hidden Markov model (calm / active / stressed) on hourly return and realised
volatility, refitted every 90 days on the past only, with forward-only (filtered) probabilities: the colour at an
hour uses nothing after that hour. From 1 Oct 2025 (shaded) the last model fitted before it is frozen and only
filters, so the October 2025 crash is shown without the model ever being fitted on it.</p>
<div id="main"></div>
<h2>How early did the regime turn stressed? (dev-period crashes)</h2>
<p class="note">Onset = first hour at least 10% below the trailing 24 h high. Lead = unbroken hours with
P(stressed) &ge; 0.5 up to the onset.</p>
{table(crash)}
<h2>Do the regimes mean something? Volatility in the next 24 h, by current regime</h2>
<p class="note">Filtered regime at hour t, realised volatility over (t, t+24h], development period only.</p>
{summ}
<h2>Crash close-ups</h2>
{"".join(f'<div id="z{i}"></div>' for i in range(len(zooms)))}
<script>
Plotly.newPlot("main", {json.dumps(main["data"])}, {json.dumps(main["layout"])}, {{responsive: true}});
{"".join(f'Plotly.newPlot("z{i}", {json.dumps(z["data"])}, {json.dumps(z["layout"])}, {{responsive: true}});' for i, z in enumerate(zooms))}
</script></body></html>"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default="BTCUSDT")
    a = p.parse_args()
    r = run(a.symbol)
    print(r["crash"].to_string(index=False))
    for k, v in r["summary"].items():
        print(f"\n{k}:\n{v.round(3).to_string()}")
    out = config.DATA_DIR / "reports" / "phase4.html"
    out.write_text(page(a.symbol, r), encoding="utf-8")
    print("\nwrote", out)


if __name__ == "__main__":
    main()
