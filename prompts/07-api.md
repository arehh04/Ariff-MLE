# 07 API

Context: `docs/04-api.md` is the contract. Struck-off visibility is decided: `X-Role` header (`portal` default hides, `analyst` shows).

Task: `mle/api.py` (`create_app(settings)` factory and a `--factory` entry for uvicorn) with a lifespan that applies the schema
and opens a `psycopg_pool`. Endpoints: similar, detail, `/healthz`, `/readyz`. Problem+json handlers for 404, 422 (with field list),
503 (DB outage), 500 (no stack trace). Normalise the registration number in the path. `similar()` in `repository.py`:
fetch the company's vector first, then `ORDER BY embedding <=> %(q)s` with the vector as a **parameter**, `SET LOCAL hnsw.ef_search = 200`,
tie-break same state then registration number, exclude self, hide struck-off unless role is analyst, optional state/industry filters.

Tests (TestClient on a seeded DB): everything in the API section of `docs/06-test-plan.md`.

Then show: `EXPLAIN (ANALYZE)` of the query (first with the vector joined in as a subselect, then as a parameter; tell me what changed),
and curl output for success, 404 and 422.
Acceptance: green tests and the outputs. Commit: `feat: similar companies API`.
