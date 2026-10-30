"""Notifications outside the app (Phase 13C): Telegram.

* ``telegram``  the bot client (``TELEGRAM_BOT_TOKEN``) and a fake one for tests.
* ``linking``   "Connect Telegram": a one-time code in a ``t.me/<bot>?start=<code>`` link, and the bot's
                replies to /start, /unlink and /help.
* ``dispatch``  sends every new inbox message (``alert_events``) to the user's linked chat, whatever wrote
                it: Phase 11's alert engine, the Position Guardian, or any later alert kind. A new alert kind
                needs no change here; it only has to write an inbox row.
* ``redact``    keeps the bot token and request signatures out of the logs.

Without ``TELEGRAM_BOT_TOKEN`` nothing is sent and the API says Telegram is not set up.
"""
from regimecast.notify import redact

redact.install()
