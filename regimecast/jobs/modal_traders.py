"""Phase 13D's batch jobs on Modal: trader fill histories, the history check, scorecards, the copy test.

A separate app from modal_app.py on purpose. ``modal run --detach`` keeps a temporary copy of every
function in the file running, schedules included, so running a long job from modal_app.py would also
fire a second copy of its daily update. This file has no schedule. It shares the ``regimecast-data``
Volume and its ``traders/`` folder.

Run from the repo root (set MODAL_PROFILE=saeed355ahmed and PYTHONUTF8=1 first)::

    modal run --detach regimecast/jobs/modal_traders.py::trader_fills
    python scripts/traders_run.py gate
"""
from __future__ import annotations

import modal

app = modal.App("regimecast-traders")
volume = modal.Volume.from_name("regimecast-data", create_if_missing=True)
image = (modal.Image.debian_slim(python_version="3.12")
         .pip_install_from_requirements("requirements.txt")
         .env({"REGIMECAST_DATA_DIR": "/data"})
         .add_local_python_source("regimecast"))
common = dict(image=image, volumes={"/data": volume}, region="eu")


def _log() -> None:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


@app.function(**common, cpu=1, memory=4096, timeout=24 * 3600)
def trader_fills(per_minute: float = 1000.0) -> None:
    """Hyperliquid fill histories of the trader pool onto the Volume (traders/). One container on purpose:
    Hyperliquid's rate limit is per IP address, and this stays inside it. Resumes where it stopped."""
    _log()
    from regimecast.traders import fills

    volume.reload()
    fills.download(per_minute=per_minute, checkpoint=volume.commit)


@app.function(**common, cpu=1, memory=4096, timeout=3600)
def trader_gate() -> dict:
    """The copy test's history check on what has been downloaded so far (traders/out/gate.json)."""
    _log()
    from regimecast.traders import pipeline

    volume.reload()
    g = pipeline.gate()
    volume.commit()
    return {k: v for k, v in g.items() if k != "eligible"}


@app.function(**common, cpu=2, memory=8192, timeout=6 * 3600)
def trader_scorecards() -> dict:
    """Scorecards for every downloaded account (traders/out/scorecards.json.gz)."""
    _log()
    from regimecast.traders import pipeline

    volume.reload()
    s = pipeline.score()
    volume.commit()
    return s


@app.function(**common, cpu=2, memory=16384, timeout=6 * 3600)
def trader_copy_test(commit: str, rerun_reason: str = "") -> dict:
    """The pre-registered copy test: once (traders/out/copy_test.json). Refuses a rerun without a named bug."""
    _log()
    from regimecast.traders import pipeline

    volume.reload()
    out = pipeline.copy_test(rerun_reason=rerun_reason or None, commit=commit)
    volume.commit()
    return out
