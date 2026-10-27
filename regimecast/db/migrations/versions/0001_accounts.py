"""Accounts (Phase 8): users, sessions, profiles, saved risk checks, alerts.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("paper_balance", sa.Float, nullable=False),
        sa.Column("risk_limit", sa.Float, nullable=False),
        sa.Column("default_margin_mode", sa.String(10), nullable=False),
        sa.Column("favourite_coins", sa.JSON, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
    )
    op.create_table(
        "risk_checks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(12), nullable=False),
        sa.Column("side", sa.String(5), nullable=False),
        sa.Column("text", sa.Text, nullable=True),
        sa.Column("trade", sa.JSON, nullable=False),
        sa.Column("result", sa.JSON, nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_risk_checks_user_time", "risk_checks", ["user_id", "created_at"])
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("symbol", sa.String(12), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("threshold", sa.Float, nullable=True),
        sa.Column("note", sa.String(200), nullable=True),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_alerts_user_id", "alerts", ["user_id"])


def downgrade():
    for t in ("alerts", "risk_checks", "profiles", "sessions", "users"):
        op.drop_table(t)
