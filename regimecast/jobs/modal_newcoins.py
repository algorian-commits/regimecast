"""Phase 13B's batch job on Modal: download, validate and build features for the new-coin exam's coins.

A separate app from modal_app.py on purpose (as jobs/modal_traders.py): this file has no schedule, so running it
cannot fire a second copy of the daily update. The steps are Phase 4's (binance_archive, validate, features.build),
on the same ``regimecast-data`` Volume, in each coin's own folders. Its image also carries the JSON files next to the
code, because risk/liquidation.py reads binance_brackets.json when it is imported.

Run from the repo root (set MODAL_PROFILE=saeed355ahmed first); it fans out on Modal, so the PC can disconnect::

    modal run --detach regimecast/jobs/modal_newcoins.py::run_all

Results for the PC: ``modal volume get regimecast-data parquet/features_1h/<SYMBOL> ...`` and ``targets_1h``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import modal

COINS = ["XRPUSDT", "DOGEUSDT", "BNBUSDT", "ADAUSDT", "AVAXUSDT"]
DATASETS = ["klines", "markPriceKlines", "indexPriceKlines", "premiumIndexKlines", "fundingRate", "metrics"]

app = modal.App("regimecast-newcoins")
volume = modal.Volume.from_name("regimecast-data", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install_from_requirements("requirements.txt")
         .pip_install("orjson")
         .env({"REGIMECAST_DATA_DIR": "/data"})
         .add_local_python_source("regimecast", ignore=lambda p: p.suffix not in (".py", ".json")))
common = dict(image=image, volumes={"/data": volume}, region="eu")


def _log() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.function(**common, cpu=2, memory=8192, timeout=8 * 3600)
def archive_symbol(symbol: str) -> dict:
    """Download, repair and validate one coin's Binance history; report on the Volume. Skips files it already has."""
    import json

    import httpx

    from regimecast import config
    from regimecast.data import binance_archive as ba
    from regimecast.data import validate

    _log()
    volume.reload()
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    stats = {"archive": ba.run([symbol], DATASETS, date(2020, 1, 1), end)}
    volume.commit()
    with httpx.Client(follow_redirects=True) as client:
        stats["repair"] = {d: ba.repair(client, d, symbol) for d in DATASETS}
    report = validate.validate_symbol(symbol)
    out = config.DATA_DIR / "reports" / f"data_health_{symbol}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str))
    volume.commit()
    return stats


@app.function(**common, cpu=2, memory=16384, timeout=3 * 3600)
def features_symbol(symbol: str) -> dict:
    """The 1-minute and 1-hour feature tables and the targets for one coin on the Volume."""
    from regimecast.features import build

    _log()
    volume.reload()
    out = build.build_all(symbol)
    volume.commit()
    return out


@app.function(**common, cpu=1, memory=1024, timeout=12 * 3600)
def run_all(symbols: list[str] = COINS) -> dict:
    """Archive, then features, per coin. Writes reports/new_coins_status.json when done."""
    import json

    from regimecast import config

    arch = [o if isinstance(o, dict) else {"error": repr(o)} for o in archive_symbol.map(symbols, return_exceptions=True)]
    feats = [o if isinstance(o, dict) else {"error": repr(o)} for o in features_symbol.map(symbols, return_exceptions=True)]
    status = {"finished": datetime.now(timezone.utc).isoformat(), "symbols": symbols, "archive": arch, "features": feats}
    volume.reload()
    path = config.DATA_DIR / "reports" / "new_coins_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, default=str))
    volume.commit()
    return status
