# 01 Setup and the Python 3.14 risk check

Context: `AGENTS.md`. I am in Debian WSL with Docker, in the repo root.

Task:
1. Run `just setup && just up` and show `docker compose ps`. Check RabbitMQ (management API) and Postgres (`select 1`, and
   `select extversion from pg_extension` after `create extension if not exists vector`).
2. Show `uv run python --version`. Then add the dependencies with `uv add`: `fastembed`, `psycopg[binary,pool]`,
   `pgvector`, `aio-pika` (already there), `fastapi`, `uvicorn[standard]`, `pydantic-settings`; and dev: `pytest`,
   `pytest-asyncio`, `httpx`, `ruff`.
3. **Risk check:** confirm `fastembed` installs and imports on Python 3.14, and download `BAAI/bge-small-en-v1.5` once:
   embed two strings and print the vector length (expect 384). If anything fails, STOP and give me options with trade-offs.
4. Add to `pyproject.toml`: pytest config (`pythonpath=["."]`, `testpaths`, `asyncio_mode="auto"`) and ruff config
   (line length 110, exclude `producer`, ignore E501). Do not touch `producer/`.
5. Package layout: top-level `mle/` package, `scripts/`, `tests/unit`, `tests/integration`, `migrations/`.

Acceptance: commands and outputs shown; `uv run pytest -q` runs (even with 0 tests); `uv run ruff check .` is clean.
Commit: `chore: project setup`.
