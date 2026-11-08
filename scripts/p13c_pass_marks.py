"""13C pass marks: real Telegram, real Binance futures testnet, a real Hyperliquid address.

    & .\\scripts\\set_guardian_secrets.ps1                       # once: bot token and testnet keys, hidden input
    python scripts/p13c_pass_marks.py --hl-address 0x...        # writes data/reports/p13c_pass_marks.json

Pass marks (fixed 31 Oct 2026, docs/PLAN.md):
1. Binance futures testnet, read-only key: a test position whose risk is forced over the limit produces a Telegram
   message within 2 minutes, with the safer version inside.
2. The same for a Hyperliquid address.
3. A key with trading or withdrawal permission is refused.
4. The security tests pass (tests/test_guardian.py; CI runs them too).

Before running: open a small BTC, ETH or SOL position on the testnet (https://testnet.binancefuture.com) with
leverage, and have a Hyperliquid address that holds an open BTC, ETH or SOL position.

How it runs: the real API app in this process (its lifespan starts the model caches, the Guardian loop and the
Telegram poller) on a throwaway SQLite database, with Binance's URLs pointed at the testnet. You link Telegram by
opening the printed link on your phone. For each source the script waits for the Guardian's first reading, then
"forces" the risk over the limit the way a user would: it gives the position its own tiny risk limit (0.01%). The
clock starts there and stops when the Guardian loop's own Telegram delivery is logged as sent. Nothing is sped up.

Keys come only from environment variables, are never printed, and are not in the report.
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import argparse
import json
import os
import secrets
import subprocess
import tempfile
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "reports" / "p13c_pass_marks.json"
LIMIT_SECONDS = 120
FORCED_LIMIT = 0.0001


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hl-address", help="Hyperliquid address with an open BTC/ETH/SOL position")
    ap.add_argument("--testnet-url", default="https://testnet.binancefuture.com")
    ap.add_argument("--skip-binance", action="store_true")
    a = ap.parse_args()

    need = ["TELEGRAM_BOT_TOKEN"] + ([] if a.skip_binance else ["BINANCE_TESTNET_READ_KEY", "BINANCE_TESTNET_READ_SECRET"])
    missing = [n for n in need if not os.environ.get(n)]
    if missing:
        print("Missing:", ", ".join(missing), "- run scripts/set_guardian_secrets.ps1, then open a new terminal.")
        return 2

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="p13c_"))
    os.environ.update({
        "DATABASE_URL": f"sqlite:///{(tmp / 'pass.sqlite').as_posix()}",
        "GUARDIAN_SECRET": secrets.token_urlsafe(36),   # this run only
        "SESSION_SECRET": secrets.token_urlsafe(36),
        "SESSION_COOKIE_SECURE": "0",
        "GUARDIAN_BINANCE_FAPI_URL": a.testnet_url,
        "GUARDIAN_BINANCE_SAPI_URL": "",                 # the testnet has no /sapi: the order-test check is used
        "ALERT_ENGINE": "0", "WHALE_WATCH": "0", "TRACK_RECORD": "0",
    })

    import sqlalchemy as sa
    from fastapi.testclient import TestClient

    from regimecast import db
    from regimecast.api import main as api_main
    from regimecast.db.schema import alert_events, notify_log

    report: dict = {"run_at": datetime.now(timezone.utc).isoformat(), "testnet_url": a.testnet_url,
                    "limit_seconds": LIMIT_SECONDS, "marks": {}}

    with TestClient(api_main.app, base_url="http://testserver") as c:
        r = c.post("/auth/signup", json={"email": "passmark@example.test", "password": secrets.token_urlsafe(16)})
        assert r.status_code == 201, r.text
        # A high profile limit first, so nothing fires before the forcing step.
        assert c.patch("/me/profile", json={"risk_limit": 0.5}).status_code == 200

        link = c.post("/me/telegram/link")
        if link.status_code != 200:
            print("Telegram link failed:", link.status_code, link.json().get("detail"))
            return 1
        print("\nOpen this on your phone and tap Start (works once, 10 minutes):\n ", link.json()["url"], "\n")
        t_end = time.time() + 600
        while not c.get("/me/telegram").json()["linked"]:
            if time.time() > t_end:
                print("Not linked within 10 minutes.")
                return 1
            time.sleep(2)
        print("Telegram linked.")

        def force_and_time(source_id: int, label: str) -> dict:
            out: dict = {"source": label}
            print(f"[{label}] waiting for the Guardian's first reading (it checks once a minute)...")
            t_end = time.time() + 300
            pos = None
            while time.time() < t_end:
                ps = [p for p in c.get("/me/guardian").json()["positions"] if p["source_id"] == source_id]
                ps = [p for p in ps if p["covered"] is not False]
                if ps and ps[0]["p_liquidation"] is not None:
                    pos = ps[0]
                    break
                time.sleep(3)
            if pos is None:
                out.update(passed=False, why="no open BTC/ETH/SOL position was read and judged within 5 minutes")
                return out
            out["position"] = {k: pos[k] for k in ("symbol", "side", "leverage", "qty", "entry", "mark_price",
                                                  "liquidation_price", "p_liquidation")}
            t0 = datetime.now(timezone.utc)
            assert c.patch(f"/me/guardian/positions/{pos['id']}", json={"risk_limit": FORCED_LIMIT}).status_code == 200
            print(f"[{label}] {pos['symbol']} {pos['side']}: own limit set to {FORCED_LIMIT:.2%}, clock started.")
            eng = db.get_engine()
            t_end = time.time() + LIMIT_SECONDS + 60
            while time.time() < t_end:
                with eng.begin() as conn:
                    row = conn.execute(
                        sa.select(alert_events.c.id, alert_events.c.message, alert_events.c.detail, alert_events.c.fired_at,
                                  notify_log.c.sent_at)
                        .join(notify_log, notify_log.c.event_id == alert_events.c.id)
                        .where(alert_events.c.kind == "guardian_risk", notify_log.c.status == "sent",
                               notify_log.c.channel == "telegram")
                        .order_by(alert_events.c.id.desc())).mappings().all()
                row = next((x for x in row if (x["detail"] or {}).get("position_id") == pos["id"]), None)
                if row:
                    sent = row["sent_at"] if row["sent_at"].tzinfo else row["sent_at"].replace(tzinfo=timezone.utc)
                    secs = (sent - t0).total_seconds()
                    safer = (row["detail"] or {}).get("safer") or {}
                    has_safer = "Safer version" in row["message"] or safer.get("status") in ("safe", "none")
                    out.update(seconds_to_telegram=round(secs, 1), safer_status=safer.get("status"),
                               safer_in_message=has_safer, message=row["message"],
                               passed=secs <= LIMIT_SECONDS and has_safer)
                    print(f"[{label}] Telegram sent {secs:.0f} s after forcing; safer version inside: {has_safer}.")
                    return out
                time.sleep(2)
            out.update(passed=False, why=f"no Telegram message logged as sent within {LIMIT_SECONDS + 60} s")
            return out

        # Mark 3 first: a trading key must be refused (and nothing stored).
        tk, ts = os.environ.get("BINANCE_TESTNET_TRADE_KEY"), os.environ.get("BINANCE_TESTNET_TRADE_SECRET")
        if tk and ts and not a.skip_binance:
            r = c.post("/me/guardian/sources/binance", json={"api_key": tk, "api_secret": ts, "label": "trading key"})
            stored = len(c.get("/me/guardian").json()["sources"])
            report["marks"]["3_trading_key_refused"] = {
                "status_code": r.status_code, "detail": r.json().get("detail"), "sources_stored": stored,
                "passed": r.status_code == 400 and stored == 0}
            print("Trading key:", r.status_code, r.json().get("detail"))
        else:
            report["marks"]["3_trading_key_refused"] = {"passed": None, "why": "no BINANCE_TESTNET_TRADE_KEY set"}

        if not a.skip_binance:
            r = c.post("/me/guardian/sources/binance", json={"api_key": os.environ["BINANCE_TESTNET_READ_KEY"],
                                                             "api_secret": os.environ["BINANCE_TESTNET_READ_SECRET"],
                                                             "label": "testnet read-only"})
            if r.status_code != 201:
                report["marks"]["1_binance_testnet"] = {"passed": False, "why": f"key refused: {r.json().get('detail')}"}
                print("Read-only key refused:", r.json().get("detail"))
            else:
                report["marks"]["1_binance_testnet"] = {"permissions": r.json()["permissions"],
                                                        **force_and_time(r.json()["id"], "binance testnet")}
        if a.hl_address:
            r = c.post("/me/guardian/sources/hyperliquid", json={"address": a.hl_address, "label": "pass mark"})
            if r.status_code != 201:
                report["marks"]["2_hyperliquid"] = {"passed": False, "why": r.json().get("detail")}
            else:
                report["marks"]["2_hyperliquid"] = force_and_time(r.json()["id"], "hyperliquid")
        else:
            report["marks"]["2_hyperliquid"] = {"passed": None, "why": "no --hl-address given"}

    t = subprocess.run([sys.executable, "-m", "pytest", "tests/test_guardian.py", "-q"], cwd=ROOT,
                       capture_output=True, text=True)
    last = (t.stdout.strip().splitlines() or [""])[-1]
    report["marks"]["4_security_tests"] = {"result": last, "passed": t.returncode == 0}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print("\nResults:")
    for k, v in report["marks"].items():
        print(f"  {k}: {'PASS' if v.get('passed') else 'not run' if v.get('passed') is None else 'FAIL'}"
              f"{'' if v.get('passed') else '  (' + str(v.get('why', v.get('detail', ''))) + ')'}")
    print("wrote", OUT)
    return 0 if all(v.get("passed") for v in report["marks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
