"""Save Binance leverage brackets (maintenance margin tiers) to data/brackets.json.

Needs a READ-ONLY Binance API key (no trading, no withdrawal permission):

    set BINANCE_API_KEY=...      (Windows)   /  export BINANCE_API_KEY=...
    set BINANCE_API_SECRET=...
    python scripts/fetch_brackets.py
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
data = [d for d in r.json() if d["symbol"] in config.SYMBOLS]
# Tiers are public numbers, not secrets: they ship with the code so the API on
# Railway uses the same ones. Commit the updated file.
out = liquidation.BRACKETS_FILE
out.write_text(json.dumps({"source": "Binance USD-M GET /fapi/v1/leverageBracket (scripts/fetch_brackets.py)",
                           "fetched_at": time.strftime("%Y-%m-%d", time.gmtime()), "symbols": data}, indent=2) + "
",
               encoding="utf-8")
print("saved", len(data), "symbols to", out)
