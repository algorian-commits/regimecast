"""Hourly news mood per coin, labelled by the hour END.

A headline known at 10:20 counts for the hour ending 11:00; one known at exactly 11:00
counts for the hour ending 12:00. So the row for hour end ``t`` only uses headlines known
before ``t``: nothing from the future.

For coin C, an hour has:

* ``count``               crypto headlines about C or about the whole market ("general")
* ``mean_score``          their average sentiment score (-1..+1); empty if count is 0
* ``share_negative``      the share labelled negative; empty if count is 0
* ``policy_shock_count``  headlines in a policy-shock category (tariffs, sanctions,
                          regulation) about C or the market, plus every macro tariff or
                          sanctions headline (those hit all coins at once)

Old news (published more than a day before it was first seen) is left out. Hours when
the news source itself was down (``outage_hours``: 6+ hours in a row without a single
headline of any kind, e.g. GDELT's outage of 15 Jun - 1 Jul 2025) are left out of the
table instead of showing up as "no news".
"""
from __future__ import annotations

import pandas as pd

COIN_OF = {"BTCUSDT": "BTC", "ETHUSDT": "ETH", "SOLUSDT": "SOL"}
MOOD_COLUMNS = ["ts", "count", "mean_score", "share_negative", "policy_shock_count"]


def hour_end(t: pd.Series) -> pd.Series:
    return t.dt.floor("h") + pd.Timedelta(hours=1)


def outage_hours(known_at: pd.Series, min_run: int = 6) -> pd.DatetimeIndex:
    """Hour ends inside runs of at least ``min_run`` hours with no headline at all."""
    if known_at.empty:
        return pd.DatetimeIndex([], tz="UTC")
    n = hour_end(known_at).value_counts()
    grid = pd.date_range(n.index.min(), n.index.max(), freq="h")
    empty = n.reindex(grid, fill_value=0) == 0
    run_id = (empty != empty.shift()).cumsum()
    run_len = empty.groupby(run_id).transform("size")
    return grid[(empty & (run_len >= min_run)).to_numpy()]


def _about(coins: pd.Series, coin: str) -> pd.Series:
    return coins.str.split(",").map(lambda cs: coin in cs) | (coins == "general")


def hourly(headlines: pd.DataFrame, scores: pd.DataFrame, symbol: str,
           start: pd.Timestamp | None = None, end: pd.Timestamp | None = None,
           drop_hours: pd.DatetimeIndex | None = None) -> pd.DataFrame:
    """Mood table for one symbol. ``ts`` = hour end, one row per hour from ``start`` to ``end``
    (inclusive hour ends; default: first to last headline), hours without news have count 0.
    ``drop_hours`` (source outages) are removed.

    headlines: tagged + de-duplicated (columns known_at, scope, coins, policy, stale, title_key)
    scores:    one row per title_key with ``score`` and ``label`` from the mood model
    """
    coin = COIN_OF[symbol]
    h = headlines[~headlines["stale"]].copy()
    h["ts"] = hour_end(h["known_at"])
    crypto = h[(h["scope"] == "crypto")]
    about = crypto[_about(crypto["coins"], coin)]
    scored = about.merge(scores[["title_key", "score", "label"]], on="title_key", how="inner")
    scored["neg"] = (scored["label"] == "negative").astype(float)
    g = scored.groupby("ts").agg(count=("score", "size"), mean_score=("score", "mean"), share_negative=("neg", "mean"))

    policy = pd.concat([about[about["policy"] != ""], h[(h["scope"] == "macro_policy") & (h["policy"] != "")]])
    p = policy.groupby("ts").size().rename("policy_shock_count")

    lo = start if start is not None else min([x.index.min() for x in (g, p) if len(x)], default=None)
    hi = end if end is not None else max([x.index.max() for x in (g, p) if len(x)], default=None)
    if lo is None or hi is None:
        return pd.DataFrame(columns=MOOD_COLUMNS)
    grid = pd.date_range(pd.Timestamp(lo).ceil("h"), pd.Timestamp(hi).floor("h"), freq="h", name="ts")
    out = g.reindex(grid).join(p.reindex(grid))
    out["count"] = out["count"].fillna(0).astype(int)
    out["policy_shock_count"] = out["policy_shock_count"].fillna(0).astype(int)
    if drop_hours is not None and len(drop_hours):
        out = out[~out.index.isin(drop_hours)]
    return out.reset_index()[MOOD_COLUMNS]
