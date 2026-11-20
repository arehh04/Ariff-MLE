# Architecture and decisions

```
feed -> RabbitMQ[company-events] -> consumer -> Postgres + pgvector <- service (FastAPI) <- portal
                                       |           company_events (append-only log, source of truth)
                                       |           companies      (current state + embedding, a projection)
                                       +-> rejected_events (unattributable messages, raw, counted)
```

## Stack
Python 3.14 + uv; `aio-pika` (same lib as the producer); `psycopg[binary,pool]` v3 + `pgvector`; FastAPI + pydantic;
`fastembed` (ONNX, CPU) with `BAAI/bge-small-en-v1.5` (384d) as suggested, a hashing bag-of-words placeholder for
tests/offline (allowed by the brief if documented); pytest + httpx; ruff.

## Core design (the part to understand deeply)
1. **Parse and normalise** each message in a pure function that returns an `Event` or a `Rejected(reason, raw)`.
2. **Append-only event log.** Primary key `(registration_number, version_hash)`, where `version_hash` = sha256 of the
   normalised content (kind, UTC time, fields). Redeliveries, replays and the same record in a different timestamp
   format all collapse onto one row. Keep `message_id` only for audit.
3. **`companies` is a projection** of the log. For each field, take the value from the newest event that carries it
   (order by `source_updated_at DESC, version_hash DESC`, filter nulls). In SQL: `(array_agg(col ORDER BY ... ) FILTER
   (WHERE col IS NOT NULL))[1]`. This makes the result independent of arrival order and means a newer null cannot erase a value.
4. **Status** comes from the newest event that is a struck-off or carries data. A data-less upsert changes nothing. A newer upsert reactivates a struck-off company.
5. **Embedding text** = cleaned description; fallback `industry. name`; else none. Store the text hash and the model
   name on the row: unchanged text and model => keep the vector; identical text elsewhere => reuse the stored vector;
   otherwise embed (each distinct text once per batch). A different model name re-embeds touched companies.
6. **Batching.** Collect up to 100 messages or 200 ms, then ONE transaction: reject table writes, event inserts
   (`ON CONFLICT DO NOTHING`), per-company advisory locks, projection, upsert. **Ack after commit.**
7. **Concurrency.** With several consumers, take `pg_advisory_xact_lock(hashtextextended(reg, 0))` for the batch's
   companies **in sorted order** before inserting/projecting; otherwise each consumer can project without seeing the
   other's uncommitted event and the last commit wins with an incomplete picture. (Write a threaded test; it fails
   without the lock.)
8. **Failure policy.** Bad data -> `rejected_events` (identical raws counted in `seen_count`) and ack. Infrastructure
   error (DB/model down) -> do not ack, back off, reconnect, retry. A data-level DB error in a batch -> retry message by
   message and reject only the offender (`processing_error`).
9. **Postgres text cannot contain NUL (`\x00`)**: strip it in cleaning.
10. **Data preparation (flag, don't drop).** In the pure normaliser: description length cap from the IQR upper fence, a sane window
    for `updatedAt`, state/text normalisation. Out-of-range values add a `warnings` entry on the event; they never remove a company.

## Service
- Fetch the company's own embedding first, then run `ORDER BY embedding <=> %(vector)s LIMIT k` with the vector as a
  **parameter**. A join/subselect for the vector makes the planner skip the HNSW index (measure it with `EXPLAIN`).
- `SET LOCAL hnsw.ef_search = 200` inside the request transaction so filtered queries still fill `limit`.
- Tie-break equal distances (the feed has many identical descriptions): same `state` first, then registration number.
- Hide struck-off companies unless the caller's role is `analyst` (`X-Role` header, default `portal`). See `04-api.md`.
- `/healthz` (process) and `/readyz` (DB reachable).

## Embedding choice
Decided: default `bge-small-en-v1.5` (lightweight), hashing embedder for unit tests/offline; swap later by model name. The data is half Malay, so the deep dive compares it with a multilingual
384-d model (list candidates: `TextEmbedding.list_supported_models()`; the schema fixes `vector(384)`). Store the model name per row.

## 10x volume (talking points)
Competing consumers (safe because of the log + lock); bigger batches; embedding reuse by hash; partition/compact the
event log; HNSW tuning (`m`, `ef_construction`, `ef_search`), `halfvec`, read replicas, pgvector 0.8 iterative scans;
background re-embed into a second column when the model changes; monitoring: queue depth/age, lag, reject rate by reason,
stale rate, embed latency, API p95, % companies without embedding.

## Mapping to the job post
FastAPI, RabbitMQ, Postgres, Docker, Git, CI/CD (GitHub Actions), monitoring and evaluation mindset (the deep dive),
production concerns (idempotency, retries, health probes). Mention them in DESIGN.md only where you actually did them.
