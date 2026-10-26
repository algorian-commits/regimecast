"""Live market data (Phase 2): parsing, the few-second cache and the 503 fallback. Offline."""
from datetime import datetime, timezone

import pytest

from regimecast.api import market

PREMIUM = {"symbol": "BTCUSDT", "markPrice": "81447.02135507", "indexPrice": "81487.09239130",
           "estimatedSettlePrice": "81477.2", "lastFundingRate": "0.00006126", "interestRate": "0.0001",
           "nextFundingTime": 1792713600000, "time": 1792706740000}
OI = {"symbol": "BTCUSDT", "openInterest": "95610.532", "time": 1792706735323}
NOW = datetime(2026, 10, 23, tzinfo=timezone.utc)


def test_parse_binance():
    m = market.parse(PREMIUM, OI, NOW)
    assert m.mark_price == pytest.approx(81447.02135507)
    assert m.funding_rate == pytest.approx(0.00006126)
    assert m.open_interest_usdt == pytest.approx(95610.532 * 81447.02135507, rel=1e-9)
    assert m.next_funding_time == "2026-10-23T00:00:00+00:00" and m.exchange_time.endswith("+00:00")
    assert m.stale is False


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_cache_fresh_stale_and_down():
    clock, calls = Clock(), []

    def fetch(sym):
        calls.append(sym)
        if fail:
            raise OSError("binance down")
        return market.parse(PREMIUM, OI, NOW)

    fail = False
    c = market.MarketCache(fetch=fetch, clock=clock)
    assert c.get("BTCUSDT").mark_price > 0 and len(calls) == 1
    clock.t = market.CACHE_SECONDS - 0.1
    c.get("BTCUSDT")
    assert len(calls) == 1  # served from cache
    clock.t = market.CACHE_SECONDS + 0.1
    c.get("BTCUSDT")
    assert len(calls) == 2  # refreshed
    fail = True
    clock.t += market.CACHE_SECONDS + 1
    m = c.get("BTCUSDT")
    assert m is not None and m.stale is True  # Binance down: last value, flagged
    clock.t += market.STALE_SECONDS
    assert c.get("BTCUSDT") is None  # too old to show
    assert c.get("ETHUSDT") is None  # never fetched


def test_market_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    from regimecast.api.main import app
    tc = TestClient(app)
    monkeypatch.setattr(market, "cache", market.MarketCache(fetch=lambda s: market.parse(PREMIUM, OI, NOW)))
    r = tc.get("/market/BTCUSDT")
    assert r.status_code == 200 and r.json()["mark_price"] == pytest.approx(81447.02135507)
    assert tc.get("/market/DOGEUSDT").status_code == 422

    def down(s):
        raise OSError("blocked")
    monkeypatch.setattr(market, "cache", market.MarketCache(fetch=down))
    r = tc.get("/market/BTCUSDT")
    assert r.status_code == 503 and r.json()["status"] == "not_ready"
