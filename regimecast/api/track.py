"""Track Record endpoints: what was forecast live and how it turned out (track/engine.py).

    GET /track-record?symbol=&horizon_hours=&limit=   the rows, newest first
    GET /track-record/summary                         the scores per coin and horizon, each with its sample size

Everything here comes from the live tables. No backtest number is read or mixed in.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Literal

import sqlalchemy as sa
from fastapi import APIRouter, FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from regimecast import config, db
from regimecast.db.schema import track_forecasts, track_scores
from regimecast.track import engine as te

Symbol = Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]

SMALL_SAMPLE_ROWS = 720     # 30 days of hourly scores for one coin and horizon; fixed before the first row was logged
SMALL_SAMPLE_EVENTS = 30    # stress events: rare, so the count of events is what matters
MISSING_SHOWN = 48
SUMMARY_TTL_SECONDS = 30.0


class TrackRow(BaseModel):
    symbol: str
    horizon_hours: int
    origin: str = Field(description="Hour the forecast is for: its inputs end here and its window starts here (UTC)")
    window_end: str = Field(description="When the outcome window ends (UTC)")
    logged_at: str = Field(description="When this row was written, always before window_end")
    made_at: str = Field(description="When the model cache produced the forecast")
    forecast_variance: float = Field(description="The forecast the app showed, as variance of the log return")
    forecast_model: str
    har_s_variance: float | None
    har_s_model: str | None
    benchmark_variance: float | None = Field(description="Realised variance of the window just before this one")
    p_stress: float | None = Field(description="Chance of a stress event in the hour after origin")
    stress_level: str | None
    regime: str | None
    status: Literal["pending", "scored", "no_data"] = Field(
        description="pending: the window has not closed or not been scored yet; no_data: too few prices to score it")
    scored_at: str | None
    realised_variance: float | None = Field(description="What happened, by the same definition as the models' target")
    qlike_forecast: float | None
    qlike_har_s: float | None
    qlike_benchmark: float | None
    stress_event: bool | None = Field(description="1 h rows: did the hour hold a stress event?")


class TrackRecordOut(BaseModel):
    symbol: str
    horizon_hours: int | None
    records: list[TrackRow] = Field(description="Newest first")


class Comparison(BaseModel):
    """Two mean errors on exactly the same rows."""
    n: int = Field(description="Scored rows both errors are averaged over")
    forecast: float | None
    rival: float | None
    vs_rival_pct: float | None = Field(description="Forecast's error versus the rival's, in %. Negative: forecast better")


class StressScore(BaseModel):
    n: int = Field(description="Scored hours with a stress chance and a label")
    events: int = Field(description="Hours among them that held a stress event")
    mean_p: float | None = Field(description="Average chance the model gave")
    actual_rate: float | None = Field(description="Share of hours that held a stress event")
    brier: float | None = Field(description="Average squared gap between the chance given and what happened (lower is better)")
    brier_base_rate: float | None = Field(description="The same score for always saying the usual rate")
    small_sample: bool


class SeriesSummary(BaseModel):
    symbol: str
    horizon_hours: int
    hours_expected: int = Field(description="Hours since logging began, up to the last one whose deadline has passed")
    rows_logged: int
    hours_missing: int
    missing_recent: list[str] = Field(description="Latest missing hours, newest first (at most 48)")
    rows_pending: int
    rows_scored: int
    rows_no_data: int
    separate_windows: int = Field(description="Scored rows divided by the horizon: 24 h windows logged hourly overlap")
    small_sample: bool = Field(description="True until 720 rows (30 days) are scored for this coin and horizon")
    n_qlike: int = Field(description="Scored rows with a QLIKE for the forecast")
    qlike_forecast: float | None
    vs_benchmark: Comparison
    vs_har_s: Comparison
    stress: StressScore | None = Field(description="1 h only: the stress chance is for the next hour")
    latest_regime: str | None


class TrackRules(BaseModel):
    log_deadline_minutes: int
    settle_minutes: int
    small_sample_rows: int
    small_sample_events: int


class TrackSummaryOut(BaseModel):
    logging_started_at: str | None = Field(description="When the first live row was written; null until then")
    last_logged_at: str | None
    logger_running: bool
    rows_logged: int
    rows_scored: int
    generated_at: str
    rules: TrackRules
    series: list[SeriesSummary]


def _iso(t) -> str:
    return te._utc(t).isoformat()


def _mean(v: list[float]) -> float | None:
    return sum(v) / len(v) if v else None


def compare(rows: list, rival: str) -> Comparison:
    """Mean QLIKE of the forecast and a rival on the rows that have both."""
    pairs = [(r["qlike_forecast"], r[rival]) for r in rows if r["qlike_forecast"] is not None and r[rival] is not None]
    f, o = _mean([p[0] for p in pairs]), _mean([p[1] for p in pairs])
    return Comparison(n=len(pairs), forecast=f, rival=o, vs_rival_pct=100 * (f / o - 1) if pairs and o else None)


def stress_score(rows: list) -> StressScore:
    """Brier score of the logged stress chance, next to always saying the usual rate logged with it."""
    done = [r for r in rows if r["stress_event"] is not None and r["p_stress"] is not None]
    e = [1.0 if r["stress_event"] else 0.0 for r in done]
    brier = _mean([(r["p_stress"] - y) ** 2 for r, y in zip(done, e)])
    base = _mean([(r["stress_base_rate"] - y) ** 2 for r, y in zip(done, e) if r["stress_base_rate"] is not None])
    return StressScore(n=len(done), events=int(sum(e)), mean_p=_mean([r["p_stress"] for r in done]), actual_rate=_mean(e),
                       brier=brier, brier_base_rate=base, small_sample=sum(e) < SMALL_SAMPLE_EVENTS)


def expected_hours(first: datetime, now: datetime) -> list[datetime]:
    """Every hour from the first logged one to the last whose logging deadline has passed."""
    hour = te._current_hour(now)
    last = hour if now >= hour + te.LOG_DEADLINE else hour - timedelta(hours=1)
    n = int((last - first).total_seconds() // 3600) + 1
    return [first + timedelta(hours=k) for k in range(max(n, 0))]


def summarise(rows: list, now: datetime) -> TrackSummaryOut:
    """All the page's numbers from the joined rows. A missing hour is simply one with no row."""
    logged = [te._utc(r["logged_at"]) for r in rows]
    first = min((te._utc(r["origin_ts"]) for r in rows), default=None)
    expected = expected_hours(first, now) if first else []
    series = []
    for symbol in config.SYMBOLS:
        for horizon in te.HORIZONS:
            mine = [r for r in rows if r["symbol"] == symbol and r["horizon_hours"] == horizon]
            have = {te._utc(r["origin_ts"]) for r in mine}
            missing = [h for h in expected if h not in have]
            scored = [r for r in mine if r["status"] == "scored"]
            qf = [r["qlike_forecast"] for r in scored if r["qlike_forecast"] is not None]
            latest = max(mine, key=lambda r: te._utc(r["origin_ts"]), default=None)
            series.append(SeriesSummary(
                symbol=symbol, horizon_hours=horizon, hours_expected=len(have.union(expected)), rows_logged=len(mine),
                hours_missing=len(missing), missing_recent=[h.isoformat() for h in missing[::-1][:MISSING_SHOWN]],
                rows_pending=sum(r["status"] is None for r in mine), rows_scored=len(scored),
                rows_no_data=sum(r["status"] == "no_data" for r in mine), separate_windows=len(scored) // horizon,
                small_sample=len(scored) < SMALL_SAMPLE_ROWS, n_qlike=len(qf), qlike_forecast=_mean(qf),
                vs_benchmark=compare(scored, "qlike_benchmark"), vs_har_s=compare(scored, "qlike_har_s"),
                stress=stress_score(scored) if horizon == 1 else None,
                latest_regime=latest["regime"] if latest else None))
    return TrackSummaryOut(
        logging_started_at=min(logged).isoformat() if logged else None,
        last_logged_at=max(logged).isoformat() if logged else None,
        logger_running=te.recorder.running, rows_logged=len(rows), rows_scored=sum(r["status"] == "scored" for r in rows),
        generated_at=now.isoformat(),
        rules=TrackRules(log_deadline_minutes=int(te.LOG_DEADLINE.total_seconds() // 60),
                         settle_minutes=int(te.SETTLE.total_seconds() // 60), small_sample_rows=SMALL_SAMPLE_ROWS,
                         small_sample_events=SMALL_SAMPLE_EVENTS),
        series=series)


def _joined():
    return track_forecasts.outerjoin(track_scores, track_scores.c.forecast_id == track_forecasts.c.id)


def _unavailable(detail: str) -> JSONResponse:
    return JSONResponse({"status": "not_ready", "detail": detail}, status_code=503)


NO_DB = "the Track Record needs the database, which is not reachable right now"

router = APIRouter(tags=["track record"])
_cache: dict = {}
_cache_lock = threading.Lock()


@router.get("/track-record", response_model=TrackRecordOut, responses={503: {"description": "No database"}})
def track_record(symbol: Symbol = "BTCUSDT", horizon_hours: Literal["1", "24"] | None = None,
                 limit: int = Query(500, ge=1, le=5000)):
    """Forecasts as logged live, before their outcome was known, with the scores added once each window closed."""
    f, s = track_forecasts.c, track_scores.c
    q = sa.select(track_forecasts, s.scored_at, s.status, s.realised_variance, s.qlike_forecast, s.qlike_har_s,
                  s.qlike_benchmark, s.stress_event).select_from(_joined()).where(f.symbol == symbol)
    if horizon_hours is not None:
        q = q.where(f.horizon_hours == int(horizon_hours))
    try:
        eng = db.get_engine()
        if eng is None:
            return _unavailable(NO_DB)
        with eng.begin() as conn:
            rows = conn.execute(q.order_by(f.origin_ts.desc(), f.horizon_hours).limit(limit)).mappings().all()
    except sa.exc.SQLAlchemyError:
        return _unavailable(NO_DB)
    return TrackRecordOut(symbol=symbol, horizon_hours=int(horizon_hours) if horizon_hours else None, records=[
        TrackRow(symbol=r["symbol"], horizon_hours=r["horizon_hours"], origin=_iso(r["origin_ts"]),
                 window_end=_iso(r["target_end"]), logged_at=_iso(r["logged_at"]), made_at=_iso(r["made_at"]),
                 forecast_variance=r["forecast_variance"], forecast_model=r["forecast_model"],
                 har_s_variance=r["har_s_variance"], har_s_model=r["har_s_model"],
                 benchmark_variance=r["benchmark_variance"], p_stress=r["p_stress"], stress_level=r["stress_level"],
                 regime=r["regime"], status=r["status"] or "pending",
                 scored_at=_iso(r["scored_at"]) if r["scored_at"] else None, realised_variance=r["realised_variance"],
                 qlike_forecast=r["qlike_forecast"], qlike_har_s=r["qlike_har_s"], qlike_benchmark=r["qlike_benchmark"],
                 stress_event=r["stress_event"]) for r in rows])


@router.get("/track-record/summary", response_model=TrackSummaryOut, responses={503: {"description": "No database"}})
def track_record_summary():
    """Live scores per coin and horizon, each with its sample size, and the missing hours. Kept for 30 seconds."""
    with _cache_lock:
        hit = _cache.get("summary")
    if hit and time.monotonic() < hit[0]:
        return hit[1]
    f, s = track_forecasts.c, track_scores.c
    q = sa.select(f.symbol, f.horizon_hours, f.origin_ts, f.logged_at, f.p_stress, f.stress_base_rate, f.regime, s.status,
                  s.qlike_forecast, s.qlike_har_s, s.qlike_benchmark, s.stress_event).select_from(_joined())
    try:
        eng = db.get_engine()
        if eng is None:
            return _unavailable(NO_DB)
        with eng.begin() as conn:
            rows = conn.execute(q).mappings().all()
    except sa.exc.SQLAlchemyError:
        return _unavailable(NO_DB)
    out = summarise(rows, datetime.now(timezone.utc))
    with _cache_lock:
        _cache["summary"] = (time.monotonic() + SUMMARY_TTL_SECONDS, out)
    return out


def install(app: FastAPI) -> None:
    app.include_router(router)
