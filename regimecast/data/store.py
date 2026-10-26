"""One way to load every kind of data in RegimeCast.

Every module reads data through these functions, never by opening files itself,
so time handling is the same everywhere:

* all timestamps are UTC,
* ``start`` is inclusive and ``end`` is exclusive,
* rows come back sorted by time, then file name, then position in the file, with
  exact duplicates removed. So "keep the last copy" of a repeated time is always
  the same row: the monthly archive file (``2025-10.parquet``, published after the
  month ends) sorts after that month's daily files (``2025-10-01.parquet``) and wins.

Layout on disk (same on your PC and on Railway)::

    data/parquet/<dataset>/<SYMBOL>/<period>.parquet      archive + derived (column ts)
    data/live/<stream>/date=YYYY-MM-DD/*.parquet         recorder output (column event_ts)

Only the files and rows inside [start, end) are read (DuckDB pushes the time
filter down into the Parquet files), so asking for one week of a 6-year
dataset is fast.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from regimecast import config

# Binance changed the meaning of metrics create_time on this date (end-of-period ->
# start-of-period label). See validate.KNOWN_ISSUES.
METRICS_LABEL_CHANGE = pd.Timestamp("2026-06-25", tz="UTC")

Time = str | pd.Timestamp | None


def _utc(t: Time) -> pd.Timestamp | None:
    if t is None:
        return None
    t = pd.Timestamp(t)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _query(files: str, time_col: str, start: Time, end: Time, columns: list[str] | None,
           where: dict | None = None, hive: bool = False) -> pd.DataFrame:
    cols = ", ".join(f'"{c}"' for c in columns) if columns else "*"
    conds, params = [], []
    if (s := _utc(start)) is not None:
        conds.append(f'"{time_col}" >= ?')
        params.append(s.to_pydatetime())
    if (e := _utc(end)) is not None:
        conds.append(f'"{time_col}" < ?')
        params.append(e.to_pydatetime())
    for k, v in (where or {}).items():
        conds.append(f'"{k}" = ?')
        params.append(v)
    sql = (f"SELECT {cols}, filename AS _file, file_row_number AS _row FROM read_parquet(?, "
           f"hive_partitioning = {str(hive).lower()}, union_by_name = true, filename = true, file_row_number = true)")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += f' ORDER BY "{time_col}", _file, _row'
    with duckdb.connect() as con:
        con.execute("SET TimeZone = 'UTC'")
        df = con.execute(sql, [files, *params]).df()
    df = df.drop(columns=[c for c in ("_file", "_row", "filename", "file_row_number") if c in df]
                 + (["date"] if hive and "date" in df else []))
    if time_col in df and len(df):
        df[time_col] = pd.to_datetime(df[time_col], utc=True)
    return df.drop_duplicates().reset_index(drop=True)


# ------------------------------------------------------------------ archive and derived data
def archive(dataset: str, symbol: str, start: Time = None, end: Time = None,
            columns: list[str] | None = None) -> pd.DataFrame:
    """Any dataset stored as data/parquet/<dataset>/<SYMBOL>/*.parquet (column ``ts``):
    Binance archive (klines, markPriceKlines, fundingRate, metrics, ...) and our own
    derived tables such as ``orderbook``."""
    folder = config.parquet_dir() / dataset / symbol.upper()
    if not any(folder.glob("*.parquet")):
        return pd.DataFrame()
    if columns and "ts" not in columns:
        columns = ["ts", *columns]
    return _query(str(folder / "*.parquet"), "ts", start, end, columns)


def minute_bars(symbol: str, kind: str = "mark", start: Time = None, end: Time = None) -> pd.DataFrame:
    """1-minute OHLC bars, one row per minute (``ts`` = bar OPEN time).

    kind: ``mark`` (what liquidations use), ``last`` (traded price), ``index``, ``premium``.
    Same-minute duplicates (a known archive bug) keep the last copy. Gaps stay gaps:
    callers decide how to fill them.
    """
    dataset = {"mark": "markPriceKlines", "last": "klines", "index": "indexPriceKlines",
               "premium": "premiumIndexKlines"}[kind]
    cols = ["open", "high", "low", "close"] + (["volume", "quote_volume", "count", "taker_buy_volume"]
                                             if kind == "last" else [])
    df = archive(dataset, symbol, start, end, cols)
    if df.empty:
        return df
    return df.drop_duplicates("ts", keep="last").reset_index(drop=True)


def funding(symbol: str, start: Time = None, end: Time = None) -> pd.DataFrame:
    """Funding payments: ``ts`` (when it was charged), ``funding_rate``, ``interval_hours``."""
    df = archive("fundingRate", symbol, start, end)
    if df.empty:
        return df
    return (df.rename(columns={"last_funding_rate": "funding_rate", "funding_interval_hours": "interval_hours"})
              [["ts", "funding_rate", "interval_hours"]].drop_duplicates("ts", keep="last").reset_index(drop=True))


def metrics(symbol: str, start: Time = None, end: Time = None) -> pd.DataFrame:
    """Open interest and long/short ratios (5-minute), with ONE time convention.

    ``period_start`` = start of the 5-minute period, ``available_at`` = when the value
    was known. Join features on ``available_at`` only, or you leak the future.
    ``start``/``end`` filter on ``available_at``.
    """
    pad = pd.Timedelta(minutes=10)  # rows are relabelled below, so read a little wider
    df = archive("metrics", symbol, None if start is None else _utc(start) - pad, None if end is None else _utc(end) + pad)
    if df.empty:
        return df
    df = df.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
    period_start = df["ts"].where(df["ts"] >= METRICS_LABEL_CHANGE, df["ts"] - pd.Timedelta(minutes=5))
    df["period_start"] = period_start
    df["available_at"] = period_start + pd.Timedelta(minutes=5)
    keep = pd.Series(True, index=df.index)
    if start is not None:
        keep &= df["available_at"] >= _utc(start)
    if end is not None:
        keep &= df["available_at"] < _utc(end)
    return df[keep].reset_index(drop=True)


def orderbook(symbol: str, start: Time = None, end: Time = None, columns: list[str] | None = None) -> pd.DataFrame:
    """1-second order-book features rebuilt from Bybit history (see data/orderbook.py)."""
    return archive("orderbook", symbol, start, end, columns)


# ------------------------------------------------------------------ news (M3)
def headlines(start: Time = None, end: Time = None, columns: list[str] | None = None) -> pd.DataFrame:
    """De-duplicated news headlines (data/parquet/news/headlines/*.parquet), filtered on
    ``known_at``: when the headline was first available to us. See regimecast/sentiment."""
    folder = config.parquet_dir() / "news" / "headlines"
    if not any(folder.glob("*.parquet")):
        return pd.DataFrame(columns=columns or [])
    want = None
    if columns:
        want = ["known_at", *[c for c in columns if c != "known_at"]]
        # always read the link too: _query drops identical rows, and two headlines can
        # look identical in a few columns
        columns = [*want, *(["link_key"] if "link_key" not in want else [])]
    df = _query(str(folder / "*.parquet"), "known_at", start, end, columns)
    if want is not None:
        df = df[want]
    for c in ("published_at", "fetched_at"):
        if c in df:
            df[c] = pd.to_datetime(df[c], utc=True)
    return df


def news_mood(symbol: str, start: Time = None, end: Time = None) -> pd.DataFrame:
    """Hourly news mood: ``ts`` = hour END (a headline at 10:20 counts for 11:00), ``count``,
    ``mean_score`` (-1..1), ``share_negative``, ``policy_shock_count``. Hours without news
    have count 0 and empty score columns."""
    return archive("sentiment", symbol, start, end)


# ------------------------------------------------------------------ live recordings
def live(stream: str, start: Time = None, end: Time = None, columns: list[str] | None = None,
         **equals) -> pd.DataFrame:
    """Rows the recorder saved for one stream, e.g. ``live("liquidations", symbol="BTCUSDT")``
    or ``live("hl_positions", coin="ETH", source="watchlist")``. Filters on ``event_ts``."""
    folder = config.live_dir() / stream
    if not any(folder.glob("date=*/*.parquet")):
        return pd.DataFrame()
    if columns and "event_ts" not in columns:
        columns = ["event_ts", *columns]
    return _query(str(folder / "date=*" / "*.parquet"), "event_ts", start, end, columns, where=equals, hive=True)


def available(dataset: str, symbol: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """First and last ``ts`` of an archive dataset, or None if nothing is downloaded."""
    folder: Path = config.parquet_dir() / dataset / symbol.upper()
    if not any(folder.glob("*.parquet")):
        return None
    with duckdb.connect() as con:
        con.execute("SET TimeZone = 'UTC'")
        # cast to plain timestamps (session is UTC) so DuckDB doesn't need pytz
        lo, hi = con.execute("SELECT min(ts)::TIMESTAMP, max(ts)::TIMESTAMP FROM read_parquet(?)",
                             [str(folder / "*.parquet")]).fetchone()
    return _utc(lo), _utc(hi)
