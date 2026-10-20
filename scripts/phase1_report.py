"""Phase 1 demo page: what data we have, the first HAR result, and the October 2025
crash as seen in the order book.

    python scripts/phase1_report.py            # writes data/reports/phase1.html
    python -m http.server 8010 -d data/reports  # then open http://localhost:8010/phase1.html

Charts use Plotly from a CDN, so the page needs internet to draw.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import html
import json

import pandas as pd

from regimecast import config
from regimecast.data import store

SYMBOL = "BTCUSDT"
CRASH_FROM, CRASH_TO = "2025-10-03", "2025-10-18"
ZOOM_FROM, ZOOM_TO = "2025-10-10 18:00", "2025-10-11 03:00"


def coverage() -> list[list[str]]:
    rows = []
    for ds, what in (("klines", "1-min candles (traded price)"), ("markPriceKlines", "1-min mark price"),
                     ("indexPriceKlines", "1-min index price"), ("premiumIndexKlines", "1-min premium index"),
                     ("fundingRate", "funding payments"), ("metrics", "5-min open interest"),
                     ("orderbook", "1-sec order-book features (Bybit)")):
        span = store.available(ds, SYMBOL)
        rows.append([what, ds, *(f"{t:%Y-%m-%d %H:%M}" for t in span)] if span else [what, ds, "-", "-"])
    live = config.live_dir()
    n_live = len(list(live.rglob("*.parquet"))) if live.exists() else 0
    rows.append(["recorder backup (liquidations, Whale Watch, ...)", "data/live", f"{n_live} files", ""])
    return rows


def halts() -> list[list[str]]:
    df = store.minute_bars(SYMBOL, "last")
    z = df[df["volume"] == 0]["ts"]
    runs = z.diff().ne(pd.Timedelta("1min")).cumsum()
    r = z.groupby(runs.values).agg(["first", "count"])
    r = r[r["count"] >= 10].sort_values("first")
    return [[f"{t:%Y-%m-%d %H:%M}", f"{n} min"] for t, n in zip(r["first"], r["count"])]


def crash_series() -> dict:
    ob = store.orderbook(SYMBOL, CRASH_FROM, CRASH_TO).drop(columns="symbol").set_index("ts")
    m = pd.DataFrame({
        "mid": ob["mid"].resample("1min").last(),
        "spread_bps_max": ob["spread_bps"].resample("1min").max(),
        "depth2_bid": ob["depth_bid_2bps_usd"].resample("1min").median() / 1e6,
        "depth2_ask": ob["depth_ask_2bps_usd"].resample("1min").median() / 1e6,
        "top200": (ob["depth_bid_top200_usd"] + ob["depth_ask_top200_usd"]).resample("1min").median() / 1e6,
        "ofi": ob["ofi"].resample("1min").sum(),
    })
    mark = store.minute_bars(SYMBOL, "mark", CRASH_FROM, CRASH_TO).set_index("ts")["close"]
    m["mark"] = mark.reindex(m.index)
    m = m.dropna(how="all")
    out = {"t": [f"{t:%Y-%m-%d %H:%M}" for t in m.index]}
    for c in m.columns:
        out[c] = [None if pd.isna(v) else round(float(v), 4) for v in m[c]]
    return out


def table(head: list[str], rows: list[list], cls: str = "") -> str:
    th = "".join(f"<th>{html.escape(h)}</th>" for h in head)
    tr = "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{th}</tr></thead><tbody>{tr}</tbody></table>'


def har_table() -> str:
    f = config.DATA_DIR / "reports" / f"har_baseline_{SYMBOL}.csv"
    if not f.exists():
        return "<p>Run scripts/run_har_baseline.py first.</p>"
    t = pd.read_csv(f)
    rows = [[r.model, int(r.horizon_h), f"{r.qlike:.4f}", f"{r.qlike_vs_base_pct:+.1f}%",
             "" if pd.isna(r.dm_p) else f"{r.dm_p:.2g}", "yes" if r.in_MCS_90 else "", int(r.n)]
            for r in t.itertuples()]
    return table(["model", "horizon (h)", "QLIKE (lower is better)", "vs plain HAR", "DM p-value",
                  "in 90% best set", "test hours"], rows, "num")


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>RegimeCast Phase 1</title>
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>
:root{--bg:#fff;--fg:#1d1d1f;--mut:#6e6e73;--line:#e5e5ea;--card:#fafafa}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--fg:#f2f2f7;--mut:#a1a1a6;--line:#2c2c2e;--card:#1c1c1e}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 6px}p{margin:6px 0 12px;color:var(--mut)}
table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line)}th{color:var(--mut);font-weight:600}
table.num td:nth-child(n+3){font-variant-numeric:tabular-nums}
.chart{height:330px;border:1px solid var(--line);border-radius:10px;background:var(--card);margin:10px 0}
.tall{height:520px}.big{font-size:34px;font-weight:700}.row{display:flex;gap:16px;flex-wrap:wrap}
.stat{flex:1 1 200px;border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--card)}
.stat span{color:var(--mut);font-size:13px}
</style></head><body><main>
<h1>RegimeCast, Phase 1: data foundation</h1>
<p>BTC perpetual futures. Generated __GENERATED__ from real data by scripts/phase1_report.py.</p>

<div class="row">
<div class="stat"><div class="big">__DEPTH_BEFORE__ → __DEPTH_AFTER__</div><span>buy-side liquidity within 2 bps of price, 10 Oct 2025: 06:00 vs 21:15 UTC (median)</span></div>
<div class="stat"><div class="big">$__LOW__</div><span>lowest order-book mid price, at __LOW_AT__ UTC</span></div>
<div class="stat"><div class="big">__BEST_1H__ / __BEST_24H__</div><span>best baseline QLIKE (har_s) at 1h / 24h: the bar our model must beat</span></div>
</div>

<h2>1. What data we have</h2>
<p>Everything below is downloaded history, validated and loaded through one module (regimecast/data/store.py).</p>
__COVERAGE__
<p>Trading halts found and treated as missing data (Binance keeps printing frozen candles during a halt):</p>
__HALTS__

<h2>2. First result: HAR volatility baselines</h2>
<p>Walk-forward, out of sample, Jan 2024 to Sep 2025 (the final test period from Oct 2025 is not touched).
QLIKE measures forecast error of the variance; DM p-value tests the difference against plain HAR.</p>
__HAR__

<h2>3. The October 2025 crash in the order book</h2>
<p>Zoomed to the crash night (10 Oct 18:00 to 11 Oct 03:00 UTC). Drag on a chart to zoom, double-click to reset.
The full window (3 to 17 Oct) is loaded, so you can zoom out.</p>
<div id="c1" class="chart tall"></div>
<div id="c2" class="chart"></div>
<div id="c3" class="chart"></div>
</main>
<script>
const D = __DATA__;
const dark = matchMedia('(prefers-color-scheme: dark)').matches;
const base = {margin:{l:60,r:60,t:40,b:90}, paper_bgcolor:'rgba(0,0,0,0)', plot_bgcolor:'rgba(0,0,0,0)',
  font:{color: dark ? '#f2f2f7' : '#1d1d1f'}, legend:{orientation:'h', x:0, y:-0.22},
  xaxis:{range:['__ZOOM_FROM__','__ZOOM_TO__'], gridcolor: dark ? '#2c2c2e' : '#e5e5ea'},
  yaxis:{gridcolor: dark ? '#2c2c2e' : '#e5e5ea'}};
const L = (o) => Object.assign({}, base, o, {xaxis: Object.assign({}, base.xaxis, o.xaxis || {})});
const cfg = {responsive:true, displaylogo:false};
Plotly.newPlot('c1', [
  {x:D.t, y:D.mark, name:'Binance mark price', line:{width:1.5, color:'#2563eb'}},
  {x:D.t, y:D.mid, name:'Bybit order-book mid', line:{width:1, color:'#f59e0b'}},
  {x:D.t, y:D.top200, name:'liquidity in best 200 levels ($M)', yaxis:'y2', line:{width:1, color:'#10b981'}},
], L({title:{text:'Price and order-book liquidity'}, yaxis:{title:'USD'},
      yaxis2:{title:'$M', overlaying:'y', side:'right', showgrid:false}}), cfg);
Plotly.newPlot('c2', [
  {x:D.t, y:D.depth2_bid, name:'bids within 2 bps ($M)', line:{width:1, color:'#10b981'}},
  {x:D.t, y:D.depth2_ask, name:'asks within 2 bps ($M)', line:{width:1, color:'#ef4444'}},
], L({title:{text:'Liquidity right at the price'}, yaxis:{title:'$M'}}), cfg);
Plotly.newPlot('c3', [
  {x:D.t, y:D.spread_bps_max, name:'widest spread in the minute (bps)', line:{width:1, color:'#a855f7'}},
  {x:D.t, y:D.ofi, name:'order-flow imbalance (BTC, + = buying pressure)', yaxis:'y2', line:{width:1, color:'#64748b'}},
], L({title:{text:'Spread and order flow'}, yaxis:{title:'bps'},
      yaxis2:{title:'BTC', overlaying:'y', side:'right', showgrid:false}}), cfg);
</script></body></html>"""


def main() -> None:
    s = crash_series()
    ob = store.orderbook(SYMBOL, "2025-10-10", "2025-10-11").set_index("ts")
    before = ob.loc["2025-10-10 06:00":"2025-10-10 06:15", "depth_bid_2bps_usd"].median() / 1e6
    after = ob.loc["2025-10-10 21:15":"2025-10-10 21:30", "depth_bid_2bps_usd"].median() / 1e6
    low_at = ob["mid"].idxmin()
    har = pd.read_csv(config.DATA_DIR / "reports" / f"har_baseline_{SYMBOL}.csv").set_index(["model", "horizon_h"])
    page = (PAGE.replace("__DATA__", json.dumps(s))
            .replace("__COVERAGE__", table(["data", "dataset", "from", "to"], coverage()))
            .replace("__HALTS__", table(["halt started (UTC)", "length"], halts()))
            .replace("__HAR__", har_table())
            .replace("__DEPTH_BEFORE__", f"${before:.2f}M").replace("__DEPTH_AFTER__", f"${after:.2f}M")
            .replace("__LOW__", f"{ob['mid'].min():,.0f}").replace("__LOW_AT__", f"{low_at:%H:%M:%S}")
            .replace("__BEST_1H__", f"{har.loc[('har_s', 1), 'qlike']:.3f}")
            .replace("__BEST_24H__", f"{har.loc[('har_s', 24), 'qlike']:.3f}")
            .replace("__ZOOM_FROM__", ZOOM_FROM).replace("__ZOOM_TO__", ZOOM_TO)
            .replace("__GENERATED__", f"{pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC"))
    out = config.DATA_DIR / "reports" / "phase1.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print("wrote", out, f"({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
