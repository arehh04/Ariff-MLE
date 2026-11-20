# One image for both processes: the API (default CMD) and the consumer (`python -m mle.consumer`).
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 FASTEMBED_CACHE_PATH=/opt/models

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev

# Bake the embedding model in so the container never downloads at start-up (must match MLE_EMBEDDING_MODEL).
ARG EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='${EMBEDDING_MODEL}')"

COPY mle ./mle
COPY migrations ./migrations
COPY producer ./producer

RUN useradd --system --uid 10001 app && chown -R app /opt/models
USER app

EXPOSE 8000
# python, not curl: the slim image has no curl.
HEALTHCHECK --interval=10s --timeout=3s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["uvicorn", "mle.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
