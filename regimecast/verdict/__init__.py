"""Trade verdict for beginners (Phase 12, M9 extended): is this a well-built trade, and how to make it safer.

It judges the set-up (size, leverage, stop, costs, timing, crowding), never which way the price will go:
Phase 6b showed our data cannot predict direction, so nothing here pretends to.

rules.py         Trade Grade A-F with plain reasons, break-even win rate, fix my trade, the what-if strip
crowd.py         crowd warning: funding, top traders' long/short ratio and open-interest growth at extremes
similar.py       similar moments: the past hours most like now, and what the next 24 h did after them
calendar.py      bad timing: US macro events (macro_events.csv), weekends and thin hours
history.py       the hour-by-hour inputs on past data (walk-forward, no look-ahead) for the dev tables
evaluate.py      the measures behind the final-evaluation bars (grade grid, crowd ratio, similar-moments coverage)
live.py          the verdict for the API's risk check, from live inputs
final.py         the final-evaluation entry point (``final=True``; Phase 10 runs it once)
frozen.json      the settings fixed on the development period by scripts/run_phase12.py
"""
