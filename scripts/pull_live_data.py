"""Copy the files recorded on Railway to your own data/live folder.

    set RECORDER_URL=https://<your-recorder>.up.railway.app
    set RECORDER_TOKEN=...
    python scripts/pull_live_data.py

Only downloads files you do not already have (finished days are one day.parquet).
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import os

import httpx

from regimecast import config

url, token = os.environ["RECORDER_URL"].rstrip("/"), os.environ["RECORDER_TOKEN"]
root = config.live_dir()
with httpx.Client(timeout=120) as c:
    files = c.get(f"{url}/files", params={"token": token}).json()
    new = 0
    for f in files:
        dest = root / f["path"]
        if dest.exists() and dest.stat().st_size == f["bytes"]:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(c.get(f"{url}/files/{f['path']}", params={"token": token}).content)
        new += 1
print(f"{len(files)} files on server, {new} downloaded to {root}")
