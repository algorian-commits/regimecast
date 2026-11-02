"""The results files shipped with the code (the deployed API has no data/), and the page's wording.

``scorecards.json.gz`` and ``copy_test.json`` are written by scripts/traders_run.py. ``headline`` is
the only place that decides what the page may say about following the skilled traders: it says
"has worked" only if the copy test ran and passed (PLAN 13D pass marks).
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCORECARDS = HERE / "scorecards.json.gz"
COPY_TEST = HERE / "copy_test.json"
WORKED = "Following them has worked: the copy test passed."
NOT_SHOWN = "Past results; following them was not shown to pay."


@lru_cache(maxsize=1)
def scorecards() -> dict | None:
    if not SCORECARDS.exists():
        return None
    with gzip.open(SCORECARDS, "rt", encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def copy_test() -> dict | None:
    return json.loads(COPY_TEST.read_text(encoding="utf-8")) if COPY_TEST.exists() else None


def headline(result: dict | None) -> dict:
    """{"worked": bool, "sentence": str}; ``worked`` only when a finished test passed its bar."""
    r = result or {}
    worked = r.get("status") == "passed" and (r.get("result") or {}).get("passed") is True
    return {"worked": worked, "sentence": WORKED if worked else NOT_SHOWN}
