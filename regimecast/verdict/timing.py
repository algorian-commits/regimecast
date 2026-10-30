"""Bad-timing warnings: scheduled US macro events inside the trade's horizon, weekends, thin hours, stress rising.

Event times come from a fixed calendar shipped with the code (``macro_events.csv``: FOMC rate decisions at
2:00 pm ET, CPI and the jobs report at 8:30 am ET, all converted to UTC; past dates are the actual release
dates, so the October-November 2025 shutdown delays are in it). A trade opened at T is warned about an event
in (T, T + horizon]. Weekend = the trade is opened on a Saturday or Sunday (UTC); thin hours = the four hours
of the day with the least trading on the dev period (frozen per coin). Stress rising = the next-hour stress
probability is at least 10% and at least 1.5 times what it was 3 hours earlier.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

CALENDAR_FILE = Path(__file__).with_name("macro_events.csv")
NAMES = {"FOMC": "The US Federal Reserve's rate decision (FOMC)", "CPI": "US inflation data (CPI)",
         "JOBS": "The US jobs report"}
RISING_MIN, RISING_RATIO, RISING_LAG_H = 0.10, 1.5, 3


def load_calendar(path: Path = CALENDAR_FILE) -> pd.DataFrame:
    """time (UTC) and event, oldest first."""
    if not path.exists():
        return pd.DataFrame({"time": pd.Series(dtype="datetime64[ns, UTC]"), "event": pd.Series(dtype=str)})
    c = pd.read_csv(path, comment="#")
    return (pd.DataFrame({"time": pd.to_datetime(c["time_utc"], utc=True), "event": c["event"].str.strip()})
            .sort_values("time").reset_index(drop=True))


def stress_rising(p_now, p_before):
    p_now, p_before = np.asarray(p_now, dtype=float), np.asarray(p_before, dtype=float)
    return (p_now >= RISING_MIN) & (p_now >= RISING_RATIO * p_before)


@dataclass
class Timing:
    events: list[tuple[str, pd.Timestamp]] = field(default_factory=list)
    weekend: bool = False
    thin: bool = False
    rising: bool = False

    def warnings(self, now: pd.Timestamp) -> list[str]:
        out = []
        for ev, t in self.events:
            hours = (t - now).total_seconds() / 3600
            when = f"in {hours:.0f} hours" if hours >= 1.5 else f"in {max(1, round(hours * 60))} minutes"
            out.append(f"{NAMES.get(ev, ev)} comes out {when}, inside your trade's time: prices often jump on it.")
        if self.rising:
            out.append("Market stress is rising: the chance of a sharp move in the next hour has gone up quickly.")
        if self.weekend:
            out.append("It is the weekend: fewer traders, so prices can jump on small orders.")
        elif self.thin:
            out.append("This is one of the quietest hours of the day: fewer traders, so prices can jump on small orders.")
        return out


def check(now: pd.Timestamp, horizon_hours: int, calendar: pd.DataFrame, thin_hours=(),
          p_stress: float | None = None, p_stress_before: float | None = None) -> Timing:
    end = now + pd.Timedelta(hours=horizon_hours)
    ev = calendar[(calendar["time"] > now) & (calendar["time"] <= end)]
    rising = (p_stress is not None and p_stress_before is not None
              and bool(stress_rising(p_stress, p_stress_before)))
    return Timing(events=list(zip(ev["event"], ev["time"])), weekend=now.day_of_week >= 5,
                  thin=now.hour in set(thin_hours), rising=rising)


def flags(index: pd.DatetimeIndex, horizon_hours: int, calendar: pd.DataFrame, thin_hours=(),
          p_stress: pd.Series | None = None) -> pd.DataFrame:
    """The same checks for every hour of ``index`` at once (the evaluation grid). p_stress: hourly series."""
    t = calendar["time"].to_numpy(dtype="datetime64[ns]")
    ix = index.tz_convert("UTC").tz_localize(None).to_numpy(dtype="datetime64[ns]")
    nxt = np.searchsorted(t, ix, side="right")                       # first event strictly after the hour
    has = nxt < len(t)
    ev = np.zeros(len(ix), dtype=bool)
    ev[has] = t[nxt[has]] <= ix[has] + np.timedelta64(horizon_hours, "h")
    out = pd.DataFrame({"event": ev, "weekend": index.day_of_week >= 5,
                        "thin": np.isin(index.hour, list(thin_hours))}, index=index)
    if p_stress is not None:
        now = p_stress.reindex(index).to_numpy(dtype=float)
        before = p_stress.reindex(index - pd.Timedelta(hours=RISING_LAG_H)).to_numpy(dtype=float)
        out["rising"] = stress_rising(now, before)
    else:
        out["rising"] = False
    out["any"] = out[["event", "weekend", "thin", "rising"]].any(axis=1)
    return out
