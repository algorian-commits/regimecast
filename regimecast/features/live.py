"""Live inputs for the feature builder: the same tables as features.build.load_inputs, from
Binance's and Deribit's public APIs instead of the archive.

Running the live data through the SAME feature code as training (build.minute_features,
build.hourly_features) is what keeps live features identical in meaning to the ones the
models learned from; scripts/check_live_features.py compares the two on hours where both
exist. No order book live: the stress model the app uses is trained without it (it added
nothing, Phase 6).

Binance's 5-minute statistics (/futures/data/*) are stamped at the end of the period
(open interest, long/short ratios), except the taker buy/sell ratio, stamped at its start;
``available_at`` follows that. Both were checked hour by hour against the archive.
"""
from __future__ import annotations

import pandas as pd

from regimecast import config

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count",
              "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


def _ms(t) -> int:
    return int(pd.Timestamp(t).timestamp() * 1000)


def _klines(client, path: str, params: dict, minutes: int) -> pd.DataFrame:
    rows, end = [], None
    while len(rows) < minutes:
        p = {**params, "interval": "1m", "limit": 1500}
        if end is not None:
            p["endTime"] = end
        r = client.get(path, params=p)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows = batch + rows
        end = batch[0][0] - 1
    df = pd.DataFrame(rows[-minutes:], columns=KLINE_COLS)
    out = pd.DataFrame({"ts": pd.to_datetime(df["open_time"], unit="ms", utc=True)})
    for c in ("open", "high", "low", "close", "volume", "quote_volume", "taker_buy_volume"):
        out[c] = df[c].astype(float)
    out["count"] = df["count"].astype(int)
    return out.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def _stats(client, path: str, symbol: str, limit: int = 500) -> pd.DataFrame:
    r = client.get(path, params={"symbol": symbol, "period": "5m", "limit": limit})
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    df["available_at"] = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
    return df


def fetch_inputs(symbol: str, minutes: int = 31 * 24 * 60, detail_minutes: int = 180, client=None,
                 last: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """minutes of traded-price candles (for volatility history and stress thresholds);
    detail_minutes of mark/index/premium candles (only the latest hours are needed)."""
    import httpx

    own = client is None
    client = client or httpx.Client(base_url=config.BINANCE_FAPI_URL, timeout=15.0)
    try:
        last = last if last is not None else _klines(client, "/fapi/v1/klines", {"symbol": symbol}, minutes)
        bars = lambda df: df[["ts", "open", "high", "low", "close"]]  # noqa: E731
        mark = bars(_klines(client, "/fapi/v1/markPriceKlines", {"symbol": symbol}, detail_minutes))
        index = bars(_klines(client, "/fapi/v1/indexPriceKlines", {"pair": symbol}, detail_minutes))
        premium = bars(_klines(client, "/fapi/v1/premiumIndexKlines", {"symbol": symbol}, detail_minutes))
        fr = client.get("/fapi/v1/fundingRate", params={"symbol": symbol, "limit": 100})
        fr.raise_for_status()
        f = pd.DataFrame(fr.json())
        funding = pd.DataFrame({"ts": pd.to_datetime(f["fundingTime"].astype("int64"), unit="ms", utc=True),
                                "funding_rate": f["fundingRate"].astype(float), "interval_hours": 8})
        oi = _stats(client, "/futures/data/openInterestHist", symbol)
        top = _stats(client, "/futures/data/topLongShortPositionRatio", symbol)
        glob = _stats(client, "/futures/data/globalLongShortAccountRatio", symbol)
        taker = _stats(client, "/futures/data/takerlongshortRatio", symbol)
        taker["available_at"] += pd.Timedelta(minutes=5)   # this one is stamped at the period START
    finally:
        if own:
            client.close()
    m = oi[["available_at", "sumOpenInterest", "sumOpenInterestValue"]].astype(
        {"sumOpenInterest": float, "sumOpenInterestValue": float})
    m = m.rename(columns={"sumOpenInterest": "sum_open_interest", "sumOpenInterestValue": "sum_open_interest_value"})
    for df, src, dst in ((top, "longShortRatio", "sum_toptrader_long_short_ratio"),
                         (glob, "longShortRatio", "count_long_short_ratio"),
                         (taker, "buySellRatio", "sum_taker_long_short_vol_ratio")):
        m = m.merge(df[["available_at", src]].astype({src: float}).rename(columns={src: dst}),
                    on="available_at", how="outer")
    m = m.sort_values("available_at").reset_index(drop=True)
    m["period_start"] = m["available_at"] - pd.Timedelta(minutes=5)
    m["ts"] = m["period_start"]
    return {"last": last, "mark": mark, "index": index, "premium": premium, "funding": funding,
            "metrics": m, "ob": pd.DataFrame()}


def fetch_dvol(symbol: str, hours: int = 6) -> pd.DataFrame:
    from regimecast.data import dvol

    if symbol not in dvol.CURRENCY:
        return pd.DataFrame()
    now = pd.Timestamp.now(tz="UTC")
    return dvol.fetch(symbol, start=now - pd.Timedelta(hours=hours), end=now)
