"""Save Binance leverage brackets (maintenance margin tiers) to data/brackets.json.

Needs a READ-ONLY Binance API key (no trading, no withdrawal permission):

    set BINANCE_API_KEY=...      (Windows)   /  export BINANCE_API_KEY=...
    set BINANCE_API_SECRET=...
    python scripts/fetch_brackets.py
    python scripts/fetch_brackets.py --add XRPUSDT DOGEUSDT     (append coins, leave the rest untouched)
"""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))  # repo root

import hashlib
import hmac
import json
import os
import time
import urllib.parse

import httpx

from regimecast import config
from regimecast.risk import liquidation

key, secret = os.environ["BINANCE_API_KEY"], os.environ["BINANCE_API_SECRET"]
q = urllib.parse.urlencode({"timestamp": int(time.time() * 1000)})
sig = hmac.new(secret.encode(), q.encode(), hashlib.sha256).hexdigest()
r = httpx.get(f"{config.BINANCE_FAPI_URL}/fapi/v1/leverageBracket?{q}&signature={sig}",
              headers={"X-MBX-APIKEY": key}, timeout=20)
r.raise_for_status()
add = [a.upper() for a in sys.argv[sys.argv.index("--add") + 1:]] if "--add" in sys.argv else []
out = liquidation.BRACKETS_FILE
if add:
    # Only appends these coins: the tiers already in the file stay exactly as they are.
    saved = json.loads(out.read_text(encoding="utf-8"))
    have = {d["symbol"] for d in saved["symbols"]}
    saved["symbols"] += [d for d in r.json() if d["symbol"] in add and d["symbol"] not in have]
    saved.setdefault("added", []).append({"fetched_at": time.strftime("%Y-%m-%d", time.gmtime()), "symbols": add})
    out.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    print("added", add, "to", out)
    raise SystemExit
data = [d for d in r.json() if d["symbol"] in config.SYMBOLS]
# Tiers are public numbers, not secrets: they ship with the code so the API on
# Railway uses the same ones. Commit the updated file.
out.write_text(json.dumps({"source": "Binance USD-M GET /fapi/v1/leverageBracket (scripts/fetch_brackets.py)",
                           "fetched_at": time.strftime("%Y-%m-%d", time.gmtime()), "symbols": data}, indent=2) + "\n",
               encoding="utf-8")
print("saved", len(data), "symbols to", out)
