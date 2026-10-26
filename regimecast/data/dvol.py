"""Deribit DVOL: the options market's 30-day implied volatility for BTC and ETH.

DVOL is an annualised volatility in percent (like a VIX for crypto), published by
Deribit from options prices; history starts in March 2021. SOL has no DVOL.

Stored hourly as data/parquet/dvol/<SYMBOL>/all.parquet with ``ts`` = the candle's
OPEN time (Deribit's label). The close of the candle opened at H-1h is known at H, so
features join on ``ts + 1h`` (features/build.py does this).

    python -m regimecast.data.dvol            # BTC and ETH, full history
"""
from __future__ import annotations

import time

import pandas as pd

from regimecast import config

URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
CURRENCY = {"BTCUSDT": "BTC", "ETHUSDT": "ETH"}
START = pd.Timestamp("2021-03-01", tz="UTC")


def fetch(symbol: str, start: pd.Timestamp = START, end: pd.Timestamp | None = None, client=None) -> pd.DataFrame:
    """Hourly DVOL candles for one symbol between start and end (paged backwards)."""
    import httpx

    end = end or pd.Timestamp.now(tz="UTC")
    own = client is None
    client = client or httpx.Client(timeout=30)
    rows, cursor = [], int(end.timestamp() * 1000)
    try:
        while True:
            r = client.get(URL, params={"currency": CURRENCY[symbol], "start_timestamp": int(start.timestamp() * 1000),
                                        "end_timestamp": cursor, "resolution": 3600})
            r.raise_for_status()
            res = r.json()["result"]
            if not res["data"]:
                break
            rows.extend(res["data"])
            nxt = res.get("continuation")
            if not nxt or nxt >= cursor:
                break
            cursor = nxt
            time.sleep(0.1)  # be polite to Deribit
    finally:
        if own:
            client.close()
    df = pd.DataFrame(rows, columns=["ms", "open", "high", "low", "close"]).drop_duplicates("ms")
    df.insert(0, "ts", pd.to_datetime(df.pop("ms"), unit="ms", utc=True))
    return df.sort_values("ts").reset_index(drop=True)


def save(symbol: str) -> int:
    df = fetch(symbol)
    out = config.parquet_dir() / "dvol" / symbol
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "all.parquet", index=False)
    return len(df)


if __name__ == "__main__":
    for s in CURRENCY:
        print(s, save(s), "hours")
