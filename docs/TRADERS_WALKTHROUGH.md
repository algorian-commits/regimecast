# Popular trader tracker: a plain-words walkthrough (Phase 13D)

**Status:** done. The copy test ran once and passed (8 Nov 2026); 7 of 1,560 accounts with perp trades pass the skill test. The numbers and their limits are in the Results section at the end.

## What it is for

On Hyperliquid, an on-chain exchange, every account's positions and trades are public. So you can watch what the biggest traders do. The page answers three questions:

1. Which of them show real skill, once luck is accounted for?
2. What do those traders hold right now, and where would big tracked positions be liquidated?
3. Would copying them have made money?

## The trap this is built to avoid

Lists of "top traders" are mostly survivors of luck. Give a thousand people a coin to flip for a year and a few will look like geniuses. Two things guard against that here:

- **The skill test is corrected for how many accounts are tested.** Being lucky once is easy when thousands get a try.
- **The copy test picks traders on one period and judges them on a later one.** A record that was only luck tends not to carry on.

We also fixed the pass mark on 31 Oct 2026, before downloading any fills, so it could not be bent to fit the result. It is written in `docs/PLAN.md` under 13D.

## Who is tracked

**The pool:** every account above $1M on Hyperliquid's public leaderboard, 2,884 accounts on 31 Oct. Whale Watch's 160 accounts are among them.

Many of the largest have no perp trades at all. They seem to hold spot coins or staked tokens. They show up as "no trades", and aren't scored.

## How the history is limited

Hyperliquid only serves about an account's most recent 10,000 fills (a fill is one executed piece of an order). So:

- A very busy account's history may reach back only a day or two.
- A quiet account's history can reach back years.

For each account we record how far back it goes. A `*` next to the day count on the page means older fills are no longer available.

## The scorecard: every number explained

| Number | What it means | How to read it |
|---|---|---|
| **Days** | Days from the account's first available fill to the download, counting quiet days as zero. | The sample size. Under 30 days and the skill test isn't run at all. |
| **Trades** | Finished round trips in one coin: from flat to flat. A flip from long to short ends one trade and starts another. | The other sample size. Under 10 and the skill test isn't run. |
| **Win rate** | Share of finished trades with a profit after fees. | High doesn't mean good: many small wins and one huge loss is a high win rate and a losing trader. |
| **Profit factor** | Total won divided by total lost. | Above 1 made money. Blank if nothing was lost, or nothing was won or lost. |
| **Net PnL** | Realised profit minus fees over the history, in dollars. | Doesn't count funding or open positions (see limits). |
| **Return** | Net PnL as a share of today's account value. | Rough, because the account may have been a very different size earlier. |
| **Sharpe** | Average daily PnL divided by how much it swings, scaled to a year. | Higher is steadier. About 1 is good, and above 3 over a short history usually means too little data. Shown only with 30+ days. |
| **Max drop** | The largest fall in cumulative PnL from an earlier peak, in dollars. | How bad the worst stretch was. |
| **Leverage** | An estimate: average peak trade size divided by today's account value. | Fills don't record leverage or past account values, so treat it as a rough size. |
| **Typical hold** | The median time a finished trade stayed open. | Half of trades were shorter, half longer. |
| **Main coins** | The coins with the most traded value. | |

## The skill test

**The question:** is this account's average daily PnL really above zero, or could it be luck?

**How it works:**

1. Take the account's daily PnL and shift it so its average is exactly zero. That is a world where the trader has no edge but the same ups and downs.
2. Rebuild a fake history from that series by drawing blocks of consecutive days at random, 2,000 times. Blocks keep good and bad streaks together, because trading days aren't independent.
3. The **p-value** is the share of those fake histories whose average is at least as high as the real one. A small p-value means luck rarely produces a record this good.

**Correcting for many tests (Benjamini-Hochberg):** with thousands of accounts, some will have tiny p-values by chance. This correction sets a stricter bar so that, among accounts called skilled, about 5% at most are expected to be false alarms. Every account with perp fills counts in the correction, including those with too little history to test.

**Skill score:** the average daily PnL divided by its uncertainty. It doesn't depend on account size, and it's what ranks accounts.

**Labels:**

- **Skilled:** passed the corrected test, with average daily PnL above zero.
- **Losing:** net realised loss over the history.
- **Unproven:** everything else. It can't be told apart from luck, which doesn't mean it has no skill.

## What the scorecards can't tell you

- **Funding and open positions aren't counted.** Fills don't include funding payments or unrealised gains and losses.
- **Survivors only.** The pool is today's leaderboard, so accounts that blew up and left are missing. That flatters everyone in it.
- **Short histories.** Busy accounts can't be tested, however good they look.
- **An address isn't always a person.** It may be a fund, a market maker or a bot, whose edge can't be copied from the outside.

## What the page shows

- **The copy test's verdict**, at the top.
- **Net positioning:** per coin, the dollars skilled traders hold long minus short, with how many are long, short and flat.
- **Skilled traders' open positions:** coin, side, size, leverage, entry and liquidation price.
- **Their recent trades** from the last 7 days.
- **The liquidation map.**
- **The scorecards**, filtered by label.
- **Alerts.**

Positions are BTC, ETH and SOL only. They come live from Hyperliquid's public data, and each shows when it was last read.

## The liquidation map

For each coin it adds up tracked open positions by the price at which Hyperliquid would liquidate them. Bands are 1% wide and counted from the current price.

- **Longs** are liquidated if the price falls to them, so they normally sit below the price line.
- **Shorts** are liquidated if the price rises to them, so they normally sit above it.

A liquidation is a forced trade, so a big cluster near the price is a place where forced selling or buying could start and speed a move up. **It doesn't say the price will go there.**

**Limits:**

- It covers **tracked Hyperliquid accounts only, not the whole market**.
- It's a snapshot: a trader adding margin moves their liquidation price.
- Accounts are read one at a time, so a full sweep of about 1,560 accounts takes up to half an hour. The skilled ones are re-read every 2 minutes, and the page shows how old the oldest reading is.
- Positions more than 20% from the price, or with no liquidation price, aren't drawn. The page says how much that leaves out.

## The two alerts

Add a "Skilled trader move or liquidation cluster" alert on your account page, per coin. Alerts go to the in-app inbox now, and to Telegram once 13C is merged.

- **Skilled trader:** a skilled account opened, closed or flipped a position of at least $1M.
- **Liquidation cluster:** at least $10M of tracked longs, or of tracked shorts, would be liquidated within 1.5% of the price. It fires once, and again only after the amount has fallen below half of that.

Neither alert says what to do. Both thresholds can be changed by environment variable.

## The copy test

**The question:** after a delay, fees and funding, would copying the skilled traders have beaten copying random accounts, and beaten simply holding the coins?

**Step 1, the history check.** An account counts if its first and last perp fills are at least 182 days apart. With at least 60 such accounts, the test runs on past data. With fewer, it becomes an 8-week forward test.

**Step 2, the periods.** The common history of those accounts runs from the latest of their first fills to the earliest download. Period A is the first two thirds, and period B is the last third.

**Step 3, choosing on A only.** Accounts are ranked by skill score using only fills from period A. The top 10% are copied. Only accounts with a BTC, ETH or SOL fill in A can be chosen, because those are the coins copied. A test checks that changing anything in B can't change who is picked.

**Step 4, copying in B.**

- **Delay:** the copier acts 5 minutes after each of the trader's fills, at the end of that minute. A price jump inside the delay is missed, as it would be in real life.
- **Fees:** Hyperliquid's taker fee, 0.045%, on every change of position.
- **Funding:** Hyperliquid's real hourly rates.
- **Prices:** Binance 1-minute closes, because Hyperliquid only serves recent candles. The two track each other closely but aren't identical.
- **Sizing:** equal risk per trader. Each is scaled by how much their copied daily PnL swung in period A, so one huge account can't dominate.

**Step 5, the comparisons, on the same days.**

- **Random copying:** 200 random groups of the same size from the same accounts, copied the same way. The median Sharpe is the bar.
- **Buy-and-hold:** a third each in BTC, ETH and SOL, paying funding.

**Step 6, the pass mark.** All three must hold:

1. The copy portfolio's Sharpe beats the random median.
2. It beats buy-and-hold.
3. The 90% range of (copy Sharpe minus random median Sharpe), from resampling blocks of days, sits entirely above 0. This means the lead isn't just noise.

**What the page may say.** "Following them has worked" appears only if the test passed. In every other case it says "Past results; following them was not shown to pay." One function decides this, and a test covers it.

**It runs once.** Each run is logged with its commit, and a second run refuses to start unless it names the bug it fixes.

Our final evaluation found that direction can't be predicted from market signals, so failing here would not be a surprise. It would be a useful, honest result.

## Results

**History check.** All 2,884 pool accounts were downloaded, about 31.5 million fills.

- 1,560 accounts have perp fills; the other 1,324 hold spot or staked coins only.
- For 882, Hyperliquid no longer serves their older fills, so their history is cut short.
- 755 accounts have 6+ months of perp fills, far more than the 60 needed. So the copy test ran on past data, as pre-registered, not as a forward test.
- The median account's perp history spans 168 days, and the top quarter spans 456 days or more.

**Scorecards.** Out of 1,560 accounts with perp trades:

| Label | Accounts | What it means |
|---|---|---|
| Skilled | 7 | Average daily profit above zero, and still significant after correcting for testing 1,560 accounts |
| Unproven | 753 | Can't be told apart from luck |
| Losing | 800 | Net realised loss over the history |

Only 619 had enough history for the test (30+ days and 10+ trades). Their median Sharpe ratio is 0.09, close to zero, which is what a mostly-luck pool looks like.

**About the 7 skilled accounts:**

- They have 66 to 686 days of history and 10 to 147 finished trades each.
- Their realised Sharpe ratios run from 3.7 to 9.0.
- Most hold trades for a few hours to a few days.

**Read their records with care.** Win rates of 70-100% and profit factors in the tens or hundreds usually mean losing trades are still open. Open positions don't count until they are closed. One account has won every one of its 55 finished trades.

**A bug I found and fixed in my own test.** The first run of the scorecards found no skilled account at all. The reason was a flaw in the test settings, not the traders:

- 2,000 random draws can't show a p-value below 1/2,001 = 0.0005.
- The correction's strictest cut-off over 1,560 accounts is 0.05/1,560 = 0.00003.
- So 7 accounts sat at the smallest p-value the test could produce, and none could ever pass.

Accounts with a p-value under 0.01 are now redrawn 100,000 times. This is the same method, only finer, and it's logged in the plan's decision log as found after seeing the first result. The skill score, which ranks accounts and picks them for the copy test, is unchanged.

**Copy test: PASSED.** It ran once, on 8 Nov 2026, from commit `be49d43`.

- **Period A:** skill was measured from 1 May to 31 Aug 2026.
- **Who was copied:** of the 395 accounts that traded BTC, ETH or SOL in period A, the top 40 (the top 10%) by skill score.
- **Period B:** they were copied from 31 Aug to 1 Nov 2026 (62 days), with a 5-minute delay, the 0.045% taker fee and funding.

| | Sharpe ratio in period B |
|---|---|
| Copying the top 40 | **4.00** |
| Random groups of 40 (median of 200) | 0.06 |
| The middle 90% of random groups | -3.37 to 2.87 |
| Buy-and-hold BTC, ETH, SOL | -0.61 |

All three parts of the bar hold:

1. Copying beat the random median, and in fact beat all 200 random groups.
2. It beat buy-and-hold.
3. The 90% range of its lead over random copying runs from 1.57 to 7.13, so it lies above 0.

So the page may say "following them has worked", and it does.

**What this result does and doesn't show:**

- **It is one 62-day window.** The range of the lead is wide, and a different two months could look different.
- **Period B was a falling market** (buy-and-hold lost money). Part of the gain may come from accounts that were short going into a fall. The test can't tell skill at choosing direction apart from a bias that happened to suit these two months.
- **The pool is today's leaderboard,** so accounts that blew up are missing. The random groups come from the same pool, so that bias hits the comparison with random copying equally. It does flatter the comparison with buy-and-hold.
- **Prices are Binance's 1-minute closes,** covering 99.5% of minutes. A real copier on Hyperliquid would get slightly different fills, and large copies could move the price.
- **Different groups:** the copy test followed the top 40 by skill score in period A. The page's "skilled" label is the 7 that pass the strict correction over each account's whole history.

Paper results, not advice.
