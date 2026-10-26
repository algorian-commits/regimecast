"""Phase 6 results page from scripts/run_phase6.py outputs.

    python scripts/phase6_report.py        # data/reports/phase6.html
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import pandas as pd

from regimecast import config

REP = config.DATA_DIR / "reports"
NAMES = {"persist": "rule: stress continues", "vol_rule": "rule: volatility above its 95th percentile",
         "regime_rule": "rule: regime says stressed", "gbm": "stress classifier", "gbm_cal": "stress classifier, calibrated"}


def t(df, fmt="{:,.3f}"):
    return df.to_html(border=0, classes="t", na_rep="-", float_format=lambda v: fmt.format(v))


sc = pd.read_csv(REP / "phase6_scores.csv")
sc["model"] = sc["model"].map(NAMES)
auc = sc.pivot_table(index="model", columns="symbol", values="auc")
ap = sc.pivot_table(index="model", columns="symbol", values="avg_precision")
al = pd.read_csv(REP / "phase6_alarms.csv")
al["model"] = al["model"].map(NAMES)
lt = pd.read_csv(REP / "phase6_leadtimes.csv")
ob = pd.read_csv(REP / "phase6_orderbook.csv")
sh = pd.read_csv(REP / "phase6_shap_importance.csv", index_col=0).head(10)
page = f"""<!doctype html><html><head><meta charset="utf-8"><title>RegimeCast Phase 6</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}}
.t{{border-collapse:collapse;font-size:13px}}.t td,.t th{{padding:4px 10px;border-bottom:1px solid #ddd;text-align:right}}
p.note{{color:#555}}</style></head><body>
<h1>Phase 6: stress early warning</h1>
<p class="note">Question: will there be a market stress event in the next hour? (5+ minutes in a row of 15-minute
volatility above the 99th percentile of the previous 30 days, defined without any model; about 4% of hours.)
Walk-forward, scored 2022-01 to 2025-09, about 28,500 hours per coin. Final test period closed.</p>
<h2>How well it ranks risky hours (AUC; 0.5 = coin flip, 1 = perfect)</h2>{t(auc)}
<h2>Precision across all alarm levels (average precision; base rate about 0.04)</h2>{t(ap)}
<h2>As an alarm</h2>
<p class="note">At "high" (30%+) the classifier is right about 36-45% of the time with 0.3-0.4 false alarms a day;
at 50%+ about 57-58%. At about one alarm a day it is only slightly better than "stress continues".</p>
{t(al.round(3), "{:,.3f}")}
<h2>Before the crashes in the scored period</h2>
<p class="note">Hours the calibrated probability had been at 20%+ before the crash onset (10% below the 24 h high).</p>
{t(lt)}
<h2>Order book (May to Sep 2025 only)</h2>
<p class="note">With five months of history the hourly model cannot learn from the order book: identical with and
without it. Minute-level evidence is in the 10 October forensic timeline.</p>{t(ob)}
<h2>What drives it (mean |SHAP|, last year of the development period)</h2>{t(sh)}
<p><a href="oct10_forensics.html">10 October 2025, minute by minute</a></p>
</body></html>"""
(REP / "phase6.html").write_text(page, encoding="utf-8")
print("wrote", REP / "phase6.html")
