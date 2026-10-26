"""Trade parser (M8): schema, retries, follow-up questions, guardrails, rule reader, /parse-trade. Offline:
the Anthropic API is never called (a fake client stands in)."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from regimecast.api import parser as api_parser
from regimecast.api.main import app
from regimecast.parsing import claude, prompts, rules
from regimecast.parsing.schema import (FIELDS, REFUSE_ADVICE, REFUSE_OFF_TOPIC, UNSUPPORTED, ParsedTrade, ParseIn,
                                       combined_text, json_schema, respond)

GOOD = {"intent": "trade", "asset": "BTC", "side": "long", "leverage": 10, "entry": 58000, "stop": 56000,
        "qty_coins": 0.2, "notional_usdt": None, "margin_usdt": None, "margin_mode": None, "wallet_usdt": None}


def reply(payload, stop_reason="end_turn"):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(stop_reason=stop_reason, usage=SimpleNamespace(input_tokens=900, output_tokens=60),
                           content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)])


class FakeClient:
    """Answers with the given replies in order and records every request."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []
        self.messages = self

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


# ------------------------------------------------------------------ Claude call, retries
def test_valid_answer_first_time():
    fake = FakeClient(reply(GOOD))
    r = claude.parse("long 0.2 btc at 58k 10x stop 56k", client=fake)
    assert r.attempts == 1 and r.trade.entry == 58000 and r.trade.qty_coins == 0.2
    assert (r.input_tokens, r.output_tokens) == (900, 60)
    call = fake.calls[0]
    assert call["model"] == "claude-haiku-5-5" and call["system"] == prompts.V2
    assert call["output_config"]["format"] == {"type": "json_schema", "schema": json_schema()}
    assert not {"temperature", "top_p", "top_k"} & set(call)          # rejected by current models
    assert "<user_text>\nlong 0.2 btc at 58k 10x stop 56k\n</user_text>" in call["messages"][0]["content"]


def test_retry_on_invalid_json_then_success():
    fake = FakeClient(reply("{not json"), reply(GOOD))
    r = claude.parse("long 0.2 btc at 58k 10x", client=fake)
    assert r.attempts == 2 and r.trade.asset == "BTC" and "not valid JSON" in r.errors[0]
    second = fake.calls[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant", "user"]   # append-only retry
    assert "invalid" in second[2]["content"]


def test_retry_on_pydantic_error_and_misread_shorthand():
    bad_number = {**GOOD, "leverage": -5}
    misread = {**GOOD, "entry": 58}                                     # "58k" read as 58
    fake = FakeClient(reply(bad_number), reply(misread), reply(GOOD))
    r = claude.parse("long 0.2 btc at 58k 10x", client=fake)
    assert r.attempts == 3 and r.trade.entry == 58000
    assert "leverage" in r.errors[0] and "not a plausible BTC price" in r.errors[1]
    assert "58k' = 58000" in fake.calls[2]["messages"][-1]["content"]


def test_gives_up_after_three_invalid_answers():
    fake = FakeClient(reply("x"), reply("y"), reply("z"))
    with pytest.raises(claude.ParseFailed, match="3 attempts"):
        claude.parse("long btc", client=fake)
    assert len(fake.calls) == claude.MAX_ATTEMPTS


def test_cut_off_answer_is_retried():
    fake = FakeClient(reply('{"intent": "tr', stop_reason="max_tokens"), reply(GOOD))
    assert claude.parse("long btc", client=fake).attempts == 2


def test_refusal_and_api_errors_raise():
    import anthropic
    import httpx2
    with pytest.raises(claude.ParseFailed, match="declined"):
        claude.parse("x", client=FakeClient(reply("", stop_reason="refusal")))
    err = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    with pytest.raises(claude.ParseFailed, match="APIConnectionError"):
        claude.parse("x", client=FakeClient(err))


def test_prompt_v1_selectable():
    fake = FakeClient(reply(GOOD))
    claude.parse("long btc", version="v1", client=fake)
    assert fake.calls[0]["system"] == prompts.V1


def test_schema_matches_model():
    s = json_schema()
    assert list(s["properties"]) == FIELDS == list(ParsedTrade.model_fields)
    assert s["required"] == FIELDS and s["additionalProperties"] is False


# ------------------------------------------------------------------ follow-up, guardrails, warnings
def test_refusals_never_echo_fields():
    advice = respond(ParsedTrade(intent="advice", asset="BTC", leverage=10), "test")
    assert advice.status == "refused" and advice.message == REFUSE_ADVICE and advice.trade.asset is None
    off = respond(ParsedTrade(intent="off_topic"), "test")
    assert off.status == "refused" and off.message == REFUSE_OFF_TOPIC
    other = respond(ParsedTrade(intent="trade", asset="OTHER", side="long"), "test")
    assert other.status == "unsupported" and other.message == UNSUPPORTED


def test_follow_up_question_for_missing_fields():
    r = respond(ParsedTrade(intent="trade", asset="ETH", side="short"), "test")
    assert r.status == "needs_info" and r.missing == ["leverage", "size"]
    assert r.message == ("Almost there. Please tell me the leverage (for example 10x) and the size (in coins, as a "
                         "dollar position, or the margin you put in).")
    cross = respond(ParsedTrade(intent="trade", asset="SOL", side="long", leverage=5, margin_usdt=100,
                                margin_mode="cross"), "test")
    assert cross.missing == ["wallet_usdt"] and "wallet balance" in cross.message


def test_ok_with_warnings():
    t = ParsedTrade(intent="trade", asset="BTC", side="long", leverage=200, entry=80000, stop=82000,
                    qty_coins=0.1, margin_usdt=50)
    r = respond(t, "test")
    assert r.status == "ok" and r.missing == [] and r.message is None
    text = " ".join(r.warnings)
    assert "200x" in text and "wrong side" in text and "More than one size" in text
    assert "live price" in " ".join(respond(t.model_copy(update={"entry": None}), "test").warnings)


def test_history_is_read_with_the_answer():
    assert combined_text(ParseIn(text="10x", history=["long 0.1 btc at 81k"])) == "long 0.1 btc at 81k\n10x"


# ------------------------------------------------------------------ rule reader
@pytest.mark.parametrize("text, want", [
    ("long 0.2 btc at 58k 10x stop 56k",
     dict(asset="BTC", side="long", qty_coins=0.2, entry=58000, leverage=10, stop=56000)),
    ("Shorting SOL with $300 margin at 25x, cross, my wallet is 1.2k",
     dict(asset="SOL", side="short", margin_usdt=300, leverage=25, margin_mode="cross", wallet_usdt=1200)),
    ("$2k position on eth long 5x from 2400, stop 3% below",
     dict(asset="ETH", side="long", notional_usdt=2000, leverage=5, entry=2400, stop=2328)),
    ("ETH short 20x @ 2,650 using 250 usdt, sl 2700",
     dict(asset="ETH", side="short", leverage=20, entry=2650, margin_usdt=250, stop=2700)),
    ("buy 1.5 eth at market 3x", dict(asset="ETH", side="long", qty_coins=1.5, leverage=3)),
    ("10x long on DOGE with 100 bucks", dict(asset="OTHER", side="long", leverage=10, margin_usdt=100)),
])
def test_rules_reader(text, want):
    got = rules.parse(text).model_dump(exclude_none=True)
    assert got == {"intent": "trade", **want}


def test_rules_reader_intents():
    assert rules.parse("what leverage should I use on bitcoin?").intent == "advice"
    assert rules.parse("tell me a joke").intent == "off_topic"


# ------------------------------------------------------------------ POST /parse-trade
@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(api_parser, "limiter", api_parser.Limiter(60, 2000))
    return TestClient(app)


def test_endpoint_without_key_uses_rules(api, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = api.post("/parse-trade", json={"text": "long 0.2 btc at 58k 10x"})
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["status"] == "ok" and b["parser"] == "rules" and b["warnings"][0] == api_parser.RULES_NO_KEY


def test_endpoint_with_claude(api, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    fake = FakeClient(reply({**GOOD, "leverage": None}))
    monkeypatch.setattr(api_parser, "_client", lambda: fake)
    b = api.post("/parse-trade", json={"text": "long 0.2 btc at 58k stop 56k"}).json()
    assert b["parser"] == "claude-haiku-5-5, prompt v2"
    assert b["status"] == "needs_info" and b["missing"] == ["leverage"]


def test_endpoint_falls_back_when_claude_fails_or_cap_reached(api, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    monkeypatch.setattr(api_parser, "_client", lambda: FakeClient(reply("x"), reply("x"), reply("x")))
    b = api.post("/parse-trade", json={"text": "short 1 eth at 2600 5x"}).json()
    assert b["parser"] == "rules" and b["warnings"][0] == api_parser.RULES_FALLBACK and b["trade"]["entry"] == 2600
    monkeypatch.setattr(api_parser, "limiter", api_parser.Limiter(0, 0))
    monkeypatch.setattr(api_parser, "_client", lambda: pytest.fail("no API call past the cap"))
    assert api.post("/parse-trade", json={"text": "short 1 eth"}).json()["parser"] == "rules"


def test_endpoint_rejects_bad_input(api):
    assert api.post("/parse-trade", json={"text": ""}).status_code == 422
    assert api.post("/parse-trade", json={"text": "x" * 1001}).status_code == 422
    assert api.post("/parse-trade", json={"text": "x", "history": ["a"] * 5}).status_code == 422


def test_limiter_minute_and_day():
    now = [0.0]
    lim = api_parser.Limiter(per_minute=2, per_day=3, clock=lambda: now[0])
    assert lim.allow() and lim.allow() and not lim.allow()
    now[0] = 61
    assert lim.allow() and not lim.allow()      # day cap of 3 reached
    now[0] = 86400 + 1
    assert lim.allow()                          # new UTC day
