# Track Record: walkthrough

Plain English. The page is at `/track-record`. The code is `regimecast/track/engine.py`, `regimecast/api/track.py` and
`web/src/components/track-record.tsx`. Tests: `tests/test_track_record.py`.

## What it is

Testing on past data can always be questioned: you might have tuned until it looked good. A Track Record answers that.
Every hour the app writes down what it forecasts, **before the outcome is known**, then scores it once the outcome is
in. The final evaluation is about a past year. This is about the days from logging's first row onwards, starting from
nothing.

## What happens each hour

Take the hour that starts at 14:00.

1. **Logging, between 14:00 and 14:30.** The model cache refreshes and makes a forecast from data up to 14:00. Once that
   forecast is there, the logger writes a row for each coin and horizon (1 h and 24 h):
   - the forecast the app shows, and the HAR-S forecast next to it, each with its model's name;
   - the benchmark: how wild the last window really was (13:00-14:00 for 1 h, the last 24 h for 24 h);
   - the stress chance, the regime, when the forecast was made and when the row was written.
2. **Scoring.** The 1 h window closes at 15:00, and the scorer adds its result a little after 15:07, in a separate row.
   The 24 h window closes at 14:00 tomorrow and is scored the same way.
3. **The page** shows forecast against outcome, error against the benchmark, the latest rows and the missing hours.

## Why you can trust it

| Rule | How it is enforced |
|---|---|
| Logged before the outcome | A row can only be written in the first 30 minutes of its own hour. The database also refuses any row whose time is not before its window's end. |
| No look-ahead | A forecast is logged for 14:00 only if its inputs end exactly at 14:00. An older forecast is never relabelled. The benchmark reads only candles closed by 14:00. |
| No backfilling | Nothing can write a row for an earlier hour. An hour with no row stays missing, and the page counts it. |
| No rewriting | The scorer adds new rows and never edits. Database triggers refuse any change or deletion, including from our own code. |
| No duplicates after a restart | One row per coin, horizon and hour. A restarted process finds the row there and keeps the first one. |
| Same outcome as the evaluation | Realised variance comes from `realized.build_targets`, QLIKE from `evaluate.qlike`, the stress label from `build.minute_features`. These are the functions the models were tested with. A test checks that a slice of candles gives exactly what the full history gives, including with trading halts in the window. |
| No mixing with backtests | Every number on the page comes from the two live tables. The final evaluation is only a link, labelled as backtest results. |

**The honest limit.** The times come from our own server, and someone with full control of the database could remove the
triggers. So this shows our discipline, and is not independent proof. The page says so. If it matters later, each hour's
rows could also be committed to Git, as the paper agent's log is (parking list in `docs/PLAN.md`).

## What every number means

- **Forecast:** how big the price move is likely to be over the window. The charts show it as a % move; the tables store
  it as variance (the % move squared), which is what the models forecast.
- **What happened (realised volatility):** how much the price really moved, added up from 1-minute prices for 1 h and
  5-minute prices for 24 h.
- **Benchmark:** the simplest forecast possible, "the next window will be as wild as the last one". A forecast that
  can't beat it adds nothing.
- **QLIKE (the error):** how far a forecast was from what happened, punishing "expected too little" harder than
  "expected too much", which suits risk. 0 is perfect, and lower is better. One wild hour can move the average a lot.
- **Difference:** our average error against the rival's on the same rows. -10% means our error was 10% lower. This is
  the same sign as in the final evaluation.
- **n:** how many rows a number rests on. It is shown next to every score.
- **Separate days:** 24 h forecasts are logged hourly, so their windows overlap. 240 scored rows are only about 10
  separate days of evidence.
- **Brier score:** for the stress chance, the average squared gap between the chance given and what happened (1 or 0).
  Lower is better. It sits next to the same score for always saying the usual rate.
- **Small sample:** each coin and horizon is labelled this until it has 720 scored rows (30 days), and the stress score
  until it has seen 30 stress events. Both limits were fixed before the first row. They only control the label: there
  is no pass mark here and the page gives no verdict.
- **Waiting / no data / missing:** waiting means not scored yet. No data means too few prices in the window to score it
  (no number is made up, after 6 hours it is marked this way). Missing means no forecast was logged that hour.

## How we know the scoring matches the evaluation

Checked once on 4 Nov 2026, on real Binance candles fetched the way the scorer fetches them, against the training
archive (`data/`, which ends in late October). This is a check of the scorer and adds nothing to the live record.

- **Realised variance:** 18 windows (3 coins, 2 horizons, 3 start times). The largest gap was 0.00000000000009%, which is
  rounding.
- **Stress label:** 18 of 18 hours agree, including 12 hours that really held a stress event.

## What is and isn't scored

| Item | Scored? | How |
|---|---|---|
| Shown forecast | Yes | QLIKE against realised variance |
| HAR-S | Yes | QLIKE, same rows |
| Benchmark | Yes | QLIKE, same rows |
| Stress chance | Yes, on 1 h rows | Brier score, against the classifier's own label, rebuilt from Binance candles by the training code with its full 30-day lookback |
| Regime | No | Logged for the record. There is no right or wrong answer to score it against. |

The stress chance is about the next hour, so it isn't scored on the 24 h rows, though it is logged there too.

## If something fails

The logger and the scorer are two background threads in the API. They only read the model cache and make their own
small Binance calls, so they don't slow a risk check.

- **Binance down while logging:** the row waits up to 20 minutes for the benchmark, then is written without it. That row
  is left out of the comparison with the benchmark, and n shows it.
- **Binance down while scoring:** the row stays "waiting" and is tried again.
- **Database down:** the page says the record isn't available, the rest of the app carries on, and hours that pass stay
  missing.
- **Restart during an hour:** if a row is written by 30 minutes past the hour, nothing is lost. If not, that hour is missing.

`TRACK_RECORD=0` switches both threads off.
