# Test plan ("whether it works and keeps working")

## Unit (no Docker)
Table-driven tests for every row of `02-feed-findings.md`: registration number rules; the four timestamp shapes
equal the same instant; junk timestamps -> none; state casing/aliases; repetition collapse and the IQR-based length cap (outlier flagged in `warnings`, company kept); out-of-window timestamp flagged, event kept; NUL
stripping; `parse_message` for each reject reason, struck-off, data-missing, data-as-string, missing timestamp;
same record in two timestamp formats hashes identically, different content hashes differently; consumer batching
(`size` cutoff, `wait` flush) with a plain `asyncio.Queue`.

## Integration (real Postgres + pgvector; use a separate `companies_test` database; a fake counting embedder)
1. Newer replaces older; older arriving late is ignored; newer null does not erase; no timestamp never beats a real one; equal timestamps resolve the same in both orders.
2. **Replay twice** -> identical snapshot, no new events.
3. **Shuffle** the real feed (3 seeds, different batch sizes) -> identical snapshot to in-order. This is the proof of F4.
4. **Four concurrent consumers** (threads, own connections, interleaved slices) -> identical snapshot. Prove the test is real by removing the lock once: it must fail.
5. **No lost companies**: set of stored regs == set of normalised regs parsed from the feed (2,000); `sum(seen_count)` of rejects == number of unattributable lines; the two add up to the line count.
6. Poison messages land in the reject table with the right reason; identical poison is counted, not duplicated.
7. Struck-off: sets status and keeps data; a late older upsert cannot resurrect; a newer upsert reactivates; a data-less upsert does not change status.
8. Embeddings: identical text embedded once; unchanged text not re-embedded; changed text gets a new vector; changed model name re-embeds; fallback text until a real description arrives.
9. Failure isolation: an embedder that raises on one text only rejects that message; a closed connection raises `OperationalError` (so the consumer retries instead of acking).

## API (FastAPI TestClient on a seeded DB)
Shape and ordering; self excluded; identical-description ties prefer the same state; struck-off hidden for the default `portal` role and visible with `X-Role: analyst`, bad role -> 422;
lower-case/padded reg number works; `limit` and filters; no-description is 200 + reason; 404 and 422 are problem+json;
detail endpoint; probes; DB outage -> problem+json, no traceback.

## Mutation habit
After a green run, break the code on purpose (oldest-wins ordering; ignore struck-off; remove the lock) and confirm tests go red.
Put one sentence about this in DESIGN.md.

## Reconcile script (independent oracle)
Re-read the feed with the parser, fold it in plain Python (newest non-null per field; status rule), diff against the DB:
missing, unexpected, wrong values; also print rejects expected vs stored. Exit code 1 on mismatch. Run it after every `just publish`.

## Measurements to put in DESIGN.md (run them, do not estimate)
Reconcile output; `just test` totals; `EXPLAIN (ANALYZE)` of the similarity query (look for `Index Scan using ...hnsw`);
p50/p95/p99 of 500 requests; embedding throughput; replay time.
