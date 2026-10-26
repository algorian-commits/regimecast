"""python -m regimecast.db upgrade   -> apply all migrations to DATABASE_URL."""
import os
import sys

from regimecast import db

if __name__ == "__main__":
    if sys.argv[1:] != ["upgrade"] or not os.environ.get("DATABASE_URL"):
        sys.exit("usage: DATABASE_URL=... python -m regimecast.db upgrade")
    db.upgrade(db.make_engine(os.environ["DATABASE_URL"]))
    print("database schema is up to date")
