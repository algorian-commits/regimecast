"""The agent's rule and the two baselines it is judged against.

Agent, per coin, at each hour end H (only signals stamped at or before H):

    exposure = min(1, target_vol / forecast_vol)          volatility target (M7 forecast)
             x 0.5  if P(stress next hour) >= stress_elevated   (M10 part 1)
             x 0    if P(stress next hour) >= stress_high
             x regime_mult  if the regime is "stressed"        (M4)

Baselines on the same hours, fees and funding:
* buy-and-hold: 1x long in each perpetual, a third of the account each, bought at the first
  hour and never traded again (the quantities stay fixed, as for someone who just holds);
* volatility targeting: min(1, target_vol / last week's realised volatility), no RegimeCast model.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

ELEVATED_MULT = 0.5   # exposure kept when stress is "elevated" (fixed, not tuned)
NO_CUT = 2.0          # a stress cut-off that never triggers (probabilities are <= 1)


@dataclass(frozen=True)
class Settings:
    target_vol: float = 0.5        # annualised volatility the position is sized to
    stress_elevated: float = 0.10  # P(stress next hour) at which exposure is halved
    stress_high: float = 0.30      # ... and at which it goes to zero
    regime_mult: float = 0.5       # exposure kept when the regime is "stressed"
    band: float = 0.05             # skip changes smaller than this (share of the coin's third)

    def to_dict(self) -> dict:
        return asdict(self)


def exposure(sig: pd.DataFrame, s: Settings) -> pd.Series:
    """Exposure 0..1 for one coin from its signal table (signals.load). NaN if the volatility
    forecast is missing (the backtester then keeps the position)."""
    base = np.minimum(1.0, s.target_vol / sig["vol_ann"])
    p = sig["p_stress"].fillna(0.0)
    m_stress = np.where(p >= s.stress_high, 0.0, np.where(p >= s.stress_elevated, ELEVATED_MULT, 1.0))
    m_regime = np.where(sig["regime"] == 2, s.regime_mult, 1.0)
    return (base * m_stress * m_regime).rename("exposure")


def reasons(row: pd.Series, s: Settings) -> list[str]:
    """Plain-English reasons for one decision (for the live log and the app)."""
    out = []
    base = min(1.0, s.target_vol / row["vol_ann"])
    out.append(f"Forecast volatility {100 * row['vol_ann']:.0f}% a year vs target {100 * s.target_vol:.0f}%: "
               f"{'full position' if base >= 1 else f'hold {100 * base:.0f}%'}")
    p = row.get("p_stress", 0.0) or 0.0
    if s.stress_elevated > 1 and s.stress_high > 1:
        out.append(f"Stress risk next hour {100 * p:.0f}%: no stress cut in the frozen settings")
    elif p >= s.stress_high:
        out.append(f"Stress risk next hour {100 * p:.0f}% (>= {100 * s.stress_high:.0f}%): out of the market")
    elif p >= s.stress_elevated:
        out.append(f"Stress risk next hour {100 * p:.0f}% (>= {100 * s.stress_elevated:.0f}%): halved")
    else:
        out.append(f"Stress risk next hour {100 * p:.0f}%: below the cut-off, no cut")
    stressed = row.get("regime") == 2
    if s.regime_mult >= 1:
        out.append(f"Regime {'stressed' if stressed else 'not stressed'}: no regime cut in the frozen settings")
    elif stressed:
        out.append(f"Regime is stressed: kept {100 * s.regime_mult:.0f}%")
    else:
        out.append("Regime is not stressed: no cut")
    return out


def agent_targets(signals: dict[str, pd.DataFrame], s: Settings, index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({c: exposure(sig, s).reindex(index) for c, sig in signals.items()})


def vol_target_targets(signals: dict[str, pd.DataFrame], target_vol: float, index: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({c: np.minimum(1.0, target_vol / sig["trail_vol_ann"]).reindex(index)
                         for c, sig in signals.items()})


def buy_and_hold_targets(coins: list[str], index: pd.DatetimeIndex) -> pd.DataFrame:
    t = pd.DataFrame(np.nan, index=index, columns=coins)
    t.iloc[0] = 1.0
    return t
