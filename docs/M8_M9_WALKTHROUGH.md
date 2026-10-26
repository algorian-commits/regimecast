# M8 Trade Parser and M9 Full Risk Check: walkthrough

One page, plain English, for presenting. Numbers come from `data/reports/m8_*` (rebuilt by
`python scripts/m8_parser_eval.py`).

## What it does

You type a trade the way you would say it:

> short eth at 2600 stop 2700

RegimeCast answers *"Almost there. Please tell me the leverage (for example 10x) and the size (in coins, as a dollar
position, or the margin you put in)."* You reply *"10x with 0.5 eth"*, and it shows you the trade as form fields to
check. Press **Check risk** and you get the full, explained risk check. Three steps:

| Step | What you see |
|---|---|
| 1 Type | A text box with examples. Questions for anything missing; a polite refusal for advice questions ("should I long BTC?") or anything that is not a trade. |
| 2 Confirm | The usual Risk Check form, already filled in. Fields taken from your words are marked **from your text**. If you gave the size in coins or as a dollar position, it shows how it worked out the margin; if you gave no entry price, it uses the live price and says so. You can change anything. |
| 3 Results | Chance of liquidation, liquidation price, highest safe leverage, fees, the stop-loss check; the market regime and the stress gauge; a chart of what drives the volatility forecast (exact SHAP values); the plain-English explanation. Saved automatically. |

**Saved checks** sit beside the steps (below them on a phone), with filters by coin and by date. Click one to see
its result again. They are kept in this browser for now; when accounts arrive (Phase 8, `/me/checks`) they move
to your account without changing the page (`web/src/lib/history.ts`, one interface with two stores).

## How the parser works (M8)

```
text ─► Claude Haiku 5.5 ─► fixed JSON schema ─► Pydantic check ─► plausibility check ─► follow-up / refusal / confirm
              ▲                                         │ fails               │ fails
              └──────── "that was invalid because …" ◄──┴─────────────────────┘   (at most 3 calls)
```

* **Model:** Claude Haiku 5.5 (`claude-haiku-5-5`), Anthropic's small, fast model: $0.10 per million input tokens
  and $0.50 per million output tokens, so one parse costs about $0.0003 (3 rupees buys about 30). Effort is set to
  low; reading one sentence needs no deep thinking.
* **Fixed schema.** The model must answer with exactly these fields: intent (trade, advice or off-topic), asset,
  side, leverage, entry, stop, size in coins, size as a dollar position, size as margin, margin mode, wallet
  balance. Anything not stated is null: it is told never to guess. Shorthand like "58k", "x10", "half a bitcoin",
  "stop 3% below" is converted to plain numbers.
* **Checked twice.** Pydantic checks the types and limits (for example leverage above 0). Then a plausibility check
  catches misread shorthand: a BTC entry of 58 means "58k" was read wrong. In both cases the model is told what
  was wrong and tries again, at most three calls in total, and never past 7 seconds.
* **Guardrails by design.** The model has **no free-text field**, so it cannot write advice even if asked to. Every
  sentence you see (the follow-up questions, the refusals, the warnings) is fixed text written in our code. The
  user's message is wrapped as data with an instruction never to follow instructions inside it; attempts like
  "ignore your instructions" are classed as off-topic and refused.
* **Always works.** If no API key is set, the paid-call cap is reached (60 a minute, 2,000 a day by default), or
  the API fails, a simple rule reader (regular expressions, no AI) reads the text instead and the page says so.

## The test set (200 messages)

`docs/m8/cases_200.csv`: 176 trades (110 plain, 16 missing a field, 13 tricky such as numbers in words or a
take-profit next to a stop, 12 percentage stops, 10 in Roman Urdu like *"bhai btc pe 10x long lagaya hai 81k pe"*,
5 trades phrased as questions, 10 other coins), 11 advice questions and 13 off-topic messages including prompt
injections.

**Labels.** As for the M3 sentiment labels: the messages and labels were drafted by one AI annotator (Claude)
following written rules (`docs/m8/LABEL_RULES.md`); a second AI annotator (Claude), who saw only the rules and the
messages, labelled all 200 again blind. They agreed on **every field of all 200 messages** (intent kappa 1.00), so
no disagreements needed settling. These are **AI labels, not human labels**. Full agreement says the rules are clear
for these messages; it also means the set is probably cleaner than what real users type, and that the annotators
and the parser come from the same model family. Real user messages would make a harder second test.

**Order of work, for honesty:** the rule reader and both prompts were written and frozen before the test set was
drafted, and nothing was tuned on the test set afterwards.

## Results

Accuracy per field: `intent` over all 200 messages, the other fields over the 176 trades. "All fields" = every
field of a message right at once. "App outcome" = the app does the right thing (refuse, ask for exactly the missing
fields, or go to the confirm step). 95% intervals in `data/reports/m8_parser_accuracy.csv`.

| Field | Rule reader (no AI) | Claude, prompt v1 | Claude, prompt v2 (used in the app) |
|---|---|---|---|
| intent | 98.5% | pending | pending |
| asset | 100% | | |
| side | 98.9% | | |
| leverage | 99.4% | | |
| entry | **83.0%** | | |
| stop | 98.3% | | |
| size in coins | 98.9% | | |
| size as dollar position | 96.0% | | |
| size as margin | **91.5%** | | |
| margin mode | 100% | | |
| wallet balance | 100% | | |
| **all fields right** | **76.0%** | | |
| app outcome | 90.5% | | |

Target from the proposal: 95% per field. The rule reader misses it on entry and margin: it cannot tell a price from
a margin when they sit next to each other without "at" ("at 190 margin 150", "81k pe, 200$ margin").

**Claude columns:** waiting for the API key. Then `python scripts/m8_parser_eval.py` runs both prompts once
(about $0.15) and fills this table; answers are saved so reruns are free.

The two prompts: **v1** is what a first attempt would be (one paragraph: the fields, null when not stated, "58k =
58000"). **v2** adds the written labelling rules field by field and six worked examples that are not in the test
set (`regimecast/parsing/prompts.py`).

## The API

`POST /parse-trade` with `{"text": "...", "history": [earlier messages]}` returns

```json
{"status": "needs_info", "missing": ["leverage", "size"],
 "message": "Almost there. Please tell me the leverage (for example 10x) and the size (...).",
 "trade": {"intent": "trade", "asset": "ETH", "side": "short", "entry": 2600, "stop": 2700, ...},
 "warnings": [], "parser": "claude-haiku-5-5, prompt v2"}
```

`status` is `ok`, `needs_info`, `refused` (advice or off-topic) or `unsupported` (a coin other than BTC, ETH, SOL).

## Honest limits

* AI labels on AI-written messages; real user text will be messier. 176 trades measure accuracy to about +/- 3
  points near 95%.
* The parser reads; it does not check that the trade makes sense. A stop on the wrong side or 200x leverage is
  passed through with a warning, and the risk check then refuses it with a clear message.
* Saved checks live in one browser until Phase 8 accounts exist; clearing the browser clears them.
* Take-profit and horizon are not read from the text (the horizon is chosen on the confirm step).

## Demo

```bat
:: once: store the key (hidden input), then open a new terminal
powershell -ExecutionPolicy Bypass -File scripts\set_anthropic_key.ps1
python scripts/m8_parser_eval.py          :: accuracy table (both prompts + rule reader)
python -m pytest tests/test_parser.py     :: 26 tests, the API is never called
```

Live: open https://web-production-210f7.up.railway.app/risk-check, type *"short eth at 2600 stop 2700"*, answer
*"10x with 0.5 eth"*, press **Check risk**, then open the saved check from the list. Try *"should I long bitcoin
right now?"* to see the refusal.
