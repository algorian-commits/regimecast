"""Positions on Binance USD-M futures by a READ-ONLY API key, and the check that refuses any other key.

Permission check, on every save (and again every few hours while the key is stored):
* Binance (live): ``GET /sapi/v1/account/apiRestrictions`` says what the key may do. The key is accepted only if
  reading is on and every trading, withdrawal and transfer permission is off.
* Futures testnet / demo trading (``GUARDIAN_BINANCE_SAPI_URL`` empty): there is no restrictions endpoint, so
  the key is tested on ``POST /fapi/v1/order/test``, which checks an order without placing it. Binance answers
  -2015 ("permissions for action") to a key that may not trade: accepted. Any other answer means the key got
  past the permission check: refused. If the check cannot be made, the key is refused.

Requests are signed with HMAC-SHA256 as Binance asks. The key travels only in the ``X-MBX-APIKEY`` header and
the secret never leaves this process; errors carry Binance's code and message, never the URL (it holds the
signature).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import urllib.parse
from dataclasses import asdict, dataclass

import httpx

FAPI = os.environ.get("GUARDIAN_BINANCE_FAPI_URL", "https://fapi.binance.com").rstrip("/")
SAPI = os.environ.get("GUARDIAN_BINANCE_SAPI_URL", "https://api.binance.com").rstrip("/")
COVERED = {"BTCUSDT", "ETHUSDT", "SOLUSDT"}
TRADE_FLAGS = ("enableSpotAndMarginTrading", "enableFutures", "enableMargin", "enableVanillaOptions",
               "enablePortfolioMarginTrading", "enableFixApiTrade")
MOVE_FLAGS = ("enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer")
NO_PERMISSION = (-2015, -2014, -1022)   # "permissions for action", bad key format, bad signature


class BinanceError(Exception):
    def __init__(self, status: int, code: int | None, msg: str):
        super().__init__(f"Binance {status} {code}: {msg[:120]}")
        self.status, self.code, self.msg = status, code, msg


@dataclass
class Permissions:
    reading: bool
    trading: bool
    withdrawals: bool
    checked_by: str          # "apiRestrictions" (live) or "order_test" (testnet)

    @property
    def read_only(self) -> bool:
        return self.reading and not self.trading and not self.withdrawals

    def as_dict(self) -> dict:
        return asdict(self)

    def refusal(self) -> str | None:
        if self.withdrawals:
            return "This key can withdraw or transfer funds. RegimeCast only accepts read-only keys: untick everything except \"Enable Reading\"."
        if self.trading:
            return "This key can trade. RegimeCast only accepts read-only keys: untick everything except \"Enable Reading\"."
        if not self.reading:
            return "This key cannot read your account. Tick \"Enable Reading\" (and nothing else)."
        return None


class BinanceClient:
    def __init__(self, key: str, secret: str, fapi: str = FAPI, sapi: str = SAPI):
        self._key, self._secret = key, secret.encode()
        self.fapi, self.sapi = fapi, sapi
        self._http = httpx.Client(timeout=10.0, headers={"X-MBX-APIKEY": key})

    def __repr__(self) -> str:   # never show the key
        return f"BinanceClient(fapi={self.fapi!r})"

    def _signed(self, method: str, base: str, path: str, params: dict | None = None):
        q = urllib.parse.urlencode({**(params or {}), "recvWindow": 10_000, "timestamp": int(time.time() * 1000)})
        sig = hmac.new(self._secret, q.encode(), hashlib.sha256).hexdigest()
        try:
            r = self._http.request(method, f"{base}{path}?{q}&signature={sig}")
        except httpx.HTTPError as e:
            raise BinanceError(0, None, f"not reachable ({type(e).__name__})") from None
        try:
            body = r.json()
        except ValueError:
            body = None
        if r.status_code != 200:
            code = body.get("code") if isinstance(body, dict) else None
            msg = body.get("msg", "") if isinstance(body, dict) else ""
            raise BinanceError(r.status_code, code, str(msg))
        return body

    def restrictions(self) -> dict:
        return self._signed("GET", self.sapi, "/sapi/v1/account/apiRestrictions")

    def can_trade(self) -> bool:
        """Testnet check: does Binance let this key get as far as checking an order? (Nothing is placed.)"""
        try:
            self._signed("POST", self.fapi, "/fapi/v1/order/test",
                         {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quantity": "0.001"})
        except BinanceError as e:
            if e.code in NO_PERMISSION or e.status == 401:
                return False
            if e.status in (0, 404) or e.status >= 500:
                raise   # the check could not be made: the caller refuses the key
            return True   # a complaint about the order itself: the key passed the permission check
        return True

    def positions(self) -> list[dict]:
        return parse(self._signed("GET", self.fapi, "/fapi/v2/positionRisk"))


def check_permissions(client) -> Permissions:
    """What the key may do. Raises BinanceError if Binance cannot be asked (the key is then refused)."""
    if client.sapi:
        r = client.restrictions()
        return Permissions(reading=bool(r.get("enableReading")), trading=any(bool(r.get(f)) for f in TRADE_FLAGS),
                           withdrawals=any(bool(r.get(f)) for f in MOVE_FLAGS), checked_by="apiRestrictions")
    trading = client.can_trade()
    client.positions()   # reading: it must be able to read positions
    return Permissions(reading=True, trading=trading, withdrawals=False, checked_by="order_test")


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse(rows) -> list[dict]:
    """Open positions from ``/fapi/v2/positionRisk`` in RegimeCast's shape (one-way and hedge mode)."""
    out = []
    for p in rows or []:
        amt = _f(p.get("positionAmt")) or 0.0
        if amt == 0:
            continue
        ps = str(p.get("positionSide", "BOTH")).upper()
        side = "long" if ps == "LONG" or (ps == "BOTH" and amt > 0) else "short"
        liq = _f(p.get("liquidationPrice"))
        sym = str(p.get("symbol", ""))
        out.append({"symbol": sym[:12], "side": side, "qty": abs(amt), "entry": _f(p.get("entryPrice")) or 0.0,
                    "leverage": _f(p.get("leverage")),
                    "margin_mode": "isolated" if str(p.get("marginType", "")).lower() == "isolated" else "cross",
                    "liquidation_price": liq if liq and liq > 0 else None, "mark_price": _f(p.get("markPrice"))})
    return out


class FakeBinance:
    """Tests: a key with chosen permissions and positions. Records nothing of the key."""

    def __init__(self, restrictions: dict | None = None, positions: list[dict] | None = None, sapi: str = "x",
                 trade_ok: bool = False):
        self._r = restrictions if restrictions is not None else {"enableReading": True}
        self._p = positions or []
        self.sapi, self.fapi, self.trade_ok = sapi, "x", trade_ok

    def restrictions(self) -> dict:
        return self._r

    def can_trade(self) -> bool:
        return self.trade_ok

    def positions(self) -> list[dict]:
        return parse(self._p)
