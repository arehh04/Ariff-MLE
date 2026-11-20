# 08 Proofs that it keeps working

Context: `docs/06-test-plan.md` items 2-4 and "Reconcile script".

Task:
1. Integration tests: replay twice -> identical snapshot; shuffle the real feed (3 seeds, different batch sizes) -> identical
   snapshot; 4 concurrent consumers (threads, separate connections, interleaved slices) -> identical snapshot;
   no-lost-companies (2000 stored, rejects accounted for). The snapshot excludes bookkeeping timestamps.
2. Mutation checks, one at a time, each shown red then green again: oldest-wins ordering; status rule ignored; advisory lock removed
   (the concurrency test must fail, run it a few times). Restore each change.
3. `scripts/reconcile.py`: independent fold of the feed in plain Python vs the database: missing, unexpected, wrong values, struck-off,
   no-embedding count, rejects expected vs stored; exit code 1 on mismatch. Run it after `just publish`.

Acceptance: outputs shown; explain in 5 sentences why the concurrency test would catch a missing lock.
Commit: `test: replay, order and concurrency proofs; reconcile script`.
