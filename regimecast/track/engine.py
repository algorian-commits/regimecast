"""Track Record: log every forecast live, before its outcome is known, and score it after the window closes.

The rules that make the record mean something:

* **Forecast for hour H, or nothing.** Each hour H (an hour end, as everywhere in RegimeCast) gets one row per coin and
  horizon, with what the app shows for H (``ForecastCache.get``) and HAR-S next to it. A forecast whose inputs do not
  end exactly at H is never logged as H's: the logger waits for the next refresh.
* **No backfilling.** A row can only be written in the first ``LOG_DEADLINE`` of its own hour, so it is always written
  before its window ends (the database checks that too). An hour that passes without one stays missing for good, and
  the page counts it.
* **Permanent.** Forecast rows and score rows only take inserts: the scorer adds a separate row and the database
  refuses UPDATE and DELETE on both (migration 0003_track). A restart cannot duplicate a row (one per coin, horizon
  and hour).
* **Same outcome as the evaluation.** Realised variance comes from ``realized.build_targets`` on Binance's 1-minute
  traded-price candles, and QLIKE from ``evaluate.qlike``: the code the models were evaluated with. The stress label
  comes from the training code as well (``build.minute_features``), with its full 30-day lookback.

Two background threads in the API, like the alert engine: one logs, one scores, so slow Binance calls while scoring
cannot make the logger miss an hour. They read the model cache and never touch a request. Any failure (database,
Binance) is logged and retried on the next tick. TRACK_RECORD=0 switches both off.
"""
from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import numpy as np
import pandas as pd
import sqlalchemy as sa

from regimecast import config, db
from regimecast.db.schema import track_forecasts, track_scores
from regimecast.vol import evaluate, realized

log = logging.getLogger("track.engine")

HORIZONS = (1, 24)
EVERY_SECONDS = 60.0
LOG_DEADLINE = timedelta(minutes=30)      # a row must be written this soon after H, or H stays missing
COMPLETE_WAIT = timedelta(minutes=20)     # wait this long for the other fields before logging the row without them
SETTLE = timedelta(minutes=7)             # score once Binance's candles have settled, and past the 5-minute forward fill
AFTER_WINDOW_MINUTES = 6                  # candles after the window: ffill of the last minutes works as in training
BEFORE_WINDOW_MINUTES = 30                # candles before the window: its first returns and forward fill
STRESS_LOOKBACK_MINUTES = 31 * 24 * 60    # the stress threshold looks back 30 days, plus its 15-minute variance
GIVE_UP = timedelta(hours=6)              # a window still lacking prices this long after closing has no outcome
RETRY_AFTER = timedelta(minutes=15)       # a window not scored by then (a gap, an outage) is retried less often...
RETRY_EVERY_MINUTES = 15                  # ...since each try reads a month of candles
MAX_SCORE_PER_TICK = 24                   # per coin, oldest first


def _utc(t) -> datetime:
    if isinstance(t, str):
        t = datetime.fromisoformat(t)
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def _current_hour(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


def _ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


# ------------------------------------------------------------------ the definitions (shared with the evaluation)
def realised_variance(bars: pd.DataFrame, origin: datetime, horizon: int) -> float | None:
    """Realised variance over (origin, origin + horizon], exactly as the models' target (vol/realized.py);
    None if the window has too few prices."""
    targets = _targets(bars)
    return _target_at(targets, origin, horizon)


def _targets(bars: pd.DataFrame) -> pd.DataFrame | None:
    return realized.build_targets(realized.minute_close(bars)) if not bars.empty and (bars["volume"] > 0).any() else None


def _target_at(targets: pd.DataFrame | None, origin: datetime, horizon: int) -> float | None:
    if targets is None:
        return None
    t = pd.Timestamp(origin)
    if t not in targets.index:
        return None
    v = float(targets.at[t, f"y_{horizon}h"])
    return v if math.isfinite(v) else None


def qlike(realised: float | None, forecast: float | None) -> float | None:
    """The evaluation's loss (vol/evaluate.py), or None when it is undefined: a missing value, or a variance of zero."""
    if not realised or not forecast or realised <= 0 or forecast <= 0:
        return None
    return float(evaluate.qlike(np.array([realised]), np.array([forecast]))[0])


def stress_hours(bars: pd.DataFrame) -> pd.Series:
    """Stress-event minutes per hour (index = hour end), by the training feature code. Only traded-price candles
    feed the label; the other inputs are left empty."""
    from regimecast.features import build

    empty = {k: None for k in ("mark", "index", "premium", "funding", "metrics")}
    m = build.minute_features({"last": bars, **empty}).set_index("ts")
    return m["stress"].resample("1h", label="right", closed="right").sum(min_count=1)


def stress_event(hours: pd.Series, origin: datetime) -> bool | None:
    """The stress classifier's label: did the hour after ``origin`` hold a stress minute? (models/stress.py)"""
    t = pd.Timestamp(origin) + pd.Timedelta(hours=1)
    if t not in hours.index or pd.isna(hours.at[t]):
        return None
    return bool(hours.at[t] > 0)


# ------------------------------------------------------------------ what the logger reads
def fetch_bars(symbol: str, minutes: int, end: datetime) -> pd.DataFrame:
    """The last ``minutes`` 1-minute candles that closed by ``end``, from Binance USD-M."""
    import httpx

    from regimecast.features.live import _klines

    with httpx.Client(base_url=config.BINANCE_FAPI_URL, timeout=10.0) as client:
        return _klines(client, "/fapi/v1/klines", {"symbol": symbol}, minutes, _ms(end) - 60_000)


class LiveView:
    """The API's own model cache and Binance's candles (a test passes a fake)."""

    def __init__(self, fetch: Callable[[str, int, datetime], pd.DataFrame] = fetch_bars):
        self.fetch = fetch

    def forecast(self, s, h):
        from regimecast.vol import live
        return live.cache.get(s, h)

    def har_s(self, s, h):
        from regimecast.vol import live
        return live.cache.get_har_s(s, h)

    def stress(self, s):
        from regimecast.vol import live
        return live.cache.get_stress(s)

    def regime(self, s):
        from regimecast.vol import live
        return live.cache.get_regime(s)

    def bars(self, s, minutes, end):
        return self.fetch(s, minutes, end)


def _for_hour(item: dict | None, hour: datetime) -> dict | None:
    """The item only if its inputs end exactly at this hour: never an older value under this hour's name."""
    return item if item and _utc(item["inputs_until"]) == hour else None


def _benchmark(view, symbol: str, horizon: int, hour: datetime) -> float | None:
    """The simple rival: the realised variance of the window that has just ended (hour - horizon, hour]. Only candles
    closed by ``hour`` are read."""
    try:
        bars = view.bars(symbol, horizon * 60 + BEFORE_WINDOW_MINUTES, hour)
        return realised_variance(bars[bars["ts"] + pd.Timedelta(minutes=1) <= pd.Timestamp(hour)],
                                 hour - timedelta(hours=horizon), horizon)
    except Exception as e:      # Binance hiccup: the logger tries again next minute
        log.warning("track benchmark %s %sh failed: %r", symbol, horizon, e)
        return None


def _insert_new(conn: sa.Connection, table: sa.Table, row: dict, key: list[str]) -> int:
    """Insert unless the row already exists (a restart, or two processes during a deploy)."""
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(table).values(**row).on_conflict_do_nothing(index_elements=key).returning(table.c[key[0]])
    return len(conn.execute(stmt).fetchall())   # rowcount is not reliable here: psycopg answers -1


# ------------------------------------------------------------------ logger
def build_row(view, symbol: str, horizon: int, hour: datetime, now: datetime) -> dict | None:
    """The row for this coin, horizon and hour; None while it should wait (no forecast for this hour yet)."""
    fc = _for_hour(view.forecast(symbol, horizon), hour)
    if fc is None:
        return None
    har = _for_hour(view.har_s(symbol, horizon), hour)
    st = _for_hour(view.stress(symbol), hour)
    rg = _for_hour(view.regime(symbol), hour)
    bench = _benchmark(view, symbol, horizon, hour)
    if any(x is None for x in (har, st, rg, bench)) and now < hour + COMPLETE_WAIT:
        return None     # refreshes write these one after another: give them a moment
    return {"symbol": symbol, "horizon_hours": horizon, "origin_ts": hour, "target_end": hour + timedelta(hours=horizon),
            "logged_at": now, "made_at": _utc(fc["made_at"]), "forecast_variance": float(fc["variance"]),
            "forecast_model": fc["model"][:80], "har_s_variance": float(har["variance"]) if har else None,
            "har_s_model": har["model"][:80] if har else None, "benchmark_variance": bench,
            "p_stress": float(st["p_stress_next_hour"]) if st else None,
            "stress_base_rate": float(st["base_rate"]) if st else None, "stress_level": st["level"] if st else None,
            "stress_model": st["model"][:80] if st else None, "regime": rg["regime"] if rg else None}


def log_hour(engine: sa.Engine, view, now: datetime | None = None) -> int:
    """Write this hour's rows that are not there yet; returns how many. Past the deadline it writes nothing, ever."""
    clock = (lambda: now) if now else (lambda: datetime.now(timezone.utc))
    now = clock()
    hour = _current_hour(now)
    if now >= hour + LOG_DEADLINE:
        return 0
    with engine.begin() as conn:
        have = {(r.symbol, r.horizon_hours) for r in conn.execute(
            sa.select(track_forecasts.c.symbol, track_forecasts.c.horizon_hours)
            .where(track_forecasts.c.origin_ts == hour))}
    rows = []
    for symbol in config.SYMBOLS:
        for horizon in HORIZONS:
            if (symbol, horizon) in have:
                continue
            try:
                row = build_row(view, symbol, horizon, hour, now)
            except Exception as e:    # one coin failing must not cost the others their row
                log.warning("track row %s %sh failed: %r", symbol, horizon, e)
                continue
            if row:
                rows.append(row)
    if not rows:
        return 0
    written = 0
    with engine.begin() as conn:     # the database refuses a row past its window's end
        for row in rows:
            written += _insert_new(conn, track_forecasts, {**row, "logged_at": clock()},
                                   ["symbol", "horizon_hours", "origin_ts"])
    return written


# ------------------------------------------------------------------ scorer
def score_due(engine: sa.Engine, fetch: Callable[[str, int, datetime], pd.DataFrame] = fetch_bars,
              now: datetime | None = None) -> int:
    """Add a score row for every forecast whose window has closed and has none yet; returns how many. A fetch that
    fails writes nothing, so the forecast stays pending until Binance answers."""
    now = now or datetime.now(timezone.utc)
    closed_by = now - SETTLE
    f, s = track_forecasts.c, track_scores.c
    with engine.begin() as conn:
        due = conn.execute(
            sa.select(track_forecasts).select_from(track_forecasts.outerjoin(track_scores, s.forecast_id == f.id))
            .where(s.forecast_id.is_(None), f.target_end <= closed_by).order_by(f.target_end, f.id)).mappings().all()
    retry_now = now.minute % RETRY_EVERY_MINUTES == 0
    by_symbol: dict[str, list] = {}
    for r in due:
        if retry_now or _utc(r["target_end"]) > now - RETRY_AFTER:
            by_symbol.setdefault(r["symbol"], []).append(r)
    written = 0
    for symbol, rows in by_symbol.items():
        try:
            written += _score_symbol(engine, symbol, rows[:MAX_SCORE_PER_TICK], fetch, now)
        except Exception as e:     # Binance hiccup: this coin stays pending
            log.warning("track score %s failed: %r", symbol, e)
    return written


def _score_symbol(engine: sa.Engine, symbol: str, rows: list, fetch, now: datetime) -> int:
    end = max(_utc(r["target_end"]) for r in rows) + timedelta(minutes=AFTER_WINDOW_MINUTES)
    start = min(_utc(r["origin_ts"]) - timedelta(minutes=STRESS_LOOKBACK_MINUTES if r["horizon_hours"] == 1
                                                 else BEFORE_WINDOW_MINUTES) for r in rows)
    bars = fetch(symbol, int((end - start).total_seconds() // 60), end)
    bars = bars[bars["ts"] + pd.Timedelta(minutes=1) <= pd.Timestamp(now)]
    targets = _targets(bars)
    has_1h = any(r["horizon_hours"] == 1 for r in rows)
    hours = pd.Series(dtype=float)
    if has_1h and targets is not None:
        try:
            hours = stress_hours(bars)
        except Exception as e:     # the stress label must not hold up the volatility scores
            log.warning("track stress label %s failed: %r", symbol, e)
    out = []
    for r in rows:
        score = _score_row(r, targets, hours, now)
        if score:
            out.append(score)
    written = 0
    with engine.begin() as conn:
        for score in out:
            written += _insert_new(conn, track_scores, score, ["forecast_id"])
    return written


def _score_row(r, targets: pd.DataFrame | None, hours: pd.Series, now: datetime) -> dict | None:
    origin, horizon = _utc(r["origin_ts"]), r["horizon_hours"]
    y = _target_at(targets, origin, horizon)
    if y is None and now < _utc(r["target_end"]) + GIVE_UP:
        return None       # prices not there yet: try again next tick
    return {"forecast_id": r["id"], "scored_at": now, "status": "scored" if y is not None else "no_data",
            "realised_variance": y, "qlike_forecast": qlike(y, r["forecast_variance"]),
            "qlike_har_s": qlike(y, r["har_s_variance"]), "qlike_benchmark": qlike(y, r["benchmark_variance"]),
            "stress_event": stress_event(hours, origin) if horizon == 1 and r["p_stress"] is not None else None}


# ------------------------------------------------------------------ background threads
class Recorder:
    def __init__(self, view=None, every: float = EVERY_SECONDS):
        self.view, self.every = view, every
        self.last_logged_at: str | None = None
        self.last_scored_at: str | None = None
        self.running = False
        self._threads: list[threading.Thread] = []

    def tick_log(self) -> int:
        eng = db.get_engine()
        if eng is None:
            return 0
        n = log_hour(eng, self.view or LiveView())
        if n:
            self.last_logged_at = datetime.now(timezone.utc).isoformat()
        return n

    def tick_score(self) -> int:
        eng = db.get_engine()
        if eng is None:
            return 0
        n = score_due(eng)
        if n:
            self.last_scored_at = datetime.now(timezone.utc).isoformat()
        return n

    def safely(self, tick: Callable[[], int]) -> None:
        """One run of a loop: whatever goes wrong (database, Binance) waits for the next tick and stops nothing else."""
        try:
            tick()
        except Exception as e:
            log.warning("track record tick failed: %r", e)

    def start(self) -> None:
        if self._threads or os.environ.get("TRACK_RECORD", "1") == "0":
            return

        def loop(tick, first_wait):
            time.sleep(first_wait)
            while True:
                t0 = time.monotonic()
                self.safely(tick)
                time.sleep(max(1.0, self.every - (time.monotonic() - t0)))

        self.running = True
        for name, tick, wait in (("track-log", self.tick_log, 45), ("track-score", self.tick_score, 90)):
            th = threading.Thread(target=loop, args=(tick, wait), name=name, daemon=True)
            th.start()
            self._threads.append(th)


recorder = Recorder()
