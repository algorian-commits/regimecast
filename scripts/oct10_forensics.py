"""The 10 October 2025 crash on one timeline (Phase 6, descriptive only).

    python scripts/oct10_forensics.py        # data/reports/oct10_forensics.html and .csv

Minute by minute for BTCUSDT, 18:00 to 23:00 UTC: mark price, Bybit order-book depth and
spread, open interest, our stress-event flags, tariff headlines as they reached our news
feed, and the hourly stress probability of the model frozen on 30 Sep 2025 (fitted on the
development period only; shown, never scored or tuned on: the final test period stays
closed). Reported events (whale short, the post) come from press coverage, cited on the page.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import json
import warnings

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.features import build
from regimecast.models import harness, stress as S

warnings.filterwarnings("ignore")
SYM = "BTCUSDT"
A, B = pd.Timestamp("2025-10-10 18:00", tz="UTC"), pd.Timestamp("2025-10-10 23:00", tz="UTC")
REPORTED = [  # time (UTC), what, source
    ("2025-10-10 20:49", "last large BTC/ETH short opened on Hyperliquid (reported)",
     "https://www.theblock.co/post/374290/hyperliquid-whale-who-made-150-million-with-short-bet-opens-new-160-million-short"),
    ("2025-10-10 20:50", "Trump posts the 100% tariff on China (reported)",
     "https://www.theblock.co/post/374290/hyperliquid-whale-who-made-150-million-with-short-bet-opens-new-160-million-short"),
]


def timeline() -> pd.DataFrame:
    mark = store.minute_bars(SYM, "mark", A - pd.Timedelta(minutes=1), B)
    t = pd.DataFrame(index=pd.date_range(A, B, freq="1min"))
    m = mark.set_index("ts")
    m.index = m.index + pd.Timedelta(minutes=1)                       # known at the candle's close
    t["mark"], t["mark_low"] = m["close"], m["low"]
    ob = store.orderbook(SYM, A - pd.Timedelta(minutes=1), B).set_index("ts")
    g = ob.resample("1min", label="right", closed="right")
    t["bid_depth_2bps_usd_min"] = g["depth_bid_2bps_usd"].min()
    t["ask_depth_2bps_usd_min"] = g["depth_ask_2bps_usd"].min()
    t["spread_bps_max"] = g["spread_bps"].max()
    met = store.metrics(SYM, A - pd.Timedelta(hours=1), B).set_index("available_at")["sum_open_interest_value"]
    t["open_interest_usd"] = met.reindex(t.index, method="ffill")
    last = store.minute_bars(SYM, "last", A - pd.Timedelta(days=31), B)   # 30 days for the stress threshold
    close = last.drop_duplicates("ts").set_index("ts")["close"]
    close.index = close.index + pd.Timedelta(minutes=1)
    r = np.log(close).diff()
    t["stress_event_minute"] = build.stress_minutes((r ** 2).rolling(15, min_periods=12).sum()).reindex(t.index)
    news = store.headlines(A, B)
    tariff = news[news["policy"] == "tariffs"].set_index("known_at")
    t["tariff_headlines_15m"] = tariff.resample("15min").size().reindex(t.index, method="ffill").fillna(0)
    hundred = news[news["title"].str.contains("100%|100 percent", case=False, na=False) & (news["known_at"] >= A)]
    first_100 = hundred["known_at"].min()
    # hourly stress probability from the model frozen at the end of the development period
    h, tg = store.features(SYM, "1h"), store.targets(SYM)
    reg = pd.read_parquet(config.DATA_DIR / "reports" / f"regimes_base_{SYM}.parquet")
    dv = config.parquet_dir() / "dvol" / SYM / "all.parquet"
    X, y = S.dataset(h, tg, reg, pd.read_parquet(dv) if dv.exists() else None)
    dev = X.index <= harness.holdout_start() - pd.Timedelta(hours=1)
    model = S.StressGBM().fit(X[dev], y[dev])
    p = model.predict(X.loc[A - pd.Timedelta(hours=1): B])
    t["p_stress_next_hour_frozen"] = p.reindex(t.index, method="ffill")
    t["regime_p_stressed_frozen"] = reg["p_stressed"].reindex(t.index, method="ffill")
    t.attrs["first_100pct_headline"] = str(first_100)
    return t


def page(t: pd.DataFrame) -> str:
    x = [i.isoformat() for i in t.index]
    events = [(pd.Timestamp(ts, tz="UTC"), what) for ts, what, _ in REPORTED]
    events.append((pd.Timestamp(t.attrs["first_100pct_headline"]), "first '100% tariff' headline in our news feed"))
    onset = t.index[(t["mark_low"] < t["mark"].cummax() * 0.95)].min()
    events.append((onset, "BTC mark price 5% below its 18:00-onward high"))
    shapes = [{"type": "line", "x0": e.isoformat(), "x1": e.isoformat(), "yref": "paper", "y0": 0, "y1": 1,
               "line": {"dash": "dot", "width": 1, "color": "#555"}} for e, _ in events]
    ann = [{"x": e.isoformat(), "y": 1 - 0.07 * i, "yref": "paper", "text": w, "showarrow": False, "xanchor": "left",
            "font": {"size": 10}, "bgcolor": "rgba(255,255,255,0.7)"} for i, (e, w) in enumerate(events)]
    def series(col, name, axis, color, fill=False):
        d = {"type": "scatter", "x": x, "y": t[col].round(4).where(t[col].notna(), None).tolist(), "name": name,
             "xaxis": "x", "yaxis": axis, "line": {"color": color, "width": 1.3}}
        if fill:
            d["fill"] = "tozeroy"
        return d
    data = [series("mark", "BTC mark price", "y", "#222"),
            series("bid_depth_2bps_usd_min", "bids within 0.02% (USD, min per minute)", "y2", "#2e6fd0", True),
            series("spread_bps_max", "spread (bps, max per minute)", "y3", "#e0a020"),
            series("open_interest_usd", "open interest (USD)", "y4", "#7a4fd0"),
            series("tariff_headlines_15m", "tariff headlines per 15 min (our feed)", "y5", "#888"),
            series("p_stress_next_hour_frozen", "P(stress next hour), frozen model", "y6", "#d0393b", True),
            series("stress_event_minute", "stress-event minute", "y6", "#000")]
    lay = {"height": 1100, "showlegend": True, "legend": {"orientation": "h"}, "shapes": shapes, "annotations": ann,
           "grid": {"rows": 6, "columns": 1, "shared_xaxes": True, "pattern": "independent"},
           **{f"yaxis{'' if i == 1 else i}": {"domain": [1 - i / 6 + 0.02, 1 - (i - 1) / 6], "title": tt}
              for i, tt in enumerate(["price", "bid depth", "spread", "OI", "news", "stress"], start=1)},
           "xaxis": {"anchor": "y6"}}
    src = "".join(f'<li>{html_escape(w)}: <a href="{u}">{u}</a></li>' for _, w, u in REPORTED)
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>10 October 2025, minute by minute</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"></script>
<style>body{{font-family:system-ui,sans-serif;max-width:1100px;margin:24px auto;padding:0 16px;color:#222}}p.note{{color:#555}}</style>
</head><body><h1>10 October 2025: the crash, minute by minute (BTCUSDT, UTC)</h1>
<p class="note">Descriptive case study. The stress probability comes from the model frozen on 30 Sep 2025 (fitted on
the development period only); the October 2025 crash is in the closed final test period, so nothing here is scored or
tuned on. Order book: Bybit, rebuilt from its history files. News: our GDELT-based feed (15-minute batches).</p>
<div id="tl"></div>
<h2>Sequence</h2><ul>{"".join(f"<li><b>{e:%H:%M}</b> {html_escape(w)}</li>" for e, w in sorted(events))}</ul>
<p class="note">Reading it: a large short was placed one minute before the post; the order book emptied within five
minutes of the post, before the liquidation cascade; our news feed only had the headline about 40 minutes after the
post. For the app, the order book is the earliest usable signal; headlines are too slow.</p>
<h2>Sources for reported events</h2><ul>{src}</ul>
<script>Plotly.newPlot("tl", {json.dumps(data)}, {json.dumps(lay)}, {{responsive: true}});</script></body></html>"""


def html_escape(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


if __name__ == "__main__":
    t = timeline()
    out = config.DATA_DIR / "reports"
    t.to_csv(out / "oct10_forensics.csv")
    (out / "oct10_forensics.html").write_text(page(t), encoding="utf-8")
    w = t.loc["2025-10-10 20:40":"2025-10-10 21:40"]
    print(w.iloc[::5][["mark", "bid_depth_2bps_usd_min", "spread_bps_max", "open_interest_usd", "tariff_headlines_15m",
                       "stress_event_minute", "p_stress_next_hour_frozen"]].round(3).to_string())
    print("first 100% headline:", t.attrs["first_100pct_headline"])
    print("wrote", out / "oct10_forensics.html")
