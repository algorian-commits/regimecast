"""13A pass mark: the risk check answers in under 2 s for 95% of 50 live requests, on the live combination.

    python scripts/p13a_latency.py                                   # this branch, a local API on live Binance data
    python scripts/p13a_latency.py --base-url https://<site>/api --out p13a_latency_deployed.json   # after deploying

Without --base-url it runs the API in this process, waits for its first real refresh (live Binance and Deribit data)
and times real HTTP requests. Each answer is also checked: it must come from the combination, with both parts'
drivers, and hold no crowd or up/down figure. Writes data/reports/p13a_latency.json.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import json
import threading
import time

import httpx
import numpy as np

COINS = {"BTCUSDT": 100000.0, "ETHUSDT": 4000.0, "SOLUSDT": 200.0}
OUT_DIR = pathlib.Path(__file__).resolve().parents[1] / "data" / "reports"
BANNED = ("crowd", "p_up", "coin_flip", "went up")


def serve(port: int) -> None:
    import uvicorn

    from regimecast.api.main import app

    threading.Thread(target=uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning")).run, daemon=True).start()


ap = argparse.ArgumentParser()
ap.add_argument("--base-url")
ap.add_argument("--n", type=int, default=50)
ap.add_argument("--out", default="p13a_latency.json")
a = ap.parse_args()
local = a.base_url is None
base = a.base_url or "http://127.0.0.1:8765"
if local:
    serve(8765)
c = httpx.Client(base_url=base, timeout=30)

t0 = time.perf_counter()
ready = {}
while time.perf_counter() - t0 < 300 and len(ready) < len(COINS):
    for s in COINS:
        try:
            for H in (1, 24):
                f = c.get("/forecast", params={"symbol": s, "horizon_hours": H})
                if f.status_code == 200 and f.json()["model"].startswith("HAR-S + LightGBM"):
                    ready.setdefault(s, {})[H] = f.json()
        except httpx.TransportError:
            pass
    ready = {s: v for s, v in ready.items() if len(v) == 2}
    if len(ready) < len(COINS):
        time.sleep(2)
assert len(ready) == len(COINS), f"no combination forecast after 5 minutes: {sorted(ready)}"
print(f"combination live for every coin after {time.perf_counter() - t0:.0f} s", flush=True)

times, problems = [], []
symbols = list(COINS)
for i in range(a.n):
    s = symbols[i % 3]
    body = {"symbol": s, "side": "long" if i % 2 else "short", "entry": COINS[s], "leverage": 10 + i % 4 * 5, "margin": 100,
            "horizon_hours": 24 if i % 4 else 1}
    t = time.perf_counter()
    r = c.post("/risk-check", json=body)
    times.append(time.perf_counter() - t)
    j = r.json()
    if r.status_code != 200 or not j["volatility_source"].startswith("HAR-S + LightGBM") or not j["drivers_gbm"] or not j["verdict"]:
        problems.append({"i": i, "status": r.status_code})
    text = r.text.lower()
    problems += [{"i": i, "banned": b} for b in BANNED if b in text]

x = np.array(times)
out = {"base_url": "local API, this branch, live Binance data" if local else base, "n": a.n,
       "under_2s": int((x < 2).sum()), "typical_s": round(float(np.median(x)), 3), "p95_s": round(float(np.quantile(x, 0.95)), 3),
       "slowest_s": round(float(x.max()), 3), "problems": problems,
       "passes": bool((x < 2).mean() >= 0.95 and not problems),
       "forecasts": {s: {f"{H}h": {"model": ready[s][H]["model"], "sigma_pct": round(ready[s][H]["sigma_pct"], 3),
                                   "inputs_until": ready[s][H]["inputs_until"]} for H in (1, 24)} for s in COINS},
       "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
OUT_DIR.mkdir(parents=True, exist_ok=True)
(OUT_DIR / a.out).write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
print({k: out[k] for k in ("n", "under_2s", "typical_s", "p95_s", "slowest_s", "problems", "passes")})
print(json.dumps(out["forecasts"]["BTCUSDT"]))
print(json.dumps(c.post("/risk-check", json={"symbol": "BTCUSDT", "side": "long", "entry": 100000.0, "leverage": 10, "margin": 100}).json()["drivers_gbm"][:4]))
