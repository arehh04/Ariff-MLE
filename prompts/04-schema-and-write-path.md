# 04 Schema, projection and write path

Context: `docs/03-architecture.md` (core design 2-9), `docs/05-schema.sql` (reference only; adapt it and explain each column),
`docs/06-test-plan.md` (integration). `AGENTS.md`: the reserved decisions are made (see "Decisions made"); record them in the docstring.

Task:
1. `migrations/001_init.sql` (idempotent) and `mle/db.py` (apply migrations under an advisory lock so consumer and service can start together; connect with the pgvector type registered).
2. `mle/repository.py`: `process_batch(conn, embedder, messages) -> BatchResult` in ONE transaction:
   rejects -> `rejected_events` (identical raw counted); events -> `company_events ON CONFLICT DO NOTHING`;
   per-company advisory locks in sorted order; projection query (newest non-null per field; status rule); decide what needs embedding
   (hash + model unchanged => keep; reuse a stored vector for identical text; embed each distinct text once); upsert `companies`.
   Add `process_batch_isolated` (data-level DB errors retried per message and rejected as `processing_error`; infrastructure errors propagate).
3. Integration tests (real Postgres, separate `companies_test` database, truncate between tests, fake counting embedder):
   items 1, 5, 6, 7, 8, 9 of `docs/06-test-plan.md`. Prove at least two tests by deliberately breaking the code once.

Constraints: parameterised SQL only; no ORM; all SQL in `repository.py`.
Acceptance: all tests green, shown; explain the projection query line by line; list what is not covered yet (replay/shuffle/concurrency come in 08).
Commit: `feat: event log, projection and write path`.
