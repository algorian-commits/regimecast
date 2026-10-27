"""Live paper log: the frozen agent decides every hour on live data, before the outcome is known.

    python -m regimecast.agent.live step --dir state     # one hourly step (GitHub Actions)

Runs on GitHub Actions a few minutes after each hour end (.github/workflows/agent_live.yml) and
keeps its state on the data branch ``agent-data`` (like the news mood on ``news-data``):

    state.json        positions and equity of the paper account and of buy-and-hold
    log.jsonl         one line per hour: inputs, decision, reasons, trades, fees, funding, equity
    api/latest.json   what GET /agent serves (regimecast/api/agent.py)

Each step first marks the positions decided in the previous step to the new mark price and
pays funding for any settlement in between, then decides and logs the new positions. The step
is committed and pushed straight away, so the decision for hour H is on GitHub before the hour
it is about plays out; git history shows when each line was written.

Inputs come from RegimeCast's own API on Railway (GitHub's servers are in the US, where Binance's
futures API is blocked): /market (mark price, funding rate), /forecast (24 h volatility, HAR-S:
the live app has no live combination yet), /stress and /regime. A coin whose inputs are missing
or more than 3 hours old keeps its position for that hour, and the log says so.
Paper fills are at the mark price when the step runs, with the 0.05% taker fee. Funding at a
settlement uses the last funding rate seen before it (Binance's estimate for that settlement).
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.agent import rules as R
from regimecast.agent.backtest import FUNDING_HOURS, TAKER_FEE

API_URL = os.environ.get("AGENT_API_URL", "https://api-production-bb6a.up.railway.app")
MAX_INPUT_AGE_H = 3
CURVE_POINTS = 2000   # equity points kept in api/latest.json
REGIME_CODE = {"calm": 0, "active": 1, "stressed": 2}
FROZEN_FILE = Path(__file__).with_name("frozen.json")   # = final.FROZEN_FILE (final.py needs the data store)


def load_frozen() -> tuple[R.Settings, dict]:
    d = json.loads(FROZEN_FILE.read_text())
    return R.Settings(**d["settings"]), d


def fetch_inputs(symbols, client=None) -> dict:
    """{symbol: {mark, funding_rate, vol_ann, p_stress, regime, p_stressed, stamps}} from our API.
    A missing piece is None."""
    import httpx

    own = client is None
    client = client or httpx.Client(base_url=API_URL, timeout=20.0)

    def get(path, **params):
        try:
            r = client.get(path, params=params)
            return r.json() if r.status_code == 200 else None
        except Exception:
            return None

    out = {}
    try:
        for s in symbols:
            m, f = get(f"/market/{s}"), get("/forecast", symbol=s)
            st, rg = get("/stress", symbol=s), get("/regime", symbol=s)
            out[s] = {
                "mark": m["mark_price"] if m and not m.get("stale") else None,
                "funding_rate": m["funding_rate"] if m else None,
                "vol_ann": f["annualised_vol_pct"] / 100 if f else None,
                "p_stress": st["p_stress_next_hour"] if st else None,
                "regime": REGIME_CODE.get(rg["regime"]) if rg else None,
                "p_stressed": rg["p_stressed"] if rg else None,
                "stamps": {"forecast": f and f.get("inputs_until"), "stress": st and st.get("inputs_until"),
                           "regime": rg and rg.get("inputs_until")},
            }
    finally:
        if own:
            client.close()
    return out


def _fresh(stamp: str | None, hour: pd.Timestamp) -> bool:
    if not stamp:
        return False
    t = pd.Timestamp(stamp)
    return t <= hour and hour - t <= pd.Timedelta(hours=MAX_INPUT_AGE_H)


def settlements_between(a: pd.Timestamp, b: pd.Timestamp) -> list[pd.Timestamp]:
    """Funding times T with a < T <= b."""
    hours = pd.date_range(a.floor("1h") + pd.Timedelta(hours=1), b.floor("1h"), freq="1h")
    return [h for h in hours if h.hour in FUNDING_HOURS]


def new_state(symbols, settings: R.Settings) -> dict:
    z = {s: 0.0 for s in symbols}
    return {"started_at": None, "settings": settings.to_dict(), "last_hour": None, "last_run": None,
            "agent": {"equity": 1.0, "q": dict(z)}, "buy_and_hold": {"equity": 1.0, "q": dict(z)},
            "last_mark": {s: None for s in symbols}, "last_funding_rate": {s: None for s in symbols}}


def step(state: dict, inputs: dict, now: datetime, settings: R.Settings) -> tuple[dict, dict | None]:
    """One hourly step. Returns (new state, log line); the line is None if this hour is done already."""
    hour = pd.Timestamp(now).floor("1h")
    if state["last_hour"] and pd.Timestamp(state["last_hour"]) >= hour:
        return state, None
    symbols = list(state["agent"]["q"])
    k = len(symbols)
    marks = {s: inputs[s]["mark"] or state["last_mark"][s] for s in symbols}
    line = {"hour": hour.isoformat(), "decided_at": pd.Timestamp(now).isoformat(), "coins": {}}

    # 1. mark the previous positions, 2. funding for settlements since the last step
    last_run = pd.Timestamp(state["last_run"]) if state["last_run"] else None
    paid = {"agent": 0.0, "buy_and_hold": 0.0}
    for acct in ("agent", "buy_and_hold"):
        a = state[acct]
        for s in symbols:
            if a["q"][s] and state["last_mark"][s] and marks[s]:
                a["equity"] += a["q"][s] * (marks[s] - state["last_mark"][s])
        if last_run is not None:
            for _ in settlements_between(last_run, pd.Timestamp(now)):
                for s in symbols:
                    rate = state["last_funding_rate"][s]
                    if rate is not None and a["q"][s] and marks[s]:
                        cash = a["q"][s] * marks[s] * rate
                        a["equity"] -= cash
                        paid[acct] += cash

    # 3. decide
    ag = state["agent"]
    sleeve = max(ag["equity"], 0.0) / k
    fee = 0.0
    for s in symbols:
        x = inputs[s]
        fresh = all(_fresh(x["stamps"].get(n), hour) for n in ("forecast", "stress", "regime"))
        c = {"mark": marks[s], "funding_rate": x["funding_rate"], "vol_ann": x["vol_ann"], "p_stress": x["p_stress"],
             "regime": x["regime"], "p_stressed": x["p_stressed"], "inputs_until": x["stamps"]}
        held = ag["q"][s] * marks[s] / sleeve if marks[s] and sleeve > 0 else 0.0
        if not fresh or x["vol_ann"] is None or not marks[s]:
            c.update(exposure_before=held, target=None, exposure=held, traded=0.0,
                     reasons=["Inputs missing or out of date: position kept"])
        else:
            row = pd.Series({"vol_ann": x["vol_ann"], "p_stress": x["p_stress"], "regime": x["regime"]})
            tgt = float(R.exposure(row.to_frame().T.astype(float), settings).iloc[0])
            tgt = min(max(tgt, 0.0), 1.0)
            why = R.reasons(row, settings)
            delta = 0.0
            if abs(tgt - held) >= settings.band:
                delta = tgt * sleeve - ag["q"][s] * marks[s]
                ag["q"][s] += delta / marks[s]
                fee += abs(delta) * TAKER_FEE
            else:
                why.append(f"Change of {100 * (tgt - held):+.0f} points is inside the {100 * settings.band:.0f}-point "
                           "no-trade band: position kept")
            c.update(exposure_before=held, target=tgt, exposure=ag["q"][s] * marks[s] / sleeve, traded=delta,
                     reasons=why)
        line["coins"][s] = c
    ag["equity"] -= fee

    # buy-and-hold: a third in each coin at the first step, then never traded
    bh = state["buy_and_hold"]
    if state["started_at"] is None and all(marks[s] for s in symbols):
        for s in symbols:
            n = bh["equity"] / k
            bh["q"][s] = n / marks[s]
            bh["equity"] -= n * TAKER_FEE
        state["started_at"] = hour.isoformat()

    for s in symbols:
        state["last_mark"][s] = marks[s]
        if inputs[s]["funding_rate"] is not None:
            state["last_funding_rate"][s] = inputs[s]["funding_rate"]
    state["last_hour"], state["last_run"] = hour.isoformat(), pd.Timestamp(now).isoformat()
    line.update(fee=fee, funding={k2: v for k2, v in paid.items()}, equity=ag["equity"], bh_equity=bh["equity"],
                total_exposure=float(np.mean([line["coins"][s]["exposure"] for s in symbols])))
    return state, line


def summary(state: dict, log: list[dict], frozen: dict) -> dict:
    """api/latest.json: the frozen settings, the equity curves and the latest decision."""
    curve = [{"hour": x["hour"], "agent": round(x["equity"], 6), "buy_and_hold": round(x["bh_equity"], 6),
              "exposure": round(x["total_exposure"], 4)} for x in log]
    if len(curve) > CURVE_POINTS:
        curve = curve[:: int(np.ceil(len(curve) / CURVE_POINTS))] + [curve[-1]]
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "started_at": state["started_at"],
            "hours_logged": len(log), "settings": frozen["settings"], "frozen_at": frozen["frozen_at"],
            "equity": state["agent"]["equity"], "bh_equity": state["buy_and_hold"]["equity"],
            "curve": curve, "latest": log[-1] if log else None}


def run_step(d: Path, inputs: dict | None = None, now: datetime | None = None) -> dict | None:
    settings, frozen = load_frozen()
    sf, lf = d / "state.json", d / "log.jsonl"
    state = json.loads(sf.read_text()) if sf.exists() else new_state(config.SYMBOLS, settings)
    if state["settings"] != settings.to_dict():
        raise SystemExit("frozen.json changed since the paper log started; start a new log instead of mixing them")
    now = now or datetime.now(timezone.utc)
    inputs = inputs if inputs is not None else fetch_inputs(list(state["agent"]["q"]))
    state, line = step(state, inputs, now, settings)
    if line is None:
        return None
    with lf.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line) + "\n")
    sf.write_text(json.dumps(state, indent=1))
    log = [json.loads(x) for x in lf.read_text(encoding="utf-8").splitlines() if x.strip()]
    (d / "api").mkdir(parents=True, exist_ok=True)
    (d / "api" / "latest.json").write_text(json.dumps(summary(state, log, frozen)))
    return line


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["step"])
    ap.add_argument("--dir", default="state")
    a = ap.parse_args()
    d = Path(a.dir)
    d.mkdir(parents=True, exist_ok=True)
    line = run_step(d)
    if line is None:
        print("this hour is logged already")
    else:
        print(json.dumps({k: line[k] for k in ("hour", "decided_at", "equity", "bh_equity", "total_exposure")}))


if __name__ == "__main__":
    main()
