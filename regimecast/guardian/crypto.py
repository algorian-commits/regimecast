"""Encryption at rest for Binance read-only keys.

The Fernet key is derived (HKDF-SHA256) from ``GUARDIAN_SECRET``, a server secret Saeed sets on Railway (any
random string of 32+ characters). The database only ever holds Fernet tokens; without the server secret they
cannot be read. Without ``GUARDIAN_SECRET`` Binance keys are switched off: no key is accepted or stored.
Changing the secret makes the stored keys unreadable; their sources then show an error and users add them again.
"""
from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MIN_SECRET = 32
_cache: tuple[str, Fernet] | None = None


class NotConfigured(Exception):
    pass


def _secret() -> str:
    return os.environ.get("GUARDIAN_SECRET", "").strip()


def enabled() -> bool:
    return len(_secret()) >= MIN_SECRET


def fernet() -> Fernet:
    global _cache
    s = _secret()
    if len(s) < MIN_SECRET:
        raise NotConfigured("Binance keys are switched off on this server (GUARDIAN_SECRET is not set).")
    if _cache is None or _cache[0] != s:
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                   info=b"regimecast-guardian-exchange-keys-v1").derive(s.encode())
        _cache = (s, Fernet(base64.urlsafe_b64encode(key)))
    return _cache[1]


def encrypt(text: str) -> str:
    return fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise NotConfigured("The stored key cannot be read with this server's GUARDIAN_SECRET.") from None
