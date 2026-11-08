"""Track Record: logged before the outcome, scored by the evaluation's own maths, restart-safe, gaps stay gaps.
Same database setup as tests/test_accounts.py (SQLite here, PostgreSQL in CI)."""
import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from regimecast import db
from regimecast.api import main
from regimecast.api import track as api
from regimecast.db.schema import metadata, track_forecasts, track_scores
from regimecast.features import build
from regimecast.track import engine as te
from regimecast.vol import evaluate, realized

H = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
COINS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


@pytest.fixture()
def eng(monkeypatch):
    """A fresh migrated database per test: the triggers refuse the DELETE other test modules clean up with."""
    e = db.make_engine(os.environ.get("TEST_DATABASE_URL") or "sqlite://")
    with e.begin() as c:
        metadata.drop_all(c)
        c.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    db.upgrade(e)
    monkeypatch.setattr(db, "_engine", e)
    api._cache.clear()
    yield e
    with e.begin() as c:     # leave nothing the next module's row-by-row clean-up would be refused on
        metadata.drop_all(c)
        c.execute(sa.text("DROP TABLE IF EXISTS alembic_version"))
    e.dispose()


def make_bars(start: str, minutes: int, seed: int = 1) -> pd.DataFrame:
    """1-minute candles (``ts`` = open time) in Binance's shape, with volatility that changes over time."""
    rng = np.random.default_rng(seed)
    scale = 0.0008 * (1 + 0.5 * np.sin(np.arange(minutes) / 700))
    close = 100 * np.exp(np.cumsum(rng.normal(0, scale)))
    n = minutes
    return pd.DataFrame({"ts": pd.date_range(start, periods=n, freq="1min", tz="UTC"), "open": close, "high": close * 1.001,
                         "low": close * 0.999, "close": close, "volume": 5.0, "quote_volume": 500.0,
                         "taker_buy_volume": 2.5, "count": 10})


class View:
    """A market and model cache the test sets by hand. ``hour`` is what the cache's forecasts are currently for."""

    def __init__(self, hour=H, variance=1e-4, bars=None):
        self.hour, self.variance = hour, variance
        self.bars_data = bars if bars is not None else make_bars("2026-11-03", 4 * 24 * 60)
        self.ends: list = []
        self.fail_bars = False
        self.no_stress = False

    def _fc(self, model, var):
        return {"variance": var, "model": model, "inputs_until": self.hour.isoformat(),
                "made_at": (self.hour + timedelta(minutes=3)).isoformat()}

    def forecast(self, s, h):
        return self._fc("HAR-S + LightGBM (fitted to 2026-09-30)", self.variance * h)

    def har_s(self, s, h):
        return self._fc("HAR-S (fitted to 2025-09-30)", 1.1 * self.variance * h)

    def stress(self, s):
        if self.no_stress:
            return None
        return {"p_stress_next_hour": 0.2, "base_rate": 0.05, "level": "elevated", "inputs_until": self.hour.isoformat(),
                "model": "stress classifier (fitted to 2025-09-30)"}

    def regime(self, s):
        return {"regime": "calm", "inputs_until": self.hour.isoformat()}

    def bars(self, s, minutes, end):
        self.ends.append(end)
        if self.fail_bars:
            raise OSError("Binance is down")
        b = self.bars_data
        return b[b["ts"] + pd.Timedelta(minutes=1) <= pd.Timestamp(end)].tail(minutes)


def rows(eng, table=track_forecasts):
    with eng.begin() as c:
        return c.execute(sa.select(table)).mappings().all()


def as_utc(t):
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t


def rows_for_summary(eng):
    f, s = track_forecasts.c, track_scores.c
    with eng.begin() as c:
        return c.execute(sa.select(f.symbol, f.horizon_hours, f.origin_ts, f.logged_at, f.p_stress, f.stress_base_rate,
                                   f.regime, s.status, s.qlike_forecast, s.qlike_har_s, s.qlike_benchmark, s.stress_event)
                         .select_from(track_forecasts.outerjoin(track_scores, s.forecast_id == f.id))).mappings().all()


# ------------------------------------------------------------------ the logger: no look-ahead
def test_one_row_per_coin_and_horizon_with_every_field(eng):
    assert te.log_hour(eng, View(), H + timedelta(minutes=11)) == 6
    got = rows(eng)
    assert {(r["symbol"], r["horizon_hours"]) for r in got} == {(c, h) for c in COINS for h in (1, 24)}
    r = next(x for x in got if x["symbol"] == "BTCUSDT" and x["horizon_hours"] == 24)
    assert as_utc(r["origin_ts"]) == H and as_utc(r["target_end"]) == H + timedelta(hours=24)
    assert r["forecast_variance"] == pytest.approx(24e-4) and r["har_s_variance"] == pytest.approx(1.1 * 24e-4)
    assert r["forecast_model"].startswith("HAR-S + LightGBM") and r["har_s_model"].startswith("HAR-S (")
    assert r["p_stress"] == 0.2 and r["stress_level"] == "elevated" and r["regime"] == "calm"
    assert as_utc(r["made_at"]) == H + timedelta(minutes=3) and as_utc(r["logged_at"]) == H + timedelta(minutes=11)
    assert r["benchmark_variance"] > 0


def test_every_row_is_written_before_its_window_ends(eng):
    te.log_hour(eng, View(), H + timedelta(minutes=29))
    assert all(as_utc(r["logged_at"]) < as_utc(r["target_end"]) for r in rows(eng))


def test_database_refuses_a_row_written_after_its_window(eng):
    bad = {"symbol": "BTCUSDT", "horizon_hours": 1, "origin_ts": H, "target_end": H + timedelta(hours=1),
           "logged_at": H + timedelta(hours=1), "made_at": H, "forecast_variance": 1e-4, "forecast_model": "x"}
    with pytest.raises(sa.exc.IntegrityError):
        with eng.begin() as c:
            c.execute(sa.insert(track_forecasts).values(**bad))


def test_an_older_forecast_is_never_logged_under_this_hour(eng):
    stale = View(hour=H - timedelta(hours=1))
    assert te.log_hour(eng, stale, H + timedelta(minutes=5)) == 0
    assert te.log_hour(eng, stale, H + timedelta(minutes=25)) == 0
    assert rows(eng) == []


def test_benchmark_reads_only_candles_closed_by_the_hour(eng):
    bars = make_bars("2026-11-03", 3 * 24 * 60)
    v1, v2 = View(bars=bars), View(bars=bars.copy())
    future = v2.bars_data["ts"] >= pd.Timestamp(H)
    v2.bars_data.loc[future, "close"] *= np.exp(np.random.default_rng(9).normal(0, 0.05, future.sum()))
    a = te._benchmark(v1, "BTCUSDT", 24, H)
    assert a == te._benchmark(v2, "BTCUSDT", 24, H)
    assert all(e <= H for e in v1.ends)
    # and it is exactly the last window's realised variance on the full series
    t = realized.build_targets(realized.minute_close(bars))
    assert a == pytest.approx(t.at[pd.Timestamp(H - timedelta(hours=24)), "y_24h"])
    assert te._benchmark(v1, "BTCUSDT", 1, H) == pytest.approx(t.at[pd.Timestamp(H - timedelta(hours=1)), "y_1h"])


# ------------------------------------------------------------------ restarts and missing hours
def test_restart_writes_no_duplicates_and_keeps_the_first_values(eng):
    assert te.log_hour(eng, View(), H + timedelta(minutes=11)) == 6
    first = rows(eng)
    assert te.log_hour(eng, View(variance=9e-4), H + timedelta(minutes=12)) == 0     # new process, new numbers
    assert rows(eng) == first


def test_two_processes_racing_for_the_same_hour_write_one_row(eng):
    r = te.build_row(View(), "BTCUSDT", 1, H, H + timedelta(minutes=11))
    key = ["symbol", "horizon_hours", "origin_ts"]
    with eng.begin() as c:
        assert te._insert_new(c, track_forecasts, r, key) == 1
        assert te._insert_new(c, track_forecasts, r, key) == 0
    assert len(rows(eng)) == 1


def test_a_late_process_cannot_backfill_the_hour(eng):
    assert te.log_hour(eng, View(), H + te.LOG_DEADLINE) == 0
    assert rows(eng) == []
    assert te.log_hour(eng, View(hour=H), H + timedelta(hours=1, minutes=5)) == 0    # next hour, this hour's forecast
    assert rows(eng) == []


def test_missing_hours_stay_visible(eng):
    te.log_hour(eng, View(hour=H), H + timedelta(minutes=10))
    two = H + timedelta(hours=2)
    te.log_hour(eng, View(hour=two), two + timedelta(minutes=10))        # hour H + 1 never logged: a restart, an outage
    s = api.summarise(rows_for_summary(eng), two + timedelta(minutes=45))
    assert s.logging_started_at == (H + timedelta(minutes=10)).isoformat()
    for x in s.series:
        assert (x.hours_expected, x.rows_logged, x.hours_missing) == (3, 2, 1)
        assert x.missing_recent == [(H + timedelta(hours=1)).isoformat()]


def test_this_hour_is_not_called_missing_before_its_deadline(eng):
    te.log_hour(eng, View(hour=H), H + timedelta(minutes=10))
    next_hour = H + timedelta(hours=1)
    assert api.summarise(rows_for_summary(eng), next_hour + timedelta(minutes=5)).series[0].hours_missing == 0
    assert api.summarise(rows_for_summary(eng), next_hour + te.LOG_DEADLINE).series[0].hours_missing == 1


def test_waits_for_the_other_fields_then_logs_without_them(eng):
    v = View()
    v.no_stress = True
    assert te.log_hour(eng, v, H + timedelta(minutes=5)) == 0
    assert te.log_hour(eng, v, H + te.COMPLETE_WAIT) == 6
    assert all(r["p_stress"] is None and r["forecast_variance"] > 0 for r in rows(eng))


def test_failures_never_reach_the_caller(eng, monkeypatch):
    v = View()
    v.fail_bars = True
    assert te.log_hour(eng, v, H + timedelta(minutes=5)) == 0       # Binance down: wait
    assert te.log_hour(eng, v, H + te.COMPLETE_WAIT) == 6           # still logged, without the benchmark
    assert all(r["benchmark_variance"] is None for r in rows(eng))

    def boom():
        raise sa.exc.OperationalError("connect", {}, OSError("database is down"))

    monkeypatch.setattr(db, "get_engine", boom)
    rec = te.Recorder(view=View())
    rec.safely(rec.tick_log)
    rec.safely(rec.tick_score)
    c = TestClient(main.app)
    assert c.get("/track-record/summary").status_code == 503
    assert c.get("/track-record").status_code == 503
    assert c.get("/health").status_code == 200


# ------------------------------------------------------------------ permanence
def test_the_database_refuses_changes_to_forecasts_and_scores(eng):
    v = View()
    te.log_hour(eng, v, H + timedelta(minutes=10))
    te.score_due(eng, lambda s, m, e: v.bars(s, m, e), H + timedelta(hours=1, minutes=8))
    assert len(rows(eng, track_scores)) == 3
    for stmt in (sa.update(track_forecasts).values(forecast_variance=1.0), sa.delete(track_forecasts),
                 sa.update(track_scores).values(qlike_forecast=0.0), sa.delete(track_scores)):
        with pytest.raises(sa.exc.DBAPIError, match="never changed"):
            with eng.begin() as c:
                c.execute(stmt)


# ------------------------------------------------------------------ the scorer
def test_qlike_is_the_evaluations_on_known_numbers():
    assert te.qlike(2e-4, 1e-4) == pytest.approx(2 - math.log(2) - 1)
    assert te.qlike(1e-4, 1e-4) == pytest.approx(0)
    assert te.qlike(1e-4, 2e-4) == pytest.approx(evaluate.qlike([1e-4], [2e-4])[0])
    assert te.qlike(None, 1e-4) is None and te.qlike(1e-4, None) is None and te.qlike(0.0, 1e-4) is None


def test_not_scored_until_the_window_has_closed_and_settled(eng):
    v = View()
    te.log_hour(eng, v, H + timedelta(minutes=10))
    fetch = lambda s, m, e: v.bars(s, m, e)              # noqa: E731
    end = H + timedelta(hours=1)
    assert te.score_due(eng, fetch, end) == 0
    assert te.score_due(eng, fetch, end + te.SETTLE - timedelta(seconds=1)) == 0
    assert te.score_due(eng, fetch, end + te.SETTLE) == 3                                # the 1 h rows only
    assert all(as_utc(s["scored_at"]) >= end for s in rows(eng, track_scores))
    assert all(as_utc(s["scored_at"]) > as_utc(f["logged_at"]) for s in rows(eng, track_scores)
               for f in rows(eng) if f["id"] == s["forecast_id"])


@pytest.mark.parametrize("gaps", [False, True])
def test_realised_variance_matches_the_models_target(eng, gaps):
    """A slice of candles gives the value the full history gives: the target the models were evaluated on."""
    full = make_bars("2026-09-28", 40 * 24 * 60, seed=3)
    if gaps:     # a trading halt and zero-volume minutes in both windows
        t = full["ts"]
        full.loc[(t >= "2026-11-05 13:10") & (t < "2026-11-05 13:13"), "volume"] = 0.0
        full.loc[(t >= "2026-11-05 15:00") & (t < "2026-11-05 15:04"), "volume"] = 0.0
        full.loc[(t >= "2026-11-05 11:57") & (t < "2026-11-05 12:02"), "volume"] = 0.0
    want = realized.build_targets(realized.minute_close(full))
    v = View(bars=full)
    assert te.log_hour(eng, v, H + timedelta(minutes=10)) == 6
    fetch = lambda s, m, e: v.bars(s, m, e)              # noqa: E731
    assert te.score_due(eng, fetch, H + timedelta(hours=24, minutes=15)) == 6
    forecasts = {r["id"]: r for r in rows(eng)}
    for s in rows(eng, track_scores):
        f = forecasts[s["forecast_id"]]
        y = want.at[pd.Timestamp(H), f"y_{f['horizon_hours']}h"]
        assert s["status"] == "scored" and s["realised_variance"] == pytest.approx(y, rel=1e-12)
        assert s["qlike_forecast"] == pytest.approx(y / f["forecast_variance"] - math.log(y / f["forecast_variance"]) - 1)
        assert s["qlike_har_s"] == pytest.approx(evaluate.qlike([y], [f["har_s_variance"]])[0])
        assert s["qlike_benchmark"] == pytest.approx(evaluate.qlike([y], [f["benchmark_variance"]])[0])


def test_stress_label_matches_the_classifiers_label_on_a_slice():
    """The label from 31 days of candles equals training's, from the full history: models/stress.py on hourly_features."""
    full = make_bars("2026-08-01", 90 * 24 * 60, seed=5)
    rng = np.random.default_rng(6)
    close = full["close"].to_numpy().copy()
    burst = np.zeros(len(close))
    burst[-3 * 24 * 60 + 200:-3 * 24 * 60 + 260] = rng.normal(0, 0.02, 60)    # a violent hour near the end
    full["close"] = close * np.exp(np.cumsum(burst))
    m = build.minute_features({"last": full, **{k: None for k in ("mark", "index", "premium", "funding", "metrics")}})
    sm = m.set_index("ts")["stress"].resample("1h", label="right", closed="right").sum(min_count=1)
    label = (sm.shift(-1) > 0).astype(float).where(sm.shift(-1).notna())
    assert label.tail(5 * 24).sum() > 0
    origins = label.index[-4 * 24:-2]
    start = origins[0] - pd.Timedelta(minutes=te.STRESS_LOOKBACK_MINUTES)
    hours = te.stress_hours(full[full["ts"] >= start])
    for o in origins:
        assert te.stress_event(hours, o.to_pydatetime()) == bool(label.at[o])


def test_scoring_marks_the_stress_label_on_1h_rows_only(eng):
    v = View(bars=make_bars("2026-09-28", 40 * 24 * 60))
    te.log_hour(eng, v, H + timedelta(minutes=10))
    assert te.score_due(eng, lambda s, m, e: v.bars(s, m, e), H + timedelta(hours=24, minutes=15)) == 6
    forecasts = {r["id"]: r for r in rows(eng)}
    for s in rows(eng, track_scores):
        assert (s["stress_event"] is None) == (forecasts[s["forecast_id"]]["horizon_hours"] == 24)


def test_binance_failure_leaves_it_pending_then_it_scores(eng):
    v = View()
    te.log_hour(eng, v, H + timedelta(minutes=10))
    v.fail_bars = True
    end = H + timedelta(hours=1)
    assert te.score_due(eng, lambda s, m, e: v.bars(s, m, e), end + timedelta(minutes=8)) == 0
    assert rows(eng, track_scores) == []
    v.fail_bars = False
    assert te.score_due(eng, lambda s, m, e: v.bars(s, m, e), end + timedelta(minutes=9)) == 3


def test_window_without_prices_stays_pending_then_is_marked_no_data_without_inventing_a_number(eng):
    v = View()
    te.log_hour(eng, v, H + timedelta(minutes=10))
    empty = make_bars("2026-11-03", 10)                      # Binance answers, but has nothing for the window
    fetch = lambda s, m, e: empty                             # noqa: E731
    end = H + timedelta(hours=1)
    assert te.score_due(eng, fetch, end + timedelta(minutes=8)) == 0
    assert te.score_due(eng, fetch, end + te.GIVE_UP) == 3
    assert all(s["status"] == "no_data" and s["realised_variance"] is None and s["qlike_forecast"] is None
               for s in rows(eng, track_scores))


def test_old_unscored_windows_are_retried_less_often_to_spare_binance(eng):
    v = View()
    te.log_hour(eng, v, H + timedelta(minutes=10))
    calls = []
    def fetch(s, m, e):
        calls.append(s)
        return v.bars(s, m, e)
    assert te.score_due(eng, fetch, H + timedelta(hours=1, minutes=31)) == 0 and calls == []
    assert te.score_due(eng, fetch, H + timedelta(hours=1, minutes=45)) == 3


# ------------------------------------------------------------------ the numbers
def _row(symbol="BTCUSDT", h=1, hours=0, status="scored", qf=0.1, qh=0.2, qb=0.3, p=0.2, base=0.05, event=False):
    t = H + timedelta(hours=hours)
    return {"symbol": symbol, "horizon_hours": h, "origin_ts": t, "logged_at": t + timedelta(minutes=10), "p_stress": p,
            "stress_base_rate": base, "regime": "calm", "status": status, "qlike_forecast": qf, "qlike_har_s": qh,
            "qlike_benchmark": qb, "stress_event": event}


def _series(summary, symbol="BTCUSDT", h=1):
    return next(s for s in summary.series if s.symbol == symbol and s.horizon_hours == h)


def test_summary_with_nothing_logged_has_nulls_and_zero_samples():
    s = api.summarise([], H)
    assert s.logging_started_at is None and s.rows_logged == 0 and len(s.series) == 6
    for x in s.series:
        assert x.rows_scored == 0 and x.qlike_forecast is None and x.vs_benchmark.n == 0 and x.small_sample
        assert x.vs_benchmark.vs_rival_pct is None


def test_comparison_is_on_the_same_rows_with_its_sample_size():
    rows_ = [_row(hours=0, qf=0.1, qb=0.3), _row(hours=1, qf=0.3, qb=0.5), _row(hours=2, qf=9.0, qb=None)]
    x = _series(api.summarise(rows_, H + timedelta(hours=3)))
    assert x.n_qlike == 3 and x.qlike_forecast == pytest.approx(9.4 / 3)
    assert x.vs_benchmark.n == 2 and x.vs_benchmark.forecast == pytest.approx(0.2) and x.vs_benchmark.rival == pytest.approx(0.4)
    assert x.vs_benchmark.vs_rival_pct == pytest.approx(-50)


def test_small_sample_label_ends_at_the_written_threshold():
    n = api.SMALL_SAMPLE_ROWS
    short = _series(api.summarise([_row(hours=k) for k in range(n - 1)], H + timedelta(hours=n)))
    enough = _series(api.summarise([_row(hours=k) for k in range(n)], H + timedelta(hours=n)))
    assert short.small_sample and not enough.small_sample and enough.rows_scored == n


def test_24h_windows_report_how_few_are_separate():
    x = _series(api.summarise([_row(h=24, hours=k) for k in range(48)], H + timedelta(hours=48)), h=24)
    assert x.rows_scored == 48 and x.separate_windows == 2 and x.stress is None


def test_stress_score_is_brier_against_the_usual_rate():
    rows_ = [_row(hours=0, p=0.5, base=0.1, event=True), _row(hours=1, p=0.1, base=0.1, event=False),
             _row(hours=2, p=0.1, base=0.1, event=None)]
    st = _series(api.summarise(rows_, H + timedelta(hours=3))).stress
    assert st.n == 2 and st.events == 1 and st.actual_rate == 0.5 and st.mean_p == pytest.approx(0.3)
    assert st.brier == pytest.approx((0.25 + 0.01) / 2) and st.brier_base_rate == pytest.approx((0.81 + 0.01) / 2)
    assert st.small_sample


# ------------------------------------------------------------------ the API
def test_api_serves_rows_newest_first_with_pending_and_scored(eng):
    v = View()
    te.log_hour(eng, v, H + timedelta(minutes=10))
    te.score_due(eng, lambda s, m, e: v.bars(s, m, e), H + timedelta(hours=1, minutes=8))
    two = H + timedelta(hours=2)
    te.log_hour(eng, View(hour=two), two + timedelta(minutes=10))
    c = TestClient(main.app)
    r = c.get("/track-record", params={"symbol": "ETHUSDT", "horizon_hours": 1})
    assert r.status_code == 200
    got = r.json()["records"]
    assert [x["status"] for x in got] == ["pending", "scored"] and got[0]["origin"] > got[1]["origin"]
    assert got[1]["realised_variance"] > 0 and got[1]["qlike_forecast"] is not None
    assert got[0]["realised_variance"] is None and got[0]["qlike_forecast"] is None
    assert c.get("/track-record", params={"horizon_hours": 2}).status_code == 422
    s = c.get("/track-record/summary").json()
    assert s["rows_logged"] == 12 and s["rows_scored"] == 3 and s["logging_started_at"].startswith("2026-11-05T12:10")
    assert s["rules"] == {"log_deadline_minutes": 30, "settle_minutes": 7, "small_sample_rows": 720, "small_sample_events": 30}


def test_api_with_an_empty_table_answers_200(eng):
    c = TestClient(main.app)
    assert c.get("/track-record").json()["records"] == []
    assert c.get("/track-record/summary").json()["logging_started_at"] is None


def test_summary_stays_fast_with_a_year_of_rows():
    rows_ = [_row(symbol=c, h=h, hours=k) for c in COINS for h in (1, 24) for k in range(24 * 365)]
    import time
    t0 = time.perf_counter()
    api.summarise(rows_, H + timedelta(days=366))
    assert time.perf_counter() - t0 < 2.0
