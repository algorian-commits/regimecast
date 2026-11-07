"""Positions on Hyperliquid by wallet address. Every account's positions are public there, so no key is needed:
one POST to ``/info`` with ``{"type": "clearinghouseState", "user": <address>}``.

Hyperliquid reports each position's liquidation price itself (cross or isolated), and its value at the mark
price, so the mark is ``positionValue / |size|``. The Guardian uses both as reported.
"""
from __future__ import annotations

import os
import re

import httpx

API = os.environ.get("HYPERLIQUID_API_URL", "https://api.hyperliquid.xyz").rstrip("/")
ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")
COVERED = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT"}


class HyperliquidError(Exception):
    pass


def normalise_address(address: str) -> str | None:
    a = (address or "").strip()
    return a.lower() if ADDRESS.match(a) else None


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


def parse(state: dict) -> list[dict]:
    """Open positions in RegimeCast's shape (one dict per coin and side)."""
    out = []
    for item in state.get("assetPositions") or []:
        p = item.get("position") or {}
        size = _f(p.get("szi")) or 0.0
        if size == 0:
            continue
        coin = str(p.get("coin", ""))
        value = _f(p.get("positionValue"))
        lev = p.get("leverage") or {}
        liq = _f(p.get("liquidationPx"))
        out.append({
            "symbol": COVERED.get(coin, coin[:12]), "side": "long" if size > 0 else "short",
            "qty": abs(size), "entry": _f(p.get("entryPx")) or 0.0,
            "leverage": _f(lev.get("value")), "margin_mode": "cross" if lev.get("type") == "cross" else "isolated",
            "liquidation_price": liq if liq and liq > 0 else None,
            "mark_price": value / abs(size) if value else None,
        })
    return out


class HyperliquidClient:
    def __init__(self, api: str = API):
        self.api = api

    def positions(self, address: str) -> list[dict]:
        try:
            r = httpx.post(f"{self.api}/info", json={"type": "clearinghouseState", "user": address}, timeout=10)
        except httpx.HTTPError as e:
            raise HyperliquidError(f"Hyperliquid not reachable ({type(e).__name__})") from None
        if r.status_code != 200:
            raise HyperliquidError(f"Hyperliquid answered {r.status_code}")
        body = r.json()
        if not isinstance(body, dict):
            raise HyperliquidError("Hyperliquid did not return an account")
        return parse(body)


class FakeHyperliquid:
    """Tests: positions per address, set by hand."""

    def __init__(self, states: dict[str, dict] | None = None):
        self.states = states or {}

    def positions(self, address: str) -> list[dict]:
        if address not in self.states:
            raise HyperliquidError("Hyperliquid answered 500")
        return parse(self.states[address])
