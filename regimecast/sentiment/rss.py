"""RSS collector for the four crypto news sites, to keep the news mood current.

Each run reads the feeds once. Times:

* ``published_at`` the feed's own time, converted to UTC
* ``fetched_at``   when we read it
* ``known_at``     ``published_at`` (never later than ``fetched_at``): a headline published
  at 10:20 counts for the hour ending 11:00, as the plan asks. The live table can still
  change for the last hour or two as late items arrive; anything that reads it at time t
  only ever sees what was stored by t.
"""
from __future__ import annotations

import calendar
from datetime import datetime, timezone

import httpx
import pandas as pd

FEEDS = {
    "coindesk.com": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph.com": "https://cointelegraph.com/rss",
    "theblock.co": "https://www.theblock.co/rss.xml",
    "decrypt.co": "https://decrypt.co/feed",
}
HEADERS = {"User-Agent": "Mozilla/5.0 (RegimeCast student project; news mood)"}


def parse_feed(content: bytes | str, source: str, fetched_at: pd.Timestamp) -> pd.DataFrame:
    import feedparser

    feed = feedparser.parse(content)
    rows = []
    for e in feed.entries:
        title = " ".join((e.get("title") or "").split())
        link = e.get("link") or ""
        if not title or not link:
            continue
        t = e.get("published_parsed") or e.get("updated_parsed")
        pub = pd.Timestamp(calendar.timegm(t), unit="s", tz="UTC") if t else fetched_at
        rows.append({"title": title, "link": link, "source": source, "summary": (e.get("summary") or "")[:500],
                     "published_at": pub, "fetched_at": fetched_at})
    df = pd.DataFrame(rows, columns=["title", "link", "source", "summary", "published_at", "fetched_at"])
    df["known_at"] = df[["published_at", "fetched_at"]].min(axis=1) if len(df) else df["fetched_at"]
    df["origin"] = "rss"
    df["scope"] = "crypto"  # these sites only publish crypto news
    df["gdelt_tone"] = float("nan")
    return df


def fetch_all(feeds: dict[str, str] = FEEDS, client: httpx.Client | None = None) -> pd.DataFrame:
    """All current items of every feed. A feed that fails is skipped (and reported)."""
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True, headers=HEADERS)
    parts = []
    try:
        for source, url in feeds.items():
            now = pd.Timestamp(datetime.now(timezone.utc))
            try:
                r = client.get(url)
                r.raise_for_status()
                df = parse_feed(r.content, source, now)
                print(f"rss {source}: {len(df)} items", flush=True)
                parts.append(df)
            except httpx.HTTPError as e:
                print(f"rss {source}: failed ({e})", flush=True)
    finally:
        if own:
            client.close()
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
