"""Trade parser (M8): turn a typed trade ("long 0.2 btc at 58k, 10x, stop 56k") into checked fields.

schema.py   the fixed output schema (Pydantic), plausibility checks, follow-up questions and refusals
prompts.py  the two prompt versions compared in the evaluation
claude.py   the Anthropic API call with retries on invalid output
rules.py    a no-AI rule reader: the baseline in the evaluation and the fallback when no API key is set
"""
