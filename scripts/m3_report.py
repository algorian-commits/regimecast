"""Phase 3 (M3) results: model accuracy on hand labels, news mood vs BTC price, and the
10 Oct 2025 tariff crash as a case study.

    python scripts/m3_report.py

Ground truth for accuracy: ``final_label`` in docs/m3/labels_100_review2.csv when it
exists (two blind labelling passes, disagreements settled by the written rule), else
the draft labels in docs/m3/labels_100.csv; a filled ``saeed_label`` always wins.

Writes to data/reports/:
    m3_accuracy.md / .csv         accuracy, macro F1 and confusion matrices, both models
    m3_price_check.md             does the mood move before, with, or after big BTC moves?
    m3_event_study.csv            the numbers behind it
    m3_crash_case_study.csv       hour by hour, 10-11 Oct 2025
    phase3.html                   demo page (Plotly from a CDN, needs internet to draw)

The price statistics use May-Sep 2025 only: from 2025-10-01 is the final test period
(README, rule 5), which is touched once, with --final-test. The crash case study is
the one exception, by plan: it is descriptive (no model is fitted or chosen on it).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import html
import json

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.sentiment import evaluate as ev
from regimecast.sentiment import models, pipeline

ROOT = pathlib.Path(__file__).resolve().parents[1]
LABELS = ROOT / "docs" / "m3" / "labels_100.csv"
REVIEW = ROOT / "docs" / "m3" / "labels_100_review2.csv"  # second, blind labelling pass + settled labels
DEV_START = "2025-05-01"
CASE_FROM, CASE_TO = "2025-10-10 06:00", "2025-10-11 13:00"  # hour ends


# ------------------------------------------------------------------ 1. accuracy
def accuracy_tables() -> tuple[pd.DataFrame, dict, pd.DataFrame, dict]:
    lab = pd.read_csv(LABELS, encoding="utf-8-sig", keep_default_na=False)
    base = lab["draft_label"]
    agree = {}
    if REVIEW.exists():
        rv = pd.read_csv(REVIEW, encoding="utf-8-sig", keep_default_na=False)
        lab = lab.merge(rv, on="id", how="left", validate="one_to_one")
        base = lab["final_label"]
        agree = {"agreement": float((lab["draft_label"] == lab["review2_label"]).mean()),
                 "kappa": cohen_kappa(lab["draft_label"], lab["review2_label"]),
                 "disagreements": int((lab["draft_label"] != lab["review2_label"]).sum())}
    human = lab["saeed_label"].str.strip() != ""
    lab["truth"] = np.where(human, lab["saeed_label"].str.strip().str.lower(), base)
    rows, conf = [], {}
    for m in models.MODELS:
        s = pipeline.load_scores(m)
        pred = lab[["title_key"]].merge(s[["title_key", "label"]].drop_duplicates("title_key"), how="left")["label"]
        if pred.isna().any():  # not scored yet (e.g. a fresh machine): score just these
            p = models.score(lab["headline"].tolist(), models.load(m))
            pred = p["label"]
        lab[f"pred_{m}"] = pred.to_numpy()
        a = ev.accuracy(lab["truth"], lab[f"pred_{m}"])
        rows.append({"model": m, "hf_repo": models.MODELS[m], **a})
        conf[m] = ev.confusion(lab["truth"], lab[f"pred_{m}"])
    acc = pd.DataFrame(rows)
    if human.any():
        acc["labels"] = f"checked by a person ({int(human.sum())} changed)"
    elif agree:
        acc["labels"] = (f"two independent AI annotators (Claude), labelled blind, agreement {agree['agreement']:.0%}, "
                         f"Cohen's kappa {agree['kappa']:.2f}, {agree['disagreements']} disagreements settled by the "
                         f"written rule; not human-labelled")
    else:
        acc["labels"] = "one AI annotator's draft (Claude), not checked"
    return acc, conf, lab, agree


def cohen_kappa(a: pd.Series, b: pd.Series) -> float:
    po = float((a == b).mean())
    pe = sum(float((a == c).mean()) * float((b == c).mean()) for c in ev.CLASSES)
    return (po - pe) / (1 - pe)


# ------------------------------------------------------------------ 2. against price
def hourly_btc(start, end) -> pd.Series:
    return ev.hourly_close(store.minute_bars("BTCUSDT", "last", start, end))


def mood_features(m: pd.DataFrame, end) -> pd.DataFrame:
    m = m.set_index("ts")
    grid = pd.date_range(m.index.min(), end, freq="h")
    m = m.reindex(grid)  # source outages stay missing, not zero
    return pd.DataFrame({
        "count_z": ev.abnormal(m["count"].astype(float)),
        "score_z": ev.abnormal(m["mean_score"]),
        "neg_share_z": ev.abnormal(m["share_negative"]),
        "policy_z": ev.abnormal(m["policy_shock_count"].astype(float)),
    })


def price_check(final_test: bool) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    end = pd.Timestamp.now(tz="UTC").floor("h") if final_test else pd.Timestamp(config.HOLDOUT_START, tz="UTC")
    close = hourly_btc(pd.Timestamp(DEV_START, tz="UTC") - pd.Timedelta(days=31), end)
    feats = mood_features(store.news_mood("BTCUSDT", None, end + pd.Timedelta(hours=7)), end)
    moves = ev.big_moves(close)
    moves = moves[(moves["ts"] >= pd.Timestamp(DEV_START, tz="UTC")) & (moves["ts"] < end)]
    feats_p = feats[(feats.index >= pd.Timestamp(DEV_START, tz="UTC")) & (feats.index < end)]
    es = pd.concat([ev.event_study(feats_p, moves.loc[moves["direction"] == d, "ts"]).assign(moves=d)
                    for d in ("down", "up") if (moves["direction"] == d).any()])
    r = np.log(close).diff()
    absz = (r / r.shift(1).rolling(720, min_periods=360).std()).abs()
    absz = absz[(absz.index >= pd.Timestamp(DEV_START, tz="UTC")) & (absz.index < end)]
    ll = pd.concat([ev.lead_lag(feats_p[c], absz).assign(feature=c) for c in feats_p.columns])
    pt = ev.predictive_test(feats_p, absz)
    return describe_price_check(moves, es, ll, pt, end, final_test), es, moves


def describe_price_check(moves, es, ll, pt, end, final_test) -> str:
    period = f"{DEV_START} to {end:%Y-%m-%d}" + ("" if final_test else " (development period; the final test period from 2025-10-01 is not used)")
    lines = [f"# News mood vs BTC price (M3)", "", f"Period: {period}.", "",
             f"Big move = an hour whose BTC return is at least 4x the past-30-day hourly volatility "
             f"(volatility from the past only; follow-through hours within 3 h folded in). "
             f"Found **{(moves['direction'] == 'down').sum()} down** and **{(moves['direction'] == 'up').sum()} up** moves.", "",
             "Mood measures are 'abnormal' values: how far this hour is from the previous 7 days, in standard deviations. "
             "Hour labels are hour ENDS, so lag 0 = headlines from the same hour as the move, "
             "-1 = the hour before it, +1 = the hour after.", ""]
    for d in ("down", "up"):
        sub = es[es["moves"] == d]
        if sub.empty:
            continue
        lines += [f"## Around {d} moves (average, 90% bootstrap interval)", "",
                  "| feature | -3h | -2h | -1h | same hour | +1h | +2h | +3h |", "|---|---|---|---|---|---|---|---|"]
        for f in sub["feature"].unique():
            cells = []
            for k in (-3, -2, -1, 0, 1, 2, 3):
                r = sub[(sub["feature"] == f) & (sub["lag_hours"] == k)]
                if r.empty:
                    cells.append("-")
                    continue
                r = r.iloc[0]
                star = "**" if (r["lo90"] > 0 or r["hi90"] < 0) else ""
                cells.append(f"{star}{r['mean']:+.2f}{star} [{r['lo90']:+.2f}, {r['hi90']:+.2f}]")
            lines.append(f"| {f} | " + " | ".join(cells) + " |")
        lines += ["", "Bold = the 90% interval excludes 0 (the normal level).", ""]
    lines += ["## Correlation with the size of the move (|return| in volatility units), all hours", "",
              "corr(feature at t + lag, |move| at t). Negative lag = mood first.", "",
              "| feature | " + " | ".join(f"{k:+d}h" for k in range(-3, 4)) + " |", "|---|" + "---|" * 7]
    for f in ll["feature"].unique():
        s = ll[(ll["feature"] == f)].set_index("lag_hours")["corr"]
        lines.append(f"| {f} | " + " | ".join(f"{s.get(k, np.nan):+.3f}" for k in range(-3, 4)) + " |")
    lines += ["", "## Does the mood help forecast the NEXT hour's move size?", "",
              "OLS of next-hour |move| on this hour's |move|, the past 24 h average |move|, and one mood measure; "
              "Newey-West errors.", "", "| feature | coefficient | t | p | R2 without | R2 with | hours |",
              "|---|---|---|---|---|---|---|"]
    for _, r in pt.iterrows():
        lines.append(f"| {r['feature']} | {r['coef']:+.4f} | {r['t_stat']:+.2f} | {r['p_value']:.3f} | "
                     f"{r['r2_without']:.4f} | {r['r2_with']:.4f} | {r['n_hours']} |")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 3. crash case study
def case_study() -> tuple[pd.DataFrame, list[dict]]:
    lo, hi = pd.Timestamp(CASE_FROM, tz="UTC"), pd.Timestamp(CASE_TO, tz="UTC")
    close = hourly_btc(lo - pd.Timedelta(hours=2), hi + pd.Timedelta(minutes=1))
    m = store.news_mood("BTCUSDT", lo, hi + pd.Timedelta(minutes=1)).set_index("ts")
    h = store.headlines(lo - pd.Timedelta(hours=1), hi, ["title", "source", "scope", "coins", "policy", "stale", "copies"])
    h = h[~h["stale"]]
    h["ts"] = h["known_at"].dt.floor("h") + pd.Timedelta(hours=1)
    macro = h[(h["scope"] == "macro_policy") & (h["policy"] == "tariffs")].groupby("ts").size()
    china = h["title"].str.contains(r"china|beijing|xi\b", case=False) & h["title"].str.contains(r"tariff", case=False)
    t = pd.DataFrame({"btc_close": close, "btc_return_pct": 100 * np.log(close).diff()}).reindex(m.index)
    t = t.join(m).join(macro.rename("macro_tariff_headlines")).fillna({"macro_tariff_headlines": 0})
    t["china_tariff_headlines"] = h[china].groupby("ts").size().reindex(t.index).fillna(0).astype(int)
    minute = store.minute_bars("BTCUSDT", "last", lo, hi).set_index("ts")["close"]
    firsts = []
    for name, pat in (
            ("Trump threatens China with 'massive' tariffs (1st post)",
             r"(?=.*trump)(?=.*(?:china|xi\b|beijing))(?=.*(?:threat|massive))(?=.*tariff)"),
            ("100% tariff on China (2nd post)", r"(?=.*100\s?(?:%|percent))(?=.*china)(?=.*tariff)"),
            ("crypto headline mentioning the tariffs", None)):
        hit = h[h["title"].str.contains(pat, case=False, regex=True)] if pat else \
            h[(h["scope"] == "crypto") & (h["policy"] == "tariffs")]
        hit = hit[hit["known_at"] >= pd.Timestamp("2025-10-10 14:00", tz="UTC")]
        if len(hit):
            r = hit.iloc[0]
            px = minute[minute.index < r["known_at"]]
            firsts.append({"what": name, "known_at": f"{r['known_at']:%Y-%m-%d %H:%M} UTC", "title": r["title"],
                           "source": r["source"], "btc_then": round(float(px.iloc[-1]), 1) if len(px) else None})
    return t.reset_index().rename(columns={"ts": "hour_end"}), firsts


# ------------------------------------------------------------------ page
def page(acc, conf, price_md, case, firsts, mood_all) -> str:
    esc = html.escape
    def table(df):
        head = "".join(f"<th>{esc(str(c))}</th>" for c in df.columns)
        body = "".join("<tr>" + "".join(f"<td>{esc(v if isinstance(v, str) else f'{v:.3f}' if isinstance(v, float) else str(v))}</td>"
                                         for v in row) + "</tr>" for row in df.itertuples(index=False))
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    a = acc[["model", "hf_repo", "n", "accuracy", "macro_f1", "recall_positive", "recall_negative", "recall_neutral", "labels"]]
    conf_html = "".join(f"<h3>{esc(m)}: rows = label, columns = model</h3>{table(c.reset_index())}" for m, c in conf.items())
    c = case
    data = {"t": [f"{x:%Y-%m-%d %H:%M}" for x in c["hour_end"]], "price": c["btc_close"].round(1).tolist(),
            "count": c["count"].tolist(), "score": c["mean_score"].round(3).fillna(0).tolist(),
            "neg": c["share_negative"].round(3).fillna(0).tolist(), "macro": c["macro_tariff_headlines"].tolist(),
            "policy": c["policy_shock_count"].tolist()}
    wk = mood_all.set_index("ts").resample("D").agg({"count": "sum", "mean_score": "mean", "policy_shock_count": "sum"})
    daily = {"t": [f"{x:%Y-%m-%d}" for x in wk.index], "count": wk["count"].tolist(),
             "score": wk["mean_score"].round(3).where(wk["mean_score"].notna(), None).tolist(),
             "policy": wk["policy_shock_count"].tolist()}
    firsts_html = "".join(f"<li><b>{esc(f['known_at'])}</b> first {esc(f['what'])} headline: "
                          f"&ldquo;{esc(f['title'])}&rdquo; ({esc(f['source'])}); BTC then: {f['btc_then']:,.0f}</li>"
                          for f in firsts)
    price_html = "<pre>" + esc(price_md) + "</pre>"
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>RegimeCast news mood</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root {{ --bg:#fcfcfb; --ink:#0b0b0b; --muted:#52514e; --line:#2a78d6; --event:#e34948; --grid:#e6e5e0; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#1a1a19; --ink:#f0efec; --muted:#c3c2b7; --line:#3987e5; --event:#e66767; --grid:#33332f; }} }}
body {{ background:var(--bg); color:var(--ink); font:15px/1.5 system-ui, sans-serif; max-width:1100px; margin:0 auto; padding:16px; }}
table {{ border-collapse:collapse; margin:8px 0 16px; font-size:13px; display:block; overflow-x:auto; }}
td, th {{ border-bottom:1px solid var(--grid); padding:4px 8px; text-align:left; }}
pre {{ white-space:pre-wrap; font-size:12px; }} .note {{ color:var(--muted); }}
</style></head><body>
<h1>News mood (M3)</h1>
<p class="note">Headlines from GDELT (May 2025 to now) and the CoinDesk, Cointelegraph, The Block and Decrypt RSS feeds,
tagged by coin and policy shock, scored by {esc(models.MODELS[pipeline.MOOD_MODEL])}, counted per hour (labelled by hour end).</p>
<h2>The 10 October 2025 tariff crash, hour by hour (UTC)</h2>
<ul>{firsts_html}</ul>
<div id="crash" style="height:900px"></div>
<h2>Every day since May 2025 (BTC news mood)</h2>
<div id="daily" style="height:560px"></div>
<h2>Accuracy on 100 hand-labelled headlines</h2>
{table(a)}
{conf_html}
<h2>Mood vs price</h2>
{price_html}
<script>
const d = {json.dumps(data)}, w = {json.dumps(daily)};
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const line = css('--line'), ink = css('--ink'), grid = css('--grid'), bg = css('--bg');
const ax = t => ({{title: {{text: t, font: {{size: 12}}}}, gridcolor: grid, zeroline: false}});
const base = {{paper_bgcolor: bg, plot_bgcolor: bg, font: {{color: ink}}, showlegend: false, margin: {{l: 70, r: 20, t: 20, b: 40}},
  hovermode: 'x unified', grid: {{rows: 5, columns: 1, pattern: 'independent'}}}};
const s = (y, i, kind) => ({{x: d.t, y, type: kind || 'scatter', mode: 'lines', line: {{color: line, width: 2}}, marker: {{color: line}},
  xaxis: 'x', yaxis: i === 1 ? 'y' : 'y' + i}});
Plotly.newPlot('crash', [s(d.price, 1), s(d.count, 2, 'bar'), s(d.score, 3), s(d.neg, 4), s(d.macro, 5, 'bar')],
  Object.assign({{}}, base, {{yaxis: ax('BTC price'), yaxis2: ax('crypto headlines'), yaxis3: ax('mean score'),
   yaxis4: ax('share negative'), yaxis5: ax('macro tariff headlines'), xaxis: {{gridcolor: grid}},
   grid: {{rows: 5, columns: 1, shared_xaxes: true, roworder: 'top to bottom'}}}}), {{responsive: true}});
const sw = (y, i, kind) => ({{x: w.t, y, type: kind || 'scatter', mode: 'lines', line: {{color: line, width: 2}}, marker: {{color: line}},
  xaxis: 'x', yaxis: i === 1 ? 'y' : 'y' + i}});
Plotly.newPlot('daily', [sw(w.count, 1, 'bar'), sw(w.score, 2), sw(w.policy, 3, 'bar')],
  Object.assign({{}}, base, {{yaxis: ax('headlines / day'), yaxis2: ax('mean score'), yaxis3: ax('policy-shock / day'),
   xaxis: {{gridcolor: grid}}, grid: {{rows: 3, columns: 1, shared_xaxes: true, roworder: 'top to bottom'}}}}), {{responsive: true}});
</script></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--final-test", action="store_true", help="run the price statistics on the final test period too (once)")
    a = ap.parse_args()
    out = config.DATA_DIR / "reports"
    out.mkdir(parents=True, exist_ok=True)

    acc, conf, lab, agree = accuracy_tables()
    acc.to_csv(out / "m3_accuracy.csv", index=False)
    lab.to_csv(out / "m3_labels_with_predictions.csv", index=False)
    md = ["# Sentiment model accuracy (M3)", "", f"100 headlines (random, 6 per month). Labels: {acc['labels'].iloc[0]}.",
          f"Class split: {lab['truth'].value_counts().to_dict()}.", "",
          "| model | accuracy | macro F1 | recall positive | recall negative | recall neutral |", "|---|---|---|---|---|---|"]
    md += [f"| {r.model} ({r.hf_repo}) | {r.accuracy:.2f} | {r.macro_f1:.2f} | {r.recall_positive:.2f} | "
           f"{r.recall_negative:.2f} | {r.recall_neutral:.2f} |" for r in acc.itertuples()]
    for m, c in conf.items():
        md += ["", f"Confusion matrix, {m} (rows = label, columns = model):", "", "| | " + " | ".join(c.columns) + " |",
               "|---|---|---|---|"] + [f"| {i} | " + " | ".join(str(v) for v in row) + " |" for i, row in c.iterrows()]
    (out / "m3_accuracy.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))

    price_md, es, moves = price_check(a.final_test)
    es.to_csv(out / "m3_event_study.csv", index=False)
    moves.to_csv(out / "m3_big_moves.csv", index=False)
    (out / "m3_price_check.md").write_text(price_md, encoding="utf-8")
    print(price_md)

    case, firsts = case_study()
    case.to_csv(out / "m3_crash_case_study.csv", index=False)
    print(case.to_string())
    print(json.dumps(firsts, indent=1))

    (out / "phase3.html").write_text(page(acc, conf, price_md, case, firsts, store.news_mood("BTCUSDT")), encoding="utf-8")
    print("wrote", out / "phase3.html")


if __name__ == "__main__":
    main()
