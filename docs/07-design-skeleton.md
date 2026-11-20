# DESIGN.md skeleton (copy to the repo root as DESIGN.md; every number must come from YOUR runs)

## 1. Overview
One paragraph and the flow diagram. For each of the three requirements (latest wins / no lost companies / fast),
name the mechanism that delivers it.

## 2. What the feed contains
Table from your profile script: problem, count, handling. Mention anything you found that the docs did not.

## 3. Decisions
| Decision | Choice | Why | Rejected alternative |
Event log + projection; version hash; reject table + ack; ack after commit; per-company lock; batching and embedding
reuse; struck-off semantics; query shape (HNSW + parameter vector); tie-break; error format; embedding model.

## 4. Assumptions
Numbered list (timestamps without zone, epoch ms, identity, null handling, struck-off, state not trusted,
no-description behaviour, queue declaration, timestamp sanity window flagged not dropped, outlier handling, roles via `X-Role`).

## 5. Results (pasted from real runs)
- `just reconcile` output
- test counts, and the mutation checks you did
- `EXPLAIN` snippet and p50/p95/p99, with the machine described
- anything that surprised you

## 6. Deep dive (<= 1 hour): pick one
Recommendation quality (model A/B on bilingual descriptions, metric, table, decision) / latency at scale /
production monitoring / LLM explanations design (no paid APIs).

## 7. 10x volume
Consumer, event-log growth, reads, backfills, monitoring.

## 8. What's left
Honest list: observability, timestamp range checks, re-embed job, diversified ranking, auth, load/chaos tests.

## 9. AI tools used (REQUIRED, must be true)
Which tools, for what (planning, scaffolding, tests, docs, profiling), what you wrote or changed yourself, and what
you reviewed. State that you can explain every file.
