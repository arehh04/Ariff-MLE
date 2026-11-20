import pytest

from mle.config import Settings


def test_postgres_port_is_honoured(monkeypatch):
    port = "6543"
    monkeypatch.setenv("POSTGRES_PORT", port)
    assert Settings().database_url == f"postgresql://postgres:postgres@localhost:{port}/companies"


def test_rabbitmq_port_is_honoured(monkeypatch):
    port = "5673"
    monkeypatch.setenv("RABBITMQ_PORT", port)
    assert Settings().rabbitmq_url == f"amqp://guest:guest@localhost:{port}/"


def test_defaults_match_the_compose_file(monkeypatch):
    for name in ("POSTGRES_PORT", "RABBITMQ_PORT", "MLE_DATABASE_URL", "MLE_BATCH_SIZE"):
        monkeypatch.delenv(name, raising=False)
    s = Settings()
    assert (
        s.database_url.endswith("localhost:5432/companies")
        and s.batch_size == 100
        and s.batch_wait_seconds == 0.2
    )
    assert s.embedder == "fastembed" and "multilingual" in s.embedding_model


@pytest.mark.parametrize(("var", "value", "attr", "expected"),
                         [("MLE_BATCH_SIZE", "7", "batch_size", 7), ("MLE_BATCH_WAIT_SECONDS", "0.5", "batch_wait_seconds", 0.5),
                          ("MLE_POOL_MAX", "3", "pool_max", 3), ("MLE_EMBEDDER", "hashing", "embedder", "hashing")])  # fmt: skip
def test_mle_prefixed_overrides(monkeypatch, var, value, attr, expected):
    monkeypatch.setenv(var, value)
    assert getattr(Settings(), attr) == expected


def test_explicit_database_url_wins_over_the_port_variable(monkeypatch):
    monkeypatch.setenv("POSTGRES_PORT", "9999")
    monkeypatch.setenv("MLE_DATABASE_URL", "postgresql://u:p@db:1/x")
    assert Settings().database_url == "postgresql://u:p@db:1/x"
