"""Database connection and migrations. Config comes from the environment, defaulting to the compose values."""

from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

from mle.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_MIGRATION_LOCK = 7_243_001  # arbitrary constant: every process that migrates must use the same key


def dsn() -> str:
    return get_settings().database_url


def migrate(conninfo: str | None = None) -> list[str]:
    """Apply every migrations/*.sql in name order. Files are idempotent, so running them on every start is safe.

    A session-level advisory lock makes the consumer and the service safe to start together: the second one waits,
    then re-runs the same no-op statements.
    """
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    with psycopg.connect(conninfo or dsn(), autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK,))
        try:
            for file in files:
                conn.execute(
                    file.read_text(encoding="utf-8")
                )  # no params => multi-statement script is allowed
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK,))
    return [f.name for f in files]


def connect(conninfo: str | None = None) -> psycopg.Connection:
    """Autocommit connection with the pgvector type registered. Call `migrate()` first (it creates the extension).

    Autocommit means each `with conn.transaction():` block is a real transaction, which is what the write path wants.
    """
    conn = psycopg.connect(conninfo or dsn(), autocommit=True)
    register_vector(conn)
    return conn
