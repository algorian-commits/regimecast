"""POST /parse-trade (M8): read a typed trade into fields the Risk Check form can confirm.

Claude reads the text when ANTHROPIC_API_KEY is set. Without a key, past the spending caps, or when the API fails,
the rule reader answers instead and the response says so (`parser` and a warning), so the page always works.
"""
from __future__ import annotations

import os
import threading
import time
from functools import lru_cache

from fastapi import APIRouter

from regimecast.parsing import claude, prompts, rules
from regimecast.parsing.schema import ParseIn, ParseOut, combined_text, respond

router = APIRouter()

RULES_NO_KEY = "Read by the simple rule reader (the AI reader is not set up). Check every field."
RULES_FALLBACK = "The AI reader was unavailable, so the simple rule reader was used. Check every field."


class Limiter:
    """Caps the paid API calls (a public page must not run up a bill): per minute and per UTC day."""

    def __init__(self, per_minute: int, per_day: int, clock=time.time):
        self.per_minute, self.per_day, self.clock = per_minute, per_day, clock
        self.recent: list[float] = []
        self.day, self.today = None, 0
        self.lock = threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            now = self.clock()
            day = int(now // 86400)
            if day != self.day:
                self.day, self.today = day, 0
            self.recent = [t for t in self.recent if now - t < 60]
            if len(self.recent) >= self.per_minute or self.today >= self.per_day:
                return False
            self.recent.append(now)
            self.today += 1
            return True


limiter = Limiter(int(os.environ.get("PARSE_PER_MINUTE", 60)), int(os.environ.get("PARSE_PER_DAY", 2000)))


@lru_cache(maxsize=1)
def _client():
    return claude.make_client()


@router.post("/parse-trade", response_model=ParseOut)
def parse_trade(p: ParseIn) -> ParseOut:
    """Read a typed trade ("long 0.2 btc at 81k, 10x, stop 79k") into fields, ask for what is missing, or refuse
    politely if it is not a trade. Never gives advice: every sentence in the answer is fixed text."""
    text = combined_text(p)
    if not claude.has_key():
        return respond(rules.parse(text), "rules", [RULES_NO_KEY])
    if not limiter.allow():
        return respond(rules.parse(text), "rules", [RULES_FALLBACK])
    try:
        r = claude.parse(text, version=prompts.DEFAULT_VERSION, client=_client())
    except claude.ParseFailed:
        return respond(rules.parse(text), "rules", [RULES_FALLBACK])
    return respond(r.trade, f"{claude.MODEL}, prompt {prompts.DEFAULT_VERSION}")
