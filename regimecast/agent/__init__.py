"""Paper-trading risk agent (M10, part 2): a risk manager for someone who holds BTC, ETH and SOL.

backtest.py  hourly simulator: positions at the mark price, taker fees, 8-hourly funding, a no-trade band
rules.py     the agent's rule and the baselines (buy-and-hold, plain volatility targeting)
signals.py   the walk-forward, out-of-sample signals it reads (Phases 4-6) and the market data
final.py     the final-evaluation entry point (``final=True``; Phase 10 runs it once)
live.py      the hourly live paper log (GitHub Actions, branch ``agent-data``)
"""
