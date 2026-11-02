"""Fill histories of the pool (PLAN 13D task 1), stored as Parquet, with how far back each one goes.

``userFillsByTime`` pages forward from a start time, at most 2000 fills a page, and only
the account's 10,000 most recent fills exist. So a busy account's history is short (its
oldest available fill may be days ago: ``truncated``), while a quiet one can go back
years. The depth recorded per account is the time from its oldest available fill to the
download, which is what the copy test's history check reads.

Files under ``DATA_DIR/traders``:

* ``pool.parquet``           the pool as selected at download time
* ``fills/<address>.parquet`` one row per fill (all coins, perps and spot)
* ``depth.jsonl``            one line per downloaded account (appended, so a stopped run resumes)

    python -m regimecast.traders.fills download [--limit N] [--per-minute 900]
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import pandas as pd

from regimecast import config
from regimecast.traders.hl import FILLS_AVAILABLE, FILLS_PAGE, Budget, Info, fetch_leaderboard, select_pool

log = logging.getLogger("traders.fills")

# Download order: only accounts with fills this old can enter the copy test (182 days) or pass the skill test (30)
DEEP_DAYS = (182, 30)
COLUMNS = ["time", "coin", "px", "sz", "side", "dir", "start_position", "closed_pnl", "fee", "fee_token",
           "crossed", "tid", "twap_id", "liquidated"]


def root() -> Path:
    return config.DATA_DIR / "traders"


def to_frame(fills: list[dict]) -> pd.DataFrame:
    """Raw fills -> typed table (time in UTC, numbers as floats)."""
    if not fills:
        return pd.DataFrame({c: pd.Series(dtype="float64") for c in COLUMNS}).astype(
            {"time": "datetime64[ms, UTC]", "coin": str, "side": str, "dir": str, "fee_token": str})
    df = pd.DataFrame({
        "time": pd.to_datetime([int(f["time"]) for f in fills], unit="ms", utc=True),
        "coin": [f["coin"] for f in fills],
        "px": [float(f["px"]) for f in fills],
        "sz": [float(f["sz"]) for f in fills],
        "side": [f["side"] for f in fills],
        "dir": [f.get("dir", "") for f in fills],
        "start_position": [float(f.get("startPosition") or 0.0) for f in fills],
        "closed_pnl": [float(f.get("closedPnl") or 0.0) for f in fills],
        "fee": [float(f.get("fee") or 0.0) for f in fills],
        "fee_token": [f.get("feeToken") or "" for f in fills],
        "crossed": [bool(f.get("crossed")) for f in fills],
        "tid": [int(f.get("tid") or 0) for f in fills],
        "twap_id": [str(f["twapId"]) if f.get("twapId") is not None else "" for f in fills],
        "liquidated": [bool(f.get("liquidation")) and (f["liquidation"].get("liquidatedUser") or "").lower()
                       == f.get("_user", "") for f in fills],
    })
    return df.drop_duplicates("tid", keep="first").sort_values(["time", "tid"]).reset_index(drop=True)


def download_account(info: Info, address: str, older_than_ms: int | None = None) -> list[dict] | None:
    """Every available fill of one account, oldest first (pages forward from the oldest).

    With ``older_than_ms`` the first call asks only for fills before that time, and an empty
    answer returns None: the account has no history that old (a cheap check, weight 20)."""
    out: dict[int, dict] = {}
    t = 0
    first = older_than_ms is not None
    while True:
        body = {"type": "userFillsByTime", "user": address, "startTime": t, "aggregateByTime": False}
        if first:
            body["endTime"] = older_than_ms
        page = info.post(body)
        if first and not page:
            return None
        new = 0
        for f in page:
            tid = int(f.get("tid") or 0)
            if tid not in out:
                f["_user"] = address
                out[tid] = f
                new += 1
        if first and len(page) < FILLS_PAGE:
            t = older_than_ms   # everything older is in hand; carry on from the cutoff
        elif len(page) < FILLS_PAGE or new == 0:
            break
        else:
            t = max(int(f["time"]) for f in page)   # fills sharing the last millisecond are de-duplicated by tid
        first = False
    return sorted(out.values(), key=lambda f: (int(f["time"]), int(f.get("tid") or 0)))


def depth_row(address: str, df: pd.DataFrame, downloaded_at: pd.Timestamp) -> dict:
    n = len(df)
    first = df["time"].min() if n else None
    last = df["time"].max() if n else None
    return {"address": address, "n_fills": n, "first_fill": first.isoformat() if n else None,
            "last_fill": last.isoformat() if n else None, "downloaded_at": downloaded_at.isoformat(),
            "truncated": n >= FILLS_AVAILABLE,
            "depth_days": round((downloaded_at - first).total_seconds() / 86400, 2) if n else 0.0}


def load_depth() -> pd.DataFrame:
    f = root() / "depth.jsonl"
    if not f.exists():
        return pd.DataFrame(columns=["address", "n_fills", "first_fill", "last_fill", "downloaded_at", "truncated",
                                     "depth_days"])
    rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return pd.DataFrame(rows).drop_duplicates("address", keep="last").reset_index(drop=True)


def load_checked(days: int) -> set[str]:
    """Accounts already found to have no fill older than ``days`` days."""
    f = root() / f"no_fills_older_than_{days}d.txt"
    return set(f.read_text().split()) if f.exists() else set()


def perp_depth() -> pd.DataFrame:
    """Per downloaded account: first and last perp fill, and how many (the copy test's history check)."""
    from regimecast.traders.scorecard import perp_fills

    rows = []
    for r in load_depth().itertuples():
        p = perp_fills(pd.read_parquet(root() / "fills" / f"{r.address}.parquet", columns=["time", "coin"]))
        n = len(p)
        rows.append({"address": r.address, "n_fills": n, "first_fill": p["time"].min().isoformat() if n else None,
                     "last_fill": p["time"].max().isoformat() if n else None, "downloaded_at": r.downloaded_at,
                     "truncated": bool(r.truncated), "n_all_fills": r.n_fills})
    return pd.DataFrame(rows)


def load_fills(address: str) -> pd.DataFrame:
    return pd.read_parquet(root() / "fills" / f"{address}.parquet")


def download(limit: int | None = None, per_minute: float = 900.0, checkpoint=None) -> pd.DataFrame:
    """Accounts with the oldest fills first (``DEEP_DAYS``), then the rest. ``checkpoint()`` runs every 25 accounts (Modal: commit the Volume)."""
    r = root()
    (r / "fills").mkdir(parents=True, exist_ok=True)
    pool_file = r / "pool.parquet"
    if pool_file.exists():
        pool = pd.read_parquet(pool_file)
    else:
        pool = pd.DataFrame(select_pool(fetch_leaderboard()))
        pool["selected_at"] = pd.Timestamp.now(tz="UTC").isoformat()
        pool.to_parquet(pool_file, index=False)
    log.info("pool: %d accounts (%d tracked by Whale Watch)", len(pool), int(pool["whale_watch"].sum()))
    done = set(load_depth()["address"])
    todo = [a for a in pool["address"] if a not in done][:limit]
    info = Info(Budget(per_minute))
    t0 = time.time()
    n = 0

    def save(a: str, fills: list[dict]) -> None:
        nonlocal n
        df = to_frame(fills)
        df.to_parquet(r / "fills" / f"{a}.parquet", index=False)
        with open(r / "depth.jsonl", "a") as out:   # closed each time: a Volume commit skips open files
            out.write(json.dumps(depth_row(a, df, pd.Timestamp.now(tz="UTC"))) + "\n")
        n += 1
        if n % 25 == 0:
            log.info("%d/%d accounts saved, %.0f min", n, len(todo), (time.time() - t0) / 60)
            if checkpoint:
                checkpoint()

    later = todo
    for days in DEEP_DAYS:   # one cheap question per account per step; only those with so old a fill are fetched
        cutoff = int((pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=days)).timestamp() * 1000)
        checked = load_checked(days)   # answers from before a restart: a Modal container can be replaced at any time
        left = []
        for i, a in enumerate(later, 1):
            if a in checked:
                left.append(a)
                continue
            try:
                fills = download_account(info, a, older_than_ms=cutoff)
            except Exception as e:
                log.warning("%s: %r (skipped, rerun to retry)", a, e)
                continue
            if fills is None:
                left.append(a)
                with open(r / f"no_fills_older_than_{days}d.txt", "a") as out:
                    out.write(a + "\n")
                if i % 100 == 0 and checkpoint:
                    checkpoint()
            else:
                save(a, fills)
        later = left
        log.info("older than %d days done (%d saved, %d left, %.0f min)", days, n, len(later), (time.time() - t0) / 60)
        if checkpoint:
            checkpoint()
    for a in later:
        try:
            save(a, download_account(info, a))
        except Exception as e:
            log.warning("%s: %r (skipped, rerun to retry)", a, e)
    if checkpoint:
        checkpoint()
    return load_depth()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["download"])
    ap.add_argument("--limit", type=int)
    ap.add_argument("--per-minute", type=float, default=900.0)
    a = ap.parse_args()
    download(a.limit, a.per_minute)


if __name__ == "__main__":
    main()
