"""Linking a Telegram chat to a RegimeCast account, and the bot's replies.

1. The logged-in user presses "Connect Telegram". The API makes a random one-time code (128 bits), keeps only
   HMAC(SESSION_SECRET, code) with a 10-minute expiry, and answers ``https://t.me/<bot>?start=<code>``.
2. The user opens the link and taps Start; Telegram sends the bot ``/start <code>``.
3. The bot (``Poller``, long-polling getUpdates) finds the code, links that private chat to the account,
   deletes the code (it works once) and replies. A chat belongs to one account; linking it again moves it.

``/unlink`` (or ``/stop``) in the chat, or "Unlink" in the app, removes the link at once.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import sqlalchemy as sa

from regimecast.db.schema import alert_events, telegram_codes, telegram_links
from regimecast.notify import telegram as tg

log = logging.getLogger("notify.linking")

CODE_MINUTES = 10
_CODE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")   # what Telegram allows in a start parameter

HELP = ("This is the RegimeCast alert bot. It sends your RegimeCast alerts here: Position Guardian warnings "
        "about your open positions, and the alerts you set in the app.\n\n"
        "To connect: open RegimeCast, go to Guardian and press \"Connect Telegram\".\n"
        "/unlink stops all messages to this chat.")


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def code_hash(code: str) -> str:
    from regimecast.api.accounts import session_secret   # the same server secret as the session cookies
    return hmac.new(session_secret(), b"telegram-link:" + code.encode(), hashlib.sha256).hexdigest()


def new_code(conn: sa.Connection, user_id: int, now: datetime) -> tuple[str, datetime]:
    """A fresh one-time code for this user (older unused codes stop working)."""
    conn.execute(sa.delete(telegram_codes).where(
        sa.or_(telegram_codes.c.user_id == user_id, telegram_codes.c.expires_at < now)))
    code = secrets.token_urlsafe(16)
    expires = now + timedelta(minutes=CODE_MINUTES)
    conn.execute(sa.insert(telegram_codes).values(code_hash=code_hash(code), user_id=user_id, expires_at=expires))
    return code, expires


def link(conn: sa.Connection, user_id: int, chat_id: str, username: str | None, now: datetime) -> None:
    """Link the chat to the user. Alerts already in the inbox are not sent; only new ones."""
    conn.execute(sa.delete(telegram_links).where(
        sa.or_(telegram_links.c.user_id == user_id, telegram_links.c.chat_id == chat_id)))
    last = conn.execute(sa.select(sa.func.max(alert_events.c.id)).where(alert_events.c.user_id == user_id)).scalar()
    conn.execute(sa.insert(telegram_links).values(user_id=user_id, chat_id=chat_id, username=(username or None),
                                                  enabled=True, kinds=None, last_event_id=int(last or 0),
                                                  linked_at=now))


def unlink_chat(conn: sa.Connection, chat_id: str) -> bool:
    return bool(conn.execute(sa.delete(telegram_links).where(telegram_links.c.chat_id == chat_id)).rowcount)


def redeem(conn: sa.Connection, code: str, chat_id: str, username: str | None, now: datetime) -> int | None:
    """The user id the code belonged to (now linked), or None if the code is unknown, used or expired."""
    if not _CODE.match(code):
        return None
    h = code_hash(code)
    row = conn.execute(sa.select(telegram_codes).where(telegram_codes.c.code_hash == h)).mappings().first()
    if row is None:
        return None
    conn.execute(sa.delete(telegram_codes).where(telegram_codes.c.code_hash == h))   # works once, either way
    if _utc(row["expires_at"]) <= now:
        return None
    link(conn, row["user_id"], chat_id, username, now)
    return row["user_id"]


def handle_update(conn: sa.Connection, client: tg.Client, update: dict, now: datetime) -> str | None:
    """React to one message sent to the bot. Returns what happened (for tests and the log)."""
    msg = update.get("message") or {}
    chat, text = msg.get("chat") or {}, (msg.get("text") or "").strip()
    if chat.get("type") != "private" or not text.startswith("/"):
        return None   # groups and plain chatter are ignored
    chat_id = str(chat["id"])
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@", 1)[0].lower()
    if cmd == "/start" and arg.strip():
        uid = redeem(conn, arg.strip(), chat_id, chat.get("username"), now)
        if uid is None:
            client.send_message(chat_id, "That link has expired or was already used. In RegimeCast, go to "
                                         "Guardian and press \"Connect Telegram\" again for a new one.")
            return "bad_code"
        client.send_message(chat_id, "Connected. RegimeCast will send your alerts here, including Position "
                                     "Guardian warnings with a safer version of the position.\n"
                                     "/unlink stops all messages to this chat.")
        return "linked"
    if cmd in ("/unlink", "/stop"):
        gone = unlink_chat(conn, chat_id)
        client.send_message(chat_id, "Unlinked. RegimeCast will not message this chat any more." if gone
                            else "This chat is not linked to a RegimeCast account.")
        return "unlinked" if gone else "not_linked"
    client.send_message(chat_id, HELP)
    return "help"


class Poller:
    """Reads the bot's messages with long polling (no public webhook needed). One per bot token: run it in
    one process only (Railway runs one API replica); TELEGRAM_POLL=0 switches it off."""

    def __init__(self, client_factory: Callable[[], tg.Client | None] = tg.get_client, timeout: int = 25):
        self.client_factory, self.timeout = client_factory, timeout
        self.offset: int | None = None
        self.last_error: str | None = None
        self.handled = 0
        self._thread: threading.Thread | None = None

    def poll_once(self, client: tg.Client, engine: sa.Engine, timeout: int | None = None) -> int:
        updates = client.get_updates(self.offset, self.timeout if timeout is None else timeout)
        for u in updates:
            self.offset = int(u["update_id"]) + 1
            try:
                with engine.begin() as conn:
                    handle_update(conn, client, u, datetime.now(timezone.utc))
                self.handled += 1
            except Exception as e:   # one bad message must not stop the bot
                log.warning("telegram update failed: %s", type(e).__name__)
        return len(updates)

    def start(self) -> None:
        if self._thread is not None or os.environ.get("TELEGRAM_POLL", "1") == "0":
            return
        from regimecast import db

        def loop():
            while True:
                client, eng = self.client_factory(), db.get_engine()
                if client is None or eng is None:
                    time.sleep(60)
                    continue
                try:
                    self.poll_once(client, eng)
                    self.last_error = None
                except Exception as e:
                    self.last_error = type(e).__name__
                    log.warning("telegram polling failed: %s", e if isinstance(e, tg.TelegramError) else type(e).__name__)
                    time.sleep(10)

        self._thread = threading.Thread(target=loop, name="telegram-poller", daemon=True)
        self._thread.start()


poller = Poller()
