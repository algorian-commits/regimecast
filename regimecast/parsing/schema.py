"""The parser's fixed output schema, its checks, and what we say back to the user.

The model only fills these fields. It has no free-text field, so it cannot write advice: every sentence the user
sees (follow-up questions, refusals, warnings) is written here, by code.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Asset = Literal["BTC", "ETH", "SOL", "OTHER"]
Intent = Literal["trade", "advice", "off_topic"]

# Wide sanity bands for a price typed by a user. A value outside means the shorthand was misread
# (e.g. "58k" read as 58), so the parser is asked to try again.
PRICE_BANDS = {"BTC": (1_000.0, 2_000_000.0), "ETH": (50.0, 100_000.0), "SOL": (1.0, 10_000.0)}

FIELDS = ["intent", "asset", "side", "leverage", "entry", "stop", "qty_coins", "notional_usdt", "margin_usdt",
          "margin_mode", "wallet_usdt"]


class ParsedTrade(BaseModel):
    """What the user typed, field by field. null = not stated (never guessed)."""
    intent: Intent = Field(description="trade: describes a position to check; advice: asks what to trade; off_topic: anything else")
    asset: Asset | None = Field(None, description="BTC, ETH or SOL; OTHER for any other coin")
    side: Literal["long", "short"] | None = None
    leverage: float | None = Field(None, gt=0, le=1000)
    entry: float | None = Field(None, gt=0, description="Entry price in USDT; null if not given or 'at market'")
    stop: float | None = Field(None, gt=0, description="Stop-loss price in USDT")
    qty_coins: float | None = Field(None, gt=0, description="Size in coins, e.g. 0.2 (BTC)")
    notional_usdt: float | None = Field(None, gt=0, description="Size as the position's dollar value")
    margin_usdt: float | None = Field(None, gt=0, description="Size as the margin put in")
    margin_mode: Literal["isolated", "cross"] | None = None
    wallet_usdt: float | None = Field(None, gt=0, description="Cross margin: the whole wallet balance")

    @field_validator("entry", "stop")
    @classmethod
    def _finite(cls, v):
        if v is not None and v != v:  # NaN
            raise ValueError("must be a number")
        return v


def json_schema() -> dict:
    """The schema sent to the API as structured output: every field present, null when not stated.

    Written by hand (not generated from ParsedTrade) because structured outputs accept only a subset of JSON
    Schema; number limits are checked afterwards by ParsedTrade, and a failure triggers a retry."""
    num = {"type": ["number", "null"]}

    def enum(*vals):
        return {"type": ["string", "null"], "enum": [*vals, None]}

    props = {
        "intent": {"type": "string", "enum": ["trade", "advice", "off_topic"]},
        "asset": enum("BTC", "ETH", "SOL", "OTHER"),
        "side": enum("long", "short"),
        "leverage": num, "entry": num, "stop": num,
        "qty_coins": num, "notional_usdt": num, "margin_usdt": num,
        "margin_mode": enum("isolated", "cross"),
        "wallet_usdt": num,
    }
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def plausibility_errors(t: ParsedTrade) -> list[str]:
    """Problems that mean the text was misread (sent back to the model for a retry), not user mistakes."""
    errs = []
    band = PRICE_BANDS.get(t.asset or "")
    for name in ("entry", "stop"):
        v = getattr(t, name)
        if band and v is not None and not band[0] <= v <= band[1]:
            errs.append(f"{name} {v:g} is not a plausible {t.asset} price in USDT; re-read shorthand such as "
                        f"'58k' = 58000 or '3.2k' = 3200")
    return errs


# ------------------------------------------------------------------ what the user sees
MISSING_PHRASES = {
    "asset": "which coin (BTC, ETH or SOL)",
    "side": "whether it is a long or a short",
    "leverage": "the leverage (for example 10x)",
    "size": "the size (in coins, as a dollar position, or the margin you put in)",
    "wallet_usdt": "your cross-margin wallet balance",
}
REFUSE_ADVICE = ("I only read the trade you describe; I can't tell you what to trade or when. Describe a position "
                 "(coin, long or short, size, leverage, entry) and I'll show you its risk.")
REFUSE_OFF_TOPIC = ("That doesn't look like a trade. Try something like: \"Long 0.1 BTC at 81k, 10x, stop 79k\".")
UNSUPPORTED = "RegimeCast covers BTC, ETH and SOL perpetual futures only."


def missing_fields(t: ParsedTrade) -> list[str]:
    miss = [f for f in ("asset", "side", "leverage") if getattr(t, f) is None]
    if t.qty_coins is None and t.notional_usdt is None and t.margin_usdt is None:
        miss.append("size")
    if t.margin_mode == "cross" and t.wallet_usdt is None:
        miss.append("wallet_usdt")
    return miss


def follow_up_question(missing: list[str]) -> str:
    parts = [MISSING_PHRASES[m] for m in missing]
    joined = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Almost there. Please tell me {joined}."


def warnings_for(t: ParsedTrade) -> list[str]:
    w = []
    if t.entry is None:
        w.append("No entry price given: the live price is used.")
    if t.leverage is not None and t.leverage > 150:
        w.append(f"{t.leverage:g}x is more than Binance allows (150x on BTC and ETH, 100x on SOL).")
    if t.stop is not None and t.entry is not None and t.side:
        if (t.side == "long" and t.stop >= t.entry) or (t.side == "short" and t.stop <= t.entry):
            w.append("The stop is on the wrong side of the entry (a long's stop goes below it, a short's above).")
    sizes = [x for x in (t.qty_coins, t.notional_usdt, t.margin_usdt) if x is not None]
    if len(sizes) > 1:
        w.append("More than one size was given; check the size on the next step.")
    return w


class ParseIn(BaseModel):
    text: str = Field(min_length=1, max_length=1000, description="The trade as typed, e.g. 'long 0.2 btc at 58k 10x'")
    history: list[str] = Field([], max_length=4, description="Earlier messages for the same trade (answers to "
                                                             "a follow-up question), oldest first")

    @field_validator("history")
    @classmethod
    def _short(cls, v):
        if any(len(h) > 1000 for h in v):
            raise ValueError("each earlier message must be at most 1000 characters")
        return v


class ParseOut(BaseModel):
    status: Literal["ok", "needs_info", "refused", "unsupported"] = Field(
        description="ok: ready to confirm; needs_info: ask `message`; refused: not a trade; unsupported: other coin")
    trade: ParsedTrade
    missing: list[str] = Field([], description="Required fields still missing: asset, side, leverage, size, wallet_usdt")
    message: str | None = Field(None, description="Follow-up question or polite refusal, written by code")
    warnings: list[str] = []
    parser: str = Field(description="Which parser answered, e.g. 'claude-haiku-5-5, prompt v2' or 'rules'")


def respond(t: ParsedTrade, parser: str, extra_warnings: list[str] | None = None) -> ParseOut:
    """Turn parsed fields into the API answer: refuse, ask a follow-up question, or hand over for confirmation."""
    if t.intent == "advice":
        return ParseOut(status="refused", trade=ParsedTrade(intent="advice"), message=REFUSE_ADVICE, parser=parser)
    if t.intent == "off_topic":
        return ParseOut(status="refused", trade=ParsedTrade(intent="off_topic"), message=REFUSE_OFF_TOPIC, parser=parser)
    if t.asset == "OTHER":
        return ParseOut(status="unsupported", trade=t, message=UNSUPPORTED, parser=parser)
    warn = (extra_warnings or []) + warnings_for(t)
    miss = missing_fields(t)
    if miss:
        return ParseOut(status="needs_info", trade=t, missing=miss, message=follow_up_question(miss),
                        warnings=warn, parser=parser)
    return ParseOut(status="ok", trade=t, warnings=warn, parser=parser)


def combined_text(p: ParseIn) -> str:
    """Earlier answers plus the newest message, so a reply like '10x' is read together with the trade."""
    return "\n".join([*p.history, p.text])
