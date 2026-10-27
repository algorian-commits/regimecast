"""Build a crash replay file from stored data, with the app's frozen models (M12).

For every hour of the window it runs the same models the live app runs, loaded from the
files the app ships (nothing is fitted here):

* volatility: HAR-S (``vol/har_live.json``) on the hourly feature table,
* regime: the HMM (``models/regimes_live.json``), forward-filtered from 30 days before
  the window, like the live app's 31-day history,
* stress gauge: the LightGBM classifier (``models/stress_live/``) on the features built
  by the training code (``models/stress.dataset``), with DVOL where it exists.

For every minute it keeps the mark price (low, high, close): liquidations are triggered
by the mark price. The API (replay/engine.py) turns these into the dashboard and Risk
Check view for any position the viewer picks.

No look-ahead: an hourly row stamped H (hour END) uses data up to H only (the feature
table's rule, tested in tests/test_features.py), and a minute closing at T is shown with
the last hourly row H <= T (engine.hour_index). tests/test_replay.py checks both.

The final test period stays closed: a window that reaches 2025-10-01 or later is refused
unless the final evaluation has been run and written up (docs/FINAL_EVALUATION.md).

    python scripts/make_replay.py ftx_2022          # the FTX collapse, November 2022
    python scripts/make_replay.py oct_2025          # only after the final evaluation
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from regimecast import config

EVENTS_DIR = Path(__file__).with_name("events")
FINAL_EVAL_DOC = Path(__file__).resolve().parents[2] / "docs" / "FINAL_EVALUATION.md"
WARMUP = pd.Timedelta(days=35)   # HAR-S needs 30 days of hourly history; the regime filter starts 30 days back

EVENTS = {
    "ftx_2022": {
        "title": "FTX collapse, November 2022",
        "start": "2022-11-06T00:00:00Z", "end": "2022-11-11T00:00:00Z",
        "summary": "FTX, then the second-largest crypto exchange, ran out of money. On 6 Nov Binance said it would "
                   "sell its FTT tokens; withdrawals from FTX stopped on 8 Nov and BTC fell from about 21,000 to "
                   "15,700 USDT in two days.",
        "marks": [["2022-11-06T15:47:00Z", "Binance says it will sell its FTT"],
                  ["2022-11-08T16:03:00Z", "Binance signs a letter of intent to buy FTX"],
                  ["2022-11-09T21:00:00Z", "Binance walks away from the deal"],
                  ["2022-11-10T13:30:00Z", "US inflation lower than expected; prices bounce"]],
    },
    "oct_2025": {
        "title": "Tariff crash, 10 October 2025",
        "start": "2025-10-09T12:00:00Z", "end": "2025-10-12T00:00:00Z",
        "summary": "A 100% tariff on Chinese goods was announced on a Friday evening; the largest liquidation day in "
                   "crypto history followed. Inside the final test period: generated only after the final evaluation.",
        "marks": [["2025-10-10T20:50:00Z", "Tariff post"]],
    },
}


class FinalTestPeriodClosed(RuntimeError):
    pass


def check_window(start: pd.Timestamp, end: pd.Timestamp) -> None:
    """Refuse any window that reaches the final test period before the final evaluation exists."""
    if end > pd.Timestamp(config.HOLDOUT_START, tz="UTC") and not FINAL_EVAL_DOC.exists():
        raise FinalTestPeriodClosed(
            f"{start:%Y-%m-%d} to {end:%Y-%m-%d} reaches the final test period (from {config.HOLDOUT_START}). "
            f"It is generated only after the final evaluation has run (docs/FINAL_EVALUATION.md).")


# ------------------------------------------------------------------ data
def fetch_mark_minutes(symbol: str, start: pd.Timestamp, end: pd.Timestamp, client=None) -> pd.DataFrame:
    """1-minute mark-price candles from Binance's public REST API (for coins whose archive
    is not on this PC). Same columns as store.minute_bars(kind="mark")."""
    import httpx

    own = client is None
    client = client or httpx.Client(base_url=config.BINANCE_FAPI_URL, timeout=20.0)
    rows, t = [], int(start.timestamp() * 1000)
    try:
        while t < int(end.timestamp() * 1000):
            r = client.get("/fapi/v1/markPriceKlines", params={"symbol": symbol, "interval": "1m", "startTime": t,
                                                               "endTime": int(end.timestamp() * 1000) - 1,
                                                               "limit": 1500})
            r.raise_for_status()
            batch = r.json()
            if not batch:
                break
            rows += batch
            t = batch[-1][0] + 60_000
    finally:
        if own:
            client.close()
    df = pd.DataFrame([r[:5] for r in rows], columns=["ts", "open", "high", "low", "close"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    for c in ("open", "high", "low", "close"):
        df[c] = df[c].astype(float)
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def mark_minutes(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    from regimecast.data import store

    m = store.minute_bars(symbol, "mark", start, end)
    if len(m) < 0.95 * (end - start) / pd.Timedelta(minutes=1):
        m = fetch_mark_minutes(symbol, start, end)
    return m[(m["ts"] >= start) & (m["ts"] < end)].reset_index(drop=True)


# ------------------------------------------------------------------ hourly model outputs
def hourly_states(h: pd.DataFrame, symbol: str, start: pd.Timestamp, end: pd.Timestamp,
                  har_models: dict, regime_model: dict, stress_model, dvol: pd.DataFrame | None = None) -> pd.DataFrame:
    """Model outputs per hour END in [start, end], from the hourly feature table ``h``
    (which must not extend past ``end``: nothing later is ever read)."""
    from regimecast.models import regimes, stress
    from regimecast.vol import har, live as vol_live

    h = h[h["ts"] <= end].sort_values("ts").reset_index(drop=True)
    hi = h.set_index("ts")
    out = pd.DataFrame(index=hi.index[hi.index >= start - pd.Timedelta(hours=1)])
    for key, horizon in (("1h", 1), ("24h", 24)):
        X = har.har_features(hi, horizon)
        var = vol_live.model_from_dict(har_models[key]).predict(X)
        out[f"sigma_{key}"] = np.sqrt(var.where(var > 0)).reindex(out.index)

    x = regimes.regime_inputs(h)
    x = x[x.index >= start - pd.Timedelta(days=30)].iloc[24:]   # the live window: 31 days, first day dropped
    reg = regimes.live_probabilities(x[regime_model["cols"]].dropna(), regime_model)
    for n in regimes.NAMES:
        out[f"p_{n}"] = reg[f"p_{n}"].reindex(out.index)
    out["regime"] = reg["regime"].reindex(out.index).map(lambda r: regimes.NAMES[int(r)] if pd.notna(r) else None)

    if stress_model is not None:
        booster, meta = stress_model
        t = pd.DataFrame({"ts": h["ts"], "y_1h": np.nan, "y_24h": np.nan})
        X, _ = stress.dataset(h, t, reg, dvol)
        X = X.reindex(out.index)
        rows = X[meta["features"]]
        p = booster.predict(rows)
        contrib = booster.predict(rows, pred_contrib=True)[:, :-1]
        out["p_stress"] = p
        out["stress_level"] = [next(name for th, name in stress.LEVELS if v >= th) for v in p]
        drivers = []
        for c in contrib:
            top = np.argsort(-np.abs(c))[:3]
            drivers.append([[stress.DRIVER_NAMES.get(meta["features"][i], meta["features"][i]),
                             "up" if c[i] > 0 else "down"] for i in top])
        out["stress_drivers"] = drivers
        out.attrs["stress_base_rate"] = meta["base_rate"]
    return out


def oos_stress(symbol: str, index: pd.DatetimeIndex) -> pd.Series | None:
    """The walk-forward, out-of-sample stress probability from Phase 6 for the same hours
    (models refitted every 90 days on past data only), shown next to the frozen model's."""
    f = config.DATA_DIR / "reports" / f"phase6_pred_{symbol}.parquet"
    if not f.exists():
        return None
    p = pd.read_parquet(f, columns=["gbm_cal"])["gbm_cal"]
    return p[p.index <= index.max()].reindex(index)


# ------------------------------------------------------------------ the file
def _r(v, nd=6):
    return None if v is None or (isinstance(v, float) and not np.isfinite(v)) else round(float(v), nd)


def coin_block(symbol: str, minutes: pd.DataFrame, hours: pd.DataFrame, oos: pd.Series | None) -> dict:
    full = pd.date_range(minutes["ts"].min(), minutes["ts"].max(), freq="1min")
    m = minutes.set_index("ts").reindex(full)
    return {
        "minute_start": full[0].isoformat(), "n_minutes": len(full),
        "mark": {c: [_r(v, 4) for v in m[c]] for c in ("low", "high", "close")},
        "hours": [{
            "t": t.isoformat(), "sigma_1h": _r(r["sigma_1h"]), "sigma_24h": _r(r["sigma_24h"]),
            "regime": r["regime"], "p_calm": _r(r["p_calm"], 4), "p_active": _r(r["p_active"], 4),
            "p_stressed": _r(r["p_stressed"], 4), "p_stress": _r(r.get("p_stress"), 4),
            "stress_level": r.get("stress_level"), "stress_drivers": r.get("stress_drivers") or [],
            "p_stress_walk_forward": _r(oos.get(t), 4) if oos is not None else None,
        } for t, r in hours.iterrows()],
        "stress_base_rate": _r(hours.attrs.get("stress_base_rate"), 4),
    }


def build(event_id: str, symbols: list[str] | None = None) -> dict:
    """Read stored data and run the frozen models for one event (needs data/ on this PC)."""
    from regimecast.data import store
    from regimecast.models import regimes, stress
    from regimecast.vol import live as vol_live

    ev = EVENTS[event_id]
    start, end = pd.Timestamp(ev["start"]), pd.Timestamp(ev["end"])
    check_window(start, end)
    har_all, reg_all = vol_live.load_models(), regimes.load_live()
    coins = {}
    for s in symbols or config.SYMBOLS:
        h = store.features(s, "1h", start - WARMUP, end + pd.Timedelta(seconds=1))
        d = config.parquet_dir() / "dvol" / s / "all.parquet"
        dv = pd.read_parquet(d) if d.exists() else None
        if dv is not None:
            dv = dv[dv["ts"] < end]
        hours = hourly_states(h, s, start, end, har_all[s], reg_all[s], stress.load_live(s), dv)
        coins[s] = coin_block(s, mark_minutes(s, start, end), hours, oos_stress(s, hours.index))
    fhs_fitted = "2025-09-30"
    return {"id": event_id, **{k: ev[k] for k in ("title", "summary", "start", "end", "marks")},
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "models": {"volatility": f"HAR-S (fitted to {har_all[config.SYMBOLS[0]]['1h']['fitted_through']})",
                       "regime": f"HMM regimes (fitted to {reg_all[config.SYMBOLS[0]]['fitted_through']})",
                       "stress": f"stress classifier (fitted to {fhs_fitted})",
                       "liquidation": f"FHS (fitted to {fhs_fitted})"},
            "coins": coins}


def save(replay: dict) -> Path:
    EVENTS_DIR.mkdir(exist_ok=True)
    f = EVENTS_DIR / f"{replay['id']}.json.gz"
    with gzip.open(f, "wt", encoding="utf-8", compresslevel=9) as fh:
        json.dump(replay, fh, separators=(",", ":"))
    return f
