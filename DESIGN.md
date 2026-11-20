# DESIGN

> Every number below comes from a command run in this repo; the command is named next to it. Nothing is estimated.

## 1. Overview
The registry publishes company changes to RabbitMQ (at-least-once, out of order, with bad messages). A consumer turns
each message into a normalised event, stores it in an append-only log, and rebuilds the company's current row (a
projection) with its embedding in the same transaction. A FastAPI service reads that table and asks Postgres (pgvector
HNSW index) for the nearest companies.

```
feed -> RabbitMQ[company-events] -> consumer -> Postgres + pgvector <- service (FastAPI) <- portal
                                       |           company_events    append-only log, source of truth
                                       |           companies         current state + embedding (projection)
                                       +-> rejected_events (unattributable messages, raw, counted)
```

| Requirement | Mechanism |
|---|---|
| Latest info wins | Each field comes from the newest event that has it, ordered by the registry's `updatedAt` (then content hash), never by arrival. A newer null cannot erase a value. Shuffled and replayed feeds give an identical database (tests). |
| No lost companies | Every event is kept in the log (key: registration number + content hash, so redeliveries collapse). Unattributable lines go to `rejected_events` and are acked. The consumer acks only after the transaction commits; infrastructure errors are retried, never acked. `scripts.reconcile` proves feed = database. |
| Fast | The service passes the stored vector as a parameter (no re-embedding per request) to an HNSW cosine index. Measured p95 is 5.7 ms for one client, 40.3 ms for eight (section 5). |

## 2. What the feed contains
From `uv run python scripts/profile_feed.py` (2,518 lines) and the reconcile output:

| Problem | Count | Handling |
|---|---|---|
| Truncated JSON | 5 | reject `invalid_json`, raw kept, acked |
| Bare `null` line | 6 | reject `not_an_object` |
| Registration number missing / not a string | 7 / 3 | reject `missing_registration_number` / `invalid_registration_number` |
| Registration number case or spaces (2,049 raw strings -> 2,000 companies) | 49 | `strip().upper()` |
| Second event type `company-struck-off` (no `data`) | 8 | status change, company kept |
| `updatedAt` shapes: ISO offset 2,428, `Z` 28, no zone 14, epoch ms 30, missing 7 | | one parser, stored as UTC; no zone = +08:00; missing = oldest possible time (flagged) |
| `data` missing 13 or a string 4 | 17 | company kept, event carries no fields, warning |
| `source` / `ingestedBy` key | 38 / 37 | unified, audit only |
| `state`: 46 raw spellings -> 16 values; also contradicts descriptions | | canonical casing, stored, never embedded or trusted |
| `natureOfBusiness` absent 43, empty 15 | 58 | never overwrites; falls back to `industry. name`, else no embedding |
| Runaway repeated description (max 3,359 chars) | 7 events | collapsed and capped at 512 chars, warning |
| Companies with more than one distinct payload (real updates) | 126 | latest wins |
| Duplicates: 2,497 events -> 2,434 stored | 63 | collapse on content hash |

Not in `docs/02-feed-findings.md`, or different from it:
- The profile counts 58 absent-or-empty descriptions (docs say 51), and 178 distinct raw descriptions (172 after cleaning).
- The largest group of companies sharing one description is **59** (docs and an old code comment said ~43). I fixed the comment.
- The IQR upper fence of description lengths is 96, but the code caps at 512. No description is between 96 and 512 characters, so both flag the same 5 distinct runaway texts (checked with a one-off script, not kept).
- 68 byte-identical lines = 63 duplicate events + 5 duplicate rejects. This is arithmetic from the numbers above, not a separate check.

## 3. Decisions
| Decision | Choice | Why | Rejected alternative |
|---|---|---|---|
| State model | Append-only `company_events` (key: registration number + content hash) and a `companies` projection | Order independence and replay safety fall out of the model; nothing is ever lost | One row with "update if newer": not order independent once null-merging is added |
| Latest wins | Per field, newest non-null by (`updatedAt` DESC, hash DESC) | Deterministic, ignores arrival order, a newer null cannot erase | Last write wins by arrival |
| Bad data | Store raw in `rejected_events` (identical raws counted in `seen_count`) and ack | The queue has no dead-letter exchange and cannot get one; requeueing a poison message loops forever | Requeue / drop |
| Acks | After the transaction commits; infra errors back off (1 s doubling to 30 s) and are not acked | At-least-once delivery means a crash after commit only causes a harmless duplicate | Ack on receipt |
| Concurrency | `pg_advisory_xact_lock(hashtextextended(reg, 0))` per company, taken in sorted order | Two consumers cannot project from a snapshot that misses the other's uncommitted event; sorted order prevents deadlock | No lock (fails the concurrency test), table lock (serialises everything) |
| Batching | Up to 100 messages or 0.2 s, one transaction | Fewer round trips; each distinct text embedded once per batch | One transaction per message |
| Embedding reuse | Same text and model keeps the stored vector; identical text elsewhere reuses its vector; otherwise embed | The feed has ~172 distinct texts for 2,000 companies | Embed every message |
| Struck-off | Company kept with `status`; default role `portal` hides it (including as the subject, 404), `analyst` sees it; a newer upsert reactivates | Deregistered is not "lost"; role is a stub header, not auth | Delete the row |
| Query shape | Inner `ORDER BY embedding <=> $vector LIMIT pool` (keeps the HNSW index usable), outer sort by distance, same state, registration number; `hnsw.ef_search = 200` | Filters after the index scan would otherwise defeat it; the outer sort makes ties deterministic | Filter and tie-break in one query (planner stops using the index) |
| Errors | RFC 7807 `application/problem+json`, no stack traces | Contract in `docs/04-api.md`; portal branches on `status` | Ad hoc JSON |
| Embedding model | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (384-d, CPU); hashing only for tests and as a fallback | Section 6: the English-only suggestion is worse than hashing on Malay queries | `BAAI/bge-small-en-v1.5` (the suggested default) |
| Model name | Stored on every row | A model swap re-embeds touched companies; mixed models are detectable | Implicit global model |
| SQL | All in `mle/repository.py`, parameterised, no ORM | One place to audit; no string-built SQL | ORM, or Alembic (one idempotent migration file does the job) |
| Image | One image for API and consumer, model baked in at build, non-root | No download at start-up | Two images |

## 4. Assumptions
1. A timestamp without an offset is Malaysia time (+08:00): 2,428 of the 2,500 dated events carry +08:00. Integers are epoch milliseconds.
2. Identity is `strip().upper()` of a string registration number. Integers and empty values are junk and rejected.
3. `data` is a snapshot, but a newer null or empty field never erases an older value (the feed merges several sources).
4. `company-struck-off` means deregistered. The company is kept and its status changes; a later newer upsert reactivates it.
5. `X-Role` (`portal` default, `analyst`) only controls visibility. It is a stub, not authentication; any other value is a 422.
6. `state` is unreliable: stored with canonical casing, never embedded, only used as a tie-break and an optional filter.
7. A company without a description is still stored and fetchable. It gets no embedding when it has neither description nor industry; the API answers 200 with `results: []` and `meta.reason: "no_description"`.
8. The registry owns the queue: it is declared `durable=True` with no other arguments (a mismatch raises `PRECONDITION_FAILED`).
9. Data preparation flags, never drops: descriptions over 512 characters are collapsed and capped with a warning, and years outside 2000-2100 are treated as invalid timestamps with a warning. No company is dropped for being an outlier.
10. A message with no usable `updatedAt` gets the oldest possible time, so any dated event overrides it.
11. When two events carry the same record, the stored `source` is the smaller one (`LEAST`), so it does not depend on arrival order.
12. The data is synthetic (`SYN-` numbers). Tests use a separate database, `companies_test`.

## 5. Results (pasted from real runs)
Machine: AMD Ryzen 7 170 (16 threads), 7.6 GB RAM visible to WSL2 (Linux 6.18), Postgres 16 + pgvector 0.8 and RabbitMQ in Docker
Desktop, one uvicorn process, benchmark client on the same machine. Dev database: 2,000 companies, 1,996 with embeddings.

**Reconcile** (`uv run python -m scripts.reconcile`, exit 0):
```
feed: 2518 lines = 2497 events + 21 unattributable
companies: expected 2000, stored 2000
struck off: expected 8, stored 8
without embedding: expected 4, stored 4
events: expected 2434, stored 2434
rejects: 21 lines in 16 distinct rows; stored 16 rows, seen_count sum 63

OK: the database matches an independent fold of the feed.
```
That is the long-lived dev database, which has had several publishes, hence `seen_count sum 63`. A fresh clone of the repo,
following the README once (publish once, consumer drained), gives the same output with `seen_count sum 21` and exit 0.
The "expected" side is a separate plain-Python fold of the feed (not the production SQL), so the two can disagree.

**Tests and checks:** `uv run ruff check .` clean, `ruff format --check` clean (54 files), `uv run pytest -q`: **638 passed**
in 175.8 s. They include seeded randomised event histories checked against the same independent fold, shuffle and replay
proofs, a threaded concurrency test, fuzzing of the parser, and API matrices (roles, filters, errors, outage).

**Mutation checks** (each: break the code on purpose, see red, restore, see green; run during development in this project):
- Remove the per-company advisory lock: the concurrency tests fail in 3 of 3 runs.
- Make reconcile ignore wrong values: 2 tests fail. Make it ignore undercounted rejects: a test fails.
- Make the English `homestay` pattern greedy again (deep-dive labels): the language-label test fails.
- The randomised oracle found two real defects, both fixed: `source` depended on arrival order, and an industry filter value with
  surrounding whitespace was not stripped before matching. A full-feed test caught a pgvector `Vector` double-wrap that silently turned
  embeddable messages into `processing_error` rejects.

**Latency** (`uv run python -m scripts.bench`, API on :8000, 1,000 random `GET /v1/companies/{reg}/similar?limit=10`, after 50 warm-up):

| client threads | p50 ms | p95 ms | p99 ms | max ms | req/s | errors |
|---|---|---|---|---|---|---|
| 1 | 3.8 | 5.7 | 6.6 | 9.2 | 249 | 0 |
| 8 | 31.7 | 40.3 | 47.2 | 58.7 | 249 | 0 |

The target was p95 < 100 ms; both rows meet it. Throughput does not grow with eight threads (249 req/s in both): one
uvicorn process is the limit, so 8 clients just queue (about 8 x the single-client latency).

**EXPLAIN** (same script, `EXPLAIN (ANALYZE, BUFFERS)` of the exact query, vector literal removed):
```
Limit  (actual time=1.030..1.031 rows=10)
  ->  Incremental Sort  Sort Key: distance, (state = 'Melaka') DESC, registration_number
        ->  Limit  (actual time=0.760..0.930 rows=38)
              ->  Index Scan using companies_embedding_hnsw on companies
                    Order By: (embedding <=> '[...]'::vector)
                    Filter: (embedding IS NOT NULL) AND (registration_number <> 'SYN-000404')
Planning Time: 0.106 ms   Execution Time: 1.999 ms
```
The index is used. Of the 3.8 ms client p50 (single client), about 2 ms is this query's execution time; I did not break the remainder down (HTTP, company lookup, JSON).

**Index accuracy:** for all 1,996 companies, the indexed top-10 equals the exact brute-force top-10 (`enable_indexscan = off`, no
pool limit), both as a set and in order (a one-off script, not kept in the repo).

**Image:** 1.89 GB before, 1.1 GB after removing the uv cache from the layers and the duplicated `chown` layer. The model is
241 MB and the virtualenv 276 MB; I did not shrink those. Container health: `healthy`, `/healthz` and `/readyz` 200.

**Surprises:**
- The index scan returned 38 rows although the pool is 100, yet matched exact search for every company, so the early stop did not change any answer here.
- The English-only model (`bge-small`) scored below the hashing placeholder on Malay queries (section 6).
- Eight threads gave no extra throughput.

## 6. Deep dive (<= 1 hour)
**Recommendation quality: does "similar" find the same business type, in English and in Malay?**

The feed has only 172 distinct descriptions for about 2,000 companies, and every business type appears in English and
Malay (`docs/02-feed-findings.md`, row 16). So an English-only model should under-match Malay. I measured it with
`scripts/eval_quality.py` (`uv run python -m scripts.eval_quality`).

*Method.* 42 business types come from keyword rules (an English and a Malay pattern per type). They use no model and not
the feed's coarse `industry` field; the pattern that matched also gives the language. For each description I rank every
other distinct description by cosine similarity (identical text is never a candidate) and score the top 10. Queries are
weighted by how many companies carry the description. `P@10` is the share of the top 10 with the same type. It cannot
reach 1.0 when a type has fewer than 10 other texts, so `capped` divides by `min(10, same-type texts)` instead. "masked"
replaces city names with `CITY` so a shared word like "Ipoh" gives no free credit.

| model (dim 384) | text | P@10 | capped | English queries | Malay queries |
|---|---|---|---|---|---|
| hashing placeholder | masked | 0.157 | 0.279 | 0.267 | 0.298 |
| `BAAI/bge-small-en-v1.5` | masked | 0.185 | 0.347 | 0.410 | 0.245 |
| `paraphrase-multilingual-MiniLM-L12-v2` | masked | 0.275 | **0.990** | 0.994 | 0.983 |

Unmasked (raw) text, capped / English / Malay: hashing 0.247 / 0.235 / 0.267, bge-small 0.353 / 0.426 / 0.234,
multilingual 0.987 / 0.992 / 0.979. Masking changes no conclusion.

*Reading it.* The English-only model is no better than the hashing baseline on Malay queries (0.245 vs 0.298) and finds
about 0.41 of the English ones. The multilingual model is near the ceiling for both languages. Its weakest queries are
the Malay parcel-delivery texts (capped 0.70), because that type has 17 same-type texts and 7 of the top 10 matched.
Recommendation: keep the multilingual model as the default (it also fits the 384-d `vector(384)` column).

*What this does not show.* 24 of the 42 types have exactly one same-type partner (the other-language twin) and 11
English-only types have none and are excluded, so the capped score is mostly "does the model find the translation".
The corpus is 172 short, template-like texts, so real descriptions will be harder. The labels were written by reading
these same descriptions, and business type is a proxy for "a human finds this similar". The metric also ignores latency,
index size, struck-off companies and companies with no description.

## 7. 10x volume
Reasoning from the measurements above; I did not run a 10x test.
- **Consumer.** Work per message is mostly one batched transaction, and embedding cost scales with *distinct texts* (~172 now), not companies, because of the reuse rule. More consumers are safe because of the per-company advisory lock (the concurrency test fails without it); they only contend on the same company. Batch size and wait are settings.
- **Event log.** 2,434 events for 2,000 companies today (about 1.2 each). Growth is linear in the number of real changes. The projection only reads the events of the companies in the batch. Later: partition or archive old versions.
- **Reads.** One uvicorn process serves about 249 req/s here, and eight clients just queue. Scale with several workers or replicas behind a pooler (not measured). An HNSW index over ~20,000 rows is still a small index.
- **A real limit I can see.** If the same text mix scaled 10x, the largest group of identical descriptions would be about 590 companies. That is more than `hnsw.ef_search = 200`, so the same-state tie-break would only see part of the group. Fix: rank one vector per distinct text and expand to companies, or raise `ef_search` and the pool.
- **Backfills and model changes.** The model name is stored per row, so a re-embed job can find stale rows. The job is not built (section 8).
- **Monitoring.** Queue depth and consumer lag, reject rate and `seen_count`, batch time, API p95 and 5xx rate, count of companies without embeddings.

## 8. What's left
- Observability: metrics, tracing and alerts (only log lines exist now).
- Re-embed job for a model change (the schema supports it; nothing runs it).
- Optional GPU extra for a larger model: not set up and untested; `scripts/eval_embeddings.py --cuda` is untested.
- Authentication and rate limiting; `X-Role` is a visibility stub.
- Diversified ranking (many near-identical companies currently fill the top 10).
- Load and chaos tests: no 10x run, no broker or database kill during a long run; consumer shutdown during a real database outage is only unit tested.
- Fixing the tie-group limit in section 7.
- Test suite speed (about 3 minutes); image size (1.1 GB, mostly the model and the virtualenv); `uv:latest` is not pinned in the Dockerfile.
- `AGENTS.md` and some `docs/` still name `bge-small` as the default; the code default is the multilingual model (section 6).
- Quality evidence is on 172 short template-like texts with business-type labels I wrote; no human relevance judgement.

## 9. AI tools used
- **Tools.** Claude Code (model Sonnet 5.5) in this repo, working prompt by prompt from `prompts/00` to `12`, with the `ponytail` and `superpowers` plugins and some skills from the `mattpocock` collection that I had it install (for a question-and-answer session to check my understanding). The `AGENTS.md` rules (honesty, small steps, ask before decisions) applied to it throughout.
- **What the assistant did.** Planning each step, writing the code in `mle/`, `scripts/` and `tests/`, the `Dockerfile` and CI workflow, running profiles, evals, benchmarks and the test suite, debugging, two review passes, and drafting this file and the README from those runs.
- **What I did.** I made the design decisions listed in `AGENTS.md` (embedding model, struck-off meaning, null handling, timezone, deep-dive topic). I set up the prompts, docs and `AGENTS.md` for the assistant (the template files come from the assessment). I did not write the modules in `mle/`, `scripts/` or `tests/` from scratch; I made some small hand edits and fixes that I did not track individually.
- **What I reviewed.** I read the code and can explain every file. My changes mostly went through the assistant as requests rather than hand edits.
- **Data.** The feed is synthetic. The assistant, like any session in Claude Code, saw feed excerpts and outputs while working. The embedding model runs locally on CPU, and no paid embedding or LLM API is used by the product.
- **Check on the numbers.** Every measurement above was produced by a command run in the assistant's session; the commands are in the README so they can be re-run.
