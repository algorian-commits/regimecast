"""Phase 12 in the API: the trade verdict, the fix and the what-if strip that /risk-check returns.

Computed with the risk check's own liquidation model (FHS on the volatility forecast it uses) and the live inputs
it already has: regime and stress (vol/live.py), and the last hour's funding, crowding and trend inputs, which come
from the same feature rows as the stress gauge. Logic lives in regimecast/verdict/; this file turns a trade into
its inputs and the results into the API's shapes. A failure here never breaks the risk check: the verdict is
left out and the rest is returned.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Callable, Literal

import pandas as pd
from pydantic import BaseModel, Field

from regimecast.risk import liquidation as liq
from regimecast.verdict import crowd as C, fix as FX, grade as G, settings, similar as S, timing as T, whatif as WI

log = logging.getLogger("api.verdict")
FUNDING_HOURS = (0, 8, 16)
CFG = settings.load()
CALENDAR = T.load_calendar()
_LIBRARIES: dict[str, S.Library | None] = {}


# ------------------------------------------------------------------ schemas
class VerdictFactor(BaseModel):
    key: str = Field(description="knockout, liquidation, stop, costs, regime, stress, crowd, timing or calm")
    score: int = Field(description="Setup: 0 fine to 4 as bad as it gets. Market: grade steps added")
    text: str


class CrowdOut(BaseModel):
    warning: bool
    side: Literal["long", "short"] | None = Field(None, description="Where the crowd is piled in, when warning")
    message: str
    funding_rate: float | None
    ls_top: float | None = Field(None, description="Top traders' long/short position ratio")
    oi_chg_24h: float | None = Field(None, description="Open-interest growth over 24 h (log)")
    p_after_warning: float | None = Field(None, description="Dev period: share of warnings followed by a 5% move against the crowd within 24 h")
    p_other: float | None = None


class SimilarOut(BaseModel):
    n: int = Field(description="Past hours compared")
    range_low_pct: float = Field(description="80% range of the next 24 h move, low end (%)")
    range_high_pct: float
    typical_move_pct: float = Field(description="Median size of the next 24 h move (%)")
    p_up: float
    coin_flip: bool = Field(description="True when the up-chance is between 40% and 60%")
    same_regime: bool
    message: str


class VerdictOut(BaseModel):
    grade: Literal["A", "B", "C", "D", "F"]
    headline: str
    reasons: list[str] = Field(description="Three plain-English reasons, the most important first")
    factors: list[VerdictFactor]
    p_knockout: float = Field(description="Chance the stop or the liquidation price is reached within the horizon")
    break_even_win_rate: float | None = Field(None, description="Share of such trades you must win to make money after fees and funding; above 1 = never")
    break_even_note: str
    crowd: CrowdOut | None = None
    similar: SimilarOut | None = None
    timing: list[str]
    not_direction: str
    settings_frozen_at: str | None = Field(None, description="When the grading settings were fixed on the dev period; null = defaults")


class TradeVersion(BaseModel):
    leverage: float
    notional_usdt: float
    margin_usdt: float
    stop: float | None
    liquidation_price: float | None
    p_liquidation: float
    p_stop_hit: float | None
    max_loss_usdt: float = Field(description="Loss at the stop plus fees, or the margin if liquidated")
    grade: Literal["A", "B", "C", "D", "F"]


class FixOut(BaseModel):
    needed: bool
    message: str
    original: TradeVersion
    fixed: TradeVersion | None = None
    changes: list[str]


class WhatIfOut(BaseModel):
    scenario: Literal["normal", "bad", "crash"]
    label: str
    move_pct: float = Field(description="Size of the price move (%)")
    up_usdt: float | None = Field(None, description="Result if the price rises that much (USDT); null for the crash day")
    up_exit: Literal["stop", "liquidation"] | None = None
    down_usdt: float = Field(description="Result if the price falls that much (USDT)")
    down_exit: Literal["stop", "liquidation"] | None = None
    basis: str


# ------------------------------------------------------------------ helpers
def tidy(price: float) -> float:
    return round(price, 1 if price >= 1000 else 2 if price >= 10 else 4)


def settlements(now: datetime, hours: int) -> int:
    """Funding settlements in (now, now + hours]."""
    start = pd.Timestamp(now).floor("1h")
    return sum(1 for k in range(1, hours + 1) if (start + pd.Timedelta(hours=k)).hour in FUNDING_HOURS)


def library(symbol: str) -> S.Library | None:
    if symbol not in _LIBRARIES:
        _LIBRARIES[symbol] = S.Library.load(settings.SIMILAR_DIR / f"{symbol}.npz")
    return _LIBRARIES[symbol]


class Context:
    """One risk check: the user's trade, the forecast, the model and the live market."""

    def __init__(self, t, pos: liq.Position, sigma: float, hit: Callable[[float, float], float], rg, st, state, now):
        self.t, self.pos, self.sigma, self.hit, self.now = t, pos, sigma, hit, now
        self.coin = t.symbol.removesuffix("USDT")
        self.margin = pos.initial_margin
        self.rate = (state or {}).get("funding_rate") or 0.0
        self.n_funding = settlements(now, t.horizon_hours)
        self.regime = rg["regime"] if rg else None
        self.st = st
        self.crowd = C.assess(state, CFG["crowd"].get(t.symbol))
        p_before = (state or {}).get("p_stress_3h_ago")
        tm = T.check(pd.Timestamp(now), t.horizon_hours, CALENDAR, CFG["thin_hours"].get(t.symbol, ()),
                     st["p_stress_next_hour"] if st else None, p_before)
        self.timing = tm.warnings(pd.Timestamp(now))

    def position(self, leverage: float, stop: float | None) -> liq.Position:
        return liq.Position(**{**self.pos.__dict__, "leverage": leverage, "qty": self.margin * leverage / self.t.entry,
                               "stop": stop})

    def stop_price(self, d: float) -> float:
        return tidy(self.t.entry * math.exp(-d if self.t.side == "long" else d))

    def version(self, leverage: float, stop: float | None) -> tuple[G.Verdict, dict, FX.Option]:
        p = self.position(leverage, stop)
        lp = liq.liquidation_price(p)
        finite = math.isfinite(lp) and lp > 0
        d_liq = liq.adverse_log_distance(p, lp)
        p_liq = self.hit(d_liq, self.sigma)
        d_stop = liq.adverse_log_distance(p, stop) if stop else None
        past = d_stop is not None and d_stop >= d_liq
        exit_d = min(d_stop, d_liq) if d_stop is not None else d_liq
        fees = liq.fees_round_trip(p)
        funding = (1 if self.t.side == "long" else -1) * self.rate * p.notional * self.n_funding
        liq_loss = p.margin_used() if finite else p.notional
        max_loss = p.qty * abs(self.t.entry - stop) + fees if stop and not past else liq_loss
        facts = G.Facts(
            coin=self.coin, horizon_hours=self.t.horizon_hours, p_knockout=self.hit(exit_d, self.sigma),
            p_liquidation=p_liq, risk_limit=self.t.risk_limit, has_stop=stop is not None, stop_past_liquidation=past,
            stop_moves=d_stop / self.sigma if d_stop is not None else None,
            cost_share=(fees + funding) / (self.sigma * p.notional), regime=self.regime,
            stress_level=self.st["level"] if self.st else None,
            p_stress=self.st["p_stress_next_hour"] if self.st else None,
            stress_base=self.st.get("base_rate") if self.st else None,
            crowd_on_side=bool(self.crowd and self.crowd.side == self.t.side),
            crowd_text=self.crowd.message(self.coin) if self.crowd else "", timing=self.timing)
        v = G.assess(facts, CFG["grade"])
        info = {"position": p, "lp": lp if finite else None, "fees": fees, "funding": funding, "liq_loss": liq_loss,
                "p_stop": self.hit(d_stop, self.sigma) if d_stop is not None else None, "facts": facts}
        return v, info, FX.Option(leverage, d_stop, v.index, p_liq, d_liq, max_loss)


def _summary(v: G.Verdict, info: dict, o: FX.Option) -> TradeVersion:
    p = info["position"]
    return TradeVersion(leverage=o.leverage, notional_usdt=round(p.notional, 2), margin_usdt=round(p.margin_used(), 2),
                        stop=p.stop, liquidation_price=round(info["lp"], 2) if info["lp"] else None,
                        p_liquidation=round(o.p_liquidation, 5),
                        p_stop_hit=round(info["p_stop"], 5) if info["p_stop"] is not None else None,
                        max_loss_usdt=round(o.max_loss, 2), grade=v.grade)


def break_even(ctx: Context, info: dict) -> tuple[float | None, str]:
    t, p = ctx.t, info["position"]
    stop_ok = t.stop is not None and not info["facts"].stop_past_liquidation
    loss = p.qty * abs(t.entry - t.stop) if stop_ok else info["liq_loss"]
    gain = p.qty * abs(t.take_profit - t.entry) if t.take_profit else loss
    cost = info["fees"] + info["funding"]
    pbe = G.break_even_win_rate(loss, gain, cost)
    exit_ = "your stop" if stop_ok else "your liquidation price"
    if pbe is None:
        return None, ""
    if pbe >= 1:
        return pbe, "Even if you were right every time, fees and funding would eat the gain."
    lead = ("With this stop and take-profit" if t.take_profit and stop_ok else
            f"With this take-profit and {exit_} as the exit" if t.take_profit else
            f"With a take-profit as far from your entry as {exit_}")
    return pbe, f"{lead}, you need to be right {100 * pbe:.0f}% of the time to make money after fees and funding."


def _fix(ctx: Context, v: G.Verdict, info: dict, o: FX.Option) -> FixOut:
    cache: dict[int, tuple] = {}

    def evaluate(lev: float, d: float) -> FX.Option:
        vv, ii, oo = ctx.version(lev, ctx.stop_price(d))
        cache[id(oo)] = (vv, ii, oo)
        return oo

    best = FX.fix(o, evaluate, ctx.t.risk_limit, ctx.sigma)
    orig = _summary(v, info, o)
    if best is None:
        noisy = o.stop_distance is None or o.stop_distance < FX.NOISE_MOVES * ctx.sigma
        sound = o.p_liquidation <= ctx.t.risk_limit and o.stop_ok and not noisy
        if sound and v.index <= FX.TARGET:
            msg = "Your trade already keeps within your risk limit, with a stop outside normal noise."
        elif sound:   # the setup is fine; only the market holds the grade down, and no change to the trade helps
            msg = (f"Your setup already keeps within your risk limit, with a stop outside normal noise. Market "
                   f"conditions hold the grade at {v.grade}; a smaller position or waiting are the only changes left.")
        else:
            msg = ("No version of this trade with the same margin meets your risk limit with a stop outside normal "
                   "noise. A smaller margin would.")
        return FixOut(needed=not sound, message=msg, original=orig, changes=[])
    fv, fi, fo = cache[id(best)]
    stop = fi["position"].stop
    fixed = _summary(fv, fi, fo)
    changes = []
    if best.leverage != o.leverage:
        changes.append(f"Leverage {o.leverage:g}x to {best.leverage:g}x: the same ${orig.margin_usdt:,.2f} margin "
                       f"buys a ${fixed.notional_usdt:,.0f} position instead of ${orig.notional_usdt:,.0f}.")
    if ctx.t.stop is None:
        changes.append(f"Add a stop at {stop:,} ({G.moves(fo.stop_distance / ctx.sigma)} typical moves away, outside "
                       f"normal noise).")
    elif stop != ctx.t.stop:
        changes.append(f"Stop from {ctx.t.stop:,} to {stop:,} ({G.moves(fo.stop_distance / ctx.sigma)} typical moves away, "
                       f"outside normal noise).")
    msg = (f"The closest safer version grades {fixed.grade} (yours: {orig.grade}): it keeps the chance of "
           f"liquidation within your {G.pct(ctx.t.risk_limit)} limit and can lose at most ${fixed.max_loss_usdt:,.2f}.")
    return FixOut(needed=True, message=msg, original=orig, fixed=fixed, changes=changes)


def _similar(ctx: Context, state: dict | None, sigma_24h: float) -> SimilarOut | None:
    lib = library(ctx.t.symbol)
    sc = CFG["similar"].get("scaling", {}).get(ctx.t.symbol)
    if lib is None or not sc or not state:
        return None
    m = S.query(lib, state, ctx.regime, sigma_24h, pd.Timestamp(ctx.now), CFG["similar"], sc)
    if m is None:
        return None
    pc = lambda x: round(100 * (math.exp(x) - 1), 2)        # noqa: E731
    return SimilarOut(n=m.n, range_low_pct=pc(m.lo), range_high_pct=pc(m.hi), typical_move_pct=pc(m.typical),
                      p_up=round(m.p_up, 4), coin_flip=m.coin_flip, same_regime=m.same_regime,
                      message=m.message(ctx.coin))


def assess(t, pos: liq.Position, sigma: float, sigma_24h: float, hit: Callable[[float, float], float],
           rg: dict | None, st: dict | None, state: dict | None,
           now: datetime | None = None) -> tuple[VerdictOut | None, FixOut | None, list[WhatIfOut]]:
    try:
        now = now or datetime.now(timezone.utc)
        ctx = Context(t, pos, sigma, hit, rg, st, state, now)
        v, info, o = ctx.version(t.leverage, t.stop)
        pbe, note = break_even(ctx, info)
        cr = ctx.crowd
        verdict = VerdictOut(
            grade=v.grade, headline=v.headline, reasons=v.reasons,
            factors=[VerdictFactor(key=f.key, score=f.score, text=f.text) for f in v.factors],
            p_knockout=round(info["facts"].p_knockout, 5),
            break_even_win_rate=round(pbe, 4) if pbe is not None else None, break_even_note=note,
            crowd=CrowdOut(warning=cr.side is not None, side=cr.side, message=cr.message(ctx.coin) or
                           "No crowd warning: funding, top traders and open interest are not all at extremes.",
                           funding_rate=cr.funding_rate, ls_top=cr.ls_top, oi_chg_24h=cr.oi_chg_24h,
                           p_after_warning=cr.p_after, p_other=cr.p_other) if cr else None,
            similar=_similar(ctx, state, sigma_24h), timing=ctx.timing, not_direction=G.NOT_DIRECTION,
            settings_frozen_at=CFG["frozen_at"])
        p = info["position"]
        rows = WI.strip(t.symbol, t.side, t.entry, p.qty, info["fees"], t.stop, info["lp"], info["liq_loss"],
                        ctx.regime, sigma_24h, CFG["whatif"].get(t.symbol))
        what_if = [WhatIfOut(scenario=r.scenario, label=r.label, move_pct=round(r.move_pct, 2),
                             up_usdt=round(r.up_usdt, 2) if r.up_usdt is not None else None, up_exit=r.up_exit,
                             down_usdt=round(r.down_usdt, 2), down_exit=r.down_exit, basis=r.basis) for r in rows]
        return verdict, _fix(ctx, v, info, o), what_if
    except Exception:   # the verdict must never take the risk check down with it
        log.exception("trade verdict failed")
        return None, None, []
