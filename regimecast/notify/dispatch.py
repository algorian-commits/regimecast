"""Telegram delivery for every alert kind.

Every fired alert is already one inbox row (``alert_events``), whoever wrote it. The dispatcher runs every
few seconds and, for each linked and enabled chat, sends the inbox rows written since the last one it handled
(``telegram_links.last_event_id``), so a new alert kind (13D's trader alerts, say) reaches Telegram with no
change here.

* Kinds: the user can choose which kinds go to Telegram (``kinds``; null = all). The inbox always gets all.
* Rate limit: at most MAX_PER_HOUR messages per user per hour (the plan's guard against Telegram's limits);
  past it, alerts stay in the inbox only and the skip is logged.
* Failures: a network error is retried on the next run, up to MAX_TRIES per alert; if the user blocked the
  bot or deleted the chat, the link is removed.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from regimecast.db.schema import alert_events, notify_log, telegram_links
from regimecast.notify import telegram as tg

log = logging.getLogger("notify.dispatch")

MAX_PER_HOUR = 20
MAX_TRIES = 3
BATCH = 50
APP_URL = os.environ.get("APP_URL", "https://web-production-210f7.up.railway.app").rstrip("/")


def format_event(ev) -> str:
    """The Telegram text for one inbox row: who it is from, the title, the message, where to see more."""
    guardian = str(ev["kind"]).startswith("guardian")
    head = "RegimeCast Guardian" if guardian else "RegimeCast alert"
    page = "/guardian" if guardian else "/account"
    return f"{head}\n{ev['title']}\n\n{ev['message']}\n\n{APP_URL}{page}"


def sent_last_hour(conn: sa.Connection, user_id: int, now: datetime) -> int:
    return conn.execute(sa.select(sa.func.count()).select_from(notify_log).where(
        notify_log.c.user_id == user_id, notify_log.c.channel == "telegram", notify_log.c.status == "sent",
        notify_log.c.sent_at > now - timedelta(hours=1))).scalar_one()


def _log(conn, user_id, event_id, status, now, error=None) -> None:
    conn.execute(sa.insert(notify_log).values(user_id=user_id, event_id=event_id, channel="telegram",
                                              status=status, error=(error or None) and error[:200], sent_at=now))


def _tries(conn, event_id) -> int:
    return conn.execute(sa.select(sa.func.count()).select_from(notify_log).where(
        notify_log.c.event_id == event_id, notify_log.c.status == "failed")).scalar_one()


def deliver_user(conn: sa.Connection, client: tg.Client, link, now: datetime) -> int:
    """Send this user's new inbox rows; returns how many were sent."""
    evs = conn.execute(sa.select(alert_events).where(
        alert_events.c.user_id == link["user_id"], alert_events.c.id > link["last_event_id"])
        .order_by(alert_events.c.id).limit(BATCH)).mappings().all()
    kinds = set(link["kinds"]) if link["kinds"] is not None else None
    done, sent = link["last_event_id"], 0
    budget = MAX_PER_HOUR - sent_last_hour(conn, link["user_id"], now)
    for ev in evs:
        if kinds is not None and ev["kind"] not in kinds:
            done = ev["id"]
            continue
        if budget <= 0:
            _log(conn, link["user_id"], ev["id"], "rate_limited", now)
            done = ev["id"]
            continue
        try:
            client.send_message(link["chat_id"], format_event(ev))
        except tg.TelegramError as e:
            if e.chat_gone:
                conn.execute(sa.delete(telegram_links).where(telegram_links.c.user_id == link["user_id"]))
                log.info("telegram chat for user %s is gone; unlinked", link["user_id"])
                return sent
            _log(conn, link["user_id"], ev["id"], "failed", now, str(e))
            if _tries(conn, ev["id"]) < MAX_TRIES:
                break   # try this one again on the next run, before anything newer
            done = ev["id"]
            continue
        _log(conn, link["user_id"], ev["id"], "sent", now)
        sent, budget, done = sent + 1, budget - 1, ev["id"]
    if done != link["last_event_id"]:
        conn.execute(sa.update(telegram_links).where(telegram_links.c.user_id == link["user_id"])
                     .values(last_event_id=done))
    return sent


def run_once(conn: sa.Connection, client: tg.Client | None, now: datetime | None = None) -> int:
    """One delivery round for every linked chat; returns how many messages were sent."""
    if client is None:
        return 0
    now = now or datetime.now(timezone.utc)
    links = conn.execute(sa.select(telegram_links).where(telegram_links.c.enabled.is_(True))).mappings().all()
    return sum(deliver_user(conn, client, ln, now) for ln in links)


def send_direct(conn: sa.Connection, client: tg.Client, user_id: int, text: str,
                now: datetime | None = None) -> bool:
    """Send one message that is not an inbox row (the "send a test message" button). Counts toward the limit."""
    now = now or datetime.now(timezone.utc)
    ln = conn.execute(sa.select(telegram_links).where(telegram_links.c.user_id == user_id)).mappings().first()
    if ln is None or sent_last_hour(conn, user_id, now) >= MAX_PER_HOUR:
        return False
    client.send_message(ln["chat_id"], text)
    _log(conn, user_id, None, "sent", now)
    return True
