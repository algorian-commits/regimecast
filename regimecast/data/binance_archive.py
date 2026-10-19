"""Download Binance USD-M futures history from the free archive (data.binance.vision).

What it does
------------
* Downloads monthly zip files for finished months and daily zip files for the
  current month (the archive only publishes a month file after it ends).
* Checks every zip against Binance's SHA-256 checksum file.
* Skips files it already has, so you can stop and re-run at any time.
* Converts each zip into a clean Parquet file with a UTC ``ts`` column.

Usage (from the repo root)::

    python -m regimecast.data.binance_archive --symbols BTCUSDT --start 2020-01 \
        --datasets klines markPriceKlines indexPriceKlines premiumIndexKlines fundingRate metrics
"""
from __future__ import annotations

import argparse
import hashlib
import io
import logging
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from regimecast import config

log = logging.getLogger("archive")

KLINE_COLS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore",
]


@dataclass(frozen=True)
class Dataset:
    name: str            # folder name in the archive
    monthly: bool        # published as monthly files?
    daily: bool          # published as daily files?
    interval: str | None  # "1m" for kline-type datasets, else None


DATASETS: dict[str, Dataset] = {
    "klines": Dataset("klines", True, True, "1m"),
    "markPriceKlines": Dataset("markPriceKlines", True, True, "1m"),
    "indexPriceKlines": Dataset("indexPriceKlines", True, True, "1m"),
    "premiumIndexKlines": Dataset("premiumIndexKlines", True, True, "1m"),
    "fundingRate": Dataset("fundingRate", True, False, None),
    "metrics": Dataset("metrics", False, True, None),
    "bookDepth": Dataset("bookDepth", False, True, None),
}


# ---------------------------------------------------------------- URLs
def file_stem(ds: Dataset, symbol: str, period: str) -> str:
    if ds.interval:
        return f"{symbol}-{ds.interval}-{period}"
    return f"{symbol}-{ds.name}-{period}"


def archive_url(ds: Dataset, symbol: str, period: str, freq: str) -> str:
    """freq is 'monthly' (period YYYY-MM) or 'daily' (period YYYY-MM-DD)."""
    parts = [config.BINANCE_ARCHIVE_URL, "data/futures/um", freq, ds.name, symbol]
    if ds.interval:
        parts.append(ds.interval)
    return "/".join(parts) + f"/{file_stem(ds, symbol, period)}.zip"


def plan_periods(ds: Dataset, start: date, end: date) -> list[tuple[str, str]]:
    """Return (freq, period) pairs to download between start and end (inclusive).

    Finished months use monthly files when the dataset has them; the current
    (unfinished) month, and datasets without monthly files, use daily files.
    """
    out: list[tuple[str, str]] = []
    this_month = date(end.year, end.month, 1)
    cur = date(start.year, start.month, 1)
    while cur <= end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        if ds.monthly and cur < this_month:
            out.append(("monthly", cur.strftime("%Y-%m")))
        elif ds.daily:
            d = max(cur, start)
            last = min(nxt - timedelta(days=1), end)
            while d <= last:
                out.append(("daily", d.isoformat()))
                d += timedelta(days=1)
        cur = nxt
    return out


# ---------------------------------------------------------------- parsing
def _to_utc(series: pd.Series) -> pd.Series:
    """Binance timestamps are ms; some newer files use microseconds."""
    s = pd.to_numeric(series, errors="coerce").astype("int64")
    unit = "us" if s.max() > 10**14 else "ms"
    return pd.to_datetime(s, unit=unit, utc=True)


def parse_csv(dataset: str, raw: bytes) -> pd.DataFrame:
    """Turn one archive CSV into a tidy DataFrame with a UTC ``ts`` column."""
    text = raw.decode("utf-8")
    first = text.split("\n", 1)[0]
    has_header = any(c.isalpha() for c in first.replace("e-", "").replace("E-", ""))

    ds = DATASETS[dataset]
    if ds.interval:  # kline-style
        df = pd.read_csv(io.StringIO(text), header=0 if has_header else None)
        df.columns = KLINE_COLS[: len(df.columns)]
        df["ts"] = _to_utc(df["open_time"])
        df = df.drop(columns=[c for c in ("ignore", "open_time", "close_time") if c in df.columns])
        for c in df.columns:
            if c != "ts":
                df[c] = pd.to_numeric(df[c], errors="coerce")
        return df[["ts"] + [c for c in df.columns if c != "ts"]]

    df = pd.read_csv(io.StringIO(text))
    if dataset == "fundingRate":
        df["ts"] = _to_utc(df["calc_time"])
        return df[["ts", "funding_interval_hours", "last_funding_rate"]]
    if dataset in ("metrics", "bookDepth"):
        # create_time / timestamp is a text datetime. NOTE: on 2026-06-25 Binance
        # changed the meaning of create_time (end-of-period -> start-of-period).
        # We keep the raw label here; validate.py documents and fixes it.
        tcol = "create_time" if "create_time" in df.columns else "timestamp"
        df["ts"] = pd.to_datetime(df[tcol], utc=True)
        return df.drop(columns=[tcol])
    raise ValueError(f"unknown dataset {dataset}")


# ---------------------------------------------------------------- download
def _get(client, url: str, retries: int = 4):
    for i in range(retries):
        try:
            r = client.get(url, timeout=60)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except Exception as e:  # network hiccup: back off and retry
            if i == retries - 1:
                raise
            log.warning("retry %s (%s)", url, e)
            time.sleep(2 ** i)
    return None


def download_one(client, dataset: str, symbol: str, freq: str, period: str, force: bool = False) -> str:
    """Download, verify and convert one file. Returns a status string."""
    ds = DATASETS[dataset]
    out = config.parquet_dir() / dataset / symbol / f"{period}.parquet"
    if out.exists() and not force:
        return "skip"
    url = archive_url(ds, symbol, period, freq)
    blob = _get(client, url)
    if blob is None:
        return "missing"
    chk = _get(client, url + ".CHECKSUM")
    if chk is None:
        log.warning("no checksum published for %s, saved unverified", url)
    else:
        expected = chk.decode().split()[0].strip().lower()
        if hashlib.sha256(blob).hexdigest() != expected:
            raise IOError(f"checksum mismatch for {url}")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        df = parse_csv(dataset, z.read(name))
    df["symbol"] = symbol
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(out)
    if freq == "monthly":  # daily files fetched while this month was running are now duplicates
        for daily in out.parent.glob(f"{period}-*.parquet"):
            daily.unlink()
    return f"ok ({len(df)} rows)"


def run(symbols: list[str], datasets: list[str], start: date, end: date, force: bool = False) -> dict:
    import httpx

    stats: dict[str, int] = {}
    with httpx.Client(follow_redirects=True) as client:
        for sym in symbols:
            for name in datasets:
                for freq, period in plan_periods(DATASETS[name], start, end):
                    status = download_one(client, name, sym, freq, period, force)
                    key = status.split(" ")[0]
                    stats[key] = stats.get(key, 0) + 1
                    if key != "skip":
                        log.info("%s %s %s %s", sym, name, period, status)
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    p.add_argument("--datasets", nargs="+", default=["klines", "markPriceKlines", "indexPriceKlines",
                                                      "premiumIndexKlines", "fundingRate", "metrics"],
                   choices=list(DATASETS))
    p.add_argument("--start", default="2020-01", help="YYYY-MM or YYYY-MM-DD")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (default: yesterday UTC)")
    p.add_argument("--force", action="store_true")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    start = datetime.strptime(a.start + ("-01" if len(a.start) == 7 else ""), "%Y-%m-%d").date()
    end = (datetime.strptime(a.end, "%Y-%m-%d").date() if a.end
           else datetime.now(timezone.utc).date() - timedelta(days=1))
    print(run([s.upper() for s in a.symbols], a.datasets, start, end, a.force))


if __name__ == "__main__":
    main()
