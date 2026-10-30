"""Position Guardian and Telegram (Phase 13C): linked chats, one-time link codes, the delivery log,
position sources and watched positions.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade():
    op.create_table(
        "telegram_links",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("chat_id", sa.String(32), nullable=False, unique=True),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("enabled", sa.Boolean, nullable=False),
        sa.Column("kinds", sa.JSON, nullable=True),
        sa.Column("last_event_id", sa.Integer, nullable=False, server_default="0"),
        sa.Column("linked_at", TS, nullable=False),
    )
    op.create_table(
        "telegram_codes",
        sa.Column("code_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", TS, nullable=False),
    )
    op.create_index("ix_telegram_codes_user_id", "telegram_codes", ["user_id"])
    op.create_table(
        "notify_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_id", sa.Integer, sa.ForeignKey("alert_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column("sent_at", TS, nullable=False),
    )
    op.create_index("ix_notify_log_user_time", "notify_log", ["user_id", "sent_at"])
    op.create_table(
        "guardian_sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("label", sa.String(60), nullable=True),
        sa.Column("address", sa.String(42), nullable=True),
        sa.Column("key_enc", sa.Text, nullable=True),
        sa.Column("secret_enc", sa.Text, nullable=True),
        sa.Column("permissions", sa.JSON, nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_error", sa.String(200), nullable=True),
        sa.Column("last_polled_at", TS, nullable=True),
        sa.Column("permissions_checked_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_guardian_sources_user_id", "guardian_sources", ["user_id"])
    op.create_table(
        "guardian_positions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("guardian_sources.id", ondelete="CASCADE"), nullable=True),
        sa.Column("check_id", sa.Integer, sa.ForeignKey("risk_checks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("symbol", sa.String(12), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("entry", sa.Float, nullable=False),
        sa.Column("qty", sa.Float, nullable=False),
        sa.Column("leverage", sa.Float, nullable=True),
        sa.Column("margin_mode", sa.String(10), nullable=False),
        sa.Column("wallet_balance", sa.Float, nullable=True),
        sa.Column("stop", sa.Float, nullable=True),
        sa.Column("liquidation_price", sa.Float, nullable=True),
        sa.Column("mark_price", sa.Float, nullable=True),
        sa.Column("muted", sa.Boolean, nullable=False),
        sa.Column("risk_limit", sa.Float, nullable=True),
        sa.Column("armed", sa.JSON, nullable=True),
        sa.Column("last", sa.JSON, nullable=True),
        sa.Column("checked_at", TS, nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.UniqueConstraint("source_id", "symbol", "side", name="uq_guardian_positions_source_symbol_side"),
    )
    op.create_index("ix_guardian_positions_user_id", "guardian_positions", ["user_id"])


def downgrade():
    for t in ("guardian_positions", "guardian_sources", "notify_log", "telegram_codes", "telegram_links"):
        op.drop_table(t)
