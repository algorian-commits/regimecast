"""Scoring for the parser test set (docs/m8/LABEL_RULES.md, "Scoring").

intent is scored on every case; the other fields only on cases labelled as trades. A field is right if both are
empty, both are numbers within 0.01%, or both are the same word.
"""
from __future__ import annotations

import math

import pandas as pd

from regimecast.parsing.schema import FIELDS

NUMERIC = ["leverage", "entry", "stop", "qty_coins", "notional_usdt", "margin_usdt", "wallet_usdt"]
TRADE_FIELDS = FIELDS[1:]


def _empty(v) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v)) or v == ""


def same(field: str, gold, pred) -> bool:
    if _empty(gold) or _empty(pred):
        return _empty(gold) and _empty(pred)
    if field in NUMERIC:
        g, p = float(gold), float(pred)
        return abs(g - p) <= 1e-4 * max(abs(g), abs(p))
    return str(gold).strip().lower() == str(pred).strip().lower()


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% interval for a proportion (better than +/- 2 SE near 100%)."""
    if n == 0:
        return float("nan"), float("nan")
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h


def score(gold: pd.DataFrame, pred: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(per-field accuracy table, per-case right/wrong table) for one parser. Both frames are indexed by id."""
    pred = pred.reindex(gold.index)
    right = pd.DataFrame(index=gold.index)
    for f in FIELDS:
        right[f] = pd.array([same(f, g, p) for g, p in zip(gold[f], pred[f])], dtype="boolean")
    trade = gold["intent"] == "trade"
    right.loc[~trade, TRADE_FIELDS] = pd.NA          # only scored on trades
    right["all_fields"] = right[FIELDS].fillna(True).all(axis=1)
    rows = []
    for f in FIELDS + ["all_fields"]:
        col = right[f].dropna().astype(bool)
        k, n = int(col.sum()), int(col.size)
        lo, hi = wilson(k, n)
        rows.append({"field": f, "right": k, "n": n, "accuracy": k / n if n else float("nan"),
                     "ci95_low": lo, "ci95_high": hi})
    return pd.DataFrame(rows), right


def agreement(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    """Per-field agreement between two label sets (all cases where either calls it a trade)."""
    rows = []
    for f in FIELDS:
        mask = (a["intent"] == "trade") | (b["intent"] == "trade") if f != "intent" else a.index == a.index
        agree = [same(f, x, y) for x, y in zip(a.loc[mask, f], b.loc[mask, f])]
        rows.append({"field": f, "agree": sum(agree), "n": len(agree), "rate": sum(agree) / len(agree)})
    return pd.DataFrame(rows)


def cohen_kappa(a: list, b: list) -> float:
    cats = sorted(set(a) | set(b))
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0
