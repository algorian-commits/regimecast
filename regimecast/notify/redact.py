"""Keep secrets out of the logs.

httpx logs every request URL at INFO level. Telegram puts the bot token in the URL
(``/bot<token>/sendMessage``) and Binance puts the request signature in the query string, so a
filter on the HTTP libraries' loggers rewrites those parts before anything is written.
"""
from __future__ import annotations

import logging
import re

PATTERNS = [
    (re.compile(r"/bot\d+:[A-Za-z0-9_-]+"), "/bot<redacted>"),
    (re.compile(r"(signature=)[0-9A-Za-z%+/=_-]+"), r"\1<redacted>"),
    (re.compile(r"(X-MBX-APIKEY['\"]?[:=]\s*['\"]?)[A-Za-z0-9]+", re.I), r"\1<redacted>"),
]
LOGGERS = ("httpx", "httpcore", "urllib3", "uvicorn.access")


def clean(text: str) -> str:
    for pat, sub in PATTERNS:
        text = pat.sub(sub, text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:   # a broken format string is not ours to fix
            return True
        cleaned = clean(msg)
        if cleaned != msg:
            record.msg, record.args = cleaned, ()
        return True


_FILTER = RedactFilter()


def install() -> None:
    for name in LOGGERS:
        lg = logging.getLogger(name)
        if _FILTER not in lg.filters:
            lg.addFilter(_FILTER)
