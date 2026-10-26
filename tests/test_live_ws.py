"""Live dashboard feed (Phase 8, M5): Binance messages -> snapshot, the WebSocket, regime candles. Offline."""
import asyncio
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from regimecast.api import live_ws, main
from regimecast.vol import live

# Real message shapes from wss://fstream.binance.com/{market,public}/stream (26 Oct 2026)
MARK = {"stream": "btcusdt@markPrice@1s", "data": {"e": "markPriceUpdate", "E": 1793043926001, "s": "BTCUSDT",
        "p": "80029.03990255", "ap": "80029.03990255", "P": "80043.18069852", "i": "80049.57391304",
        "r": "0.00010000", "T": 1793059200000, "st": 1}}
KLINE = {"stream": "btcusdt@kline_1h", "data": {"e": "kline", "E": 1793043953760, "s": "BTCUSDT", "k": {
         "t": 1793041200000, "T": 1793044799999, "s": "BTCUSDT", "i": "1h", "o": "79958.90", "c": "80039.50",
         "h": "80090.00", "l": "79867.30", "v": "2273.712", "x": False}}}
DEPTH = {"stream": "btcusdt@depth20@500ms", "data": {"e": "depthUpdate", "E": 1793043931767, "T": 1793043931766,
         "s": "BTCUSDT", "b": [["80000.0", "2"], ["79999.0", "1"], ["79990.0", "0"]],
         "a": [["80001.0", "1"], ["80020.0", "1"]]}}


class Clock:
    t = 1793043960.0

    def __call__(self):
        return self.t


def test_book_summary():
    b = live_ws.book_summary(DEPTH["data"]["b"], DEPTH["data"]["a"])
    mid = 80000.5
    assert b["bid"] == 80000 and b["ask"] == 80001 and b["mid"] == mid
    assert b["spread_bps"] == pytest.approx(1 / mid * 1e4)
    assert b["bid_depth_usdt"] == pytest.approx(2 * 80000 + 79999)   # the empty level is ignored
    assert b["ask_depth_usdt"] == pytest.approx(80001 + 80020)
    # within 2 bps of the mid (about 16 USDT): 80000, 79999 and 80001, not 80020
    assert b["depth_2bps_usdt"] == pytest.approx(2 * 80000 + 79999 + 80001)
    assert b["imbalance"] == pytest.approx((239999 - 160021) / (239999 + 160021))
    assert b["levels"] == 2 and b["reach_bps"] == pytest.approx(1.5 / mid * 1e4)
    assert live_ws.book_summary([], DEPTH["data"]["a"]) is None


def test_state_folds_messages_and_flags_stale_coins():
    clock = Clock()
    st = live_ws.LiveState(["BTCUSDT", "ETHUSDT"], clock=clock)
    for m in (MARK, KLINE, DEPTH, {"stream": "x", "data": {"e": "markPriceUpdate", "s": "DOGEUSDT"}}):
        st.apply(m)
    st.upstream = {"market": True, "public": True}
    snap = st.snapshot()
    btc = snap["coins"]["BTCUSDT"]
    assert snap["type"] == "tick" and snap["upstream"] == "ok" and set(snap["coins"]) == {"BTCUSDT", "ETHUSDT"}
    assert btc["mark_price"] == pytest.approx(80029.0399) and btc["funding_rate"] == 0.0001
    assert btc["candle"] == {"t": "2026-10-26T19:00:00+00:00", "open": 79958.9, "high": 80090.0, "low": 79867.3,
                             "close": 80039.5, "volume": 2273.712, "closed": False}
    assert btc["book"]["bid"] == 80000 and btc["stale"] is False
    assert snap["coins"]["ETHUSDT"] == {"stale": True}   # nothing received yet
    clock.t += live_ws.STALE_AFTER_S + 1
    st.upstream["public"] = False
    snap = st.snapshot()
    assert snap["upstream"] == "reconnecting" and snap["coins"]["BTCUSDT"]["stale"] is True


def test_stream_urls_use_binances_market_and_public_routes():
    u = live_ws.stream_url("wss://fstream.binance.com", "market", ["BTCUSDT", "ETHUSDT"], live_ws.MARKET_STREAMS)
    assert u == ("wss://fstream.binance.com/market/stream?streams="
                 "btcusdt@markPrice@1s/btcusdt@kline_1h/ethusdt@markPrice@1s/ethusdt@kline_1h")


def test_hub_reconnects_after_binance_drops():
    connects = []

    class FakeWS:
        def __init__(self, msgs):
            self.msgs = msgs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.msgs:
                raise ConnectionError("binance closed the stream")
            return json.dumps(self.msgs.pop(0))

    def connect(url, **kw):
        connects.append(url)
        return FakeWS([MARK] if len(connects) == 1 else [KLINE])

    async def run():
        hub = live_ws.Hub(live_ws.LiveState(["BTCUSDT"]), connect=connect, base_url="wss://x")
        task = asyncio.create_task(hub._run("market", live_ws.MARKET_STREAMS))
        for _ in range(400):
            await asyncio.sleep(0.01)
            if "candle" in hub.state.coins["BTCUSDT"]:
                break
        task.cancel()
        return hub

    hub = asyncio.run(run())
    c = hub.state.coins["BTCUSDT"]
    assert len(connects) >= 2 and "mark_price" in c and "candle" in c   # second connection after the drop
    assert connects[0].startswith("wss://x/market/stream?streams=btcusdt@markPrice@1s")


def test_websocket_sends_snapshots(monkeypatch):
    st = live_ws.LiveState(["BTCUSDT"])
    st.apply(MARK)
    hub = live_ws.Hub(st)
    hub.autostart = False
    monkeypatch.setattr(live_ws, "hub", hub)
    monkeypatch.setattr(live_ws, "SEND_EVERY_S", 0.01)
    with TestClient(main.app).websocket_connect("/ws/live") as ws:
        a, b = ws.receive_json(), ws.receive_json()
    assert a["type"] == b["type"] == "tick" and a["coins"]["BTCUSDT"]["mark_price"] == pytest.approx(80029.0399)


# ------------------------------------------------------------------ regime-coloured candles
NOW = pd.Timestamp("2026-10-26 14:37:20", tz="UTC")


def bars(days=31, seed=0, sd=0.0008):
    rng = np.random.default_rng(seed)
    ts = pd.date_range(end=NOW.floor("1min"), periods=days * 1440, freq="1min")
    close = 80_000 * np.exp(np.cumsum(rng.normal(0, sd, len(ts))))
    return pd.DataFrame({"ts": ts, "open": close, "high": close * 1.0001, "low": close * 0.9999, "close": close,
                         "volume": 1.0})


def test_hourly_candles_complete_hours_and_regime_at_close():
    from regimecast.models import regimes

    model = regimes.load_live()["BTCUSDT"]
    b = bars()
    hist = live.regime_history(b, model, NOW)
    c = live.hourly_candles(b, NOW, hist)
    assert c[-1]["t"] == "2026-10-26T13:00:00+00:00"          # 14:00-14:37 is still open
    one = b[(b["ts"] >= "2026-10-26 13:00") & (b["ts"] < "2026-10-26 14:00")]
    assert c[-1]["open"] == one["open"].iloc[0] and c[-1]["close"] == one["close"].iloc[-1]
    assert c[-1]["high"] == one["high"].max() and c[-1]["volume"] == 60
    end = pd.Timestamp("2026-10-26 14:00", tz="UTC")             # the 13:00 candle closes at 14:00
    assert c[-1]["regime"] == regimes.NAMES[int(hist.at[end, "regime"])]
    assert c[0]["regime"] is None                                 # first day: no 24 h average yet
    assert live.regime_from_bars(b, model, NOW)["regime"] == c[-1]["regime"]
    # no look-ahead: a wild move after 10:00 changes nothing about the candles before it
    cut = pd.Timestamp("2026-10-26 10:00", tz="UTC")
    wild = b.copy()
    wild.loc[wild["ts"] >= cut, "close"] *= np.exp(np.cumsum(np.random.default_rng(5).normal(0, 0.01, (wild["ts"] >= cut).sum())))
    c2 = live.hourly_candles(wild, NOW, live.regime_history(wild, model, NOW))
    before = [x for x in c if x["t"] < "2026-10-26T10:00"]
    assert [x for x in c2 if x["t"] < "2026-10-26T10:00"] == before


def test_candles_endpoint(monkeypatch):
    cache = live.ForecastCache(symbols=["BTCUSDT"], fetch=lambda s: bars())
    cache.refresh_all()   # the stress gauge needs more live inputs and fails here; candles do not depend on it
    monkeypatch.setattr(main.vol_live, "cache", cache)
    c = TestClient(main.app)
    r = c.get("/candles", params={"symbol": "BTCUSDT", "hours": 48}).json()
    assert len(r["candles"]) == 48 and r["candles"][-1]["regime"] in ("calm", "active", "stressed")
    monkeypatch.setattr(main.vol_live, "cache", live.ForecastCache(symbols=["BTCUSDT"], fetch=None, models={}))
    r = c.get("/candles", params={"symbol": "BTCUSDT"})
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
