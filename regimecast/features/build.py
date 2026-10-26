"""Feature tables every model reads: one row per minute and one row per hour.

The one rule: **a row labelled t uses only data known at t.** So ``ts`` is the time
the row became known, not the start of the period it describes:

* a 1-minute candle that opens at 10:59 closes, and is known, at 11:00;
* open interest is known at ``available_at`` (store.metrics handles Binance's
  label change), funding when it is charged, order-book minutes at their end;
* hourly rows are labelled by the END of the hour (as in vol/realized.py).

Slow-moving inputs (funding, open interest, ratios) are joined "as of" t: the last
value known at or before t. The leakage tests in tests/test_features.py change
every input after a time T and check that no row at or before T moves.

Inputs per symbol (loaded with ``load_inputs``):
    last     Binance 1-minute candles (traded price, volume, taker buys)
    mark     1-minute mark price          index    1-minute index price
    premium  1-minute premium index       funding  funding payments
    metrics  open interest and long/short ratios (5-minute)
    ob       1-minute order-book summary (Bybit, from May 2025; empty before)

Run on Modal (regimecast/jobs/modal_app.py::features_all) or locally::

    python -m regimecast.features.build --symbols BTCUSDT
"""
from __future__ import annotations

import argparse
import logging

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.vol import realized

log = logging.getLogger("features")

MIN = pd.Timedelta(minutes=1)
HOUR = pd.Timedelta(hours=1)
METRIC_COLS = {"sum_open_interest": "oi", "sum_open_interest_value": "oi_usd",
               "sum_toptrader_long_short_ratio": "ls_top", "count_long_short_ratio": "ls_accounts",
               "sum_taker_long_short_vol_ratio": "taker_ls"}
OB_COLS = ["spread_bps_mean", "spread_bps_max", "ofi", "n_updates",
           "depth_bid_2bps_usd_mean", "depth_ask_2bps_usd_mean", "depth_bid_2bps_usd_min", "depth_ask_2bps_usd_min",
           "depth_bid_top50_usd_mean", "depth_ask_top50_usd_mean", "imbalance_top50_mean", "imbalance_2bps_mean",
           "bid_range_bps_mean", "ask_range_bps_mean"]


# ------------------------------------------------------------------ inputs
def load_inputs(symbol: str, start=None, end=None) -> dict[str, pd.DataFrame]:
    from regimecast.data import store

    return {"last": store.minute_bars(symbol, "last", start, end),
            "mark": store.minute_bars(symbol, "mark", start, end),
            "index": store.minute_bars(symbol, "index", start, end),
            "premium": store.minute_bars(symbol, "premium", start, end),
            "funding": store.funding(symbol, start, end),
            "metrics": store.metrics(symbol, start, end),
            "ob": store.archive("orderbook_1m", symbol, start, end)}


def _close_at_end(bars: pd.DataFrame, col: str = "close") -> pd.Series:
    """1-minute bar column re-indexed by the bar's CLOSE time (when it is known)."""
    if bars is None or bars.empty:
        return pd.Series(dtype=float)
    s = bars.drop_duplicates("ts", keep="last").set_index("ts")[col].sort_index()
    s.index = s.index + MIN
    return s


def _asof(grid: pd.DatetimeIndex, df: pd.DataFrame, time_col: str, cols: dict[str, str]) -> pd.DataFrame:
    """Last value known at or before each grid time."""
    out = pd.DataFrame(index=grid)
    if df is None or df.empty:
        for new in cols.values():
            out[new] = np.nan
        return out
    right = df[[time_col, *cols]].dropna(subset=[time_col]).sort_values(time_col).rename(columns=cols)
    left = pd.DataFrame({"t": grid})
    m = pd.merge_asof(left, right.rename(columns={time_col: "t"}), on="t", direction="backward")
    return m.set_index("t")[list(cols.values())]


# ------------------------------------------------------------------ minute features
def minute_features(inp: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per minute, ``ts`` = minute end (when everything in the row was known)."""
    last = inp["last"]
    close = realized.minute_close(last)          # halts -> missing, short gaps filled
    close.index = close.index + MIN              # open time -> close time
    grid = close.index
    r = np.log(close).diff()
    f = pd.DataFrame(index=grid)
    f["close"] = close
    f["ret_1m"] = r
    f["ret_15m"] = r.rolling(15, min_periods=12).sum()
    f["ret_60m"] = r.rolling(60, min_periods=50).sum()
    f["rv_15m"] = (r ** 2).rolling(15, min_periods=12).sum()
    f["rv_60m"] = (r ** 2).rolling(60, min_periods=50).sum()
    hi, lo = _close_at_end(last, "high").reindex(grid), _close_at_end(last, "low").reindex(grid)
    f["range_1m"] = np.log(hi / lo)
    vol = _close_at_end(last, "volume").reindex(grid)
    f["volume_usd"] = _close_at_end(last, "quote_volume").reindex(grid)
    f["taker_buy_share"] = (_close_at_end(last, "taker_buy_volume").reindex(grid) / vol.where(vol > 0))
    mark, index = _close_at_end(inp["mark"]).reindex(grid), _close_at_end(inp["index"]).reindex(grid)
    f["mark"] = mark
    f["mark_index_gap"] = np.log(mark / index)
    f["premium"] = _close_at_end(inp["premium"]).reindex(grid)
    f = f.join(_asof(grid, inp["funding"], "ts", {"funding_rate": "funding_rate"}))
    f = f.join(_asof(grid, inp["metrics"], "available_at", METRIC_COLS))
    f["oi_chg_60m"] = np.log(f["oi"] / f["oi"].shift(60))
    ob = inp.get("ob")
    if ob is not None and not ob.empty:
        o = ob.drop_duplicates("ts", keep="last").set_index("ts").reindex(grid)  # ob ts = minute end already
        for c in OB_COLS:
            f[f"ob_{c}"] = o[c] if c in o else np.nan
    else:
        for c in OB_COLS:
            f[f"ob_{c}"] = np.nan
    f.index.name = "ts"
    return f.reset_index()


# ------------------------------------------------------------------ hourly features
def hourly_features(inp: dict[str, pd.DataFrame], minute: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per hour, ``ts`` = hour END. Realised measures as in vol/realized.py."""
    close_open_label = realized.minute_close(inp["last"])   # realized.* expects open-time labels
    h = realized.hourly_measures(close_open_label)
    h["rv5"] = realized.hourly_rv5_subsampled(close_open_label).where(h["n"] >= realized.MIN_RETURNS_PER_HOUR)
    h["rv_24h"] = h["rv"].rolling(24, min_periods=18).mean()
    h["rv_168h"] = h["rv"].rolling(168, min_periods=120).mean()
    h["ret_24h"] = h["ret"].rolling(24, min_periods=18).sum()
    m = (minute if minute is not None else minute_features(inp)).set_index("ts")
    g = m.resample("1h", label="right", closed="right")   # minute ends in (H-1h, H] -> hour H
    agg = pd.DataFrame({
        "close": g["close"].last(),          # traded price at the hour's end
        "mark": g["mark"].last(),
        "volume_usd": g["volume_usd"].sum(min_count=1),
        "taker_buy_share": g["taker_buy_share"].mean(),
        "premium_mean": g["premium"].mean(),
        "mark_index_gap": g["mark_index_gap"].last(),
        "range_max_1m": g["range_1m"].max(),
        "ob_spread_bps_mean": g["ob_spread_bps_mean"].mean(),
        "ob_spread_bps_max": g["ob_spread_bps_max"].max(),
        "ob_depth_top50_usd_mean": (g["ob_depth_bid_top50_usd_mean"].mean() + g["ob_depth_ask_top50_usd_mean"].mean()),
        "ob_depth_2bps_usd_min": (m["ob_depth_bid_2bps_usd_min"] + m["ob_depth_ask_2bps_usd_min"])
                                 .resample("1h", label="right", closed="right").min(),
        "ob_imbalance_top50_mean": g["ob_imbalance_top50_mean"].mean(),
        "ob_ofi": g["ob_ofi"].sum(min_count=1),
        "ob_range_bps_mean": (g["ob_bid_range_bps_mean"].mean() + g["ob_ask_range_bps_mean"].mean()) / 2,
    })
    h = h.join(agg, how="left")
    grid = h.index
    h = h.join(_asof(grid, inp["funding"], "ts", {"funding_rate": "funding_rate"}))
    h = h.join(_asof(grid, inp["metrics"], "available_at", METRIC_COLS))
    h["oi_chg_24h"] = np.log(h["oi"] / h["oi"].shift(24))
    h["log_rv"] = np.log(h["rv"].clip(lower=1e-12))
    h.index.name = "ts"
    return h.reset_index()


# ------------------------------------------------------------------ build and save
def build_all(symbol: str) -> dict:
    """Build both tables for one symbol and save them under data/parquet/features_*."""
    inp = load_inputs(symbol)
    if inp["last"].empty:
        return {"symbol": symbol, "status": "no data"}
    m = minute_features(inp)
    h = hourly_features(inp, m)
    m_dir = config.parquet_dir() / "features_1m" / symbol
    m_dir.mkdir(parents=True, exist_ok=True)
    for year, part in m.groupby(m["ts"].dt.year):
        tmp = m_dir / f"{year}.tmp"
        part.to_parquet(tmp, index=False)
        tmp.replace(m_dir / f"{year}.parquet")
    h_dir = config.parquet_dir() / "features_1h" / symbol
    h_dir.mkdir(parents=True, exist_ok=True)
    tmp = h_dir / "all.tmp"
    h.to_parquet(tmp, index=False)
    tmp.replace(h_dir / "all.parquet")
    ob_hours = int(h["ob_spread_bps_mean"].notna().sum())
    return {"symbol": symbol, "minutes": len(m), "hours": len(h), "first": str(h["ts"].min()),
            "last": str(h["ts"].max()), "hours_with_order_book": ob_hours}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--symbols", nargs="+", default=config.SYMBOLS)
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    for s in a.symbols:
        print(build_all(s.upper()))


if __name__ == "__main__":
    main()
