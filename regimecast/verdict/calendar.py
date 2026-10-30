"""Bad timing: scheduled US macro events, weekends and thin hours.

``macro_events.csv`` is a fixed file in the repo: every Fed rate decision (FOMC statement, 14:00 New York
time, plus the two emergency cuts of March 2020), US inflation report (CPI, 08:30) and jobs report
(nonfarm payrolls, 08:30) from 2020 to 2027, with the time converted to UTC. Dates from the Federal
Reserve's FOMC calendar and the BLS release archives and schedules (fetched 30 Oct 2026); the 2025
shutdown's moved releases (CPI 24 Oct, jobs 20 Nov and 16 Dec 2025) are as published.

Thin hours are the UTC hours with the least trading, chosen on the development period
(scripts/run_phase12.py) and frozen in frozen.json.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

EVENTS_FILE = Path(__file__).with_name("macro_events.csv")
LOOKBACK_HOURS = 1      # an event that happened in the last hour still counts: prices are still settling


@lru_cache(maxsize=1)
def load_events(path: Path = EVENTS_FILE) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    return df.sort_values("time_utc").reset_index(drop=True)


def events_within(now, horizon_hours: int, events: pd.DataFrame | None = None) -> list[dict]:
    """Events in (now - LOOKBACK_HOURS, now + horizon]: the ones that can move the price during the trade."""
    ev = load_events() if events is None else events
    now = pd.Timestamp(now)
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    a, b = now - pd.Timedelta(hours=LOOKBACK_HOURS), now + pd.Timedelta(hours=horizon_hours)
    hit = ev[(ev["time_utc"] > a) & (ev["time_utc"] <= b)]
    return [{"name": r.name, "kind": r.kind, "time_utc": r.time_utc.isoformat()} for r in hit.itertuples()]


def event_flags(index: pd.DatetimeIndex, horizon_hours: int, events: pd.DataFrame | None = None) -> np.ndarray:
    """For many hours at once: True where events_within(t, horizon) is not empty."""
    ev = pd.DatetimeIndex((load_events() if events is None else events)["time_utc"]).as_unit("ns").asi8
    t = pd.DatetimeIndex(index).tz_convert("UTC").as_unit("ns").asi8
    hour = 3600 * 10**9
    lo = np.searchsorted(ev, t - LOOKBACK_HOURS * hour, side="right")
    hi = np.searchsorted(ev, t + horizon_hours * hour, side="right")
    return hi > lo


def is_weekend(ts) -> bool:
    return pd.Timestamp(ts).dayofweek >= 5


def thin_hours_from_volume(volume: pd.Series, n: int = 4) -> list[int]:
    """The n UTC hours of the day with the lowest median traded volume (hour START, 0-23).

    ``volume`` is indexed by hour END, so the hour that ends at 01:00 is hour 0."""
    start_hour = (volume.index - pd.Timedelta(hours=1)).hour
    med = volume.groupby(start_hour).median()
    return sorted(int(h) for h in med.nsmallest(n).index)


def in_thin_hours(ts, thin_hours: list[int]) -> bool:
    """True when the hour that starts at ``ts`` (the trade's first hour) is a thin hour."""
    return pd.Timestamp(ts).hour in set(thin_hours)
