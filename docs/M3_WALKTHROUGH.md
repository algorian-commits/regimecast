# M3 News Sentiment: walkthrough

Presenter: Aila Jabeen. One page, plain English. Numbers come from `data/reports/m3_*` (rebuilt by `python scripts/m3_report.py`).

## What M3 does

Every hour, M3 answers one question per coin (BTC, ETH, SOL): **what is the news saying right now?**
It collects crypto headlines, works out which coin each one is about, scores how positive or negative it is, and
rolls them up into an hourly "news mood":

| Field | Meaning |
|---|---|
| `count` | crypto headlines about this coin or the whole market in that hour |
| `mean_score` | average tone, from -1 (very negative) to +1 (very positive) |
| `share_negative` | share of those headlines that are negative |
| `policy_shock_count` | headlines about tariffs, sanctions or regulation (including big macro ones like a US-China tariff) |

Each hour is labelled by its **end**: a headline at 10:20 counts for the hour ending 11:00. So the 11:00 row only
uses news that was already out by 11:00: no peeking at the future. The app reads it from `GET /news-mood`.

## Data sources

* **GDELT** (history, May 2025 to now). GDELT reads news sites worldwide and publishes a file every 15 minutes.
  We scanned all of them (about 50,000 files, 250 GB) on GitHub Actions in 10 minutes and kept the headlines that
  mention crypto, plus big tariff and sanctions headlines. Result: **325,000 headlines** after removing
  copies (147,000 crypto, 179,000 macro policy). GDELT itself was down from 15 June to 1 July 2025; those hours
  are left empty, not counted as "no news".
* **RSS feeds** (live): CoinDesk, Cointelegraph, The Block, Decrypt. The live collector also reads the newest
  GDELT files, so the live numbers match the history.
* Not used: CryptoPanic (free API closed in April 2026) and Reddit.

Cleaning: the same story published by many outlets counts once (same link, or nearly the same title within 3 days);
old articles that GDELT only found a day or more later are dropped.

## Models

| Model | Trained on | Accuracy | Macro F1 |
|---|---|---|---|
| FinBERT (`ProsusAI/finbert`) | financial news sentences | **62%** | 0.60 |
| CryptoBERT (`ElKulako/cryptobert`) | crypto social-media posts | 46% | 0.35 |

Measured on **100 labelled headlines** (random, 6 per month). Labels by two independent AI annotators (Claude), each labelling blind: 95% agreement, Cohen's kappa 0.92, the 5 disagreements settled by the written rule; **not human-labelled**. The rule: would a crypto investor read the headline as good news (positive), bad news (negative: price falls, hacks, scams, crime, crackdowns) or neither (neutral: guides, ads, presale promos, analysis with no direction)? Split: 34 positive, 23 negative, 43 neutral.
FinBERT wins clearly, so the mood uses FinBERT. CryptoBERT was trained on tweets and forum posts, so it calls most news
headlines "neutral" (it found only 4 of 34 of the positive ones). FinBERT's weak spot is the same in a milder form: it
misses positive news written in a dry way ("BNY launches money market fund for stablecoin issuers").

## Does the mood move before the price? (May to Sep 2025)

We found the big BTC moves (an hour moving at least 4 times its normal size; 12 down, 11 up) and looked at
the news in the hours around them. We kept October 2025 onward aside, because the later forecasting models (M7) are
tested on it.

* **How much news there is matters; its tone doesn't lead.** In the hours around big moves, the number of
  headlines is above normal: before the move, during it and after it (about +1 standard deviation around down moves).
  Policy-shock headlines (tariffs, sanctions, regulation) are also above normal from 2 hours before down moves.
* Adding the headline count to a simple model of next-hour move size (which already knows the recent moves) improves
  it: R² goes from 4.4% to 6.2% (t = 6.4). The policy-shock count helps too (to 5.2%). The tone (mean score, share
  negative) adds nothing (p = 0.19 and 0.30).
* The tone follows the price: it turns negative 2-3 hours *after* down moves and positive with up moves.
* Caution: 23 big moves is a small sample, and with this many cells a few "significant" ones are expected by chance.
  News volume also rises in busy, volatile periods generally, so part of the signal is "volatility clusters", not news
  causing moves. M7 will test whether it still helps a real volatility model.

## Case study: the 10 October 2025 tariff crash

Times in UTC (Pakistan is UTC+5). The times of Trump's two posts are as reported in the press; everything else is from our data (`data/reports/m3_crash_case_study.csv`).

| Time | What happened | BTC |
|---|---|---|
| ~15:00 | Trump posts that China's rare-earth move is "hostile" and threatens a "massive increase" in tariffs. BTC starts falling | 121,600 → 120,400 |
| 15:45 | **First GDELT headline** of the threat (moneycontrol.com, Newsweek) | 119,500 |
| 16:45 | First *crypto* headline about it: "Bitcoin, Ethereum Dive Alongside Stocks as Trump Threatens 'Massive' China Tariffs" | 118,800 |
| 17:00-19:00 | China-tariff headlines jump from ~1 to ~25-30 an hour; crypto mood about neutral (mean score +0.09 to -0.06) | 118,000 → 117,200 |
| ~20:45-21:15 | Trump announces an extra **100% tariff** on China; the crash, record liquidations | 117,000 → 109,200 |
| 21:30 | **First GDELT headline** of the 100% tariff (Sky News). The crypto mood for the crash hours is still not negative (+0.06, +0.21) | 109,200 |
| 23:00 onward | Crypto mood turns negative (share negative 31-86% through the night) | 111,000-113,000 |

**What it shows:** the news mood reacts *with* the shock, not before it. GDELT gets a headline 40 to 50 minutes
after the market has started to move, and the hourly table only counts it at the next hour end. The headline that
mattered (Trump's post) was not a crypto headline at all, which is why M3 also counts macro tariff headlines.
Crypto-specific headlines about the tariffs only appeared later still (16:45, almost two hours after the first drop).

## Honest limits

* The labels come from AI annotators, not people. 100 labels is enough to compare two models, not to measure accuracy precisely (roughly +/- 10 points).
* Headlines only (no article text). Promotional "presale" spam is part of the data; it mostly scores neutral.
* GDELT's 15-minute batches plus crawling add delay (40-50 minutes on 10 Oct), so news can't lead fast moves in this data.

## What's next

* M7 tests the mood as a feature "with vs without" on the final test period (Oct 2025 onward).
* Faster sources for policy shocks (direct X / Truth Social feeds) are on the parking list (X's API costs ~$200/month).
* Run the collector as the Railway `worker` service; today it runs every 15 minutes from the PC (`scripts/news_worker.ps1`).

## Demo

```bat
python scripts/m3_report.py
python -m http.server 8010 -d data/reports      :: open http://localhost:8010/phase3.html
uvicorn regimecast.api.main:app                 :: open http://127.0.0.1:8000/news-mood?symbol=BTCUSDT&hours=24
```
