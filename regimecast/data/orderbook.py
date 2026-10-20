"""Order-book features from Bybit's free order-book history.

Bybit publishes one file per symbol per day: a full snapshot followed by an
update every ~100 ms (JSON lines). Raw files are ~150-350 MB zipped per coin
per day, far too big to keep, so each day is replayed once into 1-second
features and the raw file is deleted.

Depth: files have 500 levels per side until ~Aug 2025 and 200 after. We always
use the best 200 levels so features mean the same thing across the switch.
BTC's book is dense: 200 levels reach only ~2-3 bps from the price on a normal
day (``bid_range_bps`` / ``ask_range_bps``; 5th percentile 2.2 bps on 10 Oct
2025), wider when liquidity thins. So depth is measured two ways that the file
always covers: within 1 and 2 bps of mid, and over the best 10 / 50 / 200 levels.

Time: a row labelled ``ts`` = T describes the book as it stood just before T
(after every update stamped < T). Nothing from T onward is used.

Usage (from the repo root)::

    python -m regimecast.data.orderbook --symbols BTCUSDT --start 2025-10-03 --end 2025-10-17
"""
from __future__ import annotations

import argparse
import heapq
import io
import json
import logging
import tempfile
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from regimecast import config

log = logging.getLogger("orderbook")

URL = "https://quote-saver.bycsi.com/orderbook/linear/{symbol}/{day}_{symbol}_ob{levels}.data.zip"
LEVELS = 200
BANDS_BPS = (1, 2)
TOP_LEVELS = (10, 50, 200)


# ------------------------------------------------------------------ book replay
class Book:
    def __init__(self):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}
        self.best_bid: float | None = None
        self.best_ask: float | None = None

    def snapshot(self, b: list, a: list) -> None:
        self.bids = {float(p): float(q) for p, q in b}
        self.asks = {float(p): float(q) for p, q in a}
        self.best_bid = max(self.bids) if self.bids else None
        self.best_ask = min(self.asks) if self.asks else None

    def delta(self, b: list, a: list) -> None:
        lost_bid = lost_ask = False
        for p, q in b:
            p, q = float(p), float(q)
            if q == 0:
                if self.bids.pop(p, None) is not None and p == self.best_bid:
                    lost_bid = True
            else:
                self.bids[p] = q
                if self.best_bid is None or p > self.best_bid:
                    self.best_bid, lost_bid = p, False
        for p, q in a:
            p, q = float(p), float(q)
            if q == 0:
                if self.asks.pop(p, None) is not None and p == self.best_ask:
                    lost_ask = True
            else:
                self.asks[p] = q
                if self.best_ask is None or p < self.best_ask:
                    self.best_ask, lost_ask = p, False
        if lost_bid:
            self.best_bid = max(self.bids) if self.bids else None
        if lost_ask:
            self.best_ask = min(self.asks) if self.asks else None

    def ready(self) -> bool:
        return self.best_bid is not None and self.best_ask is not None

    def features(self) -> dict:
        bb, ba = self.best_bid, self.best_ask
        mid = (bb + ba) / 2
        bids = heapq.nlargest(LEVELS, self.bids.items())   # best 200 only (files before ~Aug 2025 have 500)
        asks = heapq.nsmallest(LEVELS, self.asks.items())
        row = {"mid": mid, "spread_bps": (ba - bb) / mid * 1e4, "bid1_qty": self.bids[bb], "ask1_qty": self.asks[ba],
               "bid_range_bps": (mid - bids[-1][0]) / mid * 1e4, "ask_range_bps": (asks[-1][0] - mid) / mid * 1e4}
        for bps in BANDS_BPS:
            lo, hi = mid * (1 - bps / 1e4), mid * (1 + bps / 1e4)
            db = sum(p * q for p, q in bids if p >= lo)
            da = sum(p * q for p, q in asks if p <= hi)
            row[f"depth_bid_{bps}bps_usd"] = db
            row[f"depth_ask_{bps}bps_usd"] = da
            row[f"imbalance_{bps}bps"] = (db - da) / (db + da) if db + da else 0.0
        for n in TOP_LEVELS:
            db = sum(p * q for p, q in bids[:n])
            da = sum(p * q for p, q in asks[:n])
            row[f"depth_bid_top{n}_usd"] = db
            row[f"depth_ask_top{n}_usd"] = da
            row[f"imbalance_top{n}"] = (db - da) / (db + da) if db + da else 0.0
        return row


def ofi_step(prev: tuple, book: Book) -> float:
    """Order-flow imbalance contribution of one update (Cont, Kukanov & Stoikov 2014).
    prev = (best_bid, bid_qty, best_ask, ask_qty) before the update."""
    pb, pbq, pa, paq = prev
    b, bq, a, aq = book.best_bid, book.bids[book.best_bid], book.best_ask, book.asks[book.best_ask]
    e_bid = (bq if b >= pb else 0.0) - (pbq if b <= pb else 0.0)
    e_ask = (aq if a <= pa else 0.0) - (paq if a >= pa else 0.0)
    return e_bid - e_ask


def replay(lines, symbol: str) -> pd.DataFrame:
    """Turn one day of Bybit order-book messages into 1-second feature rows."""
    book = Book()
    rows: list[dict] = []
    ofi = 0.0
    n_updates = 0
    cur = None  # the second being accumulated (unix seconds)
    for line in lines:
        m = json.loads(line)
        sec = m["ts"] // 1000
        if cur is not None and sec > cur and book.ready():
            # close every finished second BEFORE applying this update (no look-ahead)
            base = book.features()
            for s in range(cur + 1, sec + 1):
                rows.append({"ts": s, **base, "ofi": ofi, "n_updates": n_updates})
                ofi, n_updates = 0.0, 0
        cur = sec
        d = m["data"]
        prev = (book.best_bid, book.bids.get(book.best_bid, 0.0), book.best_ask,
                book.asks.get(book.best_ask, 0.0)) if book.ready() else None
        if m["type"] == "snapshot":  # start of file, or a reset after a Bybit restart
            book.snapshot(d["b"], d["a"])  # a reset is not order flow: no OFI for it
        else:
            book.delta(d["b"], d["a"])
            if prev is not None and book.ready():
                ofi += ofi_step(prev, book)
        n_updates += 1
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df.insert(0, "ts", pd.to_datetime(df.pop("ts"), unit="s", utc=True))
    df["symbol"] = symbol
    return df


# ------------------------------------------------------------------ download + process
def _url_for(symbol: str, day: date) -> list[str]:
    return [URL.format(symbol=symbol, day=day.isoformat(), levels=n) for n in (200, 500)]


def process_day(client, symbol: str, day: date, keep_raw: bool = False, force: bool = False) -> str:
    out = config.parquet_dir() / "orderbook" / symbol / f"{day.isoformat()}.parquet"
    if out.exists() and not force:
        return "skip"
    raw_dir = config.DATA_DIR / "raw" / "bybit_orderbook" if keep_raw else Path(tempfile.mkdtemp())
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw = raw_dir / f"{day.isoformat()}_{symbol}.zip"
    try:
        if not raw.exists():
            for url in _url_for(symbol, day):
                with client.stream("GET", url, timeout=600) as r:
                    if r.status_code == 404:
                        continue
                    r.raise_for_status()
                    with open(raw, "wb") as f:
                        for chunk in r.iter_bytes(1 << 20):
                            f.write(chunk)
                break
            else:
                return "missing"
        t0 = time.time()
        with zipfile.ZipFile(raw) as z, z.open(z.namelist()[0]) as fh:
            df = replay(io.TextIOWrapper(fh, encoding="utf-8"), symbol)
        out.parent.mkdir(parents=True, exist_ok=True)
        tmp = out.with_suffix(".tmp")
        df.to_parquet(tmp, index=False)
        tmp.replace(out)
        return f"ok ({len(df)} rows, replay {time.time() - t0:.0f} s)"
    finally:
        if not keep_raw:
            raw.unlink(missing_ok=True)
            raw_dir.rmdir()


def run(symbols: list[str], start: date, end: date, keep_raw: bool = False, force: bool = False) -> dict:
    import httpx

    stats: dict[str, int] = {}
    with httpx.Client(follow_redirects=True) as client:
        for sym in symbols:
            day = start
            while day <= end:
                status = process_day(client, sym, day, keep_raw, force)
                stats[status.split(" ")[0]] = stats.get(status.split(" ")[0], 0) + 1
                log.info("%s %s %s", sym, day, status)
                day += timedelta(days=1)
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD (inclusive)")
    p.add_argument("--keep-raw", action="store_true", help="keep the downloaded zips in data/raw/bybit_orderbook")
    p.add_argument("--force", action="store_true", help="rebuild days that already have features")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    d = lambda s: datetime.strptime(s, "%Y-%m-%d").date()  # noqa: E731
    print(run(a.symbols, d(a.start), d(a.end), a.keep_raw, a.force))


if __name__ == "__main__":
    main()
