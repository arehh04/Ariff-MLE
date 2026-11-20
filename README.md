# OneCredit MLE take-home: company recommendation system

A consumer reads company changes from the RabbitMQ queue `company-events` into Postgres, with a 384-dimension
embedding of each description in pgvector. A FastAPI service returns similar companies (ranked, with card details),
and the similarity search runs inside Postgres. Decisions, assumptions and measured results are in [DESIGN.md](DESIGN.md).

Template reference: https://github.com/OneCreditMY/mle-take-home (assessment `2026.11`).

## Prerequisites
- Docker with Compose v2 (Postgres+pgvector and RabbitMQ run in containers).
- [uv](https://docs.astral.sh/uv/). It installs Python 3.14 and the dependencies for you; no sudo, nothing global.
- Optional: [just](https://just.systems) (`uv tool install rust-just`). Every `just` recipe below is a one-liner you can
  run directly, so it is not required. Optional: `curl` for the examples.
- Internet on the first run: dependencies and the embedding model (about 240 MB, cached afterwards).
- Run everything from the repo root. On Windows use WSL and keep the repo on the Linux filesystem, not `/mnt/c`.

## Run it
```bash
uv sync --locked                       # just setup     install dependencies
docker compose up -d --wait            # just up        Postgres + RabbitMQ, waits until healthy

# terminal 1: the consumer (stays running; Ctrl-C finishes the current batch, then exits)
uv run python -m mle.consumer          # just consume

# terminal 2: replay the registry feed onto the queue
uv run python -m producer.publish      # just publish

# wait until the queue is drained (messages_ready and messages_unacknowledged both 0), then check the database
docker compose exec -T rabbitmq rabbitmqctl list_queues name messages_ready messages_unacknowledged
uv run python -m scripts.reconcile     # just reconcile   exits 0 when the database matches the feed

# the service
uv run uvicorn mle.api:create_app --factory --port 8000    # just serve
```
Stop with `docker compose down`. `docker compose down -v` (`just down`) also **deletes the data**.

Example requests (the default role is `portal`; `analyst` also sees struck-off companies):
```bash
curl -s "localhost:8000/v1/companies/SYN-000101/similar?limit=5"
curl -s -H "X-Role: analyst" "localhost:8000/v1/companies/SYN-000101/similar?limit=5&state=Perak"
curl -s localhost:8000/v1/companies/SYN-000101        # card header: fields plus source and embeddingStatus
curl -si localhost:8000/v1/companies/NOPE/similar     # 404 application/problem+json (RFC 7807)
curl -s localhost:8000/healthz; curl -s localhost:8000/readyz
```
The full contract is in [docs/04-api.md](docs/04-api.md). The API and the consumer both apply the SQL migrations in
`migrations/` on start (idempotent), so there is no separate migration step.

## Tests and checks
```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -q                       # needs Postgres from `docker compose up`; about 3 minutes
uv run pytest tests/unit -q            # no Docker needed
```
Integration tests use a separate database, `companies_test` (created automatically), never the dev database `companies`.
They use real Postgres + pgvector and a fast hashing embedder, so they do not need the model.

Evidence scripts (with the API running where noted):
```bash
uv run python -m scripts.reconcile                 # database vs an independent fold of the feed
uv run python -m scripts.bench                     # latency of /similar + EXPLAIN ANALYZE (API on :8000)
uv run python -m scripts.eval_quality              # deep dive: recommendation quality, English vs Malay
uv run python scripts/eval_embeddings.py           # earlier model comparison on industry labels
uv run python scripts/profile_feed.py              # what is in the feed
```

## Container image
One image runs both processes (non-root, model baked in at build time, about 1.1 GB):
```bash
docker build -t mle:latest .                                    # just build
docker run --rm -p 8000:8000 --network <compose network> \
  -e MLE_DATABASE_URL=postgresql://postgres:postgres@postgres:5432/companies mle:latest
docker run --rm --network <compose network> \
  -e MLE_DATABASE_URL=postgresql://postgres:postgres@postgres:5432/companies \
  -e MLE_RABBITMQ_URL=amqp://guest:guest@rabbitmq:5672/ mle:latest python -m mle.consumer
```
`<compose network>` is `<repo directory name>_default` (see `docker network ls`). CI (`.github/workflows/ci.yml`) runs
ruff and pytest against the same pgvector image as `docker-compose.yml`.

## Configuration (environment variables)
| Variable | Default | Meaning |
|---|---|---|
| `POSTGRES_PORT`, `RABBITMQ_PORT`, `RABBITMQ_UI_PORT` | 5432, 5672, 15672 | host ports for compose (and for the default URLs below) |
| `MLE_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/companies` | Postgres connection |
| `MLE_RABBITMQ_URL` | `amqp://guest:guest@localhost:5672/` | RabbitMQ connection |
| `MLE_EMBEDDER` | `fastembed` | `fastembed` (real model) or `hashing` (offline placeholder, much lower quality) |
| `MLE_EMBEDDING_MODEL` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | any 384-d fastembed model |
| `MLE_BATCH_SIZE` | 100 | messages per Postgres transaction |
| `MLE_BATCH_WAIT_SECONDS` | 0.2 | flush a partial batch after this long |
| `MLE_POOL_MAX` | 10 | API database connections |
| `TEST_DATABASE_URL` | `postgresql://postgres:postgres@localhost:5432/companies_test` | integration tests (must end in `companies_test`) |

The defaults are the docker-compose credentials (synthetic data, no secrets). RabbitMQ UI: http://localhost:15672 (guest/guest).

## Layout
```
mle/             consumer.py (queue -> Postgres), api.py (FastAPI), repository.py (ALL SQL), normalize.py (pure parsing),
                 embedding.py (embedders), db.py (connect + migrations), config.py (settings)
migrations/      001_init.sql: event log, companies projection, reject table, HNSW index
scripts/         reconcile, bench, eval_quality, eval_embeddings, profile_feed
tests/unit/      no Docker; tests/integration/ real Postgres + pgvector
producer/        provided by the assessment (replays data/feed.jsonl); do not modify
docs/ prompts/   requirements and findings, and the working prompts, in order
DESIGN.md        decisions, assumptions, measured results, deep dive, AI-tools disclosure
```
Provided by the assessment and not modified: `producer/`, `data/feed.jsonl`, `docker-compose.yml`, `ASSESSMENT_VERSION`,
`CHANGELOG.md`.
