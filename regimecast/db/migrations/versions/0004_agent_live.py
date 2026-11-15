"""Paper agent: the hourly log and the account state, kept in the app database.

The agent's hourly step used to run on GitHub Actions, whose scheduler skipped most hours. It now runs in the API
(regimecast/agent/runner.py), like the Track Record logger. ``agent_log`` only takes new rows (one per hour, the
same triggers as the Track Record tables); ``agent_state`` holds the one current account state.

Revision ID: 0004_agent
Revises: 0003_track
"""
from alembic import op
import sqlalchemy as sa

revision = "0004_agent"
down_revision = "0003_track"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)
MESSAGE = "agent log rows are never changed or deleted"


def upgrade():
    op.create_table(
        "agent_log",
        sa.Column("hour", sa.String(40), primary_key=True),
        sa.Column("decided_at", sa.String(40), nullable=False),
        sa.Column("saved_at", TS, nullable=False),
        sa.Column("line", sa.Text, nullable=False),
    )
    op.create_table(
        "agent_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("state", sa.Text, nullable=False),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE OR REPLACE FUNCTION agent_rows_are_permanent() RETURNS trigger AS $$ "
                   f"BEGIN RAISE EXCEPTION '{MESSAGE}'; END; $$ LANGUAGE plpgsql")
        op.execute("CREATE TRIGGER agent_log_keep BEFORE UPDATE OR DELETE ON agent_log "
                   "FOR EACH ROW EXECUTE FUNCTION agent_rows_are_permanent()")
        op.execute("CREATE TRIGGER agent_log_keep_all BEFORE TRUNCATE ON agent_log "
                   "FOR EACH STATEMENT EXECUTE FUNCTION agent_rows_are_permanent()")
    else:
        for verb in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER agent_log_keep_{verb.lower()} BEFORE {verb} ON agent_log "
                       f"BEGIN SELECT RAISE(ABORT, '{MESSAGE}'); END")


def downgrade():
    op.drop_table("agent_state")
    op.drop_table("agent_log")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS agent_rows_are_permanent()")
