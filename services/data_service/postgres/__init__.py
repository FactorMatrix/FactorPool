"""PostgreSQL connections are owned by the data service only."""
from contextlib import contextmanager
import os


def connect():
    import psycopg
    return psycopg.connect(host=os.environ.get("FACTORPOOL_DB_HOST", "db"),
                          port=int(os.environ.get("FACTORPOOL_DB_PORT", "5432")),
                          dbname=os.environ.get("FACTORPOOL_DB_NAME", "factorpool"),
                          user=os.environ.get("FACTORPOOL_DB_USER", "factorpool"),
                          password=os.environ["FACTORPOOL_DB_PASSWORD"], connect_timeout=5,
                          options="-c statement_timeout=30000 -c lock_timeout=5000")


@contextmanager
def transaction(connection=None, *, readonly=False):
    owned = connection is None
    conn = connect() if owned else connection
    try:
        with conn.transaction():
            if readonly:
                conn.execute("SET TRANSACTION READ ONLY")
            yield conn
    finally:
        if owned:
            conn.close()
