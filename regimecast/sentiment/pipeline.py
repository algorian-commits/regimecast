"""News pipeline: store headlines, score them, build the hourly mood. One command per step.

    python -m regimecast.sentiment.pipeline import-backfill --ref origin/gdelt-backfill-data
    python -m regimecast.sentiment.pipeline score --model finbert      (and cryptobert)
    python -m regimecast.sentiment.pipeline mood
    python -m regimecast.sentiment.pipeline collect                    (one live update)
    python -m regimecast.sentiment.pipeline collect --every 15         (the worker: forever)

On disk (data/ is not in git)::

    data/parquet/news/headlines/<YYYY-MM>.parquet   one row per headline (time column known_at)
    data/parquet/news/scores/<model>.parquet        one row per distinct title
    data/parquet/sentiment/<SYMBOL>/mood.parquet    hourly mood, ts = hour END (read via store.news_mood)
    data/parquet/sentiment/meta.json                when the mood was built and with which model
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from regimecast import config
from regimecast.data import store
from regimecast.sentiment import gdelt, mood, models, rss, tagging

# Model behind the hourly mood. Chosen by accuracy on the hand-labelled headlines (see
# data/reports/m3_accuracy.md); can be overridden with REGIMECAST_MOOD_MODEL.
MOOD_MODEL = os.environ.get("REGIMECAST_MOOD_MODEL", "finbert")
GDELT_CATCHUP_MAX = pd.Timedelta(days=3)

HEADLINE_COLUMNS = ["known_at", "published_at", "fetched_at", "title", "link", "source", "origin", "scope",
                    "coins", "policy", "stale", "copies", "gdelt_tone", "title_key", "link_key"]


def news_dir() -> Path:
    return config.parquet_dir() / "news"


def sentiment_dir() -> Path:
    return config.parquet_dir() / "sentiment"


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, path)


# ------------------------------------------------------------------ headlines
def from_gdelt(raw: pd.DataFrame) -> pd.DataFrame:
    """GDELT scan rows -> headline rows. GDELT's batch time is both fetched and known time.
    Its 'precise publish time' is sometimes in local time (later than the batch), so it is
    capped at the batch time."""
    df = raw.copy()
    df["fetched_at"] = df["seen_at"]
    df["known_at"] = df["seen_at"]
    df["published_at"] = df[["published_at", "seen_at"]].min(axis=1)
    df["origin"] = "gdelt"
    return df.drop(columns=["seen_at"])


def stored_headlines(start=None, end=None, columns: list[str] | None = None) -> pd.DataFrame:
    return store.headlines(start, end, columns)


def add_headlines(new: pd.DataFrame) -> int:
    """Tag, de-duplicate against what is stored, and save. Returns rows added."""
    if new.empty:
        return 0
    new = tagging.tag(new)
    first = new["known_at"].min()
    seen = stored_headlines(first - tagging.TITLE_DUP_WINDOW, None, ["title_key", "link_key"])
    old_links = stored_headlines(None, None, ["link_key"])
    if len(old_links):
        new = new[~new["link_key"].isin(set(old_links["link_key"]))]
    kept = tagging.dedupe(new, seen if len(seen) else None)
    if kept.empty:
        return 0
    kept = kept.reindex(columns=HEADLINE_COLUMNS)
    folder = news_dir() / "headlines"
    for month, part in kept.groupby(kept["known_at"].dt.strftime("%Y-%m")):
        path = folder / f"{month}.parquet"
        if path.exists():
            part = pd.concat([pd.read_parquet(path), part], ignore_index=True)
        _write(part.sort_values("known_at", kind="stable").reset_index(drop=True), path)
    return len(kept)


def import_backfill(ref: str | None = None, folder: str | None = None) -> int:
    """Load the GDELT scan results (from the git branch the Actions job writes, or a folder)."""
    frames = []
    if folder:
        frames = [pd.read_parquet(p) for p in sorted(Path(folder).glob("*.parquet"))]
    else:
        names = subprocess.run(["git", "ls-tree", "--name-only", ref], capture_output=True, text=True,
                               check=True).stdout.split()
        for n in sorted(x for x in names if x.endswith(".parquet")):
            blob = subprocess.run(["git", "show", f"{ref}:{n}"], capture_output=True, check=True).stdout
            frames.append(pd.read_parquet(io.BytesIO(blob)))
    raw = pd.concat(frames, ignore_index=True)
    print(f"backfill: {len(raw)} raw rows, {raw['seen_at'].min()} to {raw['seen_at'].max()}")
    added = add_headlines(from_gdelt(raw))
    print(f"backfill: {added} headlines added after de-duplication")
    return added


# ------------------------------------------------------------------ scores
def scores_path(model: str) -> Path:
    return news_dir() / "scores" / f"{model}.parquet"


def load_scores(model: str) -> pd.DataFrame:
    p = scores_path(model)
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["title_key", "title", "p_pos", "p_neg",
                                                                      "p_neu", "label", "conf", "score"])


def score_new(model: str, probs: models.ProbFn | None = None, batch_size: int = 64) -> int:
    """Score every crypto title not scored yet by ``model``. Returns how many were scored."""
    h = stored_headlines(None, None, ["title", "title_key", "scope", "stale"])
    if h.empty:
        return 0
    h = h[(h["scope"] == "crypto") & ~h["stale"]].drop_duplicates("title_key")
    have = load_scores(model)
    todo = h[~h["title_key"].isin(set(have["title_key"]))]
    if todo.empty:
        return 0
    probs = probs or models.load(model)
    print(f"{model}: scoring {len(todo)} titles", flush=True)
    t0 = time.time()
    s = models.score(todo["title"].tolist(), probs, batch_size, progress=len(todo) > 5000)
    s.insert(0, "title", todo["title"].to_numpy())
    s.insert(0, "title_key", todo["title_key"].to_numpy())
    out = pd.concat([have, s], ignore_index=True) if len(have) else s
    _write(out, scores_path(model))
    print(f"{model}: {len(todo)} titles in {time.time() - t0:.0f}s", flush=True)
    return len(todo)


# ------------------------------------------------------------------ mood
def build_mood(model: str = MOOD_MODEL, until: pd.Timestamp | None = None) -> dict[str, int]:
    """Rebuild the hourly mood for every symbol. The grid runs to the current hour."""
    h = stored_headlines(None, None, ["known_at", "scope", "coins", "policy", "stale", "title_key"])
    scores = load_scores(model)
    if h.empty or scores.empty:
        return {}
    now = pd.Timestamp(datetime.now(timezone.utc))
    until = until or now.floor("h")
    first = mood.hour_end(h["known_at"]).min()
    # a run of empty hours up to now is not an outage (the collector may simply not have run yet)
    gaps = mood.outage_hours(h["known_at"])
    sizes = {}
    for sym in mood.COIN_OF:
        m = mood.hourly(h, scores, sym, start=first, end=until, drop_hours=gaps)
        _write(m, sentiment_dir() / sym / "mood.parquet")
        sizes[sym] = len(m)
    meta = {"updated_at": now.isoformat(), "model": model, "model_repo": models.MODELS[model],
            "headlines": int(len(h)), "last_headline_at": h["known_at"].max().isoformat(),
            "outage_hours": int(len(gaps))}
    sentiment_dir().mkdir(parents=True, exist_ok=True)
    (sentiment_dir() / "meta.json").write_text(json.dumps(meta, indent=2))
    return sizes


def latest(symbol: str, hours: int) -> dict | None:
    """The last ``hours`` hours of mood for the API, or None if nothing is built yet."""
    meta_f = sentiment_dir() / "meta.json"
    m = store.news_mood(symbol)
    if m.empty or not meta_f.exists():
        return None
    m = m.tail(hours)
    meta = json.loads(meta_f.read_text())
    rows = [{"hour_end": t.isoformat(), "count": int(c),
             "mean_score": 0.0 if pd.isna(s) else round(float(s), 4),
             "share_negative": 0.0 if pd.isna(n) else round(float(n), 4),
             "policy_shock_count": int(p)}
            for t, c, s, n, p in zip(m["ts"], m["count"], m["mean_score"], m["share_negative"],
                                     m["policy_shock_count"])]
    return {"symbol": symbol, "updated_at": meta["updated_at"], "hours": rows}


# ------------------------------------------------------------------ live collection
def collect(probs: models.ProbFn | None = None, use_gdelt: bool = True, use_rss: bool = True) -> dict:
    """One update: new GDELT batches since the last one stored, plus the RSS feeds, then
    score and rebuild the mood. Same steps as the backfill, so the series stays consistent."""
    added = {}
    if use_gdelt:
        now = pd.Timestamp.now(tz="UTC").floor("15min")
        h = stored_headlines(None, None, ["known_at", "origin"])
        last = h.loc[h["origin"] == "gdelt", "known_at"].max() if len(h) else None
        start = now - GDELT_CATCHUP_MAX  # after a long break, catch up at most this far back
        if last is not None and pd.notna(last):
            start = max(start, last + pd.Timedelta(minutes=15))
        raw, n = gdelt.scan(start, "now", workers=8)
        added["gdelt"] = add_headlines(from_gdelt(raw)) if len(raw) else 0
        print(f"gdelt: {n} batches since {start}, {added['gdelt']} new headlines", flush=True)
    if use_rss:
        r = rss.fetch_all()
        added["rss"] = add_headlines(r.drop(columns=["summary"])) if len(r) else 0
        print(f"rss: {added['rss']} new headlines", flush=True)
    added["scored"] = score_new(MOOD_MODEL, probs)
    added["mood_hours"] = build_mood()
    return added


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("import-backfill")
    b.add_argument("--ref", default="origin/gdelt-backfill-data")
    b.add_argument("--dir")
    s = sub.add_parser("score")
    s.add_argument("--model", choices=[*models.MODELS, "all"], default="all")
    s.add_argument("--batch-size", type=int, default=64)
    sub.add_parser("mood")
    c = sub.add_parser("collect")
    c.add_argument("--every", type=float, default=0, help="minutes between runs (0 = run once)")
    c.add_argument("--no-gdelt", action="store_true")
    c.add_argument("--no-rss", action="store_true")
    a = ap.parse_args()

    if a.cmd == "import-backfill":
        import_backfill(a.ref, a.dir)
    elif a.cmd == "score":
        for m in (models.MODELS if a.model == "all" else [a.model]):
            score_new(m, batch_size=a.batch_size)
    elif a.cmd == "mood":
        print(build_mood())
    elif a.cmd == "collect":
        probs = models.load(MOOD_MODEL)  # load once, reuse every run
        while True:
            try:
                print(datetime.now(timezone.utc).isoformat(), collect(probs, not a.no_gdelt, not a.no_rss), flush=True)
            except Exception as e:  # keep the worker alive; the next run retries
                if not a.every:
                    raise
                print("collect failed:", repr(e), flush=True)
            if not a.every:
                break
            time.sleep(a.every * 60)


if __name__ == "__main__":
    main()
