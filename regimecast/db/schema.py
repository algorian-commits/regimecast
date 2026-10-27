"""App database tables (M6): users, sessions, profiles, saved risk checks, alerts.

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
)
