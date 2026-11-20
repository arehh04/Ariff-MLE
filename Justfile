set shell := ["bash", "-euo", "pipefail", "-c"]

default: setup

# Install the producer's dependencies.
setup:
    uv sync --locked

# Start Postgres (with pgvector) and RabbitMQ, and wait until both are healthy.
up:
    docker compose up -d --wait

# Stop everything and delete the volumes.
down:
    docker compose down -v

# Replay the registry feed onto the company-events queue.
publish:
    uv run python -m producer.publish

# Run the consumer: queue -> Postgres + pgvector (Ctrl-C finishes the current batch, then exits).
consume:
    uv run python -m mle.consumer

# Run the API on http://localhost:8000
serve:
    uv run uvicorn mle.api:create_app --factory --port 8000

# Diff the database against an independent fold of the feed (exit 1 on mismatch). Run after `just publish`.
reconcile:
    uv run python -m scripts.reconcile

# Build the container image (API and consumer share it).
build:
    docker build -t mle:latest .
