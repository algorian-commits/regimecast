"""Telegram Bot API client: the three calls RegimeCast needs (getMe, sendMessage, getUpdates).

The token comes from ``TELEGRAM_BOT_TOKEN`` (set on Railway by Saeed; never in the repo, a log or a chat).
Errors carry Telegram's error code and description only, never the request URL (it holds the token).
Tests use ``FakeTelegram``, which records what would have been sent.
"""
from __future__ import annotations

import os
import threading
from typing import Protocol

import httpx

API = os.environ.get("TELEGRAM_API_URL", "https://api.telegram.org")
MAX_TEXT = 4096   # Telegram's limit for one message


class TelegramError(Exception):
    def __init__(self, code: int, description: str = ""):
        super().__init__(f"Telegram error {code}: {description[:120]}")
        self.code, self.description = code, description

    @property
    def chat_gone(self) -> bool:
        """The user blocked the bot or deleted the chat: stop sending to it."""
        d = self.description.lower()
        return self.code == 403 or (self.code == 400 and ("chat not found" in d or "user is deactivated" in d))


class Client(Protocol):
    def get_me(self) -> dict: ...
    def send_message(self, chat_id: str, text: str) -> None: ...
    def get_updates(self, offset: int | None, timeout: int) -> list[dict]: ...


class TelegramClient:
    def __init__(self, token: str, api: str = API):
        self._base = f"{api}/bot{token}"
        self._http = httpx.Client(timeout=httpx.Timeout(10.0, read=40.0))
        self._me: dict | None = None

    def _call(self, method: str, payload: dict, timeout: float | None = None) -> object:
        try:
            r = self._http.post(f"{self._base}/{method}", json=payload, timeout=timeout or 10.0)
        except httpx.HTTPError as e:
            raise TelegramError(0, type(e).__name__) from None   # the exception text may hold the URL
        try:
            body = r.json()
        except ValueError:
            raise TelegramError(r.status_code, "not JSON") from None
        if not body.get("ok"):
            raise TelegramError(int(body.get("error_code") or r.status_code), str(body.get("description", "")))
        return body["result"]

    def get_me(self) -> dict:
        if self._me is None:
            self._me = self._call("getMe", {})   # type: ignore[assignment]
        return self._me   # type: ignore[return-value]

    def send_message(self, chat_id: str, text: str) -> None:
        self._call("sendMessage", {"chat_id": chat_id, "text": text[:MAX_TEXT], "disable_web_page_preview": True})

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout=timeout + 10)   # type: ignore[return-value]


class FakeTelegram:
    """Stands in for Telegram in tests: keeps sent messages, hands out queued updates."""

    def __init__(self, username: str = "regimecast_test_bot"):
        self.username = username
        self.sent: list[tuple[str, str]] = []
        self.updates: list[dict] = []
        self.fail: TelegramError | None = None   # raise this on the next sends
        self._next_update = 1

    def get_me(self) -> dict:
        return {"id": 1, "is_bot": True, "username": self.username}

    def send_message(self, chat_id: str, text: str) -> None:
        if self.fail is not None:
            raise self.fail
        self.sent.append((str(chat_id), text))

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        out = [u for u in self.updates if offset is None or u["update_id"] >= offset]
        self.updates = []
        return out

    def user_sends(self, chat_id: int | str, text: str, chat_type: str = "private", username: str = "alice") -> None:
        self.updates.append({"update_id": self._next_update, "message": {
            "message_id": self._next_update, "text": text,
            "chat": {"id": int(chat_id), "type": chat_type, "username": username},
            "from": {"id": int(chat_id), "is_bot": False, "username": username}}})
        self._next_update += 1


_lock = threading.Lock()
_client: Client | None = None
_configured = False


def get_client() -> Client | None:
    """The bot for TELEGRAM_BOT_TOKEN, or None when it is not set."""
    global _client, _configured
    if not _configured:
        with _lock:
            if not _configured:
                token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
                _client = TelegramClient(token) if token else None
                _configured = True
    return _client


def set_client(client: Client | None) -> None:
    """Use a given client (tests: FakeTelegram), or None for "not set up"."""
    global _client, _configured
    _client, _configured = client, True


def bot_username(client: Client) -> str:
    return os.environ.get("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@") or str(client.get_me().get("username", ""))
