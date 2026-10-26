"""Read a typed trade with Claude through the Anthropic API.

Model: Claude Haiku 5.5 (`claude-haiku-5-5`, $0.10 / $0.50 per million input / output tokens): the small, fast
model is enough for reading a sentence, at about $0.0003 per parse. Override with PARSER_MODEL.

The answer is forced into a fixed JSON schema (structured outputs), then checked with Pydantic and for plausible
prices. If the check fails, the model is told what was wrong and tries again (at most MAX_ATTEMPTS calls).
Tests never call the API: they pass a fake client.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from pydantic import ValidationError

from regimecast.parsing import prompts
from regimecast.parsing.schema import ParsedTrade, json_schema, plausibility_errors

MODEL = os.environ.get("PARSER_MODEL", "claude-haiku-5-5")
MAX_ATTEMPTS = 3


class ParseFailed(Exception):
    """No valid answer after MAX_ATTEMPTS, or the API could not be reached."""


@dataclass
class Result:
    trade: ParsedTrade
    attempts: int
    input_tokens: int = 0
    output_tokens: int = 0
    errors: list[str] = field(default_factory=list)  # what each failed attempt got wrong


def has_key() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def make_client(timeout: float = 10.0):
    import anthropic
    return anthropic.Anthropic(timeout=timeout, max_retries=1)


def _check(text: str | None) -> tuple[ParsedTrade | None, str | None]:
    """Parse and validate one answer; return (trade, None) or (None, what was wrong)."""
    if text is None:
        return None, "no JSON answer was returned"
    try:
        t = ParsedTrade.model_validate(json.loads(text))
    except json.JSONDecodeError as e:
        return None, f"the answer was not valid JSON ({e.msg})"
    except ValidationError as e:
        return None, "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors())
    errs = plausibility_errors(t)
    return (None, "; ".join(errs)) if errs else (t, None)


def parse(text: str, version: str = prompts.DEFAULT_VERSION, client=None, model: str = MODEL) -> Result:
    """Read `text` into a ParsedTrade. Raises ParseFailed after MAX_ATTEMPTS invalid answers or an API error."""
    import anthropic

    client = client or make_client()
    messages: list = [{"role": "user", "content": prompts.user_message(text)}]
    res = Result(trade=ParsedTrade(intent="off_topic"), attempts=0)
    for _ in range(MAX_ATTEMPTS):
        res.attempts += 1
        try:
            r = client.messages.create(
                model=model, max_tokens=2048, system=prompts.PROMPTS[version], messages=messages,
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": json_schema()}},
            )
        except anthropic.APIError as e:  # network, rate limit, overload, bad key: the caller falls back
            raise ParseFailed(f"Anthropic API error: {type(e).__name__}") from e
        res.input_tokens += r.usage.input_tokens
        res.output_tokens += r.usage.output_tokens
        if r.stop_reason == "refusal":
            raise ParseFailed("the model declined this text")
        answer = next((b.text for b in r.content if b.type == "text"), None)
        trade, err = _check(answer) if r.stop_reason != "max_tokens" else (None, "the answer was cut off")
        if trade:
            res.trade = trade
            return res
        res.errors.append(err)
        # Append-only retry: keep the model's answer and say what was wrong with it.
        messages += [{"role": "assistant", "content": r.content},
                     {"role": "user", "content": f"That answer was invalid: {err}. Return corrected JSON for the "
                                                 f"same text."}]
    raise ParseFailed(f"no valid answer after {MAX_ATTEMPTS} attempts: {res.errors[-1]}")
