"""What each headline is about, and removing copies.

* ``coins``: which of BTC, ETH, SOL the title names. A crypto headline that names none of
  them and no other coin is ``general`` (market-wide) and counts for all three coins.
  One that names only another coin (XRP, Dogecoin, ...) is ``other`` and counts for none.
* ``policy``: the "policy shock" category: ``tariffs``, ``sanctions`` or ``regulation``
  (first match in that order), or empty. Macro headlines (scope ``macro_policy``) only
  ever get tariffs or sanctions: they were collected for those words.
* De-duplication: the same link, or a near-identical title within 3 days, is one
  headline. The copy known first is kept and ``copies`` counts how many outlets ran it.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

import pandas as pd

COIN_PATTERNS = {
    "BTC": [re.compile(r"\b(bitcoins?|btc|xbt|microstrategy|saylor)\b", re.I)],
    "ETH": [re.compile(r"\b(ethereum|ether|vitalik)\b", re.I), re.compile(r"\bETH\b")],
    "SOL": [re.compile(r"\bsolana\b", re.I), re.compile(r"\bSOL\b")],
}
OTHER_COIN_RE = re.compile(
    r"\b(xrp|ripple|dogecoin|cardano|bnb|tron|trx|toncoin|avalanche|avax|polkadot|chainlink|"
    r"litecoin|shiba inu|pepe|hyperliquid|monero|stellar|xlm|polygon|matic|bonk|pi network|"
    r"cronos|aptos|arbitrum|kaspa|memecoins?)\b", re.I)

POLICY_PATTERNS = [
    ("tariffs", re.compile(r"\b(tariffs?|trade wars?|import dut(y|ies)|retaliatory dut(y|ies))\b", re.I)),
    ("sanctions", re.compile(r"\b(sanctions?|sanctioned|ofac|export controls?|export bans?|blacklist(ed)?)\b", re.I)),
    ("regulation", re.compile(
        r"\b(sec|cftc|regulat\w*|crackdown|bans?|banned|banning|lawsuits?|sues|sued|enforcement|"
        r"genius act|clarity act|mica|executive order|legislation|lawmakers|senate|congress|"
        r"central bank digital|cbdc|license|licence|tax(es)?|irs|doj|indict\w*|charged)\b", re.I)),
]

TITLE_DUP_WINDOW = pd.Timedelta(days=3)
STALE_AFTER = pd.Timedelta(hours=24)  # page published this long before we saw it = old news

_SOURCE_SUFFIX = re.compile(r"\s+[-|–—:]\s+[^-|–—:]{2,40}$")
_NON_WORD = re.compile(r"[^a-z0-9$%]+")


def coins(title: str) -> list[str]:
    return [c for c, pats in COIN_PATTERNS.items() if any(p.search(title) for p in pats)]


def coin_tag(title: str, scope: str = "crypto") -> str:
    """Comma-joined coins ("BTC,ETH"), "general", "other", or "" for macro headlines."""
    if scope != "crypto":
        return ""
    found = coins(title)
    if found:
        return ",".join(found)
    return "other" if OTHER_COIN_RE.search(title) else "general"


def policy_category(title: str, scope: str = "crypto") -> str:
    for name, pat in POLICY_PATTERNS:
        if scope != "crypto" and name == "regulation":
            continue
        if pat.search(title):
            return name
    return ""


def title_key(title: str) -> str:
    """Lower-case words only, without a trailing " - Source Name"."""
    t = _SOURCE_SUFFIX.sub("", title.strip())
    return _NON_WORD.sub(" ", t.lower()).strip()


def link_key(link: str) -> str:
    """Link without scheme, www., query string, fragment or trailing slash."""
    try:
        u = urlsplit(link.strip())
    except ValueError:
        return link.strip().lower()
    host = u.netloc.lower().removeprefix("www.")
    return f"{host}{u.path.rstrip('/')}".lower()


def _words(key: str) -> frozenset[str]:
    return frozenset(key.split())


def near_same(a: str, b: str, threshold: float = 0.85) -> bool:
    """Near-identical titles: word overlap (Jaccard) of at least ``threshold``."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return a == b
    return len(wa & wb) / len(wa | wb) >= threshold


def tag(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``coins``, ``policy``, ``title_key``, ``link_key`` and ``stale`` columns."""
    df = df.copy()
    scope = df["scope"] if "scope" in df else pd.Series("crypto", index=df.index)
    df["coins"] = [coin_tag(t, s) for t, s in zip(df["title"], scope)]
    df["policy"] = [policy_category(t, s) for t, s in zip(df["title"], scope)]
    df["title_key"] = df["title"].map(title_key)
    df["link_key"] = df["link"].map(link_key)
    df["stale"] = (df["known_at"] - df["published_at"]) > STALE_AFTER
    return df


def dedupe(df: pd.DataFrame, seen: pd.DataFrame | None = None) -> pd.DataFrame:
    """Keep the first-known copy of each headline. ``seen`` = rows already stored (they win).

    Same link (anywhere) or near-identical title within ``TITLE_DUP_WINDOW`` of a kept
    copy is a duplicate. Near-identical is checked among titles that share their first
    three words, which keeps this fast on hundreds of thousands of rows.
    """
    need = ["known_at", "title_key", "link_key"]
    old = (seen[need].sort_values("known_at", kind="stable") if seen is not None and len(seen)
           else pd.DataFrame(columns=need))
    new = df.sort_values("known_at", kind="stable").reset_index(drop=True)
    links = set(old["link_key"])
    by_prefix: dict[str, list[tuple[pd.Timestamp, str, int]]] = {}  # prefix -> [(time, key, row or -1)]
    for t, k in zip(old["known_at"], old["title_key"]):
        by_prefix.setdefault(" ".join(k.split()[:3]), []).append((t, k, -1))

    keep = []
    copies = [1] * len(new)
    for i, (t, tk, lk) in enumerate(zip(new["known_at"], new["title_key"], new["link_key"])):
        if lk in links:
            continue
        bucket = by_prefix.setdefault(" ".join(tk.split()[:3]), [])
        dup = None
        for (t0, k0, row) in reversed(bucket):
            if t - t0 > TITLE_DUP_WINDOW:
                break
            if k0 == tk or near_same(k0, tk):
                dup = row
                break
        links.add(lk)
        if dup is not None:
            if dup >= 0:
                copies[dup] += 1
            continue
        bucket.append((t, tk, i))
        keep.append(i)
    out = new.iloc[keep].copy()
    out["copies"] = [copies[i] for i in keep]
    return out.reset_index(drop=True)
