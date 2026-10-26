"""Heavy batch jobs on Modal: downloads, order-book replay and feature building.

Why Modal: the order-book history is ~400 GB raw (~1,600 coin-days) and the PC has a
few GB free and a slow link. Each job runs in its own container in the EU region
(Binance and Bybit block US servers) and writes Parquet to the ``regimecast-data``
Volume, laid out exactly like ``data/`` on the PC (``REGIMECAST_DATA_DIR=/data``), so
every module reads it through ``regimecast.data.store`` unchanged.

Run from the repo root (pin the workspace: a second Modal profile exists)::

    set MODAL_PROFILE=saeed355ahmed
    modal run regimecast/jobs/modal_app.py::archive_all
    modal run regimecast/jobs/modal_app.py::orderbook_backfill --start 2025-05-01
    modal run regimecast/jobs/modal_app.py::features_all
    modal deploy regimecast/jobs/modal_app.py      # daily order-book + features update

Results for the PC (small): ``modal volume get regimecast-data <path> data/<path>``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import modal

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
ARCHIVE_DATASETS = ["klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines", "fundingRate", "metrics"]
BOOKDEPTH_START = date(2023, 1, 1)   # Binance's bookDepth archive starts early 2023
ORDERBOOK_START = date(2025, 5, 1)   # Bybit order-book history used by the project

app = modal.App("regimecast-batch")
volume = modal.Volume.from_name("regimecast-data", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install_from_requirements("requirements.txt")
         .pip_install("orjson")
         .env({"REGIMECAST_DATA_DIR": "/data"})
         .add_local_python_source("regimecast"))
common = dict(image=image, volumes={"/data": volume}, region="eu")


def _yesterday() -> date:
    return datetime.now(timezone.utc).date() - timedelta(days=1)


def _log() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ------------------------------------------------------------------ Binance archive
@app.function(**common, cpu=2, memory=8192, timeout=6 * 3600)
def archive_symbol(symbol: str, start: str = "2020-01-01", end: str | None = None) -> dict:
    """Download, repair and validate one symbol's Binance history; report on the Volume."""
    import json

    import httpx

    from regimecast import config
    from regimecast.data import binance_archive as ba
    from regimecast.data import validate

    _log()
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date() if end else _yesterday()
    stats = {"archive": ba.run([symbol], ARCHIVE_DATASETS, s, e),
             "bookDepth": ba.run([symbol], ["bookDepth"], max(s, BOOKDEPTH_START), e)}
    volume.commit()
    with httpx.Client(follow_redirects=True) as client:
        stats["repair"] = {d: ba.repair(client, d, symbol) for d in ARCHIVE_DATASETS}
    report = validate.validate_symbol(symbol)
    out = config.DATA_DIR / "reports" / f"data_health_{symbol}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    volume.commit()
    return stats


@app.local_entrypoint()
def archive_all(symbols: str = ",".join(SYMBOLS), start: str = "2020-01-01"):
    for sym, stats in zip(symbols.split(","), archive_symbol.map(symbols.split(","), kwargs={"start": start})):
        print(sym, stats)


# ------------------------------------------------------------------ Bybit order book
@app.function(**common, cpu=1, memory=2048, timeout=1800, retries=2, max_containers=60)
def orderbook_day(symbol: str, day: str) -> str:
    """Replay one coin-day into 1-second and 1-minute features (raw file deleted after)."""
    import httpx

    from regimecast.data import orderbook

    _log()
    with httpx.Client(follow_redirects=True) as client:
        status = orderbook.process_day(client, symbol, datetime.strptime(day, "%Y-%m-%d").date())
    volume.commit()
    return status


def _days(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


@app.local_entrypoint()
def orderbook_backfill(start: str = ORDERBOOK_START.isoformat(), end: str = "", symbols: str = ",".join(SYMBOLS)):
    e = datetime.strptime(end, "%Y-%m-%d").date() if end else _yesterday()
    jobs = [(sym, d) for sym in symbols.split(",") for d in _days(datetime.strptime(start, "%Y-%m-%d").date(), e)]
    counts: dict[str, int] = {}
    for (sym, d), status in zip(jobs, orderbook_day.starmap(jobs, return_exceptions=True)):
        key = "error" if isinstance(status, Exception) else status.split(" ")[0]
        counts[key] = counts.get(key, 0) + 1
        if key not in ("ok", "skip"):
            print(sym, d, status)
    print(f"{len(jobs)} coin-days:", counts)


# ------------------------------------------------------------------ features
@app.function(**common, cpu=2, memory=16384, timeout=3 * 3600)
def features_symbol(symbol: str) -> dict:
    """Build the 1-minute and 1-hour feature tables for one symbol on the Volume."""
    from regimecast.features import build

    _log()
    volume.reload()
    out = build.build_all(symbol)
    volume.commit()
    return out


@app.local_entrypoint()
def features_all(symbols: str = ",".join(SYMBOLS)):
    for sym, out in zip(symbols.split(","), features_symbol.map(symbols.split(","))):
        print(sym, out)


# ------------------------------------------------------------------ daily upkeep
@app.function(**common, cpu=1, memory=2048, timeout=3 * 3600, schedule=modal.Cron("30 6 * * *"))
def daily_update() -> None:
    """Every day 06:30 UTC: yesterday's order book for each coin, then refresh features
    (Bybit publishes the previous day's file during the morning, UTC)."""
    day = _yesterday().isoformat()
    for status in orderbook_day.starmap([(s, day) for s in SYMBOLS], return_exceptions=True):
        print(day, status)
    for sym in SYMBOLS:
        archive_symbol.remote(sym, start=(_yesterday() - timedelta(days=3)).isoformat())
    for out in features_symbol.map(SYMBOLS):
        print(out)
