"""Phase 5 demo page from the saved results of scripts/run_phase5.py.

    python scripts/phase5_report.py                    # writes data/reports/phase5.html
Charts use Plotly from a CDN, so the page needs internet to draw.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import json

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.models import calibration as C

REP = config.DATA_DIR / "reports"
NAMES = {"har": "HAR", "har_s": "HAR-S (time-of-day/week)", "no_change": "no change", "hist_vol": "historical average",
         "gbm": "LightGBM", "combo": "HAR-S + LightGBM", "gbm_no_dvol": "LightGBM without DVOL",
         "gauss": "random walk (old app model)", "fhs": "FHS", "qgbm": "quantile LightGBM",
         "gauss_cal": "random walk, calibrated", "fhs_cal": "FHS, calibrated", "qgbm_cal": "quantile LightGBM, calibrated",
         "gauss_hars": "random walk on HAR-S", "fhs_hars": "FHS on HAR-S (live app)", "fhs_cal_hars": "FHS on HAR-S, calibrated"}


def t(df: pd.DataFrame, fmt="{:,.4f}") -> str:
    return df.to_html(border=0, classes="t", na_rep="-", float_format=lambda v: fmt.format(v))


def main():
    vol = pd.read_csv(REP / "phase5_volatility.csv").rename(columns={"Unnamed: 0": "model"})
    liqs = pd.read_csv(REP / "phase5_liquidation.csv")
    lev = pd.read_csv(REP / "phase5_by_leverage.csv")
    crash = pd.read_csv(REP / "phase5_crashes.csv")
    shap = pd.read_csv(REP / "phase5_shap_importance.csv", index_col=0)

    v = vol.copy()
    v["model"] = v["model"].map(NAMES).fillna(v["model"])
    v = v.pivot_table(index="model", columns=["symbol", "horizon_h"], values="qlike_vs_base_pct").round(1)
    l = liqs[liqs["model"].isin(["gauss_hars", "gauss", "fhs", "qgbm", "fhs_hars"])].copy()
    l["model"] = l["model"].map(NAMES)
    l["ece_pct"] = 100 * l["ece"]
    l_tab = l.pivot_table(index="model", columns=["symbol", "horizon_h"], values="ece_pct").round(2)

    charts = []
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        p = pd.read_parquet(REP / f"phase5_liq_{sym}_24h.parquet").dropna(subset=["gauss", "fhs"])
        data = [{"type": "scatter", "mode": "lines", "x": [0, 1], "y": [0, 1], "name": "perfect",
                 "line": {"dash": "dot", "color": "#999"}}]
        for m, col in (("gauss", "#d0393b"), ("fhs", "#2e6fd0")):
            r = C.reliability(p[m], p["hit"])
            data.append({"type": "scatter", "mode": "lines+markers", "x": r["predicted"].round(4).tolist(),
                         "y": r["observed"].round(4).tolist(), "name": NAMES[m], "line": {"color": col},
                         "text": [f"n={n:,}" for n in r["n"]]})
        charts.append((f"rel_{sym}", data, {"title": f"{sym}, 24 h: predicted vs actual liquidation rate",
                                           "xaxis": {"title": "predicted probability"},
                                           "yaxis": {"title": "share actually liquidated"}, "height": 380}))
    b = lev[(lev["symbol"] == "BTCUSDT") & (lev["horizon_h"] == 24)].copy()
    b = b.set_index(["leverage", "side"])[["observed", "gauss", "fhs", "n"]]
    cr = crash[(crash["horizon_h"] == 24)].pivot_table(index=["symbol", "crash", "leverage"], columns="side",
                                                         values=["observed", "fhs", "gauss"]).round(3)
    sh = shap.head(10)
    scripts = "".join(f'Plotly.newPlot("{i}", {json.dumps(d)}, {json.dumps(lay)}, {{responsive: true}});' for i, d, lay in charts)
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>RegimeCast Phase 5</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}}
.t{{border-collapse:collapse;font-size:13px}}.t td,.t th{{padding:4px 10px;border-bottom:1px solid #ddd;text-align:right}}
p.note{{color:#555}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px}}</style></head><body>
<h1>Phase 5: volatility forecaster and calibrated liquidation risk</h1>
<p class="note">Development period only, out of sample: models are refitted every 90 days on the past and
scored on 2022-01 to 2025-09 (about 32,700 hourly forecasts per coin and horizon). The final test period from
1 Oct 2025, including the October crash, stays closed until the final evaluation.</p>
<h2>1. Volatility: error vs HAR (QLIKE, %, lower is better)</h2>
<p class="note">Negative = better than HAR-RV. Every "HAR-S + LightGBM" number is significant against HAR
(Diebold-Mariano p &lt; 0.01). Full table with p-values and the Model Confidence Set: phase5_volatility.csv.</p>
{t(v, "{:,.1f}")}
<h2>2. Liquidation probability: calibration error (ECE, %, lower is better)</h2>
<p class="note">For every hour, side and leverage from 2x to 100x (about 613,000 predictions per coin and
horizon): the predicted chance of liquidation vs whether it happened. FHS is the model now used by the app.</p>
{t(l_tab, "{:,.2f}")}
<div class="grid">{"".join(f'<div id="{i}"></div>' for i, _, _ in charts)}</div>
<h3>BTC 24 h by leverage: average predicted vs actual</h3>{t(b, "{:,.4f}")}
<h2>3. During crashes</h2>
<p class="note">The honest weak spot: in the FTX and Yen-carry crash windows both models understate the risk for
longs (and overstate it for shorts), because they treat up and down moves alike while crashes are one-way. The
Phase 6 stress model adds the directional signals (funding, crowding, order book) this needs.</p>
{t(cr, "{:,.3f}")}
<h2>4. What drives the LightGBM forecast (mean |SHAP|, 24 h)</h2>
<p class="note">On the log-variance scale, last year of the development period. Order-book features have
little weight because they exist only from May 2025.</p>
{t(sh, "{:,.3f}")}
<script>{scripts}</script></body></html>"""
    (REP / "phase5.html").write_text(page, encoding="utf-8")
    print("wrote", REP / "phase5.html")


if __name__ == "__main__":
    main()
