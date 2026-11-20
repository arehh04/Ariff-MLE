# DESIGN

> Draft skeleton. Every number below must come from a command run in this repo; until then it stays `TODO`.

## 1. Overview
TODO: one paragraph + flow diagram (see `docs/03-architecture.md`).

| Requirement | Mechanism |
|---|---|
| Latest info wins | TODO (projection ordered by source `updatedAt`, not arrival) |
| No lost companies | TODO (append-only event log, reject table, ack after commit) |
| Fast | TODO (pgvector HNSW, vector passed as parameter) |

## 2. What the feed contains
TODO: table from `scripts/profile_feed.py` (problem, count, handling). Note anything not in `docs/02-feed-findings.md`.

## 3. Decisions
| Decision | Choice | Why | Rejected alternative |
|---|---|---|---|
| TODO | | | |

## 4. Assumptions
TODO: numbered list (see `docs/01-requirements.md`; items reserved for the human are decided there).

## 5. Results (pasted from real runs)
TODO: reconcile output, test counts and mutation checks, `EXPLAIN (ANALYZE)`, p50/p95/p99 with machine description.

## 6. Deep dive (<= 1 hour)
TODO: one of recommendation quality / latency / monitoring / LLM explanations design.

## 7. 10x volume
TODO.

## 8. What's left
TODO.

## 9. AI tools used
TODO: which tools, for what, what I wrote or changed myself, what I reviewed. Must be true; do not remove.
