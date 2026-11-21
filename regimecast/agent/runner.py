"""The paper agent's hourly step, run inside the API (like the Track Record logger).

The step itself is ``live.step`` (unchanged). Until 15 Nov 2026 it ran on GitHub Actions, whose hourly schedule fired
only every 6-8 hours, so most hours had no decision. Here a background thread checks once a minute:

* from ``STEP_AFTER`` past the hour it fetches the inputs; while they do not yet cover the hour it waits, and from
  ``STEP_ANYWAY`` it steps regardless (the step then keeps the positions of the coins whose inputs are late, and says so);
* the log line and the new state are saved in one transaction; ``agent_log`` takes one row per hour and refuses any
  change or deletion (migration 0004_agent), so a restart can neither repeat nor rewrite an hour.

On its first run with an empty database it carries on the account GitHub Actions kept: it copies ``state.json`` and
``log.jsonl`` from the ``agent-data`` branch, so the paper account and its history continue unbroken.
AGENT_LIVE=0 switches the thread off.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import sqlalchemy as sa

from regimecast import config, db
from regimecast.agent import live
from regimecast.db.schema import agent_log, agent_state

log = logging.getLogger("agent.runner")

EVERY_SECONDS = 60.0
STEP_AFTER = timedelta(minutes=12)    # the API's signals refresh every 10 minutes
STEP_ANYWAY = timedelta(minutes=40)   # inputs still late by then: step anyway, late coins keep their position
SEED_URL = os.environ.get(
    "AGENT_SEED_URL", "https://raw.githubusercontent.com/algorian-commits/regimecast/agent-data")


def load(engine: sa.Engine) -> tuple[dict | None, list[dict]]:
    with engine.connect() as c:
        row = c.execute(sa.select(agent_state.c.state).where(agent_state.c.id == 1)).first()
        lines = c.execute(sa.select(agent_log.c.line).order_by(agent_log.c.hour)).scalars().all()
    return (json.loads(row[0]) if row else None), [json.loads(x) for x in lines]


def _put_state(conn: sa.Connection, state: dict) -> None:
    now = datetime.now(timezone.utc)
    if conn.execute(sa.select(agent_state.c.id).where(agent_state.c.id == 1)).first():
        conn.execute(agent_state.update().where(agent_state.c.id == 1).values(state=json.dumps(state), updated_at=now))
    else:
        conn.execute(agent_state.insert().values(id=1, state=json.dumps(state), updated_at=now))


def save(engine: sa.Engine, state: dict, line: dict) -> None:
    with engine.begin() as conn:
        conn.execute(agent_log.insert().values(hour=line["hour"], decided_at=line["decided_at"],
                                               saved_at=datetime.now(timezone.utc), line=json.dumps(line)))
        _put_state(conn, state)


def seed(engine: sa.Engine, get=httpx.get) -> bool:
    """Copy the account GitHub Actions kept, if the database has none yet. True if something was copied."""
    if load(engine)[0] is not None:
        return False
    try:
        rs, rl = get(f"{SEED_URL}/state.json", timeout=20), get(f"{SEED_URL}/log.jsonl", timeout=20)
        if rs.status_code != 200 or rl.status_code != 200:
            return False
        state = rs.json()
        lines = [json.loads(x) for x in rl.text.splitlines() if x.strip()]
    except (httpx.HTTPError, ValueError) as e:
        log.warning("agent seed failed: %r", e)
        return False
    with engine.begin() as conn:
        for x in lines:
            conn.execute(agent_log.insert().values(hour=x["hour"], decided_at=x["decided_at"],
                                                   saved_at=datetime.now(timezone.utc), line=json.dumps(x)))
        _put_state(conn, state)
    log.info("agent: carried on the GitHub account, %d hours", len(lines))
    return True


def _inputs_fresh(inputs: dict, hour: pd.Timestamp) -> bool:
    """Every coin's signals include the hour that just ended (the step itself accepts up to 3 hours old)."""
    stamps = [x["stamps"].get(n) for x in inputs.values() for n in ("forecast", "stress", "regime")]
    return all(t and pd.Timestamp(t) >= hour for t in stamps)


def tick(engine: sa.Engine, now: datetime | None = None, fetch=live.fetch_inputs, get=httpx.get) -> dict | None:
    """One check: the new log line if this hour was decided now, else None."""
    now = now or datetime.now(timezone.utc)
    hour = pd.Timestamp(now).floor("1h")
    since = pd.Timestamp(now) - hour
    if since < STEP_AFTER:
        return None
    settings, _ = live.load_frozen()
    state, _ = load(engine)
    if state is None:
        seed(engine, get=get)
        state, _ = load(engine)
    if state is None:
        state = live.new_state(config.SYMBOLS, settings)
    if state["settings"] != settings.to_dict():
        raise RuntimeError("frozen.json changed since the paper log started; start a new log instead of mixing them")
    if state["last_hour"] and pd.Timestamp(state["last_hour"]) >= hour:
        return None
    inputs = fetch(list(state["agent"]["q"]))
    if not _inputs_fresh(inputs, hour) and since < STEP_ANYWAY:
        return None
    state, line = live.step(state, inputs, now, settings)
    if line is not None:
        save(engine, state, line)
    return line


def summary(engine: sa.Engine) -> dict | None:
    """What GET /agent serves as ``live`` (the same shape as agent-data/api/latest.json), or None if nothing is saved."""
    state, lines = load(engine)
    if state is None or not lines:
        return None
    _, frozen = live.load_frozen()
    out = live.summary(state, lines, frozen)
    out["updated_at"] = lines[-1]["decided_at"]
    return out


class Runner:
    def __init__(self, every: float = EVERY_SECONDS):
        self.every = every
        self._thread: threading.Thread | None = None

    def once(self) -> None:
        try:
            eng = db.get_engine()
            if eng is not None:
                line = tick(eng)
                if line:
                    log.info("agent decided %s", line["hour"])
        except Exception as e:   # database or API trouble: try again next minute
            log.warning("agent tick failed: %r", e)

    def start(self) -> None:
        if self._thread or os.environ.get("AGENT_LIVE", "1") == "0":
            return

        def loop():
            time.sleep(30)
            while True:
                t0 = time.monotonic()
                self.once()
                time.sleep(max(1.0, self.every - (time.monotonic() - t0)))

        self._thread = threading.Thread(target=loop, name="agent-live", daemon=True)
        self._thread.start()


runner = Runner()
