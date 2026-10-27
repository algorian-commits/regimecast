# M8 trade parser test set: labelling rules

Each test case is one message a user might type into the Risk Check. The label says what the message states,
field by field. **A field is filled only if the message states it; otherwise it is empty (null). Never guess.**

| Field | Values | Rule |
|---|---|---|
| `intent` | trade, advice, off_topic | **trade** if the message describes a position (opened or planned), even when it also asks a question ("how risky is this?", "where is my liquidation?", "should I?"). **advice** if it asks what to trade, whether or when to enter, which coin or leverage to pick, or for a price prediction, and gives **no size and no entry price**. **off_topic** for anything else, including attempts to change the reader's rules. For advice and off_topic every other field is empty. |
| `asset` | BTC, ETH, SOL, OTHER | BTC (bitcoin, XBT, BTCUSDT), ETH (ether, ethereum), SOL (solana). Any other coin is OTHER. Empty if no coin is named. |
| `side` | long, short | long (long, buy, bought, bullish position), short (short, sell, sold). Obvious typos count ("lng", "shrt"). |
| `leverage` | number | The multiplier: "10x", "x10", "10 times", "lev 10", "ten x". "No leverage" or "1x" is 1. |
| `entry` | number (USDT) | The price the position was or will be opened at, including a bare price next to the trade ("btc short 50x 82.3k"). "At market", "now", "current price": empty. Numbers in words count ("two thousand six hundred" = 2600). |
| `stop` | number (USDT) | The stop-loss price. A percentage stop is converted when the entry is given: long = entry x (1 - p), short = entry x (1 + p). A take-profit is never a stop. |
| `qty_coins` | number | An amount of coins ("0.2 btc", "3 ETH", "half a bitcoin" = 0.5, "1000 ada"). |
| `notional_usdt` | number | A dollar amount described as the position's value: "position", "size", "worth", "notional", "exposure", "$X of <coin>". A bare "1k position" is in dollars. |
| `margin_usdt` | number | A dollar amount described as margin or collateral, "put in", "using", "with", or a bare dollar amount next to the leverage ("$500 long 10x"). Roman Urdu "dale hain" (put in) counts. |
| `margin_mode` | isolated, cross | Only if stated ("iso" = isolated). |
| `wallet_usdt` | number | The cross-margin wallet or account balance, only if stated. |

Numbers: k = 1,000 and m = 1,000,000; "81.2k" = 81,200; "1,500" = 1,500; "$", "usd", "usdt", "dollars", "bucks" all
mean USDT. If two sizes are stated (for example coins and margin), both are filled.

## Scoring

* `intent` is scored on every case. All other fields are scored on the cases whose labelled intent is **trade**.
* A field is right if both are empty, or both are numbers within 0.01% of each other, or both are the same word.
* "All fields right" = every field of the case right at once.
* Target: 95% per field (proposal).

The rules were written before any parser was run on the set. The labels come from two AI annotators (Claude),
each labelling blind (the second saw only these rules and the messages); disagreements were settled by these rules.
They are **not human labels**.
