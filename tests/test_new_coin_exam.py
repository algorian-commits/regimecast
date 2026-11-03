"""Phase 13B: the exam's own rules (warm-up, scored window, pass counts)."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import new_coin_exam as E  # noqa: E402


def test_scored_window_starts_after_warmup_and_a_full_year(monkeypatch):
    for first, start in (("2020-01-06 09:00", "2022-01-01"),   # early listing: where the frozen scoring starts
                         ("2020-07-10 10:00", "2022-02-01"),   # warm-up to 2021-01-10, plus 12 months, next month
                         ("2020-09-23 08:00", "2022-04-01")):
        monkeypatch.setattr(E, "first_hour", lambda s, f=first: pd.Timestamp(f, tz="UTC"))
        assert E.window("X") == (start, "2026-10-01")


def test_warmup_cuts_every_read_and_is_undone(monkeypatch):
    idx = pd.date_range("2020-01-01", periods=24 * 400, freq="1h", tz="UTC")
    df = pd.DataFrame({"ts": idx})
    read = lambda *a, **k: df
    for mod, name in ((E, "_orig_features"), (E, "_orig_targets"), (E.store, "features"), (E.store, "targets")):
        monkeypatch.setattr(mod, name, read)
    with E.warmed_up():
        assert E.store.features("X")["ts"].min() >= pd.Timestamp("2020-07-01", tz="UTC")
        assert E.store.targets("X")["ts"].min() >= pd.Timestamp("2020-07-01", tz="UTC")
    assert E.store.features is read and E.store.targets is read


def test_pass_counts_are_the_preregistered_ones():
    assert [E.at_least(f, n) for f, n in ((0.8, 10), (0.5, 10), (0.9, 10), (0.8, 5))] == [8, 5, 9, 4]
