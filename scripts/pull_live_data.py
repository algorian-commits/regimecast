"""Copy the files recorded on Railway to your own data/live folder.

    set RECORDER_URL=https://<your-recorder>.up.railway.app
    set RECORDER_TOKEN=...      (Railway -> recorder service -> Variables)
    python scripts/pull_live_data.py

Only downloads files you do not already have (finished days are one day.parquet).
Run it often: this copy is our only backup of the live data.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import os

import httpx

from regimecast import config

url, token = os.environ["RECORDER_URL"].rstrip("/"), os.environ["RECORDER_TOKEN"]
root = config.live_dir()
with httpx.Client(timeout=120, headers={"Authorization": f"Bearer {token}"}) as c:
    r = c.get(f"{url}/files")
    r.raise_for_status()
    files = r.json()
    new = 0
    for f in files:
        dest = root / f["path"]
        if dest.exists() and dest.stat().st_size == f["bytes"]:
            continue
        r = c.get(f"{url}/files/{f['path']}")
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(dest)
        new += 1

# Once the server has merged a day into day.parquet, our copies of that day's small
# files are duplicates of it. Remove them (only those, so a lost server volume can
# never make us delete data we hold).
on_server = {f["path"] for f in files}
removed = 0
for day_file in root.glob("*/date=*/day.parquet"):
    folder = day_file.parent
    if f"{folder.relative_to(root).as_posix()}/day.parquet" not in on_server:
        continue
    for part in folder.glob("*.parquet"):
        if part.name != "day.parquet" and part.relative_to(root).as_posix() not in on_server:
            part.unlink()
            removed += 1
print(f"{len(files)} files on server, {new} downloaded, {removed} merged parts removed, in {root}")
