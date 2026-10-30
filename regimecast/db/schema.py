"""App database tables (M6, M12, 13C): users, sessions, profiles, saved risk checks, alerts, the alert
inbox, Whale Watch events, linked Telegram chats and the Position Guardian's sources and positions.

Defined once with SQLAlchemy Core so the same code runs on PostgreSQL (Railway) and on
SQLite (tests, local dev without a database server). The tables are created and changed
only by the migrations in regimecast/db/migrations/versions/, never by create_all.
"""
from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

users = sa.Table(
    "users", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("email", sa.String(254), nullable=False, unique=True),   # stored lower-case
    sa.Column("password_hash", sa.String(255), nullable=False),        # argon2id, never the password
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

sessions = sa.Table(
    "sessions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("token_hash", sa.String(64), nullable=False, unique=True),  # HMAC of the cookie value
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
)

profiles = sa.Table(
    "profiles", metadata,
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("paper_balance", sa.Float, nullable=False),
    sa.Column("risk_limit", sa.Float, nullable=False),
    sa.Column("default_margin_mode", sa.String(10), nullable=False),
    sa.Column("favourite_coins", sa.JSON, nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

risk_checks = sa.Table(
    "risk_checks", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("symbol", sa.String(12), nullable=False),
    sa.Column("side", sa.String(5), nullable=False),
    sa.Column("text", sa.Text, nullable=True),        # the trade as typed (Phase 7 parser), if any
    sa.Column("trade", sa.JSON, nullable=False),      # what was sent to /risk-check
    sa.Column("result", sa.JSON, nullable=False),     # what /risk-check answered
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_risk_checks_user_time", "user_id", "created_at"),
)

alerts = sa.Table(
    "alerts", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("symbol", sa.String(12), nullable=False),
    sa.Column("kind", sa.String(20), nullable=False),
    sa.Column("threshold", sa.Float, nullable=True),
    sa.Column("note", sa.String(200), nullable=True),
    sa.Column("active", sa.Boolean, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    # Phase 11 alert engine (migration 0002)
    sa.Column("check_id", sa.Integer, sa.ForeignKey("risk_checks.id", ondelete="CASCADE", name="fk_alerts_check_id"),
              nullable=True),                          # position_risk: the saved check that holds the position
    sa.Column("armed", sa.Boolean, nullable=True),     # None = not checked yet; False = fired, waiting to clear
    sa.Column("last_value", sa.Float, nullable=True),
    sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("last_fired_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("fired_count", sa.Integer, nullable=False, server_default="0"),
)

alert_events = sa.Table(   # the in-app inbox: one row per fired alert
    "alert_events", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("alert_id", sa.Integer, sa.ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True),
    sa.Column("symbol", sa.String(12), nullable=False),
    sa.Column("kind", sa.String(20), nullable=False),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("value", sa.Float, nullable=True),
    sa.Column("detail", sa.JSON, nullable=True),
    sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_alert_events_user_time", "user_id", "fired_at"),
)

whale_events = sa.Table(   # Whale Watch: public, not per user
    "whale_events", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("key", sa.String(160), nullable=False, unique=True),   # the same event is stored once
    sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
    sa.Column("symbol", sa.String(12), nullable=False),
    sa.Column("kind", sa.String(20), nullable=False),
    sa.Column("address", sa.String(42), nullable=False),
    sa.Column("title", sa.String(200), nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("detail", sa.JSON, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_whale_events_ts", "ts"),
)

whale_seen = sa.Table(   # every Hyperliquid account Whale Watch has seen (for "fresh wallet")
    "whale_seen", metadata,
    sa.Column("address", sa.String(42), primary_key=True),
    sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
)

# ------------------------------------------------------------------ Phase 13C: Telegram and the Position Guardian
telegram_links = sa.Table(   # a user's linked Telegram chat (one per user, one user per chat)
    "telegram_links", metadata,
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    sa.Column("chat_id", sa.String(32), nullable=False, unique=True),
    sa.Column("username", sa.String(64), nullable=True),
    sa.Column("enabled", sa.Boolean, nullable=False),
    sa.Column("kinds", sa.JSON, nullable=True),          # alert kinds sent to Telegram; null = every kind
    sa.Column("last_event_id", sa.Integer, nullable=False, server_default="0"),   # inbox rows already handled
    sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
)

telegram_codes = sa.Table(   # one-time codes in the t.me/<bot>?start=<code> link
    "telegram_codes", metadata,
    sa.Column("code_hash", sa.String(64), primary_key=True),   # HMAC of the code, never the code
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
)

notify_log = sa.Table(   # every delivery attempt outside the app (Telegram): the rate limit and the record
    "notify_log", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    sa.Column("event_id", sa.Integer, sa.ForeignKey("alert_events.id", ondelete="SET NULL"), nullable=True),
    sa.Column("channel", sa.String(16), nullable=False),
    sa.Column("status", sa.String(16), nullable=False),   # sent, failed, rate_limited
    sa.Column("error", sa.String(200), nullable=True),
    sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_notify_log_user_time", "user_id", "sent_at"),
)

guardian_sources = sa.Table(   # where a user's open positions come from: a Hyperliquid address or a Binance read-only key
    "guardian_sources", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("kind", sa.String(16), nullable=False),     # hyperliquid, binance
    sa.Column("label", sa.String(60), nullable=True),
    sa.Column("address", sa.String(42), nullable=True),   # hyperliquid: the public wallet address
    sa.Column("key_enc", sa.Text, nullable=True),         # binance: Fernet(GUARDIAN_SECRET) tokens, never plain
    sa.Column("secret_enc", sa.Text, nullable=True),
    sa.Column("permissions", sa.JSON, nullable=True),     # binance: what the key may do, as last checked
    sa.Column("status", sa.String(16), nullable=False),   # ok, error
    sa.Column("last_error", sa.String(200), nullable=True),
    sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("permissions_checked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

guardian_positions = sa.Table(   # watched open positions: from a source (kept in step with it) or entered by hand
    "guardian_positions", metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    sa.Column("source_id", sa.Integer, sa.ForeignKey("guardian_sources.id", ondelete="CASCADE"), nullable=True),
    sa.Column("check_id", sa.Integer, sa.ForeignKey("risk_checks.id", ondelete="SET NULL"), nullable=True),
    sa.Column("symbol", sa.String(12), nullable=False),   # BTCUSDT, ETHUSDT, SOLUSDT; other coins as the exchange names them
    sa.Column("side", sa.String(5), nullable=False),
    sa.Column("entry", sa.Float, nullable=False),
    sa.Column("qty", sa.Float, nullable=False),           # coins, always positive
    sa.Column("leverage", sa.Float, nullable=True),
    sa.Column("margin_mode", sa.String(10), nullable=False),
    sa.Column("wallet_balance", sa.Float, nullable=True),  # manual cross positions
    sa.Column("stop", sa.Float, nullable=True),
    sa.Column("liquidation_price", sa.Float, nullable=True),   # as the exchange reports it (sources)
    sa.Column("mark_price", sa.Float, nullable=True),          # the exchange's mark price at the last poll
    sa.Column("muted", sa.Boolean, nullable=False),
    sa.Column("risk_limit", sa.Float, nullable=True),     # null = the profile's risk limit
    sa.Column("armed", sa.JSON, nullable=True),           # per rule: true / false / null (alerts/rules.step)
    sa.Column("last", sa.JSON, nullable=True),            # the latest risk read-out and safer version
    sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("source_id", "symbol", "side", name="uq_guardian_positions_source_symbol_side"),
)
