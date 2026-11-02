"""From downloaded fills to the files the app ships: history check, scorecards, copy test.

Runs where the fills are (the Modal Volume, regimecast/jobs/modal_traders.py) and writes small
result files under ``DATA_DIR/traders/out``; scripts/traders_run.py copies them into the repo.

The copy test runs **once** (``copy_test``): each run is logged, and a second run refuses to
start unless it names the bug that made the first one invalid, as in the final evaluation.
"""
from __future__ import annotations

import gzip
import json
import logging
import math
import subprocess
from pathlib import Path

import numpy as np
import pandas as pd

from regimecast.traders import copytest as ct
from regimecast.traders import fills as fl
from regimecast.traders import scorecard as sc
from regimecast.traders.hl import Info

log = logging.getLogger("traders.pipeline")

LIMITS = [
    "Daily PnL is realised PnL minus fees; funding and open positions' unrealised PnL are not counted.",
    "Hyperliquid serves only about an account's latest 10,000 fills, so a busy account's history can be days long.",
    "Skill is judged on the history available; accounts on today's leaderboard are survivors, which flatters them.",
    "Return, drawdown and leverage use today's account value, because fills do not carry past account values.",
]


def out_dir() -> Path:
    d = fl.root() / "out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _clean(x):
    """JSON without NaN or infinity."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return float(x) if math.isfinite(x) else None
    return x


# ------------------------------------------------------------------ history check
def gate() -> dict:
    depth = fl.perp_depth()
    g = ct.history_gate(depth)
    pool = pd.read_parquet(fl.root() / "pool.parquet")
    g["pool_size"], g["downloaded"] = len(pool), len(depth)
    g["cut_short"] = int(depth["truncated"].sum())
    (out_dir() / "gate.json").write_text(json.dumps(_clean(g), indent=1))
    log.info("history check: %d of %d downloaded accounts have 6+ months of perp fills (need %d)",
             g["accounts_6m"], len(depth), g["needed"])
    return g


# ------------------------------------------------------------------ scorecards
def score(n_boot: int = sc.N_BOOT) -> dict:
    depth = fl.load_depth()
    pool = pd.read_parquet(fl.root() / "pool.parquet").set_index("address")
    rows = []
    for i, r in enumerate(depth.itertuples(), 1):
        p = sc.perp_fills(fl.load_fills(r.address))
        av = pool.at[r.address, "account_value"] if r.address in pool.index else None
        end = pd.Timestamp(r.downloaded_at)
        tr = sc.track_record(p, end, account_value=av, n_boot=n_boot, seed=i)
        rows.append({"address": r.address, "display_name": pool.at[r.address, "display_name"] if r.address in pool.index else "",
                     "account_value": av, "truncated": bool(r.truncated), **tr})
        if i % 200 == 0:
            log.info("scorecards: %d/%d", i, len(depth))
    cards = sc.label(pd.DataFrame(rows))
    counts = cards["label"].value_counts().to_dict()
    family = int(cards["bh_family"].iloc[0])
    order = cards.sort_values(["score", "address"], ascending=[False, True], na_position="last")
    keep = order[order["label"] != "no_trades"]
    out = {"summary": {"generated_at": pd.Timestamp.now(tz="UTC").isoformat(), "pool_size": len(pool),
                       "downloaded": len(depth), "accounts_with_perp_fills": family,
                       "counts": {k: int(counts.get(k, 0)) for k in sc.LABELS}, "fdr": sc.FDR_Q, "limits": LIMITS},
           "cards": _clean(keep.drop(columns=["bh_family"]).to_dict("records"))}
    with gzip.open(out_dir() / "scorecards.json.gz", "wt", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    cards.drop(columns=["coins"]).to_csv(out_dir() / "traders_scorecards.csv", index=False)
    log.info("scorecards: %s", out["summary"]["counts"])
    return out["summary"]


# ------------------------------------------------------------------ prices and funding for the copy test
def refresh_prices(start: pd.Timestamp, end: pd.Timestamp) -> None:
    """Fetch any Binance 1-minute files the archive on the Volume does not have yet."""
    from regimecast.data import binance_archive

    binance_archive.run([f"{c}USDT" for c in ct.COPY_COINS], ["klines"], start.date(), end.date())


def minute_closes(start: pd.Timestamp, end: pd.Timestamp) -> dict[str, pd.Series]:
    """Binance USD-M 1-minute closes from the archive on the Volume, indexed by the minute's open time."""
    from regimecast.data import store

    out = {}
    for c in ct.COPY_COINS:
        k = store.archive("klines", f"{c}USDT", columns=["ts", "close"])
        k = k[(k["ts"] >= start - pd.Timedelta(days=2)) & (k["ts"] < end)].drop_duplicates("ts").sort_values("ts")
        out[c] = pd.Series(k["close"].to_numpy(float), index=pd.DatetimeIndex(k["ts"]))
    return out


def funding_history(start: pd.Timestamp, end: pd.Timestamp, info: Info | None = None) -> dict[str, pd.Series]:
    """Hyperliquid's hourly funding rates per coin (500 a call, paged forward)."""
    info = info or Info()
    out = {}
    for c in ct.COPY_COINS:
        rows: dict[int, float] = {}
        t, stop = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
        while t < stop:
            page = info.post({"type": "fundingHistory", "coin": c, "startTime": t, "endTime": stop})
            if not page:
                break
            for r in page:
                rows[int(r["time"])] = float(r["fundingRate"])
            last = max(int(r["time"]) for r in page)
            if last <= t or len(page) < 500:
                break
            t = last
        out[c] = pd.Series(rows, dtype=float).sort_index()
        out[c].index = pd.to_datetime(out[c].index, unit="ms", utc=True)
    return out


def last_full_price_day(prices: dict[str, pd.Series]) -> pd.Timestamp:
    """Midnight after the last day every coin has prices through."""
    return min(s.index.max() for s in prices.values()).floor("D")


def price_coverage(prices: dict[str, pd.Series], a0: pd.Timestamp, end: pd.Timestamp) -> dict:
    """Share of the period's minutes each coin has a price for (reported with the result)."""
    want = (end - a0) / pd.Timedelta(minutes=1)
    return {c: float(((s.index >= a0) & (s.index < end)).sum() / want) for c, s in prices.items()}


# ------------------------------------------------------------------ the copy test (once)
def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5).stdout.strip()
    except OSError:
        return ""


def copy_test(rerun_reason: str | None = None, commit: str | None = None) -> dict:
    g = gate()
    if not g["historical_test"]:
        plain = (f"Only {g['accounts_6m']} accounts have 6+ months of fills (at least {g['needed']} are needed), so the "
                 "test becomes the pre-registered 8-week forward test.")
        out = {"status": "pending_forward", "plain": plain, "gate": _gate_public(g), "result": None}
        (out_dir() / "copy_test.json").write_text(json.dumps(_clean(out), indent=1))
        return out
    log_file = out_dir() / "copy_test_runs.jsonl"
    if log_file.exists() and not rerun_reason:
        raise RuntimeError("the copy test has already run; a rerun must name the bug it fixes (rerun_reason)")
    started = pd.Timestamp.now(tz="UTC").isoformat()
    with open(log_file, "a") as f:
        f.write(json.dumps({"started": started, "commit": commit or _commit(), "rerun_reason": rerun_reason}) + "\n")
    depth = fl.perp_depth()
    fills = {a: sc.perp_fills(fl.load_fills(a)) for a in g["eligible"]}
    per = ct.periods(depth, g["eligible"])
    refresh_prices(per["a_start"], per["end"])
    prices = minute_closes(per["a_start"], per["end"])
    cap = last_full_price_day(prices)
    per = ct.periods(depth, g["eligible"], cap)
    funding = funding_history(per["a_start"], per["end"])
    res = ct.run(fills, depth, prices, funding, end_cap=cap)
    selection = res.pop("selection")
    selection.to_csv(out_dir() / "traders_copy_selection.csv", index=False)
    r = res["result"]
    r["price_coverage"] = price_coverage(prices, per["a_start"], per["end"])
    verdict = "passed" if r["passed"] else "failed"
    plain = (f"Skill was measured on {res['a_start'][:10]} to {res['b_start'][:10]} and the top {r['k']} of "
             f"{res['universe']} accounts were copied from {res['b_start'][:10]} to {res['end'][:10]} ({r['days']} days). "
             f"Copying them {'beat' if r['passed'] else 'did not clear the bar against'} random accounts and buy-and-hold.")
    out = {"status": verdict, "plain": plain, "gate": _gate_public(g), "run_started": started,
           "result": {**res, **r, "result": None}}
    out["result"].pop("result", None)
    (out_dir() / "copy_test.json").write_text(json.dumps(_clean(out), indent=1))
    log.info("copy test: %s %s", verdict, json.dumps(_clean(r)))
    return out


def _gate_public(g: dict) -> dict:
    return {k: g[k] for k in ("accounts_with_fills", "accounts_6m", "needed", "min_span_days", "historical_test",
                              "pool_size", "downloaded", "cut_short", "span_days_quantiles")}
