"""13A pass mark: do the live model's inputs equal the archive's, over 48 consecutive hours?

    python scripts/p13a_live_check.py                # writes data/reports/p13a_live_inputs*.csv/json

For each coin and horizon: the same public endpoints the app reads (features/live.py) are asked what they say up
to the archive's last hour, and go through the training feature code. Every input of the live combination is then
compared with the archive's, hour by hour, at 1e-6 relative (both missing counts as equal). Each hour's forecast is
compared too. A last pass uses the window sizes the app really runs with, on the last hour only: they must be enough.

Funding: Binance publishes its funding archive as monthly files and the feature tables stop at the last file they
were built with, after which their funding value is stale. So the archive side reads Binance's own monthly file for
the window, in memory, through the same as-of join as the feature builder.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import io
import json
import zipfile

import httpx
import numpy as np
import pandas as pd

from regimecast import config
from regimecast.data import dvol as dvol_api, store
from regimecast.features import build, live as flive
from regimecast.models import volatility as V
from regimecast.vol import combo_live as C

OUT = pathlib.Path(__file__).resolve().parents[1] / "data" / "reports"
HOURS, TOL = 48, 1e-6
EPS = 1e-12
# The differences the pass mark allows, because they are explained. Each has a limit: a larger gap fails the check.
EXPLAINED = {
    "ls_top": (1e-4, "Binance's API rounds this ratio to 4 decimals; the archive keeps more (largest gap 0.00005)."),
    "ls_accounts": (5e-4, "The API's ratio is its long share / short share, each already rounded to 4 decimals "
                          "(checked: 0.7444 / 0.2556 = 2.9124); the archive divides the unrounded shares."),
    "taker_ls": (2e-3, "Same 5-minute row (checked by hand), but Binance's API and archive differ by about 0.1%, "
                       "beyond rounding. Their volume figures differ slightly; nothing on our side can remove it."),
}


def archive_funding(symbol: str, span: pd.DatetimeIndex) -> pd.Series:
    """Funding known at each hour, from Binance's monthly archive files (this month's and the two before)."""
    rows = []
    for month in pd.period_range(span[0].tz_localize(None).to_period("M") - 2, span[-1].tz_localize(None).to_period("M")):
        r = httpx.get(f"{config.BINANCE_ARCHIVE_URL}/data/futures/um/monthly/fundingRate/{symbol}/"
                      f"{symbol}-fundingRate-{month}.zip", timeout=30, follow_redirects=True)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        rows.append(pd.read_csv(z.open(z.namelist()[0])))
    d = pd.concat(rows)
    f = pd.DataFrame({"ts": pd.to_datetime(d["calc_time"], unit="ms", utc=True), "funding_rate": d["last_funding_rate"]})
    return build._asof(span, f.sort_values("ts"), "ts", {"funding_rate": "funding_rate"})["funding_rate"]


def compare(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Per input: hours compared, hours missing on one side only, largest relative and absolute difference."""
    rows = []
    for c in a.columns:
        x, y = a[c].astype(float), b[c].astype(float)
        both = x.notna() & y.notna()
        d = (x[both] - y[both]).abs()
        rel = d / y[both].abs().clip(lower=EPS)
        worst = rel.idxmax() if len(rel) else None
        rows.append({"input": c, "hours": int(both.sum()), "missing_one_side": int((x.isna() != y.isna()).sum()),
                     "both_missing": int((x.isna() & y.isna()).sum()), "max_rel_diff": float(rel.max()) if len(rel) else 0.0,
                     "max_abs_diff": float(d.max()) if len(d) else 0.0, "worst_hour": worst.isoformat() if worst is not None else "",
                     "live_at_worst": float(x[worst]) if worst is not None else np.nan,
                     "archive_at_worst": float(y[worst]) if worst is not None else np.nan})
    out = pd.DataFrame(rows)
    out["within_tol"] = (out["max_rel_diff"] <= TOL) & (out["missing_one_side"] == 0)
    limit = out["input"].map(lambda c: EXPLAINED.get(c, (0.0, ""))[0])
    out["explained"] = ~out["within_tol"] & (out["max_rel_diff"] <= limit) & (out["missing_one_side"] == 0)
    return out


tables, forecasts, last_hour = [], [], []
for sym in config.SYMBOLS:
    h_a, t_a = store.features(sym, "1h"), store.targets(sym)
    f = config.parquet_dir() / "dvol" / sym / "all.parquet"
    dv_a = pd.read_parquet(f) if f.exists() else None
    T = h_a["ts"].max()
    span = pd.date_range(end=T, periods=HOURS, freq="1h")
    funding_a = archive_funding(sym, span)
    inp = flive.fetch_inputs(sym, minutes=34 * 24 * 60, detail_minutes=4 * 24 * 60, until=T, stat_pages=3)
    h_l = build.hourly_features(inp)
    dv_l = dvol_api.fetch(sym, start=T - pd.Timedelta(days=4), end=T) if sym in dvol_api.CURRENCY else pd.DataFrame()
    # what the app really fetches, as of the same hour: default window sizes, last hour only
    inp_run = flive.fetch_inputs(sym, until=T)
    h_run = build.hourly_features(inp_run)
    models = C.load(sym)
    for key, H in C.HORIZONS.items():
        X_a = C.model_inputs(V.dataset(h_a, t_a, H, dv_a)[0]).reindex(span)
        X_a["funding_rate"] = funding_a
        X_l = C.model_inputs(C.live_X(h_l, dv_l, H)).reindex(span)
        tab = compare(X_l, X_a).assign(symbol=sym, horizon=key)
        tables.append(tab)
        pa, pl = models[key].predict(X_a), models[key].predict(X_l)
        forecasts.append({"symbol": sym, "horizon": key, "hours": len(pa),
                          "max_rel_diff_forecast": float((pl / pa - 1).abs().max()),
                          "max_abs_diff_sigma_pct_points": float((100 * np.sqrt(pl) - 100 * np.sqrt(pa)).abs().max())})
        X_r = C.model_inputs(C.live_X(h_run, dv_l, H)).reindex([T])
        run = compare(X_r, X_a.loc[[T]]).assign(symbol=sym, horizon=key)
        last_hour.append(run)
        print(sym, key, "inputs within 1e-6:", int(tab["within_tol"].sum()), "of", len(tab),
              "| forecast max rel diff", f"{forecasts[-1]['max_rel_diff_forecast']:.2e}",
              "| app window sizes, last hour:", int(run["within_tol"].sum()), "of", len(run), flush=True)

all_ = pd.concat(tables)
run = pd.concat(last_hour)
OUT.mkdir(parents=True, exist_ok=True)
cols = ["symbol", "horizon", "input", "hours", "both_missing", "missing_one_side", "max_rel_diff", "max_abs_diff",
        "worst_hour", "live_at_worst", "archive_at_worst", "within_tol", "explained"]
all_[cols].to_csv(OUT / "p13a_live_inputs.csv", index=False)
run[cols].to_csv(OUT / "p13a_live_inputs_app_windows.csv", index=False)
pd.DataFrame(forecasts).to_csv(OUT / "p13a_live_forecasts.csv", index=False)
summary = {"run_at": pd.Timestamp.now(tz="UTC").isoformat(), "hours": HOURS, "tolerance": TOL,
           "window_end": {s: str(store.features(s, "1h")["ts"].max()) for s in config.SYMBOLS},
           "inputs_checked": int(len(all_)), "inputs_within_tol": int(all_["within_tol"].sum()),
           "inputs_explained": int(all_["explained"].sum()),
           "unexplained": all_.loc[~all_["within_tol"] & ~all_["explained"],
                                   ["symbol", "horizon", "input", "max_rel_diff", "missing_one_side"]].to_dict("records"),
           "app_windows_unexplained": run.loc[~run["within_tol"] & ~run["explained"],
                                              ["symbol", "horizon", "input", "max_rel_diff"]].to_dict("records"),
           "explanations": {k: {"largest_rel_diff": float(all_.loc[all_["input"] == k, "max_rel_diff"].max()),
                                "limit": v[0], "why": v[1]} for k, v in EXPLAINED.items()},
           "largest_forecast_rel_diff_variance": float(pd.DataFrame(forecasts)["max_rel_diff_forecast"].max()),
           "largest_forecast_gap_sigma_pct_points": float(pd.DataFrame(forecasts)["max_abs_diff_sigma_pct_points"].max())}
summary["passes"] = not summary["unexplained"] and not summary["app_windows_unexplained"]
(OUT / "p13a_live_inputs_summary.json").write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
print({k: summary[k] for k in ("inputs_checked", "inputs_within_tol", "inputs_explained", "unexplained",
                               "app_windows_unexplained", "largest_forecast_rel_diff_variance",
                               "largest_forecast_gap_sigma_pct_points", "passes")})
