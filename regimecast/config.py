"""Shared settings. Everything can be changed with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

# Where all data lives. Default: <repo>/data
DATA_DIR = Path(os.environ.get("REGIMECAST_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))

# Assets we study (USD-M perpetual futures on Binance)
SYMBOLS = [s.strip().upper() for s in os.environ.get("REGIMECAST_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()]

# Binance endpoints (kept in env vars because Binance sometimes moves them)
BINANCE_ARCHIVE_URL = os.environ.get("BINANCE_ARCHIVE_URL", "https://data.binance.vision")
BINANCE_FAPI_URL = os.environ.get("BINANCE_FAPI_URL", "https://fapi.binance.com")
BINANCE_FSTREAM_URL = os.environ.get("BINANCE_FSTREAM_URL", "wss://fstream.binance.com")

HOURS_PER_YEAR = 24 * 365


def raw_dir() -> Path:
    return DATA_DIR / "raw"


def parquet_dir() -> Path:
    return DATA_DIR / "parquet"


def live_dir() -> Path:
    return DATA_DIR / "live"
