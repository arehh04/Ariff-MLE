# Requirements

## Functional
| ID | Requirement | How to prove it |
|---|---|---|
| F1 | Consume `company-events` continuously; ack only after the database commit | kill the consumer mid-publish, restart, counts still right |
| F2 | One row per company, keyed by normalised `registrationNumber` | reconcile script |
| F3 | Store a 384-d embedding of the business description in pgvector | `count(embedding)` |
| F4 | **Latest info wins** by `updatedAt` (source time), never by arrival order | shuffled replay gives an identical database |
| F5 | **No lost companies**: every distinct valid registration number in the feed is stored and fetchable | reconcile: feed vs DB, with every rejected message accounted for |
| F6 | Service: given a company, return the top-k similar, ranked, with the fields to draw a card | API tests |
| F7 | Similarity search runs in Postgres (pgvector) | `EXPLAIN` in DESIGN.md |
| F8 | Errors returned in a documented, consistent format | API tests |

## Non-functional
- Fast enough to render while a company page loads: measure p50/p95 and report them (target p95 < 100 ms locally).
- Idempotent (replay N times, same state) and order-independent.
- Laptop CPU, no paid APIs. Reproducible from the README.

## Assumptions (decided by the human; see AGENTS.md "Decisions made"; write them in DESIGN.md)
1. Timestamps without a zone: assume Malaysia time (+08:00), because ~97% of the feed carries +08:00. Integers = epoch **milliseconds**.
2. Identity = `strip().upper()` of a **string** registration number. Non-strings (ints) are junk.
3. `data` is a snapshot, but a newer null/empty field must not erase a known value (the feed merges several sources).
4. `company-struck-off` = deregistered: keep the company (not "lost"), mark its status. Visibility depends on the caller's role
   (header `X-Role`): the default `portal` role does not see struck-off companies; `analyst` does. A later newer upsert reactivates
   the company (status comes from the newest status-bearing event). The header is a stub, not real authentication.
5. `state` is unreliable (conflicts with descriptions): store it, normalise casing, do not embed it.
6. A company with no description is still stored and fetchable; it may have no embedding; the API explains why (200 + reason).
7. The registry owns the queue: declare it `durable=True`, **no other arguments**; no dead-letter exchange is possible.
8. Model: default is `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (MiniLM, 384-d, CPU, English+Malay); a hashing embedder is used for unit tests and as the
   documented fallback if `fastembed` will not install. The model name is stored per row so the model can be swapped later.
9. Data preparation flags, never deletes: description length outliers (IQR upper fence replaces the fixed cap; expect ~512, verify),
   timestamps outside a sane window around the feed's dates, and normalisation of state and text. Flagged values get a `warnings`
   entry; no company is dropped for being an outlier (F5 still holds).

## Cut order if you run late
deep dive -> filters on the API -> caching/metrics -> Dockerfile/CI. **Never cut:** tests for latest-wins, the reject
path, replay idempotency, reconcile, DESIGN.md (with AI disclosure).

## Out of scope (list under "what's left")
Auth, rate limiting, full observability stack, LLM explanations (design only if you choose it as the deep dive).
