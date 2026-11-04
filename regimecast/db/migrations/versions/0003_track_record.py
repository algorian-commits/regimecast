"""Track Record: forecasts logged live, before their outcome is known, and the scores added after it.

Both tables only ever take new rows. The triggers make the database itself refuse UPDATE and DELETE (and TRUNCATE on
PostgreSQL), so a forecast cannot be changed once logged, even by a bug in our own code.

Revision ID: 0003_track
Revises: 0002

Named apart from Phase 13C's 0003 on purpose: the two are independent, and db.upgrade() applies every head.
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_track"
down_revision = "0002"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
TABLES = ("track_forecasts", "track_scores")
MESSAGE = "track record rows are never changed or deleted"


def _protect(table: str) -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"CREATE TRIGGER {table}_keep BEFORE UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION track_rows_are_permanent()")
        op.execute(f"CREATE TRIGGER {table}_keep_all BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION track_rows_are_permanent()")
    else:
        for verb in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER {table}_keep_{verb.lower()} BEFORE {verb} ON {table} "
                       f"BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END")


def upgrade():
    op.create_table(
        "track_forecasts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(12), nullable=False),
        sa.Column("horizon_hours", sa.Integer, nullable=False),
        sa.Column("origin_ts", TS, nullable=False),
        sa.Column("target_end", TS, nullable=False),
        sa.Column("logged_at", TS, nullable=False),
        sa.Column("made_at", TS, nullable=False),
        sa.Column("forecast_variance", sa.Float, nullable=False),
        sa.Column("forecast_model", sa.String(80), nullable=False),
        sa.Column("har_s_variance", sa.Float, nullable=True),
        sa.Column("har_s_model", sa.String(80), nullable=True),
        sa.Column("benchmark_variance", sa.Float, nullable=True),
        sa.Column("p_stress", sa.Float, nullable=True),
        sa.Column("stress_base_rate", sa.Float, nullable=True),
        sa.Column("stress_level", sa.String(10), nullable=True),
        sa.Column("stress_model", sa.String(80), nullable=True),
        sa.Column("regime", sa.String(10), nullable=True),
        sa.UniqueConstraint("symbol", "horizon_hours", "origin_ts", name="uq_track_forecast_hour"),
        sa.CheckConstraint("logged_at < target_end", name="ck_track_logged_before_outcome"),
        sa.CheckConstraint("horizon_hours IN (1, 24)", name="ck_track_horizon"),
    )
    op.create_index("ix_track_forecasts_end", "track_forecasts", ["target_end"])
    op.create_table(
        "track_scores",
        sa.Column("forecast_id", sa.Integer, sa.ForeignKey("track_forecasts.id"), primary_key=True),
        sa.Column("scored_at", TS, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("realised_variance", sa.Float, nullable=True),
        sa.Column("qlike_forecast", sa.Float, nullable=True),
        sa.Column("qlike_har_s", sa.Float, nullable=True),
        sa.Column("qlike_benchmark", sa.Float, nullable=True),
        sa.Column("stress_event", sa.Boolean, nullable=True),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE OR REPLACE FUNCTION track_rows_are_permanent() RETURNS trigger AS $$ "
                   f"BEGIN RAISE EXCEPTION '{MESSAGE}'; END; $$ LANGUAGE plpgsql")
    for t in TABLES:
        _protect(t)


def downgrade():
    op.drop_table("track_scores")
    op.drop_table("track_forecasts")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS track_rows_are_permanent()")
