"""Trade verdict for beginners (Phase 12, M9 extended): is this trade well built, and how to make it safer.

It judges the setup (size, leverage, stop, costs, timing, crowding), never which way the price will go:
Phase 6b found that the direction of the next day's move is not predictable from our data.

grade.py     Trade Grade A-F with three plain-English reasons, and the break-even win rate
fix.py       "fix my trade": the closest safer version of the same trade
whatif.py    what the trade gains or loses on a normal day, a bad day and a crash day like 10 Oct 2025
crowd.py     crowd warning: funding, top traders' long/short ratio and open-interest growth at extremes
similar.py   similar moments: the past hours that looked most like now, and how big the next day's move was
timing.py    bad timing: US macro events (macro_events.csv), weekends, thin hours, stress rising
settings.py  the settings frozen on the dev period (frozen.json), and the defaults used before the freeze
evaluate.py  the measures behind the final evaluation's pass bars, on the dev period or the final test period
devrun.py    the dev-period run: choose, check and freeze the settings, write the tables (scripts/run_phase12.py)
final.py     the final-evaluation entry point (``final=True``; Phase 10 runs it once)
"""
