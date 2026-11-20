"""Settings from environment variables (prefix `MLE_`); defaults match docker-compose.yml."""

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_database_url() -> str:
    return f"postgresql://postgres:postgres@localhost:{os.environ.get('POSTGRES_PORT', '5432')}/companies"


def _default_rabbitmq_url() -> str:
    return f"amqp://guest:guest@localhost:{os.environ.get('RABBITMQ_PORT', '5672')}/"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MLE_")

    database_url: str = Field(default_factory=_default_database_url)
    rabbitmq_url: str = Field(default_factory=_default_rabbitmq_url)
    embedder: str = "fastembed"  # "fastembed" (real model) or "hashing" (offline placeholder)
    batch_size: int = 100  # messages per Postgres transaction
    batch_wait_seconds: float = 0.2  # flush a partial batch after this long
    embedding_model: str = "BAAI/bge-small-en-v1.5"  # used by "fastembed"; must output 384 dimensions


@lru_cache
def get_settings() -> Settings:
    return Settings()
