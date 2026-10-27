"""Alert engine (Phase 11): alert state, the in-app inbox, Whale Watch events.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade():
    with op.batch_alter_table("alerts") as b:
        b.add_column(sa.Column("check_id", sa.Integer, nullable=True))
        b.add_column(sa.Column("armed", sa.Boolean, nullable=True))
        b.add_column(sa.Column("last_value", sa.Float, nullable=True))
        b.add_column(sa.Column("last_checked_at", TS, nullable=True))
        b.add_column(sa.Column("last_fired_at", TS, nullable=True))
        b.add_column(sa.Column("fired_count", sa.Integer, nullable=False, server_default="0"))
        b.create_foreign_key("fk_alerts_check_id", "risk_checks", ["check_id"], ["id"], ondelete="CASCADE")
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("alert_id", sa.Integer, sa.ForeignKey("alerts.id", ondelete="SET NULL"), nullable=True),
        sa.Column("symbol", sa.String(12), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("value", sa.Float, nullable=True),
        sa.Column("detail", sa.JSON, nullable=True),
        sa.Column("fired_at", TS, nullable=False),
        sa.Column("read_at", TS, nullable=True),
    )
    op.create_index("ix_alert_events_user_time", "alert_events", ["user_id", "fired_at"])
    op.create_table(
        "whale_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String(160), nullable=False, unique=True),
        sa.Column("ts", TS, nullable=False),
        sa.Column("symbol", sa.String(12), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("address", sa.String(42), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("detail", sa.JSON, nullable=True),
        sa.Column("created_at", TS, nullable=False),
    )
    op.create_index("ix_whale_events_ts", "whale_events", ["ts"])
    op.create_table(
        "whale_seen",
        sa.Column("address", sa.String(42), primary_key=True),
        sa.Column("first_seen", TS, nullable=False),
    )


def downgrade():
    op.drop_table("whale_seen")
    op.drop_table("whale_events")
    op.drop_table("alert_events")
    with op.batch_alter_table("alerts") as b:
        b.drop_constraint("fk_alerts_check_id", type_="foreignkey")
        for c in ("fired_count", "last_fired_at", "last_checked_at", "last_value", "armed", "check_id"):
            b.drop_column(c)
