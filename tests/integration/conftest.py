"""Integration fixtures: real Postgres + pgvector, always the separate `companies_test` database."""

import hashlib
import json
import os

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from mle import db
from mle.embedding import UnembeddableText
from mle.normalize import Event, parse_message

TEST_DSN = os.environ.get(
    "TEST_DATABASE_URL",
    f"postgresql://postgres:postgres@localhost:{os.environ.get('POSTGRES_PORT', '5432')}/companies_test",
)
TEST_DBNAME = conninfo_to_dict(TEST_DSN)["dbname"]


def service_unavailable(message: str) -> None:
    """Skip when a service is missing on a laptop, but FAIL in CI, where a missing service must not look green."""
    if os.environ.get("CI"):
        pytest.fail(message)
    pytest.skip(message)


class CountingEmbedder:
    """Deterministic 384-d vectors from the text hash; records every text it is asked to embed."""

    def __init__(self, name: str = "fake-1", fail_on: str | None = None) -> None:
        self.name = name
        self.dim = 384
        self.fail_on = fail_on
        self.texts: list[str] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            if text == self.fail_on:
                raise UnembeddableText(f"cannot embed {text!r}")
            self.texts.append(text)
            digest = hashlib.sha256(text.encode()).digest()
            out.append([digest[i % len(digest)] / 255 for i in range(384)])
        return out


def make_event(reg: str, ts: str | None, kind: str = "company-upserted", **data: object) -> Event:
    """Build a normalised Event through the real parser. `data` keys: companyName, natureOfBusiness, state, industry."""
    payload: dict = {"type": kind, "registrationNumber": reg}
    if ts is not None:
        payload["updatedAt"] = ts
    if kind == "company-upserted":
        payload["data"] = data
    event = parse_message(None, json.dumps(payload).encode())
    assert isinstance(event, Event)
    return event


@pytest.fixture(scope="session")
def test_dsn() -> str:
    assert TEST_DBNAME == "companies_test", "integration tests must never touch the dev database"
    try:
        with psycopg.connect(make_conninfo(TEST_DSN, dbname="postgres"), autocommit=True) as admin:
            if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DBNAME,)).fetchone():
                admin.execute(
                    psycopg.sql.SQL("CREATE DATABASE {}").format(psycopg.sql.Identifier(TEST_DBNAME))
                )
    except psycopg.OperationalError as exc:
        service_unavailable(f"Postgres not reachable (run `just up`): {exc}")
    db.migrate(TEST_DSN)
    return TEST_DSN


@pytest.fixture
def conn(test_dsn: str):
    connection = db.connect(test_dsn)
    assert connection.info.dbname == "companies_test"  # TRUNCATE is only ever allowed here
    connection.execute("TRUNCATE company_events, companies, rejected_events RESTART IDENTITY")
    yield connection
    connection.close()


@pytest.fixture
def embedder() -> CountingEmbedder:
    return CountingEmbedder()
