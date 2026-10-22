"""GDELT backfill: crypto headlines (and macro policy-shock headlines) from the GKG files.

GDELT publishes one Global Knowledge Graph file every 15 minutes
(``http://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.gkg.csv.zip``). Each row is one
article GDELT processed in that 15-minute batch, with its URL, source domain, GDELT's
tone score and (in the extras column) the page title and often the precise publish time.

We scan every file in a period, keep only rows whose TITLE matches crypto or macro
policy-shock words, and save them. Times:

* ``seen_at``      the batch time: when GDELT had the article (what a live system knows)
* ``published_at`` the page's own publish time if GDELT found one, else ``seen_at``

Why not the GDELT search API: it answers HTTP 429 to this network, caps every query at
250 articles, and allows one request per 5 seconds. The raw files have no such limits;
because there are ~50,000 of them (250 GB) for May 2025 to now, the full scan runs on
GitHub Actions (``.github/workflows/gdelt_backfill.yml``) and only the small result
comes back. Any period can also be scanned locally:

    python -m regimecast.sentiment.gdelt --start 2025-10-10 --end 2025-10-11 --out oct10.parquet
"""
from __future__ import annotations

import argparse
import html
import io
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd

GKG_URL = "http://data.gdeltproject.org/gdeltv2/{stamp}.gkg.csv.zip"

# Broad on purpose: the tagger (tagging.py) decides later what each headline is about.
_CRYPTO_CI = re.compile(
    r"\b(bitcoins?|btc|ethereum|ether|solana|crypto\w*|stablecoins?|blockchain|defi|altcoins?|"
    r"binance|coinbase|memecoins?|xrp|dogecoin|web3|satoshi|microstrategy|tether|usdt|usdc)\b", re.I)
_CRYPTO_CS = re.compile(r"\b(?:ETH|SOL)\b")  # case-sensitive: "sol" is Spanish for sun
POLICY_MACRO_RE = re.compile(r"\b(tariffs?|sanctions?|sanctioned|trade war|export controls?|export bans?)\b", re.I)

_TITLE_RE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)
_PUB_RE = re.compile(r"<PAGE_PRECISEPUBTIMESTAMP>(\d{14})</PAGE_PRECISEPUBTIMESTAMP>")

COLUMNS = ["seen_at", "published_at", "title", "link", "source", "gdelt_tone", "scope"]


def is_crypto(title: str) -> bool:
    return bool(_CRYPTO_CI.search(title) or _CRYPTO_CS.search(title))


def stamps(start, end) -> list[str]:
    """GKG batch names (every 15 minutes) with start <= time < end, UTC. ``end`` may be "now"."""
    def naive(x):
        if str(x) == "now":
            return pd.Timestamp.now(tz="UTC").tz_localize(None)
        x = pd.Timestamp(x)
        return x.tz_convert("UTC").tz_localize(None) if x.tzinfo else x
    t = pd.date_range(naive(start).floor("15min"), naive(end), freq="15min", inclusive="left")
    return [x.strftime("%Y%m%d%H%M%S") for x in t]


def _ts(stamp: str) -> pd.Timestamp:
    return pd.Timestamp(pd.to_datetime(stamp, format="%Y%m%d%H%M%S"), tz="UTC")


def parse_gkg(text: str) -> list[dict]:
    """Rows of one GKG file (tab-separated, 27 columns) whose title is crypto or macro policy."""
    out = []
    for line in text.splitlines():
        f = line.split("\t")
        if len(f) < 27:
            continue
        m = _TITLE_RE.search(f[26])
        if not m:
            continue
        title = " ".join(html.unescape(m.group(1)).split())
        if not title:
            continue
        if is_crypto(title):
            scope = "crypto"
        elif POLICY_MACRO_RE.search(title):
            scope = "macro_policy"
        else:
            continue
        seen = _ts(f[1])
        p = _PUB_RE.search(f[26])
        pub = seen
        if p:
            try:
                pub = _ts(p.group(1))
            except ValueError:
                pass
        try:
            tone = float(f[15].split(",")[0])
        except (ValueError, IndexError):
            tone = float("nan")
        out.append({"seen_at": seen, "published_at": pub, "title": title, "link": f[4],
                    "source": f[3], "gdelt_tone": tone, "scope": scope})
    return out


def fetch_one(client: httpx.Client, stamp: str, tries: int = 4) -> list[dict]:
    """Download and parse one batch. A missing batch (GDELT has a few gaps) gives []."""
    for i in range(tries):
        try:
            r = client.get(GKG_URL.format(stamp=stamp))
            if r.status_code == 404:
                return []
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
            return parse_gkg(text)
        except (httpx.HTTPError, zipfile.BadZipFile):
            time.sleep(2 * (i + 1))
    print("giving up on", stamp, flush=True)
    return []


def scan(start, end, workers: int = 16) -> tuple[pd.DataFrame, int]:
    """All matching rows for [start, end). Returns (rows, number of batches scanned)."""
    names = stamps(start, end)
    rows: list[dict] = []
    with httpx.Client(timeout=120, follow_redirects=True) as client, ThreadPoolExecutor(workers) as ex:
        for i, got in enumerate(ex.map(lambda s: fetch_one(client, s), names)):
            rows.extend(got)
            if i % 500 == 0:
                print(f"{names[i]}  {i}/{len(names)} batches, {len(rows)} rows", flush=True)
    df = pd.DataFrame(rows, columns=COLUMNS)
    return df, len(names)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    df, n = scan(a.start, a.end, a.workers)
    df.to_parquet(a.out, index=False)
    print(f"wrote {a.out}: {len(df)} rows from {n} batches "
          f"({(df.scope == 'crypto').sum()} crypto, {(df.scope == 'macro_policy').sum()} macro policy)")


if __name__ == "__main__":
    main()
