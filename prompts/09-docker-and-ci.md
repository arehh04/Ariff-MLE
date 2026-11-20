# 09 (optional) Docker and CI

Task: a `Dockerfile` for service and consumer (python slim, uv, `uv sync --locked --no-dev`, model baked in at build time,
non-root user, HEALTHCHECK on `/healthz`; consumer = same image with `python -m mle.consumer`), `.dockerignore`, and
`.github/workflows/ci.yml` (Postgres service using the same pgvector image as `docker-compose.yml`, `uv sync --locked`, ruff
check + format check, pytest; tests use a `companies_test` database via an env var). Add `just build`.

Acceptance: `docker build` succeeds and `docker run` serves `/healthz` (show it); push and show the Actions run is green.
Commit: `chore: container image and CI`.
