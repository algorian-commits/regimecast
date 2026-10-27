"""Final-evaluation entry point for the paper agent (Phase 10 runs it ONCE; Phase 9 never does).

    from regimecast.agent.final import run_final
    run_final(final=True)

Runs the frozen settings (frozen.json) and both baselines on the final test period
(2025-10-01 00:00 to 2026-09-30 23:00 UTC) and writes ``data/reports/final_agent_*.csv``.

Inputs it expects from Phase 10's walk-forward run on the final test period, in
data/reports (same columns as the development files, see signals.py):
    final_liq_<coin>_24h.parquet    ts, sigma             (24 h HAR-S + LightGBM combination)
    final_stress_pred_<coin>.parquet   index ts, gbm_cal and gbm (or p_stress)
    final_regimes_<coin>.parquet    index ts, p_stressed, regime

Pass bar (docs/PLAN.md, fixed 27 Oct 2026 before opening): a higher Sharpe ratio than
buy-and-hold AND a maximum drawdown at least 25% smaller. Return, the volatility-targeting
baseline and the bootstrap interval are reported alongside, not as bars.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from regimecast import config
from regimecast.agent import backtest as B, rules as R, signals as S

FROZEN_FILE = Path(__file__).with_name("frozen.json")   # written once by scripts/run_phase9.py
FINAL_PERIOD = (config.HOLDOUT_START, "2026-10-01")
FINAL_CRASH = ("2025-10-10", "2025-10-12")


def load_frozen(path: Path = FROZEN_FILE) -> tuple[R.Settings, dict]:
    d = json.loads(path.read_text())
    return R.Settings(**d["settings"]), d


def run_final(final: bool = False, reports: Path | None = None, symbols=None) -> dict:
    if not final:
        raise ValueError("run_final reads the final test period; only Phase 10 calls it, with final=True")
    rep = reports or (config.DATA_DIR / "reports")
    symbols = symbols or config.SYMBOLS
    settings, frozen = load_frozen()
    vt = frozen["vol_target_baseline"]
    mark, funding = S.market(symbols, final=True)
    a, b = (pd.Timestamp(x, tz="UTC") for x in FINAL_PERIOD)
    m = mark[(mark.index >= a) & (mark.index < b)]
    f = funding.reindex(m.index)
    sig = S.load_all(symbols, final=True, reports=rep)
    runs = {"agent": B.simulate(m, f, R.agent_targets(sig, settings, m.index), settings.band, final=True),
            "buy_and_hold": B.simulate(m, f, R.buy_and_hold_targets(symbols, m.index), 0.0, final=True),
            "vol_target": B.simulate(m, f, R.vol_target_targets(sig, vt["target_vol"], m.index), vt["band"],
                                     final=True)}
    res = pd.DataFrame([{"strategy": k, **B.metrics(v)} for k, v in runs.items()])
    boot = pd.DataFrame([{"comparison": f"agent - {o}",
                          **B.sharpe_diff_bootstrap(runs["agent"].hourly["ret"], runs[o].hourly["ret"])}
                         for o in ("buy_and_hold", "vol_target")])
    ca, cb = (pd.Timestamp(x, tz="UTC") for x in FINAL_CRASH)
    crash = []
    for k, v in runs.items():
        h = v.hourly
        before = h.loc[h.index < ca, "equity"].iloc[-1]
        w = h[(h.index >= ca) & (h.index < cb)]
        crash.append({"strategy": k, "return": float(w["equity"].iloc[-1] / before - 1),
                      "max_drawdown": B.max_drawdown(w["equity"] / before)})
    r = res.set_index("strategy")
    verdict = {"agent_sharpe": float(r.loc["agent", "sharpe"]), "bh_sharpe": float(r.loc["buy_and_hold", "sharpe"]),
               "agent_max_drawdown": float(r.loc["agent", "max_drawdown"]),
               "bh_max_drawdown": float(r.loc["buy_and_hold", "max_drawdown"])}
    verdict["higher_sharpe"] = verdict["agent_sharpe"] > verdict["bh_sharpe"]
    verdict["drawdown_25pct_smaller"] = abs(verdict["agent_max_drawdown"]) <= 0.75 * abs(verdict["bh_max_drawdown"])
    verdict["pass"] = bool(verdict["higher_sharpe"] and verdict["drawdown_25pct_smaller"])
    res.to_csv(rep / "final_agent_results.csv", index=False)
    boot.to_csv(rep / "final_agent_bootstrap.csv", index=False)
    pd.DataFrame(crash).to_csv(rep / "final_agent_crash.csv", index=False)
    pd.DataFrame({k: v.equity for k, v in runs.items()}).to_parquet(rep / "final_agent_equity.parquet")
    (rep / "final_agent_verdict.json").write_text(json.dumps({"frozen": frozen, **verdict}, indent=2))
    return verdict
