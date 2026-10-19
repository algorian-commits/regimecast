"""Live recorder: saves Binance USD-M market data that the free archive does NOT keep.

Runs 24/7 on Railway (see Dockerfile.recorder). It records:

* ``liquidations`` - every liquidation order on Binance futures (all symbols)
  from the ``!forceOrder@arr`` stream. Binance only sends the latest
  liquidation per symbol per second, so this is a lower bound.
* ``markprice``    - mark price, index price and funding rate every second.
* ``depth``        - top 20 order-book levels, sampled every DEPTH_SAMPLE_SECONDS.
* ``open_interest``- open interest polled from REST every OI_POLL_SECONDS.

Data goes to ``$REGIMECAST_DATA_DIR/live/<stream>/date=YYYY-MM-DD/*.parquet``.
Small files are merged into one file per day by ``compact()``.

A small web server shows health and lets you download the files:

* ``GET /health``          - message counts, last message time, errors; 503 if a stream is stale
* ``GET /files``           - list recorded files
* ``GET /files/<path>``    - download one file
* ``POST /flush``          - write buffers to disk now

/files and /flush need the header ``Authorization: Bearer $RECORDER_TOKEN``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from regimecast import config

log = logging.getLogger("recorder")

DEPTH_SAMPLE_SECONDS = float(os.environ.get("DEPTH_SAMPLE_SECONDS", "5"))
OI_POLL_SECONDS = float(os.environ.get("OI_POLL_SECONDS", "60"))
FLUSH_SECONDS = float(os.environ.get("FLUSH_SECONDS", "600"))
DEPTH_LEVELS = 20
# A connection that is open but silent this long is reconnected (Binance can stop
# serving a stream without closing the socket - that is how we lost mark price once).
SILENT_RECONNECT_SECONDS = float(os.environ.get("SILENT_RECONNECT_SECONDS", "60"))
# Max age of the last row before /health reports "stale". Liquidations are bursty, so not checked.
STALE_AFTER = {"markprice": 120.0, "depth": 120.0, "open_interest": 300.0}
# Stale this long -> flush and exit, so Railway's restart policy brings up a fresh process.
EXIT_AFTER_STALE_SECONDS = float(os.environ.get("EXIT_AFTER_STALE_SECONDS", "900"))


# ------------------------------------------------------------------ parsing
def _ts(ms) -> pd.Timestamp:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC")


def parse_message(msg: dict) -> tuple[str, dict] | None:
    """Turn one combined-stream message into (stream_name, row). None = ignore."""
    data = msg.get("data", msg)
    if isinstance(data, list):  # some endpoints wrap events in a list
        data = data[0] if data else {}
    etype = data.get("e")
    if etype == "forceOrder":
        o = data["o"]
        return "liquidations", {
            "event_ts": _ts(data["E"]), "trade_ts": _ts(o["T"]), "symbol": o["s"],
            "side": o["S"],  # SELL = a long was liquidated, BUY = a short was liquidated
            "order_type": o.get("o"), "status": o.get("X"),
            "qty": float(o["q"]), "price": float(o["p"]), "avg_price": float(o["ap"]),
            "last_filled_qty": float(o.get("l", 0)), "filled_qty": float(o.get("z", 0)),
            "notional_usd": float(o["ap"]) * float(o.get("z", o["q"])),
        }
    if etype == "markPriceUpdate":
        return "markprice", {
            "event_ts": _ts(data["E"]), "symbol": data["s"], "mark": float(data["p"]),
            "index": float(data["i"]), "est_settle": float(data.get("P", "nan")),
            "funding_rate": float(data["r"]), "next_funding_ts": _ts(data["T"]),
        }
    if etype == "depthUpdate":
        row = {"event_ts": _ts(data["E"]), "tx_ts": _ts(data.get("T", data["E"])), "symbol": data["s"],
               "last_update_id": int(data.get("u", 0))}
        for side, key in (("b", "b"), ("a", "a")):
            levels = data.get(key, [])[:DEPTH_LEVELS]
            for i in range(DEPTH_LEVELS):
                p, q = (levels[i] if i < len(levels) else (None, None))
                row[f"{side}_p{i}"] = float(p) if p is not None else None
                row[f"{side}_q{i}"] = float(q) if q is not None else None
        return "depth", row
    return None


# ------------------------------------------------------------------ storage
class Store:
    def __init__(self, root: Path):
        self.root = root
        self.buf: dict[str, list[dict]] = defaultdict(list)
        self.counts: dict[str, int] = defaultdict(int)
        self.last_seen: dict[str, str] = {}
        self.last_time: dict[str, float] = {}  # time.time() of the last row, for staleness checks
        self._flush_lock = threading.Lock()  # /flush (thread pool) and flush_loop can overlap

    def add(self, stream: str, row: dict) -> None:
        self.buf[stream].append(row)
        self.counts[stream] += 1
        self.last_seen[stream] = datetime.now(timezone.utc).isoformat()
        self.last_time[stream] = time.time()

    def flush(self) -> int:
        """Write buffered rows to Parquet. Rows leave the buffer only after they are
        on disk, so a failed write (disk full, bad row) loses nothing."""
        with self._flush_lock:
            return self._flush()

    def _flush(self) -> int:
        written = 0
        for stream, rows in list(self.buf.items()):
            if not rows:
                continue
            n = len(rows)
            df = pd.DataFrame(rows[:n])
            df["day"] = df["event_ts"].dt.strftime("%Y-%m-%d")
            for day, part in df.groupby("day"):
                folder = self.root / stream / f"date={day}"
                folder.mkdir(parents=True, exist_ok=True)
                name = datetime.now(timezone.utc).strftime("%H%M%S_%f") + ".parquet"
                tmp = folder / (name + ".tmp")
                part.drop(columns="day").to_parquet(tmp, index=False)
                tmp.replace(folder / name)
                written += len(part)
            del rows[:n]
        return written


def compact(root: Path, today: str | None = None) -> int:
    """Merge all small files of each finished day into one ``day.parquet``."""
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    merged = 0
    for folder in root.glob("*/date=*"):
        day = folder.name.split("=", 1)[1]
        if day >= today:
            continue
        parts = [f for f in folder.glob("*.parquet") if f.name != "day.parquet"]
        if not parts:
            continue
        frames = [pd.read_parquet(f) for f in parts]
        day_file = folder / "day.parquet"
        if day_file.exists():
            frames.insert(0, pd.read_parquet(day_file))
        df = pd.concat(frames, ignore_index=True).drop_duplicates().sort_values("event_ts")
        tmp = folder / "day.tmp"
        df.to_parquet(tmp, index=False)
        tmp.replace(day_file)
        for f in parts:
            f.unlink()
        merged += 1
    return merged


# ------------------------------------------------------------------ recorder
class Recorder:
    def __init__(self, symbols: list[str], root: Path):
        self.symbols = [s.lower() for s in symbols]
        self.store = Store(root)
        self.root = root
        self.errors: list[str] = []
        self.started = datetime.now(timezone.utc).isoformat()
        self._t0 = time.time()
        self.connected: dict[str, bool] = {"market": False, "public": False}
        self._last_depth: dict[str, float] = {}

    def stream_urls(self) -> dict[str, str]:
        """Binance split USD-M websockets in 2026: mark price and liquidations only
        come on /market, order-book depth on /public (the old root URL is depth only)."""
        market = ["!forceOrder@arr"] + [f"{s}@markPrice@1s" for s in self.symbols]
        public = [f"{s}@depth20@500ms" for s in self.symbols]
        base = config.BINANCE_FSTREAM_URL
        return {"market": f"{base}/market/stream?streams=" + "/".join(market),
                "public": f"{base}/public/stream?streams=" + "/".join(public)}

    def _err(self, text: str) -> None:
        log.warning(text)
        self.errors = (self.errors + [f"{datetime.now(timezone.utc).isoformat()} {text}"])[-20:]

    def handle(self, raw: str) -> None:
        parsed = parse_message(json.loads(raw))
        if not parsed:
            return
        stream, row = parsed
        if stream == "depth":  # keep one snapshot every DEPTH_SAMPLE_SECONDS per symbol
            now = time.monotonic()
            if now - self._last_depth.get(row["symbol"], 0) < DEPTH_SAMPLE_SECONDS:
                return
            self._last_depth[row["symbol"]] = now
        self.store.add(stream, row)

    async def ws_loop(self, name: str) -> None:
        import websockets

        url = self.stream_urls()[name]
        backoff = 1
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, max_size=2**22) as ws:
                    self.connected[name], backoff = True, 1
                    log.info("connected: %s", url)
                    while True:
                        self.handle(await asyncio.wait_for(ws.recv(), timeout=SILENT_RECONNECT_SECONDS))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # Binance drops connections every 24h; just reconnect
                self._err(f"websocket {name}: {e!r}")
            self.connected[name] = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def oi_loop(self) -> None:
        import httpx

        async with httpx.AsyncClient(base_url=config.BINANCE_FAPI_URL, timeout=15) as client:
            while True:
                for s in self.symbols:
                    try:
                        r = await client.get("/fapi/v1/openInterest", params={"symbol": s.upper()})
                        r.raise_for_status()
                        d = r.json()
                        self.store.add("open_interest", {"event_ts": _ts(d["time"]), "symbol": d["symbol"],
                                                         "open_interest": float(d["openInterest"])})
                    except Exception as e:
                        self._err(f"open interest {s}: {e!r}")
                await asyncio.sleep(OI_POLL_SECONDS)

    async def flush_loop(self) -> None:
        last_compact = 0.0
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            try:
                n = await asyncio.to_thread(self.store.flush)
                log.info("flushed %d rows", n)
                if time.monotonic() - last_compact > 3600:
                    # merging a day of 1-second rows takes a few seconds; keep it off the event loop
                    await asyncio.to_thread(compact, self.root)
                    last_compact = time.monotonic()
            except Exception as e:
                self._err(f"flush: {e!r}")

    def stale_streams(self, now: float | None = None) -> dict[str, float]:
        """Streams whose last row is older than STALE_AFTER -> seconds since last row.
        Measured from process start for streams that have not sent anything yet."""
        now = now or time.time()
        ages = {s: now - self.store.last_time.get(s, self._t0) for s in STALE_AFTER}
        return {s: round(a) for s, a in ages.items() if a > STALE_AFTER[s]}

    async def watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            stale = self.stale_streams()
            if stale and max(stale.values()) > EXIT_AFTER_STALE_SECONDS:
                log.error("streams stale %s, flushing and exiting so the platform restarts us", stale)
                try:
                    self.store.flush()
                finally:
                    os._exit(1)

    def health(self) -> dict:
        stale = self.stale_streams()
        return {"status": "stale" if stale else "ok", "stale": stale,
                "started": self.started, "connected": self.connected, "symbols": self.symbols,
                "messages": dict(self.store.counts), "last_message": self.store.last_seen,
                "buffered": {k: len(v) for k, v in self.store.buf.items()}, "recent_errors": self.errors}


# ------------------------------------------------------------------ web app
def create_app(start_tasks: bool = True):
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.responses import FileResponse, JSONResponse

    root = config.live_dir()
    root.mkdir(parents=True, exist_ok=True)
    rec = Recorder(config.SYMBOLS, root)
    token = os.environ.get("RECORDER_TOKEN", "")

    @asynccontextmanager
    async def lifespan(app):
        tasks = []
        if start_tasks:
            tasks = [asyncio.create_task(c) for c in (rec.ws_loop("market"), rec.ws_loop("public"),
                                                       rec.oi_loop(), rec.flush_loop(), rec.watchdog_loop())]
        yield
        for t in tasks:
            t.cancel()
        rec.store.flush()

    app = FastAPI(title="RegimeCast recorder", lifespan=lifespan)
    app.state.recorder = rec

    def check(authorization: str | None) -> None:
        # Token goes in a header, not the URL, so it never shows up in request logs.
        given = (authorization or "").removeprefix("Bearer ").strip()
        if not token or not secrets.compare_digest(given.encode(), token.encode()):
            raise HTTPException(401, "bad token")

    @app.get("/health")
    def health():
        h = rec.health()
        return JSONResponse(h, status_code=200 if h["status"] == "ok" else 503)

    @app.post("/flush")
    def flush(authorization: str | None = Header(None)):
        check(authorization)
        return {"rows": rec.store.flush(), "compacted_days": compact(root)}

    @app.get("/files")
    def files(authorization: str | None = Header(None)):
        check(authorization)
        return [{"path": str(f.relative_to(root)).replace("\\", "/"), "bytes": f.stat().st_size}
                for f in sorted(root.rglob("*.parquet"))]

    @app.get("/files/{path:path}")
    def get_file(path: str, authorization: str | None = Header(None)):
        check(authorization)
        f = (root / path).resolve()
        if root.resolve() not in f.parents or not f.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(f)

    return app


def main() -> None:
    import uvicorn

    # Railway marks everything on stderr as an error, so only WARNING and up go there.
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    out, err = logging.StreamHandler(sys.stdout), logging.StreamHandler(sys.stderr)
    out.addFilter(lambda r: r.levelno < logging.WARNING)
    err.setLevel(logging.WARNING)
    for h in (out, err):
        h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[out, err])
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per open-interest poll otherwise
    # log_config=None: uvicorn logs through the handlers above. No access log: Railway keeps HTTP logs.
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")),
                log_config=None, access_log=False)


if __name__ == "__main__":
    main()
