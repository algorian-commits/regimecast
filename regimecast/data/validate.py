"""Data-health checks for the downloaded archive.

Run::

    python -m regimecast.data.validate --symbols BTCUSDT

It checks every dataset for:
* missing minutes (gaps) and duplicate timestamps,
* bad prices (high < low, close outside [low, high], zero/negative prices),
* mark price drifting too far from last price (a sign of bad rows),
* the known Binance archive problems listed in KNOWN_ISSUES.

It writes ``data/reports/data_health_<symbol>.json`` which the dashboard shows.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import pandas as pd

from regimecast import config
from regimecast.data import store

# Known problems in data.binance.vision (open GitHub issues, Sep 2026).
KNOWN_ISSUES = {
    "metrics_create_time_label_change": {
        "date": "2026-06-25",
        "what": "metrics create_time switched from end-of-period to start-of-period labels.",
        "fix": "load_metrics() shifts rows before this date back 5 minutes so every row is labelled "
               "by the START of its 5-minute period, then makes it usable only at period end.",
    },
    "markprice_gaps_duplicates": {
        "date": "2023-2025",
        "what": "missing and duplicate minutes in USD-M markPriceKlines for BTC/ETH.",
        "fix": "binance_archive --repair fills whole missing days from daily files; dedupe on ts (monthly "
               "file wins); remaining short gaps are reported and forward-filled at most 5 minutes.",
    },
    "bookdepth_frozen_ask_bands": {
        "date": "2026-09-03",
        "what": "bookDepth inner ask bands frozen since this date.",
        "fix": "do not use archive bookDepth after 2026-09-03; use our own live recorder instead.",
    },
    "bookdepth_misaligned_2025": {
        "date": "2025-04/2025-05",
        "what": "bookDepth values misaligned with mark price.",
        "fix": "exclude bookDepth for Apr-May 2025 from training.",
    },
}

METRICS_LABEL_CHANGE = store.METRICS_LABEL_CHANGE


def load(dataset: str, symbol: str) -> pd.DataFrame:
    """All rows of one archive dataset (thin wrapper over store.archive)."""
    return store.archive(dataset, symbol)


def clean_minutes(df: pd.DataFrame) -> pd.DataFrame:
    """Drop duplicate minutes (keep the last copy) and sort."""
    return df.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)


def load_metrics(symbol: str) -> pd.DataFrame:
    """Metrics with one time convention (thin wrapper over store.metrics)."""
    return store.metrics(symbol)


def gap_report(ts: pd.Series, freq: str = "1min") -> dict:
    ts = ts.sort_values()
    step = pd.Timedelta(freq)
    diffs = ts.diff()
    gaps = diffs[diffs > step]
    missing = int(((gaps / step) - 1).sum()) if len(gaps) else 0
    biggest = gaps.sort_values(ascending=False).head(5)
    return {
        "rows": int(len(ts)),
        "first": str(ts.iloc[0]) if len(ts) else None,
        "last": str(ts.iloc[-1]) if len(ts) else None,
        "duplicates": int(ts.duplicated().sum()),
        "gaps": int(len(gaps)),
        "missing_steps": missing,
        "largest_gaps": [{"after": str(ts.loc[i - 1]) if i - 1 in ts.index else None, "length": str(g)}
                         for i, g in biggest.items()],
    }


def price_checks(df: pd.DataFrame) -> dict:
    bad_hl = int((df["high"] < df["low"]).sum())
    bad_close = int(((df["close"] > df["high"] * (1 + 1e-9)) | (df["close"] < df["low"] * (1 - 1e-9))).sum())
    nonpos = int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
    return {"high_below_low": bad_hl, "close_outside_range": bad_close, "non_positive_price": nonpos}


def mark_vs_last(klines: pd.DataFrame, mark: pd.DataFrame, limit: float = 0.02) -> dict:
    """Minutes where mark close differs from last close by more than `limit` (2%)."""
    if klines.empty or mark.empty:
        return {"checked": 0}
    m = pd.merge(clean_minutes(klines)[["ts", "close"]], clean_minutes(mark)[["ts", "close"]],
                 on="ts", suffixes=("_last", "_mark"))
    dev = (m["close_mark"] / m["close_last"] - 1).abs()
    worst = m.loc[dev.nlargest(5).index, "ts"].astype(str).tolist()
    return {"checked": int(len(m)), "over_limit": int((dev > limit).sum()),
            "max_abs_dev": float(dev.max()) if len(dev) else 0.0, "worst_minutes": worst}


def validate_symbol(symbol: str) -> dict:
    report: dict = {"symbol": symbol, "generated_at": datetime.now(timezone.utc).isoformat(),
                    "known_issues": KNOWN_ISSUES, "datasets": {}}
    frames = {}
    for name in ("klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines"):
        df = load(name, symbol)
        frames[name] = df
        if df.empty:
            report["datasets"][name] = {"status": "no data"}
            continue
        r = gap_report(df["ts"])
        r.update(price_checks(clean_minutes(df)) if name != "premiumIndexKlines" else {})
        report["datasets"][name] = r
    fr = load("fundingRate", symbol)
    report["datasets"]["fundingRate"] = ({"status": "no data"} if fr.empty else
                                         {"rows": len(fr), "first": str(fr.ts.iloc[0]), "last": str(fr.ts.iloc[-1]),
                                          "duplicates": int(fr.ts.duplicated().sum()),
                                          "max_abs_rate": float(fr.last_funding_rate.abs().max())})
    mt = load("metrics", symbol)
    report["datasets"]["metrics"] = {"status": "no data"} if mt.empty else gap_report(mt["ts"], "5min")
    report["mark_vs_last"] = mark_vs_last(frames["klines"], frames["markPriceKlines"])

    out = config.DATA_DIR / "reports" / f"data_health_{symbol}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", nargs="+", default=["BTCUSDT"])
    a = p.parse_args()
    for s in a.symbols:
        r = validate_symbol(s.upper())
        print(f"\n== {s} ==")
        for name, d in r["datasets"].items():
            print(f"{name:20s} {d}")
        print("mark_vs_last", r["mark_vs_last"])


if __name__ == "__main__":
    main()
