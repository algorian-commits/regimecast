"""A no-AI rule reader for typed trades (regular expressions).

Two jobs: the baseline the Claude parser is compared with, and the fallback when no API key is set or the API is
down (the app then says the simple reader was used). Written and frozen before the test set was drafted.
"""
from __future__ import annotations

import re

from regimecast.parsing.schema import ParsedTrade

COINS = {"BTC": r"btc|bitcoin|xbt", "ETH": r"eth|ether|ethereum", "SOL": r"sol|solana"}
OTHER_COINS = (r"doge|dogecoin|xrp|ripple|ada|cardano|bnb|pepe|shib|avax|link|ltc|litecoin|dot|matic|ton|trx|sui|"
               r"arb|apt|near|wif|bonk|bch|etc|fil|uni|aave|inj|sei|tia|jup|hype")
ANY_COIN = "|".join(COINS.values())
NUM = r"(\d+(?:\.\d+)?)(?![\d.])(?:\s*([km])\b)?"  # 58k, 81.2k, 1500, 2m (never part of a longer number)
NOT_LEV = r"(?!\s*(?:x\b|×|%|times\b))"            # a price is never followed by x or %

ADVICE = re.compile(
    r"\b(should i|shall i|should we|what should|which (?:coin|one|leverage)|what leverage|how much leverage should|"
    r"recommend|suggest|good (?:time|idea|entry)|is (?:it|now) (?:a )?good|will (?:it|btc|eth|sol|bitcoin|ethereum|"
    r"solana|the price)|predict|prediction|going to (?:pump|dump|go up|go down|moon|crash)|best (?:coin|leverage|"
    r"time)|where is .* heading|buy or sell|long or short\?)")


def _num(v: str, mult: str | None) -> float:
    return float(v) * {None: 1, "": 1, "k": 1e3, "m": 1e6}[mult or None]


def _clean(text: str) -> str:
    t = text.lower().replace("×", "x").replace("–", "-")
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)        # 1,500 -> 1500
    return re.sub(r"\s+", " ", t)


def _asset(t: str) -> str | None:
    hits = [(m.start(), a) for a, pat in COINS.items() for m in re.finditer(rf"\b(?:{pat})(?:usdt)?\b", t)]
    hits += [(m.start(), "OTHER") for m in re.finditer(rf"\b(?:{OTHER_COINS})(?:usdt)?\b", t)]
    return min(hits)[1] if hits else None


def _side(t: str) -> str | None:
    m = re.search(r"\b(long(?:ing|ed)?|buy(?:ing)?|bought|short(?:ing|ed)?|sell(?:ing)?|sold)\b", t)
    if not m:
        return None
    return "long" if m.group(1).startswith(("long", "buy", "bought")) else "short"


def _leverage(t: str) -> float | None:
    if re.search(r"\bno leverage\b|\bwithout leverage\b", t):
        return 1.0
    for pat in (r"(\d+(?:\.\d+)?)\s*x\b", r"\bx\s*(\d+(?:\.\d+)?)\b", r"(\d+(?:\.\d+)?)\s*times\b",
                r"\blev(?:erage)?d?\s*(?:of|:|=|at|is)?\s*(\d+(?:\.\d+)?)"):
        m = re.search(pat, t)
        if m:
            return float(m.group(1))
    return None


STOP = r"\b(?:stop[- ]?loss|stop|sl)\b(?:\s*(?:at|@|:|=|is|of|to|around|price|on))*\s*\$?"


def _stop(t: str) -> tuple[float | None, float | None, tuple[int, int] | None]:
    """(price, percent, span) of the stop-loss."""
    m = re.search(STOP + r"(\d+(?:\.\d+)?)\s*%", t)
    if m:
        return None, float(m.group(1)) / 100, m.span()
    m = re.search(STOP + NUM + NOT_LEV, t)
    if m:
        return _num(m.group(1), m.group(2)), None, m.span()
    return None, None, None


def _entry(t: str) -> float | None:
    if re.search(r"\bat (?:the )?(?:market|current price)\b|\bmarket (?:order|price)\b", t):
        return None
    pat = (r"(?:@|\bat\b|\bentry\b|\bentered\b|\bentering\b|\bfrom\b|\bopened at\b|\bin at\b|\bprice\b)"
           r"(?:\s*(?:price|of|:|=|at|around|is|was))*\s*\$?" + NUM + NOT_LEV +
           r"(?!\s*(?:usdt|usd|dollars|bucks)?\s*(?:margin|collateral|position|of|worth|wallet|balance)\b)"
           rf"(?!\s*(?:{ANY_COIN})\b)")
    m = re.search(pat, t)
    return _num(m.group(1), m.group(2)) if m else None


def _sizes(t: str) -> dict:
    out: dict = {}
    m = re.search(rf"(?<![\$\d.])(\d+(?:\.\d+)?|\.\d+)\s*(?:{ANY_COIN})\b", t)
    if m:
        out["qty_coins"] = float(m.group(1))
    elif re.search(rf"\bhalf an? (?:{ANY_COIN})\b", t):
        out["qty_coins"] = 0.5
    money = rf"(?:\$\s*{NUM}|{NUM}\s*(?:usdt|usd|dollars|bucks)\b)"
    for m in re.finditer(money, t):
        v = _num(m.group(1) or m.group(3), m.group(2) or m.group(4))
        before, after = t[max(0, m.start() - 30):m.start()], t[m.end():m.end() + 25]
        if re.search(r"\b(wallet|balance|account)\b", before + " " + after.split(",")[0]):
            out.setdefault("wallet_usdt", v)
        elif re.search(rf"^\s*(margin|collateral)\b", after) or re.search(r"\b(margin|collateral|with|using|put in|"
                                                                        r"putting|risking)\s*(?:of)?\s*$", before):
            out.setdefault("margin_usdt", v)
        elif re.search(rf"^\s*(position|size|worth|notional|exposure|of (?:{ANY_COIN}|{OTHER_COINS}))\b", after) or \
                re.search(r"\b(position|size|notional|exposure)\s*(?:size)?\s*(?:of|is|:|=)?\s*$", before):
            out.setdefault("notional_usdt", v)
        else:
            out.setdefault("margin_usdt", v)
    for key, pat in (("margin_usdt", r"\bmargin\s*(?:of|:|=|is)?\s*" + NUM + r"(?!\s*x)"),
                     ("notional_usdt", r"\b(?:position|size)\s*(?:size)?\s*(?:of|:|=|is)?\s*" + NUM + NOT_LEV),
                     ("wallet_usdt", r"\b(?:wallet|balance)\s*(?:of|:|=|is|has)?\s*" + NUM)):
        m = re.search(pat, t)
        if m and key not in out:
            out[key] = _num(m.group(1), m.group(2))
    return out


def parse(text: str) -> ParsedTrade:
    t = _clean(text)
    asset, side, lev = _asset(t), _side(t), _leverage(t)
    stop, stop_pct, span = _stop(t)
    rest = t[:span[0]] + " " + t[span[1]:] if span else t
    entry = _entry(rest)
    sizes = _sizes(rest)
    has_position = entry is not None or bool(sizes)
    if ADVICE.search(t) and not has_position:
        return ParsedTrade(intent="advice")
    if asset is None and side is None and lev is None and not sizes:
        return ParsedTrade(intent="off_topic")
    if stop_pct is not None and entry is not None and side:
        stop = round(entry * (1 - stop_pct if side == "long" else 1 + stop_pct), 8)
    mode = "cross" if re.search(r"\bcross\b", t) else "isolated" if re.search(r"\b(isolated|iso)\b", t) else None
    try:
        return ParsedTrade(intent="trade", asset=asset, side=side, leverage=lev or None, entry=entry or None,
                           stop=stop or None, margin_mode=mode, **{k: v for k, v in sizes.items() if v > 0})
    except ValueError:  # an impossible number (e.g. 0x): keep what is valid
        return ParsedTrade(intent="trade", asset=asset, side=side)
