"""Alembic environment: runs on the connection handed over by regimecast.db.upgrade()."""
from alembic import context

conn = context.config.attributes["connection"]
context.configure(connection=conn, render_as_batch=conn.dialect.name == "sqlite")
with context.begin_transaction():
    context.run_migrations()
