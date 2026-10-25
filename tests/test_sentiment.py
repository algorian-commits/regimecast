"""M3 news sentiment tests. Offline, tiny fixtures, no ML libraries needed (models are mocked)."""
import json

import numpy as np
import pandas as pd
import pytest

from regimecast.data import store
from regimecast.sentiment import gdelt, mood, models, pipeline, rss, tagging

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def gkg_line(stamp, url, title, pub=None, tone="-2.5,1,3.5,4.5,20,0,100"):
    extras = (f"<PAGE_PRECISEPUBTIMESTAMP>{pub}</PAGE_PRECISEPUBTIMESTAMP>" if pub else "") + \
             f"<PAGE_TITLE>{title}</PAGE_TITLE>"
    f = [""] * 27
    f[0], f[1], f[2], f[3], f[4], f[15], f[26] = "id", stamp, "1", url.split("/")[2], url, tone, extras
    return "\t".join(f)


# ------------------------------------------------------------------ GDELT
def test_gdelt_parse_keeps_crypto_and_macro_policy_titles():
    text = "\n".join([
        gkg_line("20251010204500", "https://a.com/x", "Bitcoin falls 8% as Trump threatens China", "20251010203000"),
        gkg_line("20251010204500", "https://b.com/y", "Trump announces 100% tariffs on China"),
        gkg_line("20251010204500", "https://c.com/z", "Local team wins the cup"),
        gkg_line("20251010204500", "https://d.com/w", "El sol brilla en Madrid"),  # Spanish "sol", not Solana
        gkg_line("20251010204500", "https://e.com/v", "SOL &amp; ETH rally"),
        "too\tshort",
    ])
    rows = gdelt.parse_gkg(text)
    assert [r["link"] for r in rows] == ["https://a.com/x", "https://b.com/y", "https://e.com/v"]
    assert [r["scope"] for r in rows] == ["crypto", "macro_policy", "crypto"]
    assert rows[0]["seen_at"] == T("2025-10-10 20:45") and rows[0]["published_at"] == T("2025-10-10 20:30")
    assert rows[1]["published_at"] == rows[1]["seen_at"]  # no publish time -> batch time
    assert rows[2]["title"] == "SOL & ETH rally" and rows[0]["gdelt_tone"] == -2.5 and rows[0]["source"] == "a.com"


def test_gdelt_stamps_every_15_minutes_end_exclusive():
    s = gdelt.stamps("2025-10-10 20:05", "2025-10-10 21:00")
    assert s == ["20251010200000", "20251010201500", "20251010203000", "20251010204500"]
    assert len(gdelt.stamps("2025-05-01", "2025-05-02")) == 96


def test_from_gdelt_caps_local_time_publish_stamps():
    raw = pd.DataFrame({"seen_at": [T("2025-10-10 20:00")], "published_at": [T("2025-10-10 21:42")],
                        "title": ["x"], "link": ["l"], "source": ["s"], "gdelt_tone": [0.0], "scope": ["crypto"]})
    h = pipeline.from_gdelt(raw)
    assert h.loc[0, "published_at"] == h.loc[0, "known_at"] == h.loc[0, "fetched_at"] == T("2025-10-10 20:00")


# ------------------------------------------------------------------ tagging
@pytest.mark.parametrize("title,coins", [
    ("Bitcoin tops $120k", "BTC"),
    ("BTC and ETH ETFs see inflows", "BTC,ETH"),
    ("Solana ETF approved", "SOL"),
    ("Ether slides as SOL jumps", "ETH,SOL"),
    ("Crypto market loses $200 billion", "general"),
    ("XRP surges after court ruling", "other"),
    ("Stablecoin bill passes Senate", "general"),
])
def test_coin_tags(title, coins):
    assert tagging.coin_tag(title) == coins


@pytest.mark.parametrize("title,scope,cat", [
    ("Bitcoin plunges after Trump's 100% China tariff", "crypto", "tariffs"),
    ("Treasury sanctions crypto exchange Garantex", "crypto", "sanctions"),
    ("SEC sues Binance", "crypto", "regulation"),
    ("Bitcoin hits record high", "crypto", ""),
    ("EU lawmakers back new sanctions on Russia", "macro_policy", "sanctions"),
    ("Senate passes tariff bill", "macro_policy", "tariffs"),
])
def test_policy_category(title, scope, cat):
    assert tagging.policy_category(title, scope) == cat


def test_keys_ignore_source_suffix_case_and_url_noise():
    assert tagging.title_key("Bitcoin Falls 5%! - CoinDesk") == tagging.title_key("bitcoin falls 5%")
    assert tagging.link_key("https://www.CoinDesk.com/a/b/?utm=x#top") == tagging.link_key("http://coindesk.com/a/b")


def _rows(items):
    df = pd.DataFrame(items, columns=["known_at", "title", "link"])
    df["known_at"] = pd.to_datetime(df["known_at"], utc=True, format="ISO8601")
    df["published_at"] = df["known_at"]
    df["scope"] = "crypto"
    return tagging.tag(df)


def test_dedupe_same_link_near_title_and_window():
    df = _rows([
        ("2025-10-10 10:00", "Bitcoin drops below $110,000 as liquidations mount", "https://a.com/1"),
        ("2025-10-10 10:15", "Bitcoin drops below $110,000 as liquidations mount - Yahoo", "https://b.com/2"),
        ("2025-10-10 10:20", "Bitcoin drops below $110,000 as liquidations mount fast", "https://c.com/3"),
        ("2025-10-10 10:30", "Totally different headline about ether", "https://a.com/1?ref=rss"),
        ("2025-10-10 11:00", "Bitcoin drops below $100,000 for the first time since June", "https://d.com/4"),
        ("2025-10-20 10:00", "Bitcoin drops below $110,000 as liquidations mount", "https://e.com/5"),
    ])
    out = tagging.dedupe(df.sample(frac=1, random_state=3))  # order must not matter
    assert out["link"].tolist() == ["https://a.com/1", "https://d.com/4", "https://e.com/5"]
    assert out["copies"].tolist() == [3, 1, 1]  # 10 days later is a new story, not a copy
    # rows already stored win
    again = tagging.dedupe(df.iloc[[1]], seen=df.iloc[[0]])
    assert again.empty


def test_stale_flag():
    df = _rows([("2025-10-10 10:00", "Bitcoin up", "l")])
    df["published_at"] = T("2025-10-08 10:00")
    assert tagging.tag(df)["stale"].all()


# ------------------------------------------------------------------ models (mocked)
def test_score_maps_probabilities_and_batches():
    calls = []

    def fake(titles):
        calls.append(len(titles))
        return np.array([[0.7, 0.1, 0.2] if "up" in t else [0.1, 0.8, 0.1] for t in titles])

    s = models.score(["btc up", "btc down", "eth up"], fake, batch_size=2)
    assert calls == [2, 1]
    assert s["label"].tolist() == ["positive", "negative", "positive"]
    assert s["score"].round(2).tolist() == [0.6, -0.7, 0.6] and s["conf"].round(2).tolist() == [0.7, 0.8, 0.7]


# ------------------------------------------------------------------ mood
def _tagged(items):
    """items: (known_at, title, scope)"""
    df = pd.DataFrame(items, columns=["known_at", "title", "scope"])
    df["known_at"] = pd.to_datetime(df["known_at"], utc=True, format="ISO8601")
    df["published_at"] = df["known_at"]
    df["link"] = [f"https://x.com/{i}" for i in range(len(df))]
    return tagging.tag(df)


def _scores(h, neg_words=("falls", "crash")):
    s = h[["title_key"]].drop_duplicates().copy()
    neg = s["title_key"].str.contains("|".join(neg_words))
    s["score"] = np.where(neg, -0.8, 0.5)
    s["label"] = np.where(neg, "negative", "positive")
    return s


def test_mood_hour_end_labelling_and_coin_rules():
    h = _tagged([
        ("2025-10-10 10:20", "Bitcoin falls 3%", "crypto"),          # -> 11:00, BTC
        ("2025-10-10 10:59:59", "Crypto market rises", "crypto"),    # -> 11:00, all coins
        ("2025-10-10 11:00", "Ether rises", "crypto"),               # -> 12:00 (exactly on the hour), ETH only
        ("2025-10-10 11:30", "XRP rises", "crypto"),                 # other coin: nobody
        ("2025-10-10 12:10", "Trump threatens 100% tariffs on China", "macro_policy"),  # -> 13:00 policy, all
        ("2025-10-10 12:40", "SEC crackdown: bitcoin crash", "crypto"),  # -> 13:00 BTC, regulation
    ])
    sc = _scores(h)
    btc = mood.hourly(h, sc, "BTCUSDT").set_index("ts")
    assert btc.index.tolist() == [T("2025-10-10 11:00"), T("2025-10-10 12:00"), T("2025-10-10 13:00")]
    assert btc.loc[T("2025-10-10 11:00"), "count"] == 2
    assert btc.loc[T("2025-10-10 11:00"), "mean_score"] == pytest.approx((-0.8 + 0.5) / 2)
    assert btc.loc[T("2025-10-10 11:00"), "share_negative"] == 0.5
    assert btc.loc[T("2025-10-10 12:00"), "count"] == 0 and pd.isna(btc.loc[T("2025-10-10 12:00"), "mean_score"])
    assert btc.loc[T("2025-10-10 13:00"), "policy_shock_count"] == 2
    eth = mood.hourly(h, sc, "ETHUSDT").set_index("ts")
    assert eth.loc[T("2025-10-10 12:00"), "count"] == 1 and eth.loc[T("2025-10-10 13:00"), "policy_shock_count"] == 1


def test_mood_never_uses_future_headlines():
    """Adding headlines after hour end t must not change the row for t."""
    base = [("2025-10-10 10:20", "Bitcoin falls", "crypto"), ("2025-10-10 10:40", "Bitcoin rises", "crypto")]
    later = base + [("2025-10-10 11:00", "Bitcoin crash", "crypto"), ("2025-10-10 13:05", "Bitcoin falls", "crypto")]
    h1, h2 = _tagged(base), _tagged(later)
    a = mood.hourly(h1, _scores(h1), "BTCUSDT").set_index("ts").loc[T("2025-10-10 11:00")]
    b = mood.hourly(h2, _scores(h2), "BTCUSDT").set_index("ts").loc[T("2025-10-10 11:00")]
    pd.testing.assert_series_equal(a, b)


def test_mood_skips_stale_headlines():
    h = _tagged([("2025-10-10 10:20", "Bitcoin falls", "crypto"), ("2025-10-10 10:30", "Bitcoin rises", "crypto")])
    h.loc[1, "stale"] = True
    assert mood.hourly(h, _scores(h), "BTCUSDT")["count"].tolist() == [1]


# ------------------------------------------------------------------ RSS
RSS_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Bitcoin slips as tariffs bite</title><link>https://site.com/a</link>
<pubDate>Fri, 10 Oct 2025 17:20:00 -0400</pubDate><description>d</description></item>
<item><title>Ether ETF news</title><link>https://site.com/b</link>
<pubDate>Fri, 10 Oct 2025 23:59:00 +0000</pubDate></item>
<item><title></title><link>https://site.com/c</link></item>
</channel></rss>"""


def test_rss_parse_utc_and_known_time_never_after_fetch():
    pytest.importorskip("feedparser")
    fetched = T("2025-10-10 22:00")
    df = rss.parse_feed(RSS_XML, "site.com", fetched)
    assert df["title"].tolist() == ["Bitcoin slips as tariffs bite", "Ether ETF news"]
    assert df.loc[0, "published_at"] == T("2025-10-10 21:20")  # -0400 converted to UTC
    assert df.loc[0, "known_at"] == T("2025-10-10 21:20")
    assert df.loc[1, "known_at"] == fetched  # stamped in the future -> fetch time
    assert (df["scope"] == "crypto").all() and (df["origin"] == "rss").all()


# ------------------------------------------------------------------ pipeline + store + API
def _fake_probs(titles):
    return np.array([[0.1, 0.8, 0.1] if ("falls" in t or "crash" in t) else [0.6, 0.1, 0.3] for t in titles])


def test_pipeline_end_to_end_store_and_api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from regimecast.api.main import app
    monkeypatch.setattr(store.config, "DATA_DIR", tmp_path)
    c = TestClient(app)
    r = c.get("/news-mood", params={"symbol": "BTCUSDT"})
    assert r.status_code == 503 and r.json()["status"] == "not_ready"

    raw = pd.DataFrame({
        "seen_at": [T("2025-10-10 10:15"), T("2025-10-10 10:30"), T("2025-10-10 10:30"), T("2025-10-10 12:00")],
        "published_at": [T("2025-10-10 10:05"), T("2025-10-10 10:30"), T("2025-10-10 10:30"), T("2025-10-10 12:00")],
        "title": ["Bitcoin falls 4%", "Bitcoin falls 4% - Reuters", "Crypto market crash deepens", "Ether rises"],
        "link": ["https://a.com/1", "https://b.com/2", "https://c.com/3", "https://d.com/4"],
        "source": ["a.com", "b.com", "c.com", "d.com"], "gdelt_tone": [-3.0, -3.0, -5.0, 2.0],
        "scope": ["crypto"] * 4})
    assert pipeline.add_headlines(pipeline.from_gdelt(raw)) == 3
    assert pipeline.add_headlines(pipeline.from_gdelt(raw)) == 0  # running twice adds nothing
    h = store.headlines()
    assert len(h) == 3 and h["copies"].tolist() == [2, 1, 1]
    assert pipeline.score_new("finbert", _fake_probs) == 3
    assert pipeline.score_new("finbert", _fake_probs) == 0  # already scored
    pipeline.build_mood("finbert", until=T("2025-10-10 13:00"))

    m = store.news_mood("BTCUSDT")
    assert m["ts"].tolist() == [T("2025-10-10 11:00"), T("2025-10-10 12:00"), T("2025-10-10 13:00")]
    assert m["count"].tolist() == [2, 0, 0]  # "Ether rises" known at 12:00 -> hour ending 13:00, ETH only

    r = c.get("/news-mood", params={"symbol": "ETHUSDT", "hours": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "ETHUSDT" and json.loads(json.dumps(body))  # plain JSON, no NaN
    assert [x["hour_end"] for x in body["hours"]] == ["2025-10-10T12:00:00+00:00", "2025-10-10T13:00:00+00:00"]
    assert body["hours"][0] == {"hour_end": "2025-10-10T12:00:00+00:00", "count": 0, "mean_score": 0.0,
                                "share_negative": 0.0, "policy_shock_count": 0}
    assert body["hours"][1]["count"] == 1 and body["hours"][1]["mean_score"] == 0.5
    assert c.get("/news-mood", params={"symbol": "BTCUSDT", "hours": 0}).status_code == 422


# ------------------------------------------------------------------ evaluation
from regimecast.sentiment import evaluate as ev  # noqa: E402


def test_accuracy_and_confusion():
    t = pd.Series(["positive", "negative", "neutral", "negative"])
    p = pd.Series(["positive", "neutral", "neutral", "negative"])
    a = ev.accuracy(t, p)
    assert a["accuracy"] == 0.75 and a["recall_negative"] == 0.5 and a["precision_neutral"] == 0.5
    assert a["macro_f1"] == pytest.approx((1 + 2 / 3 + 2 / 3) / 3)
    assert ev.confusion(t, p).loc["negative", "neutral"] == 1


def test_hourly_close_is_labelled_by_hour_end():
    bars = pd.DataFrame({"ts": pd.date_range("2025-10-10 10:00", periods=120, freq="1min", tz="UTC"),
                         "close": np.arange(120.0)})
    c = ev.hourly_close(bars)
    assert c[T("2025-10-10 11:00")] == 59 and c[T("2025-10-10 12:00")] == 119


def test_abnormal_and_big_moves_use_only_the_past():
    rng = np.random.default_rng(0)
    idx = pd.date_range("2025-05-01", periods=2000, freq="h", tz="UTC")
    x = pd.Series(rng.normal(size=2000), idx)
    y = x.copy()
    y.iloc[1500:] += 100  # change the future
    pd.testing.assert_series_equal(ev.abnormal(x).iloc[:1500], ev.abnormal(y).iloc[:1500])

    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.003, 2000))), idx)
    close.iloc[1000:] *= 0.95   # -5% in one hour: ~17 sigma
    close.iloc[1001:] *= 0.99   # follow-through next hour: same move, not a new event
    close.iloc[1500:] *= 1.04
    m = ev.big_moves(close)
    assert m["ts"].tolist() == [idx[1000], idx[1500]] and m["direction"].tolist() == ["down", "up"]
    close2 = close.copy()
    close2.iloc[1800:] *= 0.5
    assert ev.big_moves(close2)["ts"].tolist()[:2] == [idx[1000], idx[1500]]


def test_event_study_and_lead_lag_find_a_lead():
    idx = pd.date_range("2025-05-01", periods=500, freq="h", tz="UTC")
    events = pd.Series(idx[[100, 200, 300, 400]])
    f = pd.Series(0.0, idx)
    f[events - pd.Timedelta(hours=1)] = 5.0  # mood jumps the hour before each move
    es = ev.event_study(f.to_frame("count_z"), events, lags=[-1, 0, 1], n_boot=100)
    got = es.set_index("lag_hours")["mean"]
    assert got[-1] == 5 and got[0] == 0 and got[1] == 0
    y = pd.Series(0.0, idx)
    y[events] = 1.0
    ll = ev.lead_lag(f, y, lags=[-1, 0, 1]).set_index("lag_hours")["corr"]
    assert ll[-1] == pytest.approx(1.0) and abs(ll[1]) < 0.1


def test_predictive_test_runs():
    rng = np.random.default_rng(1)
    idx = pd.date_range("2025-05-01", periods=600, freq="h", tz="UTC")
    f = pd.Series(rng.normal(size=600), idx)
    absret = (0.01 + 0.004 * f.shift(1).fillna(0)).abs() + rng.normal(0, 0.001, 600)
    out = ev.predictive_test(f.to_frame("count_z"), pd.Series(absret, idx))
    assert out.loc[0, "p_value"] < 0.01 and out.loc[0, "r2_with"] > out.loc[0, "r2_without"]


def test_source_outage_hours_are_dropped_not_zero():
    items = [(f"2025-06-14 {h:02d}:10", "Bitcoin rises", "crypto") for h in range(0, 24)]
    items += [(f"2025-06-15 {h:02d}:10", "Bitcoin rises", "crypto") for h in range(12, 14)]  # 12h silence before
    items += [("2025-06-15 05:10", "Bitcoin rises", "crypto")]  # 5 h silent before this: a quiet spell, kept
    h = _tagged(items)
    gaps = mood.outage_hours(h["known_at"])
    assert len(gaps) == 6 and gaps.min() == T("2025-06-15 07:00") and gaps.max() == T("2025-06-15 12:00")
    m = mood.hourly(h, _scores(h), "BTCUSDT", drop_hours=gaps)
    assert T("2025-06-15 03:00") in set(m["ts"]) and T("2025-06-15 08:00") not in set(m["ts"])
