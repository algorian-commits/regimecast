"""The verdict for the app: the risk check's numbers plus live market context, judged by the frozen rules.

Live inputs, refreshed in the background every 10 minutes (like vol/live.py), so a risk check never
waits on Binance:

* crowd signals: Binance's public 1-hour history of open interest and top traders' long/short position
  ratio (about 20 days) and the funding settlements, turned into the same 14-day ranks as in
  development (crowd.py). The archive used 5-minute values at each hour end; the 1-hour history gives
  the value at the same moments;
* trend: the last day's and week's return from the hourly candles the dashboard already keeps;
* similar moments: compared with the development-period hours shipped in ``similar_pool_<coin>.parquet``
  (every past hour up to 2025-09-30 with a complete next day).

Regime, stress and the volatility forecast come from vol/live.py, the liquidation model from the API.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from regimecast import config
from regimecast.risk import liquidation as liq
from regimecast.verdict import calendar, crowd, rules, similar
from regimecast.verdict.final import POOL_FILE, load_frozen

log = logging.getLogger("verdict.live")
REFRESH_SECONDS = 600.0
FROZEN = load_frozen()


def cuts() -> tuple:
    return tuple(FROZEN.get("grading", {}).get("cuts", rules.DEFAULT_CUTS))


# ------------------------------------------------------------------ live inputs
def fetch_crowd_history(symbol: str, client=None, limit: int = 500) -> pd.DataFrame:
    """Hourly funding rate (as of each hour end), top traders' long/short ratio and 24 h open-interest growth."""
    import httpx

    own = client is None
    client = client or httpx.Client(base_url=config.BINANCE_FAPI_URL, timeout=10.0)
    try:
        def stats(path: str, col: str) -> pd.Series:
            r = client.get(path, params={"symbol": symbol, "period": "1h", "limit": limit})
            r.raise_for_status()
            df = pd.DataFrame(r.json())
            ts = pd.to_datetime(df["timestamp"].astype("int64"), unit="ms", utc=True)
            return pd.Series(df[col].astype(float).to_numpy(), index=ts).sort_index()

        oi = stats("/futures/data/openInterestHist", "sumOpenInterest")
        ls = stats("/futures/data/topLongShortPositionRatio", "longShortRatio")
        fr = client.get("/fapi/v1/fundingRate", params={"symbol": symbol, "limit": 100})
        fr.raise_for_status()
        f = pd.DataFrame(fr.json())
    finally:
        if own:
            client.close()
    return crowd_frame(oi, ls, pd.Series(f["fundingRate"].astype(float).to_numpy(),
                                         index=pd.to_datetime(f["fundingTime"].astype("int64"), unit="ms", utc=True)))


def crowd_frame(oi: pd.Series, ls: pd.Series, funding: pd.Series) -> pd.DataFrame:
    """Hourly frame (index = hour end) with the three signals and their 14-day ranks."""
    end = max(oi.index.max(), ls.index.max()).floor("1h")
    grid = pd.date_range(end - pd.Timedelta(hours=max(len(oi), len(ls)) - 1), end, freq="1h")
    asof = lambda s: s.sort_index().reindex(grid, method="ffill")   # noqa: E731  (last value known at each hour)
    h = pd.DataFrame({"oi": asof(oi), "ls_top": asof(ls), "funding_rate": asof(funding)}, index=grid)
    h["oi_chg_24h"] = np.log(h["oi"] / h["oi"].shift(24))
    return h.join(crowd.ranks(h))


def trend(candles: list[dict]) -> dict:
    """Last day's and week's log return of the traded price from the hourly candles (t = hour start)."""
    if not candles:
        return {}
    c = pd.Series([x["close"] for x in candles], index=pd.to_datetime([x["t"] for x in candles], utc=True))
    c.index = c.index + pd.Timedelta(hours=1)   # hour end
    now = c.index[-1]
    get = lambda hrs: c.get(now - pd.Timedelta(hours=hrs))   # noqa: E731
    out = {"ts": now.isoformat()}
    for k, hrs in (("ret_24h", 24), ("ret_168h", 168)):
        past = get(hrs)
        out[k] = float(np.log(c.iloc[-1] / past)) if past else None
    return out


class Pools:
    """The shipped similar-moments pools, loaded on first use."""

    def __init__(self):
        self._p: dict[str, tuple] = {}
        self._lock = threading.Lock()

    def get(self, symbol: str):
        with self._lock:
            if symbol not in self._p:
                try:
                    df = pd.read_parquet(POOL_FILE.format(s=symbol))
                except Exception as e:   # not shipped: the verdict goes on without similar moments
                    log.warning("no similar-moments pool for %s: %r", symbol, e)
                    self._p[symbol] = None
                    return None
                sc = FROZEN["similar"]["scaling"][symbol]
                Z = similar.standardise(df, sc)
                pool = similar.Pool(df["ts"], Z, df["sigma24"].to_numpy(), df["r24"].to_numpy())
                self._p[symbol] = (pool, float(FROZEN["similar"]["up_share"][symbol]))
            return self._p[symbol]


pools = Pools()


class InputsCache:
    def __init__(self, symbols=None, fetch=fetch_crowd_history):
        self.symbols = symbols or config.SYMBOLS
        self.fetch = fetch
        self.data: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def refresh(self, symbol: str) -> None:
        h = self.fetch(symbol)
        last = h.iloc[-1]
        row = {k: (None if pd.isna(last[k]) else float(last[k]))
               for k in ("funding_rate", "ls_top", "oi_chg_24h", "funding_rate_rank", "ls_top_rank", "oi_chg_24h_rank")}
        with self._lock:
            self.data[symbol] = {**row, "inputs_until": h.index[-1].isoformat(),
                                 "made_at": datetime.now(timezone.utc).isoformat()}

    def refresh_all(self) -> None:
        for s in self.symbols:
            try:
                self.refresh(s)
            except Exception as e:   # Binance hiccup: keep the last values, try again later
                log.warning("verdict inputs %s failed: %r", s, e)

    def get(self, symbol: str, max_age_s: float = 3 * 3600) -> dict | None:
        with self._lock:
            d = self.data.get(symbol)
        if not d:
            return None
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(d["made_at"])).total_seconds()
        return d if age <= max_age_s else None

    def start(self) -> None:
        if self._thread is not None:
            return

        def loop():
            while True:
                self.refresh_all()
                time.sleep(REFRESH_SECONDS)

        self._thread = threading.Thread(target=loop, name="verdict-live", daemon=True)
        self._thread.start()


cache = InputsCache()


# ------------------------------------------------------------------ the pieces
def crowd_now(symbol: str, inputs: dict | None) -> dict | None:
    if not inputs or not FROZEN.get("crowd"):
        return None
    rk = pd.DataFrame({f"{c}_rank": [inputs.get(f"{c}_rank")] for c in crowd.SIGNALS}, dtype=float)
    w = int(crowd.warning(rk, FROZEN["crowd"]["q"], FROZEN["crowd"]["rule"]).iloc[0])
    side = {1: "long", -1: "short"}.get(w)
    o = FROZEN["crowd"]["odds"].get(symbol, {}).get(side or "long", {})
    ranks = {"funding_rank": inputs.get("funding_rate_rank"), "ls_top_rank": inputs.get("ls_top_rank"),
             "oi_growth_rank": inputs.get("oi_chg_24h_rank")}
    if side is None:
        return {"side": None, "text": "No crowd warning: traders are not all leaning the same way right now.", **ranks}
    move = "drop" if side == "long" else "jump"
    text = (f"Crowd warning: funding, big traders' positions and new money all lean {side}. After moments like this "
            f"in 2022-2025, a sudden 5% {move} within a day happened {rules.pct(o['after_warning'])} of the time "
            f"(normally {rules.pct(o['normally'])}).") if o.get("after_warning") is not None else \
        f"Crowd warning: funding, big traders' positions and new money all lean {side}."
    return {"side": side, "text": text, "after_warning": o.get("after_warning"), "normally": o.get("normally"), **ranks}


def similar_now(symbol: str, regime: dict | None, sigma24: float, inputs: dict | None, tr: dict) -> dict | None:
    if not (FROZEN.get("similar") and regime and inputs and tr.get("ret_24h") is not None
            and tr.get("ret_168h") is not None):
        return None
    got = pools.get(symbol)
    if got is None:
        return None
    pool, p0 = got
    x = pd.DataFrame([{"p_stressed": regime["p_stressed"], "p_calm": regime["p_calm"], "log_sigma": math.log(sigma24),
                       "trend_24h": tr["ret_24h"] / sigma24, "trend_7d": tr["ret_168h"] / (sigma24 * math.sqrt(7)),
                       "funding_rank": inputs.get("funding_rate_rank"), "ls_rank": inputs.get("ls_top_rank"),
                       "oi_rank": inputs.get("oi_chg_24h_rank")}], dtype=float)
    z = similar.standardise(x, FROZEN["similar"]["scaling"][symbol])[0]
    if not np.isfinite(z).all():
        return None
    k = FROZEN["similar"]["k"]
    now = pd.Timestamp.now(tz="UTC")
    idx = pool.neighbours(z, now, k)
    s = similar.summarise(pool, idx, sigma24, k, FROZEN["similar"]["scaled"], FROZEN["similar"]["prior"], p0)
    if not s["n"]:
        return None
    pct = lambda v: round(100 * float(np.expm1(v)), 2)   # noqa: E731
    return {"n": s["n"], "range_lo_pct": pct(s["lo"]), "range_hi_pct": pct(s["hi"]),
            "typical_move_pct": pct(s["typical_abs"]), "p_up": round(s["p_up"], 4),
            "coin_flip": abs(s["share_up"] - 0.5) <= similar.COIN_FLIP_BAND, "text": similar.plain_text(s),
            "examples": similar.examples(pool, idx), "pool_until": pool.time(len(pool.ts) - 1).isoformat()}


def round_price(p: float) -> float:
    return round(p, 1 if p >= 1000 else 2 if p >= 10 else 4)


# ------------------------------------------------------------------ the verdict
def build(symbol: str, side: str, entry: float, qty: float, leverage: float, margin_mode: str,
          wallet_balance: float | None, stop: float | None, target: float | None, risk_limit: float,
          sigma24: float, hit24, regime: dict | None, stress: dict | None, funding_rate: float | None = None,
          inputs: dict | None = None, candles: list[dict] | None = None, now=None) -> dict:
    """Everything the beginner view shows. ``hit24(distance, sigma) -> probability`` is the API's
    liquidation model at 24 h for this side (FHS on HAR-S, or the random walk as a fallback)."""
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    s = 1 if side == "long" else -1
    notes: list[str] = []

    def evaluate(lev: float, q: float, st: float | None) -> rules.Candidate:
        p = liq.Position(symbol=symbol, side=side, entry=entry, qty=q, leverage=lev, margin_mode=margin_mode,
                         wallet_balance=wallet_balance, stop=st)
        lp = liq.liquidation_price(p)
        p_liq = hit24(liq.adverse_log_distance(p, lp), sigma24)
        p_stop = hit24(liq.adverse_log_distance(p, st), sigma24) if st else None
        return rules.Candidate(lev, q, st, p_liq, p_stop, lp if math.isfinite(lp) and lp > 0 else None)

    pos = liq.Position(symbol=symbol, side=side, entry=entry, qty=qty, leverage=leverage, margin_mode=margin_mode,
                       wallet_balance=wallet_balance, stop=stop)
    cur = evaluate(leverage, qty, stop)
    stop_past = stop is not None and cur.liq_price is not None and s * (stop - cur.liq_price) <= 0
    fr = inputs.get("funding_rate") if inputs and inputs.get("funding_rate") is not None else funding_rate
    cw = crowd_now(symbol, inputs)
    if cw is None:
        notes.append("Crowd signals are not available right now, so the crowd check was skipped.")
    events = calendar.events_within(now, rules.HORIZON_HOURS)
    weekend = calendar.is_weekend(now)
    thin = calendar.in_thin_hours(now, FROZEN.get("thin_hours", []))
    p3 = stress.get("p_stress_3h_ago") if stress else None
    rising = bool(stress and p3 is not None and rules.stress_rising(stress["p_stress_next_hour"], p3))

    def facts(c: rules.Candidate, past: bool) -> rules.Facts:
        return rules.Facts(side=side, p_liq=c.p_liq, risk_limit=risk_limit, p_stop=c.p_stop, stop_past_liq=past,
                           cost_share=rules.cost_share(side, pos.taker_fee, fr, sigma24),
                           regime=regime["regime"] if regime else None,
                           stress_level=stress["level"] if stress else None, stress_rising=rising,
                           crowd=cw["side"] if cw else None,
                           crowd_odds=(cw["after_warning"], cw["normally"]) if cw and cw.get("after_warning") is not None
                           else None, events=events, weekend=weekend, thin_hour=thin)

    f = facts(cur, stop_past)
    g = rules.grade(f, cuts())

    funding_usdt = s * (fr or 0.0) * pos.notional * rules.HORIZON_HOURS / 8
    be, be_hint = None, None
    if stop is not None and target is not None:
        be = rules.break_even(side, entry, qty, stop, target, pos.taker_fee, funding_usdt)
    else:
        be_hint = "Add a stop and a target price to see the win rate this trade needs to make money."

    wq = FROZEN.get("what_if", {}).get("quantiles", {}).get(symbol, {})
    q = (wq.get(regime["regime"]) if regime else None) or wq.get("all")
    what = []
    if q:
        sc = rules.scenarios(side, q[side], FROZEN["what_if"]["crash_day"].get(symbol))
        what = rules.what_if(side, entry, qty, pos.margin_used(), cur.liq_price, stop, sc, pos.taker_fee)
        if any(w["key"] == "crash" and w["outcome"] == "stopped out" for w in what):
            notes.append("In a crash a stop can fill well below its price: the crash line assumes it fills exactly.")

    fix = rules.fix_trade(side, entry, qty, leverage, stop, risk_limit, sigma24, evaluate, round_price)
    fix_out, fix_note = None, "Your trade already keeps liquidation within your limit with a stop outside normal noise."
    if fix is not None:
        fpast = fix.stop is not None and fix.liq_price is not None and s * (fix.stop - fix.liq_price) <= 0
        fg = rules.grade(facts(fix, fpast), cuts())
        fpos = liq.Position(symbol=symbol, side=side, entry=entry, qty=fix.qty, leverage=fix.leverage,
                            margin_mode=margin_mode, wallet_balance=wallet_balance, stop=fix.stop)
        changes = []
        if fix.leverage != leverage:
            changes.append(f"Leverage {leverage:g}x → {fix.leverage:g}x: the same margin buys a smaller position "
                           f"(${pos.notional:,.0f} → ${fpos.notional:,.0f}).")
        if fix.stop != stop:
            changes.append(f"Stop {'added at' if stop is None else f'{stop:,.2f} →'} {fix.stop:,.2f}: outside normal "
                           f"price noise (about {rules.a_pct(fix.p_stop)} chance of being hit in a day).")
        loss = abs(entry - fix.stop) * fix.qty + liq.fees_round_trip(fpos) if fix.stop else None
        fix_out = {"leverage": fix.leverage, "qty": round(fix.qty, 6), "notional_usdt": round(fpos.notional, 2),
                   "margin_usdt": round(fpos.margin_used(), 2), "stop": fix.stop,
                   "liquidation_price": round(fix.liq_price, 2) if fix.liq_price else None,
                   "p_liquidation_24h": round(fix.p_liq, 5), "p_stop_24h": round(fix.p_stop, 4) if fix.p_stop else None,
                   "loss_at_stop_usdt": round(loss, 2) if loss is not None else None, "grade": fg["grade"],
                   "headline": fg["headline"], "points": fg["points"], "changes": changes}
        fix_note = ("The closest safer version of the same trade: same coin, direction, entry and margin, with "
                    "liquidation within your limit and a stop outside normal noise.")
        if fg["points"] > 0 and fg["grade"] not in ("A", "B"):
            fix_note += " Market conditions (not your settings) keep its grade down."

    timing = [f"{e['name']} at {e['time_utc'][:16].replace('T', ' ')} UTC is within the next day." for e in events]
    if weekend:
        timing.append("It is the weekend: fewer traders, so prices can jump more easily.")
    elif thin:
        timing.append("This is one of the quietest hours of the day for trading.")
    if rising:
        timing.append("Market stress is rising: the chance of a sudden shock has at least doubled in 3 hours.")

    return {**{k: g[k] for k in ("grade", "headline", "points", "reasons")}, "leverage": leverage,
            "all_reasons": [{k: r[k] for k in ("text", "good", "points")} for r in g["all_reasons"]],
            "p_liquidation_24h": round(cur.p_liq, 5), "p_stop_24h": round(cur.p_stop, 4) if cur.p_stop is not None else None,
            "knockout_24h": round(rules.knockout(f), 4), "break_even": be, "break_even_hint": be_hint,
            "fix": fix_out, "fix_note": fix_note, "what_if": what, "crowd": cw,
            "similar": similar_now(symbol, regime, sigma24, inputs, trend(candles or [])),
            "timing": timing, "notes": notes, "frozen_at": FROZEN.get("frozen_at")}
